"""Alembic environment for the independent TAP backend schema."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, engine_from_config, inspect, pool, text

from tap_platform.insights.adapters.mysql import metadata


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("TAP_DATABASE_URL")
if not database_url:
    raise RuntimeError("TAP_DATABASE_URL is required for TAP migrations")
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
target_metadata = metadata


def _adopt_interim_version_table(connection: Connection) -> None:
    """Adopt databases created by the interim Task 9 revision at 081c522."""
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if (
        "tap_alembic_version" in tables
        or not {
            "alembic_version",
            "tap_report_receipts",
        }
        <= tables
    ):
        return
    columns = {item["name"] for item in inspector.get_columns("tap_report_receipts")}
    versions = list(connection.execute(text("SELECT version_num FROM alembic_version")))
    if columns.isdisjoint({"manifest_digest"}) and versions == [
        ("0001_report_intake",)
    ]:
        connection.execute(
            text("ALTER TABLE alembic_version RENAME TO tap_alembic_version")
        )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        version_table="tap_alembic_version",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _adopt_interim_version_table(connection)
        # Inspection starts an implicit transaction. End it before Alembic
        # opens the migration transaction that advances the version row.
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table="tap_alembic_version",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
