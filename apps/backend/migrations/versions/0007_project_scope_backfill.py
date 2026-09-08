"""Backfill explicit Project scope and frozen compatibility event facts.

MySQL DDL implicitly commits. Validate all historical prerequisites before DDL;
never claim transactional migration rollback or silently repair old data.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import JSON

revision: str = "0007_project_scope_backfill"
down_revision: str | None = "0006_validation_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BATCH_SIZE = 500
SCOPE = {
    "enterprise_id": "local",
    "project_id": "tapper-demo",
    "actor_id": "tapper-local-user",
    "identity_mode": "validation",
    "identity_origin": "VALIDATION",
}
# Frozen 0006 descriptors: never import current application metadata or registries.
TABLES = {
    "outbox": {
        "pk": ["outbox_id"],
        "unique": {"uq_outbox_project_command": ["command_id"]},
        "indexes": {
            "ix_outbox_claim": ["status", "next_attempt_at", "created_at"],
            "ix_outbox_expired_lease": ["status", "lease_until"],
        },
    },
    "chat_turn": {
        "pk": ["turn_id"],
        "unique": {"uq_chat_turn_client_request": ["chat_id", "client_request_id"]},
        "indexes": {},
    },
    "chat_event": {
        "pk": ["event_id"],
        "unique": {"uq_chat_event_turn_sequence": ["turn_id", "sequence"]},
        "indexes": {},
    },
    "turn_snapshot": {"pk": ["turn_id"], "unique": {}, "indexes": {}},
    "knowledge_document": {
        "pk": ["document_id"],
        "unique": {"uq_knowledge_document_dedupe_key": ["dedupe_key"]},
        "indexes": {
            "ix_knowledge_document_status_updated_id": ["status", "updated_at", "document_id"],
            "ix_knowledge_document_reservation_recovery": [
                "activated_at",
                "reservation_expires_at",
                "document_id",
            ],
        },
    },
    "knowledge_document_revision": {
        "pk": ["revision_id"],
        "unique": {
            "uq_knowledge_revision_source_parser": [
                "document_id",
                "source_content_hash",
                "parser_version",
            ]
        },
        "indexes": {},
    },
    "knowledge_ingestion_job": {
        "pk": ["job_id"],
        "unique": {"uq_knowledge_job_revision_kind": ["revision_id", "kind"]},
        "indexes": {
            "ix_knowledge_job_status_retry_created": ["status", "next_attempt_at", "created_at"],
            "ix_knowledge_job_status_lease": ["status", "lease_until"],
        },
    },
    "knowledge_chunk_manifest": {
        "pk": ["chunk_id"],
        "unique": {"uq_knowledge_manifest_revision_ordinal": ["revision_id", "ordinal"]},
        "indexes": {},
    },
    "knowledge_answer_snapshot": {
        "pk": ["trace_id"],
        "unique": {},
        "indexes": {"ix_knowledge_answer_project_retention": ["created_at", "trace_id"]},
    },
    "knowledge_citation_snapshot": {
        "pk": ["citation_id"],
        "unique": {"uq_knowledge_citation_trace_id": ["trace_id", "citation_id"]},
        "indexes": {},
    },
    "knowledge_projection_state": {"pk": ["alias_name"], "unique": {}, "indexes": {}},
    "knowledge_projection_fence": {
        "pk": ["alias_name", "revision_id"],
        "unique": {},
        "indexes": {},
    },
    "knowledge_projection_cleanup": {
        "pk": ["alias_name", "physical_collection"],
        "unique": {},
        "indexes": {},
    },
    "knowledge_projection_lineage": {
        "pk": ["alias_name", "physical_collection"],
        "unique": {"uq_projection_lineage_operation": ["alias_name", "operation_id"]},
        "indexes": {},
    },
}
PARENTS = {
    "chat_event": (("turn_id", "chat_turn", "turn_id", None),),
    "turn_snapshot": (("turn_id", "chat_turn", "turn_id", None),),
    "knowledge_document": (
        ("current_revision_id", "knowledge_document_revision", "revision_id", None),
    ),
    "knowledge_document_revision": (("document_id", "knowledge_document", "document_id", None),),
    "knowledge_ingestion_job": (
        ("revision_id", "knowledge_document_revision", "revision_id", None),
    ),
    "knowledge_chunk_manifest": (
        ("revision_id", "knowledge_document_revision", "revision_id", None),
    ),
    "knowledge_citation_snapshot": (
        ("trace_id", "knowledge_answer_snapshot", "trace_id", "CASCADE"),
    ),
    "knowledge_projection_fence": (
        ("alias_name", "knowledge_projection_state", "alias_name", None),
        ("revision_id", "knowledge_document_revision", "revision_id", None),
        ("document_id", "knowledge_document", "document_id", None),
    ),
    "knowledge_projection_cleanup": (
        ("alias_name", "knowledge_projection_state", "alias_name", None),
    ),
    "knowledge_projection_lineage": (
        ("alias_name", "knowledge_projection_state", "alias_name", None),
    ),
}
OLD_FOREIGN_KEYS = {
    "chat_event": ("fk_chat_event_turn", "turn_id", "chat_turn", "turn_id", None),
    "turn_snapshot": ("fk_turn_snapshot_turn", "turn_id", "chat_turn", "turn_id", None),
    "knowledge_document": (
        "fk_knowledge_document_current_revision",
        "current_revision_id",
        "knowledge_document_revision",
        "revision_id",
        None,
    ),
    "knowledge_document_revision": (
        "fk_knowledge_revision_document",
        "document_id",
        "knowledge_document",
        "document_id",
        None,
    ),
    "knowledge_ingestion_job": (
        "fk_knowledge_job_revision",
        "revision_id",
        "knowledge_document_revision",
        "revision_id",
        None,
    ),
    "knowledge_chunk_manifest": (
        "fk_knowledge_manifest_revision",
        "revision_id",
        "knowledge_document_revision",
        "revision_id",
        None,
    ),
    "knowledge_citation_snapshot": (
        "fk_knowledge_citation_answer",
        "trace_id",
        "knowledge_answer_snapshot",
        "trace_id",
        "CASCADE",
    ),
}
COMPATIBILITY = {
    "turn.process_requested": {"turn", "chat_turn"},
    "chat.event_appended": {"turn", "chat_turn"},
    "knowledge.ingestion_requested": {"knowledge_document"},
    "knowledge.deletion_requested": {"knowledge_document"},
}


def _rows(table: sa.Table) -> Iterator[list[dict[str, Any]]]:
    connection = op.get_bind()
    keys = [table.c[name] for name in TABLES[table.name]["pk"]]
    last = None
    while True:
        statement = sa.select(table).order_by(*keys).limit(BATCH_SIZE)
        if last is not None:
            statement = statement.where(sa.tuple_(*keys) > sa.tuple_(*last))
        batch = [dict(row) for row in connection.execute(statement).mappings()]
        if not batch:
            return
        yield batch
        last = tuple(batch[-1][key.name] for key in keys)


def _envelope(row: dict[str, Any]) -> tuple[dict[str, Any], str]:
    message_type = row["message_type"]
    if (
        message_type not in COMPATIBILITY
        or row["aggregate_type"] not in COMPATIBILITY[message_type]
    ):
        raise ValueError("0007 rejects unknown historical Outbox event/aggregate before DDL")
    for name in ("outbox_id", "command_id", "aggregate_id"):
        value = row[name]
        if not isinstance(value, str) or not value or len(value) > 128 or value.strip() != value:
            raise ValueError("0007 rejects invalid historical Outbox identifier before DDL")
    sequence = row["sequence"]
    if sequence is not None and (type(sequence) is not int or not 0 <= sequence <= 2**63 - 1):
        raise ValueError("0007 rejects invalid historical Outbox sequence before DDL")
    created = row["created_at"]
    if not isinstance(created, datetime):
        raise ValueError("0007 rejects invalid historical Outbox timestamp before DDL")
    occurred = (
        created.replace(tzinfo=timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    envelope = {
        "event_id": row["outbox_id"],
        "event_type": message_type,
        "schema_version": 1,
        "occurred_at": occurred,
        "scope_kind": "PROJECT",
        **{name: value for name, value in SCOPE.items() if name != "identity_origin"},
        "aggregate_type": row["aggregate_type"],
        "aggregate_id": row["aggregate_id"],
        "aggregate_version": sequence if sequence is not None else 0,
        "correlation_id": row["outbox_id"],
        "causation_id": None,
        "idempotency_key": row["command_id"],
        "payload": {"aggregateId": row["aggregate_id"], "sequence": sequence},
    }
    content = {
        key: value
        for key, value in envelope.items()
        if key
        not in {"event_id", "occurred_at", "correlation_id", "causation_id", "idempotency_key"}
    }
    canonical = json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return envelope, "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _preflight(metadata: sa.MetaData) -> None:
    connection = op.get_bind()
    identity = connection.execute(
        sa.text(
            "SELECT 1 FROM enterprise e "
            "JOIN project p ON p.enterprise_id=e.enterprise_id "
            "JOIN actor_principal a ON a.enterprise_id=e.enterprise_id "
            "WHERE e.enterprise_id='local' AND e.enabled=1 "
            "AND p.project_id='tapper-demo' AND p.enabled=1 "
            "AND a.actor_id='tapper-local-user' AND a.enabled=1 "
            "AND a.principal_type='VALIDATION'"
        )
    ).first()
    if identity is None:
        raise ValueError("0007 requires the registered validation identity before DDL")
    for batch in _rows(metadata.tables["outbox"]):
        for row in batch:
            _envelope(row)
    for name, links in PARENTS.items():
        child = metadata.tables[name]
        for column, parent_name, target, _ in links:
            parent = metadata.tables[parent_name]
            orphan = connection.execute(
                sa.select(child.c[column])
                .select_from(child.outerjoin(parent, child.c[column] == parent.c[target]))
                .where(child.c[column].is_not(None), parent.c[target].is_(None))
                .limit(1)
            ).first()
            if orphan is not None:
                raise ValueError(f"0007 rejects historical orphan in {name} before DDL")


def upgrade() -> None:
    connection = op.get_bind()
    historical = sa.MetaData()
    historical.reflect(connection, only=list(TABLES))
    _preflight(historical)
    for name in TABLES:
        for field in SCOPE:
            op.add_column(
                name,
                sa.Column(
                    field,
                    sa.String(16 if field in {"identity_mode", "identity_origin"} else 128),
                    nullable=True,
                ),
            )
    op.add_column("outbox", sa.Column("envelope", JSON, nullable=True))
    op.add_column("outbox", sa.Column("event_content_digest", sa.String(71), nullable=True))
    current = sa.MetaData()
    current.reflect(connection, only=list(TABLES))
    for name, description in TABLES.items():
        table = current.tables[name]
        for batch in _rows(table):
            for row in batch:
                values = dict(SCOPE)
                if name == "outbox":
                    envelope, digest = _envelope(row)
                    values.update(envelope=envelope, event_content_digest=digest)
                connection.execute(
                    table.update()
                    .where(*(table.c[key] == row[key] for key in description["pk"]))
                    .values(**values)
                )
        if (
            connection.execute(
                sa.select(table)
                .where(sa.or_(*(table.c[field].is_(None) for field in SCOPE)))
                .limit(1)
            ).first()
            is not None
        ):
            raise ValueError(f"0007 incomplete scope backfill in {name}")
    # Remove old child constraints before replacing their supporting uniqueness.
    for name, (constraint, *_) in OLD_FOREIGN_KEYS.items():
        op.drop_constraint(constraint, name, type_="foreignkey")
    for name, description in TABLES.items():
        op.create_unique_constraint(
            f"uq_{name}_project_pk", name, ["project_id", *description["pk"]]
        )
        for constraint, columns in description["unique"].items():
            op.drop_index("command_id" if name == "outbox" else constraint, table_name=name)
            op.create_unique_constraint(constraint, name, ["project_id", *columns])
        for index, columns in description["indexes"].items():
            if index != "ix_knowledge_answer_project_retention":
                op.drop_index(index, table_name=name)
            op.create_index(index, name, ["project_id", *columns])
    # MySQL retains the indexes it once created for old FKs. They no longer serve
    # a declared constraint; remove only those frozen FK names when present.
    for name, (constraint, *_) in OLD_FOREIGN_KEYS.items():
        if constraint in {item["name"] for item in sa.inspect(connection).get_indexes(name)}:
            op.drop_index(constraint, table_name=name)
    for name in TABLES:
        op.create_foreign_key(
            f"fk_{name}_scope_project",
            name,
            "project",
            ["enterprise_id", "project_id"],
            ["enterprise_id", "project_id"],
        )
        op.create_foreign_key(
            f"fk_{name}_scope_actor",
            name,
            "actor_principal",
            ["enterprise_id", "actor_id"],
            ["enterprise_id", "actor_id"],
        )
        for number, (column, parent, target, ondelete) in enumerate(PARENTS.get(name, ())):
            op.create_foreign_key(
                f"fk_{name}_project_parent_{number}",
                name,
                parent,
                ["project_id", column],
                ["project_id", target],
                ondelete=ondelete,
            )
        for field in SCOPE:
            op.alter_column(
                name,
                field,
                existing_type=sa.String(
                    16 if field in {"identity_mode", "identity_origin"} else 128
                ),
                nullable=False,
            )
    op.alter_column("outbox", "envelope", existing_type=JSON, nullable=False)
    op.alter_column("outbox", "event_content_digest", existing_type=sa.String(71), nullable=False)
    op.create_check_constraint(
        "ck_outbox_envelope_object", "outbox", "json_type(envelope) = 'OBJECT'"
    )


def downgrade() -> None:
    connection = op.get_bind()
    current = sa.MetaData()
    current.reflect(connection, only=list(TABLES))
    # Reinstating global uniqueness is lossy after cross-Project reuse: refuse it
    # before any DDL rather than deleting or rewriting valid business records.
    for name, description in TABLES.items():
        table = current.tables[name]
        for columns in description["unique"].values():
            keys = [table.c[key] for key in columns]
            duplicate = connection.execute(
                sa.select(*keys)
                .where(*(key.is_not(None) for key in keys))
                .group_by(*keys)
                .having(sa.func.count() > 1)
                .limit(1)
            ).first()
            if duplicate is not None:
                raise ValueError(f"0007 downgrade cannot restore global uniqueness in {name}")
    op.drop_constraint("ck_outbox_envelope_object", "outbox", type_="check")
    for name in reversed(TABLES):
        for number in range(len(PARENTS.get(name, ()))):
            op.drop_constraint(f"fk_{name}_project_parent_{number}", name, type_="foreignkey")
        op.drop_constraint(f"fk_{name}_scope_actor", name, type_="foreignkey")
        op.drop_constraint(f"fk_{name}_scope_project", name, type_="foreignkey")
    # Dropping a column only shortens MySQL's implicit composite FK indexes.
    # Remove the now-unowned indexes first so replay cannot collide with their names.
    for name in TABLES:
        names = {f"fk_{name}_scope_actor", f"fk_{name}_scope_project"}
        names.update(
            f"fk_{name}_project_parent_{number}" for number in range(len(PARENTS.get(name, ())))
        )
        for index in sa.inspect(connection).get_indexes(name):
            if index["name"] in names:
                op.drop_index(index["name"], table_name=name)
    for name, description in TABLES.items():
        for constraint, columns in description["unique"].items():
            op.drop_index(constraint, table_name=name)
            op.create_unique_constraint(
                "command_id" if name == "outbox" else constraint, name, columns
            )
        for index, columns in description["indexes"].items():
            op.drop_index(index, table_name=name)
            if index != "ix_knowledge_answer_project_retention":
                op.create_index(index, name, columns)
        op.drop_index(f"uq_{name}_project_pk", table_name=name)
        for field in SCOPE:
            op.drop_column(name, field)
    op.drop_column("outbox", "event_content_digest")
    op.drop_column("outbox", "envelope")
    for name, (constraint, column, parent, target, ondelete) in OLD_FOREIGN_KEYS.items():
        op.create_foreign_key(constraint, name, parent, [column], [target], ondelete=ondelete)
