"""Migration ownership must not depend on application import order."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

EXPECTED_TABLES = {
    "knowledge_source",
    "knowledge_source_command",
    "knowledge_source_legacy_map",
    "knowledge_answer_source",
    "knowledge_search_audit",
    "enterprise",
    "project",
    "actor_principal",
    "project_audit",
    "outbox_archive",
    "outbox_dead_letter",
    "knowledge_operator_operation",
    "outbox",
    "chat_turn",
    "chat_event",
    "turn_snapshot",
    "knowledge_document",
    "knowledge_document_revision",
    "knowledge_ingestion_job",
    "knowledge_chunk_manifest",
    "knowledge_answer_snapshot",
    "knowledge_citation_snapshot",
    "knowledge_projection_state",
    "knowledge_projection_fence",
    "knowledge_projection_cleanup",
    "knowledge_projection_lineage",
}


def test_registry_is_complete_in_a_fresh_process_and_excludes_incidental_tables() -> None:
    root = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
from sqlalchemy import Column, Integer, Table
from tap.platform.db.schema import metadata
Table('incidental_table', metadata, Column('id', Integer, primary_key=True))
from tap.platform.db.registry import load_authoritative_metadata
first = load_authoritative_metadata()
first.remove(first.tables['outbox'])
print(json.dumps(sorted(load_authoritative_metadata().tables)))
""",
        ],
        env={**os.environ, "PYTHONPATH": str(root / "apps/backend/src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)) == EXPECTED_TABLES


def test_projection_metadata_preserves_existing_migration_constraints() -> None:
    from sqlalchemy import UniqueConstraint

    from tap.platform.db.registry import load_authoritative_metadata

    metadata = load_authoritative_metadata()
    lineage = metadata.tables["knowledge_projection_lineage"]
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_projection_lineage_operation"
        and tuple(constraint.columns.keys()) == ("project_id", "alias_name", "operation_id")
        for constraint in lineage.constraints
    )
    for table_name, column_name in (
        ("knowledge_projection_state", "updated_at"),
        ("knowledge_projection_fence", "created_at"),
        ("knowledge_projection_cleanup", "created_at"),
        ("knowledge_projection_lineage", "created_at"),
        ("knowledge_projection_lineage", "updated_at"),
    ):
        assert str(metadata.tables[table_name].c[column_name].server_default.arg).lower() == (
            "current_timestamp(6)"
        )


def test_all_business_metadata_requires_project_scope_and_parent_consistency() -> None:
    from sqlalchemy import ForeignKeyConstraint

    from tap.platform.db.registry import load_authoritative_metadata

    metadata = load_authoritative_metadata()
    for name in EXPECTED_TABLES - {"enterprise", "project", "actor_principal"}:
        table = metadata.tables[name]
        for field in (
            "enterprise_id",
            "project_id",
            "actor_id",
            "identity_mode",
            "identity_origin",
        ):
            assert field in table.c, (name, field)
            assert not table.c[field].nullable
            assert table.c[field].server_default is None
        foreign_keys = [
            item for item in table.constraints if isinstance(item, ForeignKeyConstraint)
        ]
        assert any(
            tuple(item.column_keys) == ("enterprise_id", "project_id") for item in foreign_keys
        ), name
        assert any(
            tuple(item.column_keys) == ("enterprise_id", "actor_id") for item in foreign_keys
        ), name
    assert not metadata.tables["outbox"].c.envelope.nullable
    assert not metadata.tables["outbox"].c.event_content_digest.nullable
    for child, columns in (
        ("chat_event", ("project_id", "turn_id")),
        ("knowledge_document_revision", ("project_id", "document_id")),
        ("knowledge_projection_fence", ("project_id", "revision_id")),
    ):
        assert any(
            tuple(item.column_keys) == columns
            for item in metadata.tables[child].foreign_key_constraints
        )


def test_project_audit_metadata_declares_scoped_replay_and_ordering() -> None:
    from sqlalchemy import UniqueConstraint
    from sqlalchemy.dialects.mysql import DATETIME

    from tap.platform.db.registry import load_authoritative_metadata

    metadata = load_authoritative_metadata()
    assert "project_audit" in metadata.tables
    audit = metadata.tables["project_audit"]
    assert tuple(audit.primary_key.columns.keys()) == ("audit_id",)
    assert audit.c.resource_id.nullable
    assert all(
        not column.nullable and column.server_default is None
        for column in audit.c
        if column.name != "resource_id"
    )
    assert audit.c.audit_id.type.collation == "utf8mb4_bin"
    assert audit.c.idempotency_key.type.collation == "utf8mb4_bin"
    assert isinstance(audit.c.occurred_at.type, DATETIME)
    assert audit.c.occurred_at.type.fsp == 6
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(constraint.columns.keys()) == ("enterprise_id", "project_id", "idempotency_key")
        for constraint in audit.constraints
    )
    assert any(
        tuple(index.columns.keys()) == ("enterprise_id", "project_id", "occurred_at", "audit_id")
        for index in audit.indexes
    )
