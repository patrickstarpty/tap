"""Owned, disposable MySQL gates and frozen 0005 upgrade evidence.

No URL supplied by the shell is ever used to mutate a database. Each run creates
one local Compose project; only that project's temporary resources are removed.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, create_engine, inspect, select
from sqlalchemy.engine import Connection, make_url

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "0005_projection_lineage"
PROJECT_SCOPE_REVISION = "0007_project_scope_backfill"
LEGACY_TIME = datetime(2026, 9, 4, 12, 34, 56, 123456)
# Deliberately frozen, independent of current ORM definitions. Future migrations
# must extend preservation assertions rather than regenerating historical rows.
BASELINE_ROWS: dict[str, list[dict[str, Any]]] = {
    "chat_turn": [
        {
            "turn_id": "legacy-turn",
            "chat_id": "legacy-chat",
            "client_request_id": "legacy-request",
            "message": "Legacy knowledge question",
            "state": "completed",
            "last_sequence": 1,
            "created_at": LEGACY_TIME,
        }
    ],
    "chat_event": [
        {
            "event_id": "legacy-event",
            "turn_id": "legacy-turn",
            "sequence": 1,
            "event_type": "turn.completed",
            "payload": {"state": "completed"},
            "schema_version": 1,
            "occurred_at": LEGACY_TIME,
        }
    ],
    "turn_snapshot": [
        {
            "turn_id": "legacy-turn",
            "last_sequence": 1,
            "snapshot": {"state": "completed"},
            "snapshot_version": 1,
            "updated_at": LEGACY_TIME,
        }
    ],
    "outbox": [
        {
            "outbox_id": "legacy-outbox",
            "command_id": "legacy-command",
            "aggregate_type": "turn",
            "aggregate_id": "legacy-turn",
            "sequence": 1,
            "message_type": "turn.process_requested",
            "status": "published",
            "attempt_count": 2,
            "next_attempt_at": LEGACY_TIME,
            "created_at": LEGACY_TIME,
            "published_at": LEGACY_TIME,
            "claim_token": "legacy-claim",
        }
    ],
    "knowledge_document": [
        {
            "document_id": "legacy-document",
            "filename": "legacy.md",
            "media_type": "text/markdown",
            "source_content_hash": "sha256:" + "a" * 64,
            "dedupe_key": "sha256:" + "b" * 64,
            "current_revision_id": None,
            "promoted_blob_locator": "documents/legacy/original.md",
            "reservation_parser_version": "parser-v1",
            "reservation_chunker_version": "chunker-v1",
            "reservation_pipeline_version": "pipeline-v1",
            "status": "ready",
            "stage": "ready",
            "chunk_count": 1,
            "activated_at": LEGACY_TIME,
            "created_at": LEGACY_TIME,
            "updated_at": LEGACY_TIME,
        }
    ],
    "knowledge_document_revision": [
        {
            "revision_id": "legacy-revision",
            "document_id": "legacy-document",
            "source_content_hash": "sha256:" + "a" * 64,
            "original_blob_locator": "documents/legacy/original.md",
            "normalized_blob_locator": "documents/legacy/normalized.json",
            "chunks_blob_locator": "documents/legacy/chunks.json",
            "embeddings_blob_locator": "documents/legacy/embeddings.json",
            "parser_version": "parser-v1",
            "chunker_version": "chunker-v1",
            "pipeline_version": "pipeline-v1",
            "created_at": LEGACY_TIME,
        }
    ],
    "knowledge_ingestion_job": [
        {
            "job_id": "legacy-job",
            "revision_id": "legacy-revision",
            "kind": "ingest",
            "attempt": 1,
            "status": "completed",
            "stage": "ready",
            "stage_results_json": {"chunks": 1},
            "next_attempt_at": LEGACY_TIME,
            "created_at": LEGACY_TIME,
            "updated_at": LEGACY_TIME,
            "completed_at": LEGACY_TIME,
        }
    ],
    "knowledge_chunk_manifest": [
        {
            "chunk_id": "legacy-chunk",
            "logical_chunk_id": "logical-chunk",
            "revision_id": "legacy-revision",
            "ordinal": 0,
            "root_id": "legacy-document",
            "anchor_json": {"page": 1},
            "chunk_content_hash": "sha256:" + "c" * 64,
            "embedding_model_version": "embedding-v1",
            "index_version": "index-v1",
            "created_at": LEGACY_TIME,
        }
    ],
    "knowledge_answer_snapshot": [
        {
            "trace_id": "legacy-trace",
            "query_hash": "sha256:" + "d" * 64,
            "selected_revisions_json": ["legacy-revision"],
            "created_at": LEGACY_TIME,
        }
    ],
    "knowledge_citation_snapshot": [
        {
            "citation_id": "legacy-citation",
            "trace_id": "legacy-trace",
            "document_id": "legacy-document",
            "revision_id": "legacy-revision",
            "chunk_id": "legacy-chunk",
            "source_content_hash": "sha256:" + "a" * 64,
            "chunk_content_hash": "sha256:" + "c" * 64,
            "anchor_json": {"page": 1},
            "created_at": LEGACY_TIME,
        }
    ],
    "knowledge_projection_state": [
        {
            "alias_name": "legacy-alias",
            "generation": 2,
            "physical_collection": "legacy-collection",
            "updated_at": LEGACY_TIME,
        }
    ],
    "knowledge_projection_fence": [
        {
            "alias_name": "legacy-alias",
            "revision_id": "legacy-revision",
            "document_id": "legacy-document",
            "created_at": LEGACY_TIME,
        }
    ],
    "knowledge_projection_cleanup": [
        {
            "alias_name": "legacy-alias",
            "physical_collection": "legacy-predecessor",
            "generation": 1,
            "created_at": LEGACY_TIME,
        }
    ],
    "knowledge_projection_lineage": [
        {
            "alias_name": "legacy-alias",
            "physical_collection": "legacy-collection",
            "operation_id": "legacy-operation",
            "predecessor_collection": "legacy-predecessor",
            "predecessor_generation": 1,
            "generation": 2,
            "status": "active",
            "created_at": LEGACY_TIME,
            "updated_at": LEGACY_TIME,
        }
    ],
}


def validate_revision(revision: str) -> str:
    """Accept a literal revision at/after 0005 on a single linear ancestry."""
    config = Config(str(ROOT / "apps/backend/alembic.ini"))
    config.set_main_option("path_separator", "os")
    scripts = ScriptDirectory.from_config(config)
    try:
        revisions = list(scripts.walk_revisions())
        if len(scripts.get_heads()) != 1 or len(scripts.get_bases()) != 1:
            raise ValueError("migration ancestry must be a single chain")
        for item in revisions:
            if (
                isinstance(item.down_revision, tuple)
                or item.dependencies
                or item.branch_labels
            ):
                raise ValueError("migration ancestry must be linear and unlabelled")
        identifiers = [item.revision for item in revisions]
        if revision not in identifiers or identifiers.index(
            revision
        ) > identifiers.index(BASELINE):
            raise ValueError("expected an exact migration revision at or after 0005")
    except Exception as error:
        raise ValueError("invalid migration revision or ancestry") from error
    return revision


def validate_isolated_database(url: str, project: str) -> None:
    match = re.fullmatch(r"tap-schema-([a-f0-9]{12})", project)
    parsed = make_url(url)
    if (
        match is None
        or parsed.drivername != "mysql+pymysql"
        or parsed.host != "127.0.0.1"
        or parsed.port is None
        or not 1024 <= parsed.port <= 65535
        or parsed.port == 3306
        or parsed.database != f"tap_schema_{match.group(1)}"
        or set(parsed.query) - {"charset"}
    ):
        raise ValueError("refusing a shared, default, or non-loopback database")


def _normalize_check_sql(expression: str) -> str:
    """Remove MySQL reflection decoration without changing quoted SQL content."""
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(expression):
        character = expression[position]
        if character in "'\"`":
            quote = character
            start = position
            position += 1
            while position < len(expression):
                if expression[position] == "\\":
                    position = min(position + 2, len(expression))
                elif expression[position] == quote:
                    position += 1
                    if position < len(expression) and expression[position] == quote:
                        position += 1
                    else:
                        break
                else:
                    position += 1
            quoted = expression[start:position]
            # Only unquote ordinary identifiers; meaningful quoted whitespace or
            # escaped delimiters must never compare equal to different names.
            if quote == "`" and re.fullmatch(r"`[A-Za-z_][A-Za-z0-9_$]*`", quoted):
                tokens.append(("sql", quoted[1:-1]))
            else:
                tokens.append(("quoted", quoted))
        elif character.isspace():
            while position < len(expression) and expression[position].isspace():
                position += 1
            tokens.append(("space", " "))
        elif word := re.match(r"[A-Za-z_][A-Za-z0-9_$]*", expression[position:]):
            value = word.group()
            position += len(value)
            if not (
                value.lower() in {"_utf8mb4", "_utf8", "_ascii"}
                and position < len(expression)
                and expression[position] == "'"
            ):
                tokens.append(("sql", value))
        else:
            tokens.append(("sql", character))
            position += 1

    while tokens:
        if tokens[0][0] == "space":
            tokens.pop(0)
            continue
        if tokens[-1][0] == "space":
            tokens.pop()
            continue
        if tokens[0] != ("sql", "(") or tokens[-1] != ("sql", ")"):
            break
        depth = 0
        for offset, token in enumerate(tokens):
            depth += (token == ("sql", "(")) - (token == ("sql", ")"))
            if depth == 0:
                break
        if offset != len(tokens) - 1:
            break
        tokens = tokens[1:-1]
    return "".join(value for _, value in tokens)


def schema_differences(
    connection: Connection, metadata: MetaData
) -> list[dict[str, Any]]:
    """Compare columns/types/defaults/FKs/indexes plus primary/check constraints."""
    context = MigrationContext.configure(
        connection,
        opts={
            "compare_type": True,
            "compare_server_default": True,
        },
    )
    differences: list[dict[str, Any]] = [
        {"kind": "alembic", "detail": str(diff)}
        for diff in compare_metadata(context, metadata)
    ]
    inspector = inspect(connection)
    for name in sorted(set(inspector.get_table_names()) & set(metadata.tables)):
        table = metadata.tables[name]
        expected_pk = list(table.primary_key.columns.keys())
        actual_pk = inspector.get_pk_constraint(name)["constrained_columns"]
        if expected_pk != actual_pk:
            differences.append(
                {
                    "kind": "primary_key",
                    "table": name,
                    "expected": expected_pk,
                    "actual": actual_pk,
                }
            )
        from sqlalchemy import CheckConstraint

        expected_checks = {
            (item.name, _normalize_check_sql(str(item.sqltext)))
            for item in table.constraints
            if isinstance(item, CheckConstraint)
        }
        actual_checks = {
            (item.get("name"), _normalize_check_sql(item["sqltext"]))
            for item in inspector.get_check_constraints(name)
        }
        if expected_checks != actual_checks:
            differences.append({"kind": "check_constraints", "table": name})
    return differences


def _run(arguments: list[str], *, env: Mapping[str, str], timeout: int = 240) -> str:
    try:
        result = subprocess.run(
            arguments,
            cwd=ROOT,
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("local gate command could not complete") from error
    if result.returncode:
        # Neither provider credentials nor database URLs may enter reports.
        raise RuntimeError(
            f"local gate command failed ({Path(arguments[0]).name}, exit {result.returncode})"
        )
    return result.stdout.strip()


def _local_environment() -> dict[str, str]:
    allowed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "UV_CACHE_DIR",
        "UV_PYTHON_INSTALL_DIR",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "LANG",
        "LC_ALL",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env["PYTHONPATH"] = str(ROOT / "apps/backend/src")
    env["UV_NO_SYNC"] = "1"
    return env


@dataclass(frozen=True)
class IsolatedMysql:
    url: str
    project: str

    def upgrade(self, revision: str) -> None:
        self._migrate("upgrade", revision)

    def downgrade(self, revision: str) -> None:
        self._migrate("downgrade", revision)

    def _migrate(self, direction: str, revision: str) -> None:
        validate_isolated_database(self.url, self.project)
        env = _local_environment()
        env["TAP_ALEMBIC_DATABASE_URL"] = self.url
        _run(
            [
                "uv",
                "run",
                "--project",
                "apps/backend",
                "alembic",
                "-c",
                "apps/backend/alembic.ini",
                direction,
                revision,
            ],
            env=env,
        )


def _interrupt_gate(number: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt("migration gate interrupted")


@contextmanager
def isolated_mysql() -> Iterator[IsolatedMysql]:
    """Own a unique project and database, and remove only those resources on exit."""
    if any(
        os.environ.get(key) for key in ("TAP_DATABASE_URL", "TAP_ALEMBIC_DATABASE_URL")
    ):
        raise ValueError(
            "unset caller database URLs before running an isolated migration gate"
        )
    env = _local_environment()
    context = _run(["docker", "context", "show"], env=env)
    endpoint = _run(
        [
            "docker",
            "context",
            "inspect",
            context,
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        ],
        env=env,
    )
    if not endpoint.startswith("unix://"):
        raise ValueError("migration gates require a local Docker socket")
    token = uuid.uuid4().hex[:12]
    project = f"tap-schema-{token}"
    database = f"tap_schema_{token}"
    password = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix=f"{project}-") as directory:
        compose_file = Path(directory) / "compose.json"
        compose_file.write_text(
            json.dumps(
                {
                    "services": {
                        "mysql": {
                            "image": "mysql:8.4.6",
                            "environment": {
                                "MYSQL_ROOT_PASSWORD": password,
                                "MYSQL_DATABASE": database,
                                "MYSQL_USER": "tap_gate",
                                "MYSQL_PASSWORD": password,
                            },
                            "ports": ["127.0.0.1::3306"],
                            "command": [
                                "mysqld",
                                "--character-set-server=utf8mb4",
                                "--collation-server=utf8mb4_0900_ai_ci",
                            ],
                            "healthcheck": {
                                "test": [
                                    "CMD-SHELL",
                                    'mysqladmin ping -h 127.0.0.1 -uroot -p"$$MYSQL_ROOT_PASSWORD" --silent',
                                ],
                                "interval": "2s",
                                "timeout": "5s",
                                "retries": 60,
                                "start_period": "20s",
                            },
                        }
                    }
                }
            )
        )
        compose_file.chmod(0o600)
        command = [
            "docker",
            "--context",
            context,
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(compose_file),
            "-p",
            project,
        ]
        previous_handlers = {
            number: signal.signal(number, _interrupt_gate)
            for number in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            _run([*command, "up", "-d", "--wait", "--wait-timeout", "180"], env=env)
            address = _run([*command, "port", "mysql", "3306"], env=env)
            if re.fullmatch(r"127\.0\.0\.1:\d+", address) is None:
                raise ValueError("MySQL must be published only on loopback")
            url = f"mysql+pymysql://tap_gate:{password}@{address}/{database}?charset=utf8mb4"
            validate_isolated_database(url, project)
            yield IsolatedMysql(url=url, project=project)
        finally:
            for number in previous_handlers:
                signal.signal(number, signal.SIG_IGN)
            try:
                _run([*command, "down", "--volumes", "--remove-orphans"], env=env)
            finally:
                for number, previous in previous_handlers.items():
                    signal.signal(number, previous)


def seed_baseline(connection: Connection) -> dict[str, list[dict[str, Any]]]:
    """Insert frozen old data using the old database schema, never current ORM."""
    historical = MetaData()
    historical.reflect(connection, only=list(BASELINE_ROWS))
    for name, rows in BASELINE_ROWS.items():
        connection.execute(historical.tables[name].insert(), rows)
    documents = historical.tables["knowledge_document"]
    connection.execute(
        documents.update()
        .where(documents.c.document_id == "legacy-document")
        .values(current_revision_id="legacy-revision")
    )
    return {
        name: [
            dict(row)
            for row in connection.execute(select(historical.tables[name])).mappings()
        ]
        for name in BASELINE_ROWS
    }


def assert_preserved(
    connection: Connection, before: dict[str, list[dict[str, Any]]], revision: str
) -> dict[str, int]:
    """Fail closed until a new revision explicitly registers its data assertions."""
    if revision not in {BASELINE, "0006_validation_identity", PROJECT_SCOPE_REVISION}:
        raise ValueError(
            "data preservation assertions are not registered for this revision"
        )
    if set(before) != set(BASELINE_ROWS) or any(not rows for rows in before.values()):
        raise ValueError(
            "baseline preservation evidence must cover every nonempty table"
        )
    after = MetaData()
    after.reflect(connection, only=list(before))
    counts = {}
    for name, expected in before.items():
        table = after.tables[name]
        # Compare frozen original columns only, ordered by stable primary key.
        columns = list(expected[0])
        actual = [
            dict(row)
            for row in connection.execute(
                select(*(table.c[key] for key in columns)).order_by(
                    *table.primary_key.columns
                )
            ).mappings()
        ]
        expected = sorted(
            expected,
            key=lambda row: tuple(row[key.name] for key in table.primary_key.columns),
        )
        if actual != expected:
            raise ValueError(f"baseline data changed in {name}")
        counts[name] = len(actual)
    if revision in {"0006_validation_identity", PROJECT_SCOPE_REVISION}:
        assert_identity_seed(connection)
    if revision == PROJECT_SCOPE_REVISION:
        assert_scope_backfill(connection)
    return counts


def assert_identity_seed(connection: Connection) -> dict[str, str]:
    identity = MetaData()
    identity.reflect(connection, only=["enterprise", "project", "actor_principal"])
    expected = {
        "enterprise": {"enterprise_id": "local", "enabled": True},
        "project": {
            "project_id": "tapper-demo",
            "enterprise_id": "local",
            "enabled": True,
        },
        "actor_principal": {
            "actor_id": "tapper-local-user",
            "enterprise_id": "local",
            "principal_type": "VALIDATION",
            "enabled": True,
        },
    }
    for name, row in expected.items():
        actual = [
            dict(value)
            for value in connection.execute(select(identity.tables[name])).mappings()
        ]
        if actual != [row]:
            raise ValueError(f"invalid identity seed in {name}")
    return {
        "enterprise": "local",
        "project": "tapper-demo",
        "actor": "tapper-local-user",
        "principal_type": "VALIDATION",
    }


def assert_scope_backfill(connection: Connection) -> None:
    from tap.platform.messaging.mysql_outbox import validate_outbox_row

    metadata = MetaData()
    metadata.reflect(connection, only=list(BASELINE_ROWS))
    expected = {
        "enterprise_id": "local",
        "project_id": "tapper-demo",
        "actor_id": "tapper-local-user",
        "identity_mode": "validation",
        "identity_origin": "VALIDATION",
    }
    for name in BASELINE_ROWS:
        table = metadata.tables[name]
        for row in connection.execute(select(table)).mappings():
            if any(row[field] != value for field, value in expected.items()):
                raise ValueError(f"invalid scope backfill in {name}")
            if name == "outbox":
                validate_outbox_row(dict(row))


def assert_scope_constraints(connection: Connection) -> None:
    """Probe real relational isolation, and roll back all temporary test records."""
    from sqlalchemy.exc import DBAPIError

    metadata = MetaData()
    metadata.reflect(connection)
    # Reflection opens a transaction; rollback before the explicit evidence scope.
    connection.rollback()
    transaction = connection.begin()
    try:
        connection.execute(
            metadata.tables["enterprise"]
            .insert()
            .values(enterprise_id="other-enterprise")
        )
        connection.execute(
            metadata.tables["actor_principal"]
            .insert()
            .values(
                enterprise_id="other-enterprise",
                actor_id="foreign-actor",
                principal_type="VALIDATION",
            )
        )
        connection.execute(
            metadata.tables["project"]
            .insert()
            .values(enterprise_id="local", project_id="other-project")
        )
        turns = metadata.tables["chat_turn"]
        base = dict(connection.execute(select(turns)).mappings().one())
        connection.execute(
            turns.insert().values(
                **{**base, "turn_id": "other-turn", "project_id": "other-project"}
            )
        )
        documents = metadata.tables["knowledge_document"]
        document = dict(connection.execute(select(documents)).mappings().one())
        connection.execute(
            documents.insert().values(
                **{
                    **document,
                    "document_id": "other-document",
                    "project_id": "other-project",
                    "current_revision_id": None,
                }
            )
        )
        bad_rows = [
            (
                turns,
                {
                    **base,
                    "turn_id": "bad-enterprise",
                    "client_request_id": "bad-enterprise",
                    "enterprise_id": "other-enterprise",
                },
            ),
            (
                turns,
                {
                    **base,
                    "turn_id": "bad-actor",
                    "client_request_id": "bad-actor",
                    "actor_id": "foreign-actor",
                },
            ),
            (
                turns,
                {
                    **base,
                    "turn_id": "missing-scope",
                    "client_request_id": "missing-scope",
                    "project_id": None,
                },
            ),
        ]
        events = metadata.tables["chat_event"]
        event = dict(connection.execute(select(events)).mappings().one())
        bad_rows.append(
            (
                events,
                {
                    **event,
                    "event_id": "cross-project-child",
                    "project_id": "other-project",
                    "sequence": 2,
                },
            )
        )
        revisions = metadata.tables["knowledge_document_revision"]
        revision = dict(connection.execute(select(revisions)).mappings().one())
        bad_rows.append(
            (
                revisions,
                {
                    **revision,
                    "revision_id": "cross-project-revision",
                    "project_id": "other-project",
                },
            )
        )
        citations = metadata.tables["knowledge_citation_snapshot"]
        citation = dict(connection.execute(select(citations)).mappings().one())
        bad_rows.append(
            (
                citations,
                {
                    **citation,
                    "citation_id": "cross-project-citation",
                    "project_id": "other-project",
                },
            )
        )
        outbox = metadata.tables["outbox"]
        event_row = dict(connection.execute(select(outbox)).mappings().one())
        bad_rows.append(
            (
                outbox,
                {
                    **event_row,
                    "outbox_id": "missing-envelope",
                    "command_id": "missing-envelope",
                    "envelope": None,
                },
            )
        )
        for table, row in bad_rows:
            savepoint = connection.begin_nested()
            try:
                connection.execute(table.insert().values(**row))
            except DBAPIError as error:
                savepoint.rollback()
                if (
                    error.orig is None
                    or not error.orig.args
                    or error.orig.args[0] not in {1048, 1452, 3819}
                ):
                    raise
            else:
                savepoint.rollback()
                raise ValueError(f"missing Project constraint in {table.name}")
        answers = metadata.tables["knowledge_answer_snapshot"]
        connection.execute(answers.delete())
        if connection.execute(select(citations)).first() is not None:
            raise ValueError("scoped citation cascade was not preserved")
    finally:
        transaction.rollback()


def assert_scope_rejection_paths(database: IsolatedMysql, engine: Any) -> None:
    """Prove failed preflights leave schema and historical rows available for repair."""
    from sqlalchemy import text

    # First reject the lossy downgrade before it can remove a scope column.
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO project(project_id,enterprise_id) VALUES ('downgrade-probe','local')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO chat_turn SELECT 'downgrade-turn', chat_id, client_request_id, message, state, last_sequence, created_at, enterprise_id, 'downgrade-probe', actor_id, identity_mode, identity_origin FROM chat_turn WHERE turn_id='legacy-turn'"
            )
        )
    try:
        database.downgrade("0006_validation_identity")
    except RuntimeError:
        pass
    else:
        raise ValueError("unsafe cross-Project downgrade was accepted")
    with engine.begin() as connection:
        if "project_id" not in {
            item["name"] for item in inspect(connection).get_columns("chat_turn")
        }:
            raise ValueError("downgrade preflight ran after destructive DDL")
        connection.execute(text("DELETE FROM chat_turn WHERE turn_id='downgrade-turn'"))
        connection.execute(
            text("DELETE FROM project WHERE project_id='downgrade-probe'")
        )
    database.downgrade("0006_validation_identity")
    for statement, restore in (
        (
            "UPDATE outbox SET message_type='unknown.event'",
            "UPDATE outbox SET message_type='turn.process_requested'",
        ),
        ("UPDATE outbox SET sequence=-1", "UPDATE outbox SET sequence=1"),
        (
            "UPDATE knowledge_projection_fence SET revision_id='orphan-revision'",
            "UPDATE knowledge_projection_fence SET revision_id='legacy-revision'",
        ),
    ):
        with engine.begin() as connection:
            connection.execute(text(statement))
        try:
            database.upgrade(PROJECT_SCOPE_REVISION)
        except RuntimeError:
            pass
        else:
            raise ValueError("invalid legacy data was accepted")
        with engine.begin() as connection:
            if "project_id" in {
                item["name"] for item in inspect(connection).get_columns("chat_turn")
            }:
                raise ValueError("legacy validation occurred after DDL")
            connection.execute(text(restore))
    database.upgrade(PROJECT_SCOPE_REVISION)
    with engine.connect() as connection:
        assert_scope_backfill(connection)


def run_schema_gate() -> dict[str, Any]:
    from tap.platform.db.registry import load_authoritative_metadata

    with isolated_mysql() as database:
        database.upgrade("head")
        engine = create_engine(database.url)
        try:
            with engine.connect() as connection:
                differences = schema_differences(
                    connection, load_authoritative_metadata()
                )
            return {
                "status": "failed" if differences else "passed",
                "gate": "schema-drift",
                "tables": len(load_authoritative_metadata().tables),
                "differences": differences,
            }
        finally:
            engine.dispose()


def run_migration_gate(revision: str) -> dict[str, Any]:
    validate_revision(revision)
    if revision not in {BASELINE, "0006_validation_identity", PROJECT_SCOPE_REVISION}:
        raise ValueError(
            "register data preservation assertions before checking this revision"
        )
    with isolated_mysql() as database:
        database.upgrade(BASELINE)
        engine = create_engine(database.url)
        try:
            with engine.begin() as connection:
                before = seed_baseline(connection)
            database.upgrade(revision)
            with engine.connect() as connection:
                counts = assert_preserved(connection, before, revision)
            identity_result: dict[str, Any] = {}
            if revision in {"0006_validation_identity", PROJECT_SCOPE_REVISION}:
                with engine.connect() as connection:
                    identity_result["identity_seed"] = assert_identity_seed(connection)
                database.downgrade(BASELINE)
                with engine.connect() as connection:
                    assert_preserved(connection, before, BASELINE)
                    if {"enterprise", "project", "actor_principal"} & set(
                        inspect(connection).get_table_names()
                    ):
                        raise ValueError(
                            "identity downgrade did not remove owned tables"
                        )
                database.upgrade(revision)
                with engine.connect() as connection:
                    assert_preserved(connection, before, revision)
                identity_result["downgrade_replay"] = "passed"
            if revision == PROJECT_SCOPE_REVISION:
                with engine.connect() as connection:
                    assert_scope_constraints(connection)
                assert_scope_rejection_paths(database, engine)
                identity_result.update(
                    scope_backfill="passed",
                    constraints="passed",
                    pre_ddl_rejection="passed",
                )
            return {
                **identity_result,
                "status": "passed",
                "gate": "migration-check",
                "revision": revision,
                "preserved_rows": counts,
            }
        finally:
            engine.dispose()
