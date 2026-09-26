from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


@pytest.mark.parametrize(
    "revision,table,insert_sql",
    [
        (
            "0001_report_intake",
            "tap_report_identity_claims",
            "INSERT INTO tap_report_identity_claims VALUES ('claim', 'receipt', '2026-09-26')",
        ),
        (
            "0003_insights_projection",
            "tap_insights_projection_versions",
            "INSERT INTO tap_insights_projection_versions VALUES ('v1', 'active', 1, 0, NULL, NULL, '2026-09-26', NULL)",
        ),
        (
            "0004_insights_queries",
            "tap_insights_queries",
            "INSERT INTO tap_insights_queries VALUES ('query', 'project', 'v1', '{}', '2026-09-25', '2026-09-26', 'UTC', '2026-09-26', 'v1', 1, '{}', '2026-09-26')",
        ),
    ],
)
def test_data_bearing_downgrade_refuses_before_destroying_authority(
    revision, table, insert_sql
):
    path = Path(__file__).parents[2] / "migrations" / "versions" / f"{revision}.py"
    spec = spec_from_file_location(revision, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with (
        engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        module.upgrade()
        connection.execute(text(insert_sql))
        with pytest.raises(RuntimeError, match="retention.*downgrade refused"):
            module.downgrade()
        assert (
            connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 1
        )
        connection.execute(text(f"DELETE FROM {table}"))
        module.downgrade()
        assert table not in inspect(connection).get_table_names()
        module.upgrade()
        assert table in inspect(connection).get_table_names()
