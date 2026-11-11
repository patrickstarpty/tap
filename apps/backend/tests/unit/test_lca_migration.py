"""Exercise the LCA revision without touching configured database services."""

from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from tap_platform.lca.store import (
    LcaStore,
    drafts,
    idempotency,
    metadata,
    runs,
    snapshots,
)

BACKEND = Path(__file__).parents[2]
REVISION = "0010_lca_debug_flows"
TABLES = {
    "tap_lca_debug_flows",
    "tap_lca_debug_drafts",
    "tap_lca_debug_snapshots",
    "tap_lca_debug_runs",
    "tap_lca_debug_idempotency",
    "tap_lca_debug_recordings",
}


@pytest.fixture
def migration():
    path = BACKEND / "migrations" / "versions" / f"{REVISION}.py"
    assert path.is_file(), "LCA persistence requires an explicit TAP migration"
    spec = spec_from_file_location(REVISION, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine(tmp_path, migration):
    engine = create_engine(f"sqlite:///{tmp_path / 'lca-migration.db'}")
    with (
        engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        migration.upgrade()
    yield engine
    engine.dispose()


def test_lca_revision_continues_the_single_browser_control_chain(migration):
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    directory = ScriptDirectory.from_config(config)
    assert directory.get_heads() == ["0011_lca_releases"]
    assert directory.get_revision(REVISION).down_revision == "0009_browser_control"


def test_migration_matches_store_columns_and_constraints_without_seeding(engine):
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == TABLES
        assert (
            compare_metadata(
                MigrationContext.configure(
                    connection,
                    opts={
                        "include_object": lambda obj,
                        name,
                        kind,
                        reflected,
                        compared: name != "tap_lca_releases"
                    },
                ),
                metadata,
            )
            == []
        )
        for table in TABLES:
            assert (
                connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() == 0
            )
        assert inspect(connection).get_pk_constraint("tap_lca_debug_drafts")[
            "constrained_columns"
        ] == ["flow_id", "version"]
        assert inspect(connection).get_pk_constraint("tap_lca_debug_idempotency")[
            "constrained_columns"
        ] == ["project_id", "kind", "key"]


def test_migrated_store_retains_draft_history_snapshot_and_execution_link(engine):
    store = LcaStore(engine)
    canonical = b'{"title":"' + b"x" * 70000 + b'","steps":[]}'
    flow = store.create_flow("project", "actor", "create", "Debug", canonical)
    store.save_draft("project", "actor", flow["id"], 1, b'{"steps":[]}')
    snapshot = store.freeze_snapshot(
        "project", "actor", flow["id"], 2, "lca-debug-v1", "generator-v1", []
    )
    run = store.create_run(
        "project", "actor", "run", snapshot["id"], "bundle", "a" * 64, {}
    )
    store.link_execution("project", "actor", run["id"], "execution")
    recovered = LcaStore(engine)
    assert recovered.get_flow("project", "actor", flow["id"])["version"] == 2
    assert (
        recovered.get_snapshot("project", "actor", snapshot["id"])["canonical_bytes"]
        == b'{"steps":[]}'
    )
    assert (
        recovered.get_run("project", "actor", run["id"])["execution_id"] == "execution"
    )
    with engine.connect() as connection:
        assert (
            connection.execute(
                select(drafts.c.canonical_bytes).where(drafts.c.version == 1)
            ).scalar_one()
            == canonical
        )


def test_recording_context_is_durable_and_session_identity_is_unique(engine):
    table = metadata.tables["tap_lca_debug_recordings"]
    values = dict(
        session_id="session",
        project_id="project",
        actor_id="actor",
        flow_id="flow",
        draft_version=7,
        allowed_origins=["https://example.test"],
        created_at="2026-11-10T12:00:00+00:00",
    )
    with engine.begin() as connection:
        connection.execute(table.insert().values(**values))
    with engine.connect() as connection:
        assert dict(connection.execute(select(table)).mappings().one()) == values
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(table.insert().values(**{**values, "flow_id": "other-flow"}))


@pytest.mark.parametrize(
    "constraint", ["draft", "snapshot", "execution", "idempotency"]
)
def test_migration_enforces_duplicate_identity_constraints(engine, constraint):
    store = LcaStore(engine)
    flow = store.create_flow("project", "actor", "create", "Debug", b"{}")
    snapshot = store.freeze_snapshot(
        "project", "actor", flow["id"], 1, "lca-debug-v1", "generator-v1", []
    )
    run = store.create_run(
        "project", "actor", "run", snapshot["id"], "bundle", "a" * 64, {}
    )
    table, values = {
        "draft": (
            drafts,
            dict(
                flow_id=flow["id"],
                version=1,
                canonical_bytes=b"{}",
                provenance={},
                updated_at=flow["updated_at"],
            ),
        ),
        "snapshot": (snapshots, {**snapshot, "id": "another-snapshot"}),
        "execution": (runs, {**run, "id": "another-run"}),
        "idempotency": (
            idempotency,
            dict(
                project_id="project",
                kind="flow",
                key="create",
                actor_id="other-actor",
                digest="b" * 64,
                resource_id="another-flow",
            ),
        ),
    }[constraint]
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(table.insert().values(**values))


@pytest.mark.parametrize("table", sorted(TABLES))
def test_nonempty_downgrade_refuses_before_dropping_any_lca_table(
    engine, migration, table
):
    # Populate just one authority table, so every retention guard is exercised.
    values = {
        "tap_lca_debug_flows": dict(
            id="flow",
            project_id="p",
            actor_id="a",
            created_at="now",
            title="T",
            version=1,
            canonical_bytes=b"{}",
            provenance={},
            updated_at="now",
        ),
        "tap_lca_debug_drafts": dict(
            flow_id="flow",
            version=1,
            canonical_bytes=b"{}",
            provenance={},
            updated_at="now",
        ),
        "tap_lca_debug_snapshots": dict(
            id="snapshot",
            project_id="p",
            actor_id="a",
            created_at="now",
            flow_id="flow",
            version=1,
            canonical_bytes=b"{}",
            definition_digest="d",
            schema_version="v1",
            generator_version="v1",
            allowed_origins=[],
            identity_digest="i",
        ),
        "tap_lca_debug_runs": dict(
            id="run",
            project_id="p",
            actor_id="a",
            created_at="now",
            flow_id="flow",
            snapshot_id="snapshot",
            definition_digest="d",
            bundle_id="bundle",
            input_digest="i",
            classification="debug",
            execution_key="key",
            execution_request={},
            execution_id=None,
        ),
        "tap_lca_debug_idempotency": dict(
            project_id="p",
            kind="flow",
            key="key",
            actor_id="a",
            digest="d",
            resource_id="flow",
        ),
        "tap_lca_debug_recordings": dict(
            session_id="session",
            project_id="p",
            actor_id="a",
            flow_id="flow",
            draft_version=1,
            allowed_origins=[],
            created_at="now",
        ),
    }
    with (
        engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        connection.execute(metadata.tables[table].insert().values(**values[table]))
        with pytest.raises(RuntimeError, match="retention.*downgrade refused"):
            migration.downgrade()
        assert set(inspect(connection).get_table_names()) == TABLES
        assert connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() == 1


def test_empty_downgrade_and_reupgrade_preserve_preexisting_browser_data(
    engine, migration
):
    with (
        engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        connection.execute(text("CREATE TABLE tap_browser_sessions (id VARCHAR(36))"))
        connection.execute(text("INSERT INTO tap_browser_sessions VALUES ('legacy')"))
        migration.downgrade()
        assert set(inspect(connection).get_table_names()) == {"tap_browser_sessions"}
        migration.upgrade()
        assert set(inspect(connection).get_table_names()) == TABLES | {
            "tap_browser_sessions"
        }
        assert connection.execute(
            text("SELECT id FROM tap_browser_sessions")
        ).all() == [("legacy",)]


def test_mysql_upgrade_ddl_uses_full_size_binary_columns_and_matches_metadata(
    migration,
):
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration.upgrade()
    ddl = output.getvalue()
    assert ddl.count("CREATE TABLE ") == 6
    assert ddl.count("canonical_bytes MEDIUMBLOB NOT NULL") == 3
    assert "PRIMARY KEY (project_id, kind, `key`)" in ddl
    assert "UNIQUE (flow_id, identity_digest)" in ddl
    assert "UNIQUE (execution_key)" in ddl
    for table in metadata.tables.values():
        if table.name == "tap_lca_releases":
            continue
        expected = str(CreateTable(table).compile(dialect=mysql.dialect())).strip()
        assert " ".join(expected.split()) in " ".join(ddl.split())
