"""Exercise the drift checker with real SQLAlchemy schemas and unsafe targets."""

from __future__ import annotations

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, text


@pytest.mark.parametrize(
    "url,project",
    [
        ("mysql+pymysql://tap:tap@127.0.0.1:3306/tap", "tap-tapper-demo"),
        (
            "mysql+pymysql://tap:tap@db.example:13306/tap_schema_aaaaaaaaaaaa",
            "tap-schema-aaaaaaaaaaaa",
        ),
        ("mysql+pymysql://tap:tap@127.0.0.1:13306/tap", "tap-schema-aaaaaaaaaaaa"),
        (
            "mysql+pymysql://tap:tap@127.0.0.1:3306/tap_schema_aaaaaaaaaaaa",
            "tap-schema-aaaaaaaaaaaa",
        ),
        ("sqlite:///tap_schema_aaaaaaaaaaaa", "tap-schema-aaaaaaaaaaaa"),
    ],
)
def test_shared_default_and_remote_database_targets_are_rejected(url: str, project: str) -> None:
    from scripts.migration_support import validate_isolated_database

    with pytest.raises(ValueError):
        validate_isolated_database(url, project)


def test_only_alembic_version_is_ignored_and_missing_lineage_fails() -> None:
    from scripts.migration_support import schema_differences

    metadata = MetaData()
    Table("knowledge_projection_lineage", metadata, Column("id", Integer, primary_key=True))
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        assert schema_differences(connection, MetaData()) == []
        assert schema_differences(connection, metadata)
        metadata.create_all(connection)
        assert schema_differences(connection, metadata) == []
        connection.execute(text("CREATE TABLE unexpected (id INTEGER)"))
        assert schema_differences(connection, metadata)


def test_columns_indexes_unique_and_primary_constraints_are_compared() -> None:
    from scripts.migration_support import schema_differences

    metadata = MetaData()
    Table(
        "sample",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(20), nullable=False, unique=True, index=True),
    )
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sample (id INTEGER, name VARCHAR(10))"))
        differences = schema_differences(connection, metadata)
        assert len(differences) >= 3


def test_sigterm_cleans_only_the_owned_compose_project() -> None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, signal
from scripts import migration_support as support
for name in ('TAP_DATABASE_URL', 'TAP_ALEMBIC_DATABASE_URL'):
    os.environ.pop(name, None)
projects = []
def docker_boundary(args, **kwargs):
    if args[1:3] == ['context', 'show']:
        return 'desktop-linux'
    if args[1:3] == ['context', 'inspect']:
        return 'unix:///local/docker.sock'
    project = args[args.index('-p') + 1]
    projects.append(project)
    if 'down' in args:
        owned = len(set(projects)) == 1 and project.startswith('tap-schema-')
        print('owned-cleanup' if owned else 'unsafe', flush=True)
    if 'port' in args:
        return '127.0.0.1:23306'
    return ''
support._run = docker_boundary
with support.isolated_mysql():
    os.kill(os.getpid(), signal.SIGTERM)
""",
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "apps/backend/src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "owned-cleanup" in result.stdout
    assert "unsafe" not in result.stdout


def test_cli_refuses_caller_database_urls_before_invoking_docker() -> None:
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/check-schema-drift.py")],
        env={
            **os.environ,
            "PYTHONPATH": str(root / "apps/backend/src"),
            "PATH": "",
            "TAP_DATABASE_URL": "mysql+pymysql://owner:do-not-expose@127.0.0.1:3306/tap",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["error"] == "ValueError"
    assert "do-not-expose" not in result.stdout + result.stderr


@pytest.mark.parametrize("actual_literal", ["'`OBJECT`'", "'OB  JECT'"])
def test_check_drift_preserves_literal_backticks_and_whitespace(actual_literal: str) -> None:
    from scripts.migration_support import schema_differences
    from sqlalchemy import CheckConstraint

    expected_literal = "'OBJECT'" if "`" in actual_literal else "'OB JECT'"
    expected = MetaData()
    Table(
        "sample",
        expected,
        Column("id", Integer, primary_key=True),
        Column("name", String(30)),
        CheckConstraint(f"name = {expected_literal}", name="ck_sample_name"),
    )
    actual = MetaData()
    Table(
        "sample",
        actual,
        Column("id", Integer, primary_key=True),
        Column("name", String(30)),
        CheckConstraint(f"name = {actual_literal}", name="ck_sample_name"),
    )
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            actual.create_all(connection)
            assert {"kind": "check_constraints", "table": "sample"} in schema_differences(
                connection, expected
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "reflected,expected",
    [
        (" (json_type(`envelope`)  = _utf8mb4'OBJECT') ", "json_type(envelope) = 'OBJECT'"),
        ("((`name` = _utf8mb4'('))", "name = '('"),
        ("((`name` = _utf8mb4')'))", "name = ')'"),
        ("((`name` = _utf8mb4'a''(b'))", "name = 'a''(b'"),
        (r"((`name` = _utf8mb4'a\'(b'))", r"name = 'a\'(b'"),
        ('((`name` = "(quoted  `text`)"))', 'name = "(quoted  `text`)"'),
    ],
)
def test_check_normalization_accepts_mysql_syntax_without_touching_literals(
    reflected: str, expected: str
) -> None:
    from scripts.migration_support import _normalize_check_sql

    assert _normalize_check_sql(reflected) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "name = '_utf8mb4''OBJECT'",
        r"name = 'a\'`(  b'",
        'name = "a\\"`(  b"',
        "`name with  spaces` = 'OBJECT'",
    ],
)
def test_check_normalization_preserves_quoted_text_and_escapes(expression: str) -> None:
    from scripts.migration_support import _normalize_check_sql

    assert _normalize_check_sql(expression) == expression
