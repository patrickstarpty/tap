from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
import pytest
from tap_platform.lca.store import metadata
from test_lca_release_migration import load


def test_asset_upgrade_matches_metadata_and_retains_owned_assets(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'assets.db'}")
    migration = load("0012_lca_assets")
    with (
        engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        load("0010_lca_debug_flows").upgrade()
        load("0011_lca_releases").upgrade()
        migration.upgrade()
        assert compare_metadata(MigrationContext.configure(connection), metadata) == []
        migration.downgrade()
        assert "tap_lca_assets" not in inspect(connection).get_table_names()
        migration.upgrade()
        connection.execute(
            text(
                "INSERT INTO tap_lca_assets VALUES ('a','p','u','now','test.txt','application/octet-stream','sha',1,'ref')"
            )
        )
        with pytest.raises(RuntimeError, match="retention"):
            migration.downgrade()
        assert (
            connection.execute(text("SELECT COUNT(*) FROM tap_lca_assets")).scalar()
            == 1
        )
