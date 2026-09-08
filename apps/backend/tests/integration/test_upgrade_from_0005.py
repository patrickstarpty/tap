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


@pytest.mark.parametrize("revision", ["0006_validation_identity", "0007_project_scope_backfill"])
def test_identity_and_project_scope_revision_is_literal_and_registered(revision: str) -> None:
    from scripts.migration_support import validate_revision

    assert validate_revision(revision) == revision


def test_0006_identity_nonempty_upgrade_and_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.getenv("TAP_RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires owned isolated MySQL")
    from scripts.migration_support import run_migration_gate

    # This gate owns a different fresh database; never inherit the suite database.
    monkeypatch.delenv("TAP_DATABASE_URL", raising=False)
    monkeypatch.delenv("TAP_ALEMBIC_DATABASE_URL", raising=False)
    result = run_migration_gate("0006_validation_identity")
    assert result["status"] == "passed"
    assert len(result["preserved_rows"]) == 14
    assert all(result["preserved_rows"].values())
    assert result["identity_seed"] == {
        "enterprise": "local",
        "project": "tapper-demo",
        "actor": "tapper-local-user",
        "principal_type": "VALIDATION",
    }
    assert result["downgrade_replay"] == "passed"


def test_0008_audit_nonempty_upgrade_constraints_and_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.getenv("TAP_RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires owned isolated MySQL")
    from scripts.migration_support import run_migration_gate

    monkeypatch.delenv("TAP_DATABASE_URL", raising=False)
    monkeypatch.delenv("TAP_ALEMBIC_DATABASE_URL", raising=False)
    result = run_migration_gate("0008_project_audit")
    assert result["status"] == "passed"
    assert len(result["preserved_rows"]) == 14
    assert all(result["preserved_rows"].values())
    assert result["scope_backfill"] == "passed"
    assert result["audit_constraints"] == "passed"
    assert result["audit_downgrade_replay"] == "passed"
    assert result["downgrade_replay"] == "passed"


def test_0009_operations_nonempty_upgrade_constraints_and_replay(monkeypatch):
    if os.getenv("TAP_RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires owned isolated MySQL")
    from scripts.migration_support import run_migration_gate

    monkeypatch.delenv("TAP_DATABASE_URL", raising=False)
    monkeypatch.delenv("TAP_ALEMBIC_DATABASE_URL", raising=False)
    result = run_migration_gate("0009_outbox_operations")
    assert result["status"] == "passed"
    assert len(result["preserved_rows"]) == 14
    assert result["operations_constraints"] == "passed"
    assert result["operations_downgrade_replay"] == "passed"


def test_0010_source_revision_is_literal_and_registered():
    from scripts.migration_support import validate_revision

    assert validate_revision("0010_knowledge_sources") == "0010_knowledge_sources"


def test_0010a_source_command_revision_is_literal_and_registered():
    from scripts.migration_support import validate_revision

    assert validate_revision("0010a_source_commands") == "0010a_source_commands"


def test_0011_ai_agent_skill_catalog_revision_is_literal_and_registered():
    from scripts.migration_support import validate_revision

    assert validate_revision("0011_ai_agent_skill_catalog") == "0011_ai_agent_skill_catalog"


def test_0012_conversation_revision_is_literal_and_registered():
    from scripts.migration_support import validate_revision

    assert validate_revision("0012_conversations") == "0012_conversations"


def test_0012_preserves_legacy_chat_as_conversation(monkeypatch):
    if os.getenv("TAP_RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires owned isolated MySQL")
    from scripts.migration_support import run_migration_gate

    monkeypatch.delenv("TAP_DATABASE_URL", raising=False)
    monkeypatch.delenv("TAP_ALEMBIC_DATABASE_URL", raising=False)
    result = run_migration_gate("0012_conversations")
    assert result["status"] == "passed"
    assert result["conversation_backfill"] == "passed"
    assert result["conversation_downgrade_replay"] == "passed"


