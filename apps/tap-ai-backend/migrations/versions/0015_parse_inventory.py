"""Add durable parse inventory and preserve older revisions as unreviewed."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision: str = "0015_parse_inventory"
down_revision: str | None = "0014_test_management"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _item_id(source_revision_id: str) -> str:
    payload = json.dumps(
        {
            "kind": "document",
            "locator": "document:legacy",
            "schema": "parse-inventory-v1",
            "sourceRevisionId": source_revision_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return "pi_" + hashlib.sha256(payload).hexdigest()


def _row_id(source_revision_id: str, attempt: int, item_id: str) -> str:
    payload = f"parse-inventory-row-v1\0{source_revision_id}\0{attempt}\0{item_id}".encode()
    return "pir_" + hashlib.sha256(payload).hexdigest()


def upgrade() -> None:
    op.add_column(
        "knowledge_document_revision",
        sa.Column("parse_inventory_attempt", sa.Integer()),
    )
    op.add_column(
        "knowledge_document_revision",
        sa.Column("parser_config_digest", sa.String(71)),
    )
    op.add_column(
        "knowledge_document_revision",
        sa.Column("parse_inventory_digest", sa.String(71)),
    )
    op.create_table(
        "knowledge_parse_inventory",
        sa.Column("inventory_row_id", sa.String(128), primary_key=True),
        sa.Column("source_revision_id", sa.String(128), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("item_kind", sa.String(24), nullable=False),
        sa.Column("locator", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(128)),
        sa.Column("artifact_digest", sa.String(71), nullable=False),
        sa.Column("decision_actor_id", sa.String(128)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("enterprise_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("identity_origin", sa.String(16), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "inventory_row_id",
            name="uq_knowledge_parse_inventory_project_pk",
        ),
        sa.UniqueConstraint(
            "project_id",
            "source_revision_id",
            "attempt",
            "item_id",
            name="uq_parse_inventory_attempt_item",
        ),
        sa.UniqueConstraint(
            "project_id",
            "source_revision_id",
            "attempt",
            "ordinal",
            name="uq_parse_inventory_attempt_ordinal",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "source_revision_id"],
            [
                "knowledge_document_revision.project_id",
                "knowledge_document_revision.revision_id",
            ],
            name="fk_knowledge_parse_inventory_project_parent_0",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name="fk_knowledge_parse_inventory_scope_project",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name="fk_knowledge_parse_inventory_scope_actor",
        ),
    )
    op.create_index(
        "ix_parse_inventory_revision_attempt",
        "knowledge_parse_inventory",
        ["project_id", "source_revision_id", "attempt"],
    )

    connection = op.get_bind()
    revisions = connection.execute(
        sa.text(
            "SELECT revision_id, source_content_hash, created_at, enterprise_id, project_id, "
            "actor_id, identity_mode, identity_origin FROM knowledge_document_revision"
        )
    ).mappings()
    for row in revisions:
        item_id = _item_id(row["revision_id"])
        connection.execute(
            sa.text(
                "INSERT INTO knowledge_parse_inventory "
                "(inventory_row_id, source_revision_id, attempt, item_id, ordinal, item_kind, "
                "locator, status, reason, artifact_digest, decision_actor_id, created_at, "
                "enterprise_id, project_id, actor_id, identity_mode, identity_origin) VALUES "
                "(:inventory_row_id, :source_revision_id, 0, :item_id, 0, 'document', "
                "'document:legacy', 'needs_review', 'historical-unreviewed', :artifact_digest, "
                "NULL, :created_at, :enterprise_id, :project_id, :actor_id, :identity_mode, "
                ":identity_origin)"
            ),
            {
                **row,
                "inventory_row_id": _row_id(row["revision_id"], 0, item_id),
                "source_revision_id": row["revision_id"],
                "item_id": item_id,
                "artifact_digest": row["source_content_hash"],
            },
        )
    connection.execute(
        sa.text(
            "UPDATE knowledge_document_revision SET parse_inventory_attempt=0 "
            "WHERE parse_inventory_attempt IS NULL"
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    current = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM knowledge_document_revision "
            "WHERE parser_config_digest IS NOT NULL "
            "OR parse_inventory_digest IS NOT NULL OR parse_inventory_attempt <> 0"
        )
    ).scalar_one()
    nonlegacy = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM knowledge_parse_inventory WHERE attempt <> 0 "
            "OR status <> 'needs_review' OR reason <> 'historical-unreviewed' "
            "OR locator <> 'document:legacy'"
        )
    ).scalar_one()
    if current or nonlegacy:
        raise RuntimeError("parse inventory history requires retention; downgrade refused")
    op.drop_table("knowledge_parse_inventory")
    op.drop_column("knowledge_document_revision", "parse_inventory_digest")
    op.drop_column("knowledge_document_revision", "parser_config_digest")
    op.drop_column("knowledge_document_revision", "parse_inventory_attempt")
