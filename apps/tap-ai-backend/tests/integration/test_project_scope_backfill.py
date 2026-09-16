"""Real owned 0007 backfill, preservation, constraints and pre-DDL rejection."""

import os

import pytest

REVISION = "0007_project_scope_backfill"


def test_project_scope_nonempty_upgrade_and_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.getenv("TAP_RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires owned isolated MySQL")
    from scripts.migration_support import run_migration_gate

    monkeypatch.delenv("TAP_DATABASE_URL", raising=False)
    monkeypatch.delenv("TAP_ALEMBIC_DATABASE_URL", raising=False)
    result = run_migration_gate(REVISION)
    assert result["status"] == "passed"
    assert len(result["preserved_rows"]) == 14
    assert result["scope_backfill"] == "passed"
    assert result["constraints"] == "passed"
    assert result["downgrade_replay"] == "passed"
    assert result["pre_ddl_rejection"] == "passed"


def test_project_scope_real_mysql_multiple_batches_and_nullable_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observe real 0007 DDL and backfill stages without changing frozen fixtures."""
    if os.getenv("TAP_RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires owned isolated MySQL")
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from scripts.migration_support import (
        BASELINE,
        BASELINE_ROWS,
        assert_preserved,
        isolated_mysql,
        seed_baseline,
    )
    from sqlalchemy import MetaData, create_engine, inspect, select

    monkeypatch.delenv("TAP_DATABASE_URL", raising=False)
    monkeypatch.delenv("TAP_ALEMBIC_DATABASE_URL", raising=False)
    migration_file = (
        Path(__file__).resolve().parents[2] / "migrations/versions/0007_project_scope_backfill.py"
    )
    specification = importlib.util.spec_from_file_location(
        "project_scope_stage_migration", migration_file
    )
    assert specification is not None and specification.loader is not None
    migration = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(migration)
    with isolated_mysql() as database:
        database.upgrade("0006_validation_identity")
        engine = create_engine(database.url)
        try:
            with engine.begin() as connection:
                seed_baseline(connection)
                historical = MetaData()
                historical.reflect(connection, only=list(BASELINE_ROWS))
                for table_name in ("outbox", "knowledge_projection_lineage"):
                    table = historical.tables[table_name]
                    original = dict(connection.execute(select(table)).mappings().one())
                    extra = []
                    for number in range(migration.BATCH_SIZE + 2):
                        if table_name == "outbox":
                            identity = {
                                "outbox_id": f"batch-outbox-{number:04}",
                                "command_id": f"batch-command-{number:04}",
                            }
                        else:
                            identity = {
                                "physical_collection": f"batch-collection-{number:04}",
                                "operation_id": f"batch-operation-{number:04}",
                            }
                        extra.append({**original, **identity})
                    connection.execute(table.insert(), extra)
                before = {
                    name: [
                        dict(row)
                        for row in connection.execute(select(historical.tables[name])).mappings()
                    ]
                    for name in BASELINE_ROWS
                }
            pages = {}
            observed_nullable = []
            original_rows = migration._rows
            with engine.connect() as connection:

                def observe_rows(table):
                    if "project_id" in table.c:
                        if not observed_nullable:
                            inspector = inspect(connection)
                            for name in BASELINE_ROWS:
                                columns = {
                                    item["name"]: item for item in inspector.get_columns(name)
                                }
                                assert all(columns[field]["nullable"] for field in migration.SCOPE)
                            outbox_columns = {
                                item["name"]: item for item in inspector.get_columns("outbox")
                            }
                            assert outbox_columns["envelope"]["nullable"]
                            assert outbox_columns["event_content_digest"]["nullable"]
                            assert_preserved(connection, before, BASELINE)
                            observed_nullable.append(True)
                        pages[table.name] = []
                    for batch in original_rows(table):
                        if "project_id" in table.c:
                            pages[table.name].append(len(batch))
                        yield batch

                monkeypatch.setattr(migration, "_rows", observe_rows)
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
                connection.commit()
                assert observed_nullable == [True]
                assert pages["outbox"] == [migration.BATCH_SIZE, 3]
                assert pages["knowledge_projection_lineage"] == [migration.BATCH_SIZE, 3]
                assert set(pages) == set(BASELINE_ROWS)
                for name in BASELINE_ROWS:
                    columns = {item["name"]: item for item in inspect(connection).get_columns(name)}
                    assert all(not columns[field]["nullable"] for field in migration.SCOPE)
                outbox_columns = {
                    item["name"]: item for item in inspect(connection).get_columns("outbox")
                }
                assert not outbox_columns["envelope"]["nullable"]
                assert not outbox_columns["event_content_digest"]["nullable"]
                counts = assert_preserved(connection, before, REVISION)
                assert counts["outbox"] == migration.BATCH_SIZE + 3
                assert counts["knowledge_projection_lineage"] == migration.BATCH_SIZE + 3
        finally:
            engine.dispose()
