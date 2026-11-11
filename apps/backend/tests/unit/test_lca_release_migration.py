from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.autogenerate import compare_metadata
from sqlalchemy import create_engine, text
from tap_platform.lca.store import metadata


def load(name):
    spec = spec_from_file_location(
        name, Path(__file__).parents[2] / "migrations/versions" / f"{name}.py"
    )
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_upgrade_matches_metadata_and_refuses_loss(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    base, releases = load("0010_lca_debug_flows"), load("0011_lca_releases")
    with (
        engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        base.upgrade()
        releases.upgrade()
        assert (
            compare_metadata(
                MigrationContext.configure(
                    connection,
                    opts={
                        "include_object": lambda obj,
                        name,
                        kind,
                        reflected,
                        compared: name != "tap_lca_assets"
                    },
                ),
                metadata,
            )
            == []
        )
        connection.execute(
            text(
                "INSERT INTO tap_lca_releases VALUES ('r','p','a','now','f','s',1,'title','run','digest')"
            )
        )
        with pytest.raises(RuntimeError, match="retention"):
            releases.downgrade()
        assert (
            connection.execute(text("SELECT COUNT(*) FROM tap_lca_releases")).scalar()
            == 1
        )