def test_0012_legacy_conversation_is_readable_through_new_repository(owned_project_mysql):
    import asyncio
    from dataclasses import replace

    import httpx
    from apps.backend.tests.conftest import validation_http_services
    from scripts.migration_support import LEGACY_TIME, seed_baseline
    from sqlalchemy import create_engine, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.entrypoints.tapper_runtime import create_project_audit
    from tap.interfaces.http.app import create_app
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
    from tap.modules.chat.application.conversations import ConversationService
    from tap.modules.chat.domain.conversations import (
        AnswerEvidence,
        FrozenResource,
        GraphContextStatus,
        RetrievalSummary,
        TurnInput,
    )
    from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
    from tap.modules.knowledge.ports.answers import DocumentStateChanged

    owned_project_mysql.downgrade("0005_projection_lineage")
    sync_engine = create_engine(owned_project_mysql.url)
    try:
        with sync_engine.begin() as connection:
            seed_baseline(connection)
            connection.execute(
                text(
                    "INSERT INTO chat_turn (turn_id,chat_id,client_request_id,message,state,"
                    "last_sequence,created_at) SELECT 'legacy-turn-2',chat_id,'legacy-request-2',"
                    "'Second legacy question',state,last_sequence,created_at + INTERVAL 1 SECOND "
                    "FROM chat_turn "
                    "WHERE turn_id='legacy-turn'"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO chat_event (event_id,turn_id,sequence,event_type,payload,"
                    "schema_version,occurred_at) SELECT 'legacy-event-2','legacy-turn-2',"
                    "sequence,event_type,"
                    "payload,schema_version,occurred_at + INTERVAL 1 SECOND "
                    "FROM chat_event "
                    "WHERE event_id='legacy-event'"
                )
            )
    finally:
        sync_engine.dispose()
    owned_project_mysql.upgrade("0012_conversations")

    async def scenario():
        engine = create_async_engine(
            owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy")
        )
        try:
            repository = MysqlConversationRepository(
                async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
            )
            loaded = await repository.load("legacy-chat")
            assert loaded.created_at.replace(tzinfo=None) == LEGACY_TIME
            assert [(turn.turn_id, turn.input_snapshot.value.message) for turn in loaded.turns] == [
                ("legacy-turn", "Legacy knowledge question"),
                ("legacy-turn-2", "Second legacy question"),
            ]
            assert [(event.sequence, event.event_id) for event in loaded.events] == [
                (1, "legacy-event"),
                (2, "legacy-event-2"),
            ]
            async with engine.connect() as connection:
                preserved = (
                    (
                        await connection.execute(
                            text(
                                "SELECT event_id,sequence,payload,occurred_at FROM chat_event "
                                "ORDER BY occurred_at,event_id"
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
            assert [row["sequence"] for row in preserved] == [1, 1]
            assert preserved[1]["payload"] == preserved[0]["payload"]
            app = create_app(
                replace(
                    validation_http_services(),
                    conversations=ConversationService(repository, scope=VALIDATION_SCOPE),
                )
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                first_stream = await client.get(
                    "/api/v1/projects/tapper-demo/conversations/legacy-chat/stream"
                )
                assert first_stream.text.count("id: 1\n") == 1
                assert first_stream.text.count("id: 2\n") == 1
                resumed_stream = await client.get(
                    "/api/v1/projects/tapper-demo/conversations/legacy-chat/stream",
                    headers={"Last-Event-ID": "1"},
                )
                assert "id: 1\n" not in resumed_stream.text
                assert resumed_stream.text.count("id: 2\n") == 1
            documents = MysqlDocumentRepository(
                async_sessionmaker(engine, expire_on_commit=False),
                scope=VALIDATION_SCOPE,
                audit_factory=create_project_audit,
            )
            selected = await documents.load_revision_selection(("legacy-revision",))
            assert selected[0].document_id == "legacy-document"
            with pytest.raises(DocumentStateChanged):
                await documents.load_revision_selection(("missing-revision",))
            service = ConversationService(repository, scope=VALIDATION_SCOPE)
            await service.create(
                "evidence-chat",
                "evidence-turn",
                "evidence-request",
                TurnInput(
                    message="bind legacy evidence",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    identity_mode="validation",
                    model_alias="tapper-chat",
                ),
            )
            citations = await repository.resolve_citations("legacy-trace", ("legacy-citation",))
            evidence = AnswerEvidence(
                "grounded",
                "completed",
                RetrievalSummary("completed", trace_id="legacy-trace", authorized_hit_count=1),
                GraphContextStatus.NOT_REQUESTED,
                citations=citations,
            )
            with pytest.raises(ValueError, match="frozen Turn resources"):
                await service.complete_evidence("evidence-chat", "evidence-turn", evidence)
            await service.create(
                "bound-evidence-chat",
                "bound-evidence-turn",
                "bound-evidence-request",
                TurnInput(
                    message="bind legacy evidence",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    identity_mode="validation",
                    model_alias="tapper-chat",
                    resolved_resources=(
                        FrozenResource(
                            selected[0].source_id,
                            selected[0].document_id,
                            selected[0].revision_id,
                            selected[0].source_content_hash,
                        ),
                    ),
                ),
            )
            await service.complete_evidence("bound-evidence-chat", "bound-evidence-turn", evidence)
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM turn_artifact_link "
                            "WHERE turn_id='bound-evidence-turn' AND artifact_id='legacy-citation'"
                        )
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_0010a_preserves_nonempty_predecessor_and_round_trips_empty_command_ledger(monkeypatch):
    if os.getenv("TAP_RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires owned isolated MySQL")
    from scripts.migration_support import BASELINE_ROWS, run_migration_gate

    monkeypatch.delenv("TAP_DATABASE_URL", raising=False)
    monkeypatch.delenv("TAP_ALEMBIC_DATABASE_URL", raising=False)
    result = run_migration_gate("0010a_source_commands")
    assert result["status"] == "passed"
    assert len(result["preserved_rows"]) == 14
    assert set(result["preserved_rows"]) == set(BASELINE_ROWS)
    assert all(result["preserved_rows"].values())
    assert result["source_commands_downgrade_replay"] == "passed"


def test_0010_corruption_partial_state_multisource_and_downgrade_guards(owned_project_mysql):
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from scripts.migration_support import seed_baseline
    from sqlalchemy import create_engine, inspect, text

    migration_path = ROOT / "apps/backend/migrations/versions/0010_knowledge_sources.py"
    spec = importlib.util.spec_from_file_location("source_migration_fixture", migration_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    owned_project_mysql.downgrade("0005_projection_lineage")
    engine = create_engine(owned_project_mysql.url)
    try:
        with engine.begin() as connection:
            seed_baseline(connection)
        owned_project_mysql.upgrade("0009_outbox_operations")
        with engine.begin() as connection:
            document = dict(
                connection.execute(text("SELECT * FROM knowledge_document")).mappings().one()
            )
            revision = dict(
                connection.execute(text("SELECT * FROM knowledge_document_revision"))
                .mappings()
                .one()
            )
            second_document = {
                **document,
                "document_id": "second-document",
                "current_revision_id": None,
                "dedupe_key": "sha256:" + "9" * 64,
            }
            second_revision = {
                **revision,
                "revision_id": "second-revision",
                "document_id": "second-document",
            }
            module._insert(connection, "knowledge_document", second_document)
            module._insert(connection, "knowledge_document_revision", second_revision)
            connection.execute(
                text(
                    "UPDATE knowledge_document SET "
                    "current_revision_id='second-revision' WHERE "
                    "document_id='second-document'"
                )
            )
            selected = json.dumps(["legacy-revision", "second-revision"])
            connection.execute(
                text("UPDATE knowledge_answer_snapshot SET selected_revisions_json=:selected"),
                {"selected": selected},
            )
        with engine.connect() as connection:
            for bad_selection in (
                ["missing-revision"],
                ["legacy-revision", "legacy-revision"],
                [{"unknown": "shape"}],
            ):
                with connection.begin():
                    connection.execute(
                        text(
                            "UPDATE knowledge_answer_snapshot SET selected_revisions_json=:selected"
                        ),
                        {"selected": json.dumps(bad_selection)},
                    )
                    with Operations.context(MigrationContext.configure(connection)):
                        with pytest.raises(ValueError, match="knowledge-source-migration"):
                            module.upgrade()
                    assert not set(module.NEW_TABLES).intersection(
                        inspect(connection).get_table_names()
                    )
                    connection.execute(
                        text(
                            "UPDATE knowledge_answer_snapshot SET selected_revisions_json=:selected"
                        ),
                        {"selected": selected},
                    )
            for ddl, repair in (
                (
                    "ALTER TABLE knowledge_document_revision "
                    "ADD COLUMN projection_digest VARCHAR(71) NULL",
                    "ALTER TABLE knowledge_document_revision DROP COLUMN projection_digest",
                ),
                (
                    "ALTER TABLE knowledge_document ADD COLUMN source_id VARCHAR(64) NULL",
                    "ALTER TABLE knowledge_document DROP COLUMN source_id",
                ),
                (
                    "ALTER TABLE project_audit ADD COLUMN resource_id VARCHAR(128) NULL",
                    "ALTER TABLE project_audit DROP COLUMN resource_id",
                ),
                (
                    "ALTER TABLE outbox MODIFY aggregate_id VARCHAR(128) NOT NULL",
                    "ALTER TABLE outbox MODIFY aggregate_id VARCHAR(64) NOT NULL",
                ),
                ("CREATE TABLE knowledge_source (placeholder INT)", "DROP TABLE knowledge_source"),
            ):
                connection.execute(text(ddl))
                connection.commit()
                with Operations.context(MigrationContext.configure(connection)):
                    with pytest.raises(ValueError, match="partial-schema"):
                        module.upgrade()
                connection.rollback()
                connection.execute(text(repair))
                connection.commit()
            with connection.begin(), Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
            rows = (
                connection.execute(text("SELECT * FROM knowledge_answer_source ORDER BY ordinal"))
                .mappings()
                .all()
            )
            assert [row["revision_id"] for row in rows] == ["legacy-revision", "second-revision"]
            assert len({row["source_id"] for row in rows}) == 2
            assert (
                connection.execute(
                    text("SELECT selected_revisions_json FROM knowledge_answer_snapshot")
                ).scalar_one()
                == selected
            )
            connection.rollback()
            for mutation in (
                (
                    "UPDATE knowledge_source SET name='changed' WHERE source_id="
                    "(SELECT source_id FROM knowledge_document WHERE "
                    "document_id='legacy-document')"
                ),
                (
                    "UPDATE knowledge_document_revision SET projection_digest="
                    "CONCAT('sha256:', REPEAT('a',64)) WHERE revision_id='legacy-revision'"
                ),
                "UPDATE outbox SET aggregate_id=REPEAT('x',68)",
            ):
                transaction = connection.begin()
                connection.execute(text(mutation))
                with Operations.context(MigrationContext.configure(connection)):
                    with pytest.raises(ValueError, match="knowledge-source-migration"):
                        module.downgrade()
                transaction.rollback()
                assert set(module.NEW_TABLES).issubset(inspect(connection).get_table_names())
                connection.rollback()
            with connection.begin(), Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
                module.upgrade()
            assert (
                connection.execute(text("SELECT COUNT(*) FROM knowledge_source")).scalar_one() == 2
            )
            connection.rollback()
    finally:
        engine.dispose()
