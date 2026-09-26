from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json

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


def test_semantics_upgrade_classifies_legacy_queries_without_rewriting_results():
    directory = Path(__file__).parents[2] / "migrations" / "versions"
    modules = []
    for name in (
        "0003_insights_projection",
        "0004_insights_queries",
        "0005_insights_semantics",
    ):
        spec = spec_from_file_location(name, directory / f"{name}.py")
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    engine = create_engine("sqlite://")
    with (
        engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        modules[0].upgrade()
        modules[1].upgrade()
        values = {
            "old-v1": {"metrics": []},
            "old-v2": {"metrics": [], "report_coverage": []},
        }
        for query_id, payload in values.items():
            connection.execute(
                text(
                    "INSERT INTO tap_insights_queries VALUES (:query_id, 'project', 'insights-metrics-v1', '{}', '2026-09-25', '2026-09-26', 'UTC', '2026-09-26', 'v1', 1, :payload, '2026-09-26')"
                ),
                {"query_id": query_id, "payload": json.dumps(payload)},
            )
        modules[2].upgrade()
        rows = connection.execute(
            text(
                "SELECT query_id, fact_semantics_version, result_payload FROM tap_insights_queries ORDER BY query_id"
            )
        ).all()
        assert [(row[0], row[1]) for row in rows] == [("old-v1", 1), ("old-v2", 2)]
        assert {row[0]: json.loads(row[2]) for row in rows} == values
        with pytest.raises(RuntimeError, match="query history requires retention"):
            modules[2].downgrade()
        assert "fact_semantics_version" in {
            column["name"]
            for column in inspect(connection).get_columns("tap_insights_queries")
        }
        connection.execute(text("DELETE FROM tap_insights_queries"))
        modules[2].downgrade()
        modules[2].upgrade()
        assert "payload_version" in {
            column["name"]
            for column in inspect(connection).get_columns(
                "tap_insights_projection_batches"
            )
        }
