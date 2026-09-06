"""Migration ownership must not depend on application import order."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

EXPECTED_TABLES = {
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
        and tuple(constraint.columns.keys()) == ("alias_name", "operation_id")
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
