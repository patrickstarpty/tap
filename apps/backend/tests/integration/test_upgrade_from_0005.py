"""The upgrade harness must reject ambiguous revisions and preserve nonempty data."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("revision", ["head", "heads", "base", "+1", "0005", "does_not_exist", ""])
def test_nonliteral_or_unknown_revision_is_rejected_before_docker(revision: str) -> None:
    from scripts.migration_support import validate_revision

    with pytest.raises(ValueError):
        validate_revision(revision)


def test_baseline_fixture_is_nonempty_and_contains_frozen_lineage_and_citations() -> None:
    from scripts.migration_support import BASELINE_ROWS, validate_revision

    assert validate_revision("0005_projection_lineage") == "0005_projection_lineage"
    assert len(BASELINE_ROWS) == 14
    assert all(BASELINE_ROWS.values())
    assert BASELINE_ROWS["chat_turn"][0]["turn_id"] == "legacy-turn"
    assert BASELINE_ROWS["knowledge_citation_snapshot"][0]["chunk_id"] == "legacy-chunk"
    assert BASELINE_ROWS["knowledge_projection_lineage"][0]["operation_id"] == "legacy-operation"


def test_invalid_revision_cli_exits_nonzero_with_a_safe_report() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-migration.py"), "head"],
        env={**os.environ, "PYTHONPATH": str(ROOT / "apps/backend/src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "failed"
    assert "Traceback" not in result.stderr


def test_preservation_rejects_empty_evidence_and_changed_historical_values() -> None:
    from scripts.migration_support import BASELINE_ROWS, assert_preserved
    from sqlalchemy import Column, Integer, MetaData, Table, create_engine

    engine = create_engine("sqlite://")
    metadata = MetaData()
    for name in BASELINE_ROWS:
        Table(name, metadata, Column("id", Integer, primary_key=True))
    before = {name: [{"id": 1}] for name in BASELINE_ROWS}
    with engine.begin() as connection:
        metadata.create_all(connection)
        for table in metadata.tables.values():
            connection.execute(table.insert().values(id=1))
        assert len(assert_preserved(connection, before, "0005_projection_lineage")) == 14
        with pytest.raises(ValueError, match="nonempty"):
            assert_preserved(connection, {}, "0005_projection_lineage")
        connection.execute(metadata.tables["knowledge_projection_lineage"].update().values(id=2))
        with pytest.raises(ValueError, match="knowledge_projection_lineage"):
            assert_preserved(connection, before, "0005_projection_lineage")
