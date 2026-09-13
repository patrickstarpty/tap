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


def test_0012a_conversation_governance_revision_is_literal_and_registered():
    from scripts.migration_support import validate_revision

    assert validate_revision("0012a_conversation_governance") == "0012a_conversation_governance"


def test_0013_knowledge_graph_revision_is_literal_and_registered():
    from scripts.migration_support import validate_revision

    assert validate_revision("0013_knowledge_graph") == "0013_knowledge_graph"


def test_applied_0012_upgrades_additively_and_reconciles_only_recoverable_authority(
    owned_project_mysql,
):
    import asyncio

    from scripts.migration_support import seed_baseline
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.application.assets import resolve_skill_selection, validation_asset_seed
    from tap.modules.ai.domain.assets import AssetRevisionRejected
    from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository

    owned_project_mysql.downgrade("0005_projection_lineage")
    sync_engine = create_engine(owned_project_mysql.url)
    try:
        with sync_engine.begin() as connection:
            seed_baseline(connection)
        owned_project_mysql.upgrade("0012_conversations")
        with sync_engine.begin() as connection:
            scope = {
                "enterprise_id": "local",
                "project_id": "tapper-demo",
                "actor_id": "tapper-local-user",
                "identity_mode": "validation",
                "identity_origin": "VALIDATION",
            }
            connection.execute(
                text(
                    "INSERT INTO ai_agent "
                    "(agent_id,display_name,created_at,enterprise_id,project_id,actor_id,"
                    "identity_mode,identity_origin) VALUES "
                    "('validation-knowledge-agent','Knowledge agent',UTC_TIMESTAMP(6),"
                    ":enterprise_id,:project_id,:actor_id,:identity_mode,:identity_origin)"
                ),
                scope,
            )
            connection.execute(
                text(
                    "INSERT INTO ai_agent_revision "
                    "(revision_id,agent_id,revision_number,display_name,content_digest,"
                    "system_instruction_digest,tool_allowlist,output_schema_digest,status,"
                    "created_at,enterprise_id,project_id,actor_id,identity_mode,identity_origin) "
                    "VALUES ('validation-knowledge-agent-v1','validation-knowledge-agent',1,"
                    "'Knowledge agent',:content_digest,:instruction_digest,"
                    "JSON_ARRAY('knowledge.search','knowledge.answer'),:schema_digest,'enabled',"
                    "UTC_TIMESTAMP(6),:enterprise_id,:project_id,:actor_id,:identity_mode,"
                    ":identity_origin)"
                ),
                scope
                | {
                    "content_digest": "sha256:" + "4" * 64,
                    "instruction_digest": (
                        "sha256:fce11c5d9869cb393bd12b126a667e53f2e38cea27af38891c7f6f08a123bba9"
                    ),
                    "schema_digest": "sha256:" + "5" * 64,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO skill "
                    "(skill_id,display_name,created_at,enterprise_id,project_id,actor_id,"
                    "identity_mode,identity_origin) VALUES "
                    "('validation-citation-skill','Citation skill',UTC_TIMESTAMP(6),"
                    ":enterprise_id,:project_id,:actor_id,:identity_mode,:identity_origin)"
                ),
                scope,
            )
            connection.execute(
                text(
                    "INSERT INTO skill_revision "
                    "(revision_id,skill_id,revision_number,display_name,content_digest,"
                    "instruction_template_digest,applicable_tasks,status,created_at,enterprise_id,"
                    "project_id,actor_id,identity_mode,identity_origin) VALUES "
                    "('validation-citation-skill-v1','validation-citation-skill',1,"
                    "'Citation skill',"
                    ":content_digest,:template_digest,JSON_ARRAY('knowledge.answer'),'enabled',"
                    "UTC_TIMESTAMP(6),:enterprise_id,:project_id,:actor_id,:identity_mode,"
                    ":identity_origin)"
                ),
                scope
                | {
                    "content_digest": "sha256:" + "6" * 64,
                    "template_digest": (
                        "sha256:a5c26513151960f626d1d756736dac11f529cabe0ea9f43da1d194ed37ef0702"
                    ),
                },
            )
        owned_project_mysql.upgrade("0012a_conversation_governance")
        with sync_engine.connect() as connection:
            assert {"system_instruction", "output_schema_json"} <= {
                item["name"] for item in inspect(connection).get_columns("ai_agent_revision")
            }
            assert "stream_sequence" in {
                item["name"] for item in inspect(connection).get_columns("chat_event")
            }
            old = connection.execute(
                text(
                    "SELECT system_instruction,output_schema_json,output_schema_digest "
                    "FROM ai_agent_revision WHERE revision_id='validation-knowledge-agent-v1'"
                )
            ).one()
            assert tuple(old) == (
                "knowledge-agent-system-instruction-v1",
                None,
                "sha256:" + "5" * 64,
            )
            assert (
                connection.execute(
                    text(
                        "SELECT instruction_template FROM skill_revision "
                        "WHERE revision_id='validation-citation-skill-v1'"
                    )
                ).scalar_one()
                == "citation-skill-template-v1"
            )
    finally:
        sync_engine.dispose()

    async def scenario() -> None:
        engine = create_async_engine(
            owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy")
        )
        try:
            catalog = MysqlAssetCatalog(
                async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
            )
            conversation = await MysqlConversationRepository(
                async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
            ).load("legacy-chat")
            assert [(event.sequence, event.event_id) for event in conversation.events] == [
                (1, "legacy-event")
            ]
            with pytest.raises(AssetRevisionRejected):
                await catalog.resolve_agent(
                    VALIDATION_SCOPE,
                    "validation-knowledge-agent-v1",
                    tools=frozenset({"knowledge.answer"}),
                    output_schema_digest="sha256:" + "5" * 64,
                )
            assert (
                resolve_skill_selection(
                    await catalog.resolve_skill(VALIDATION_SCOPE, "validation-citation-skill-v1"),
                    task="knowledge.answer",
                ).instruction_template
                == "citation-skill-template-v1"
            )
            await catalog.seed(validation_asset_seed(VALIDATION_SCOPE))
            current = validation_asset_seed(VALIDATION_SCOPE).agents[-1]
            assert [item.revision_id for item in await catalog.list_agents(VALIDATION_SCOPE)] == [
                "validation-knowledge-agent-v2"
            ]
            assert (
                await catalog.resolve_agent(
                    VALIDATION_SCOPE,
                    current.revision_id,
                    tools=frozenset({"knowledge.answer"}),
                    output_schema_digest=current.output_schema_digest,
                )
            ).revision_id == "validation-knowledge-agent-v2"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


_KNOWN_0012_SHAPE_ADDITIONS = {
    "a22c55e": {
        "ai_agent_revision": frozenset(),
        "skill_revision": frozenset(),
        "chat_turn": frozenset({"processing_lease_token", "processing_lease_expires_at"}),
        "chat_event": frozenset(),
    },
    "464faa6": {
        "ai_agent_revision": frozenset({"system_instruction", "output_schema_json"}),
        "skill_revision": frozenset({"instruction_template"}),
        "chat_turn": frozenset({"processing_lease_token", "processing_lease_expires_at"}),
        "chat_event": frozenset({"stream_sequence"}),
    },
}


def _apply_known_0012_shape(connection, shape):
    from sqlalchemy import inspect, text

    before = {
        table: {column["name"] for column in inspect(connection).get_columns(table)}
        for table in _KNOWN_0012_SHAPE_ADDITIONS[shape]
    }
    if shape == "464faa6":
        connection.execute(
            text(
                "ALTER TABLE ai_agent_revision "
                "ADD COLUMN system_instruction VARCHAR(8000) NOT NULL, "
                "ADD COLUMN output_schema_json JSON NOT NULL"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE skill_revision ADD COLUMN instruction_template VARCHAR(8000) NOT NULL"
            )
        )
    connection.execute(
        text(
            "ALTER TABLE chat_turn MODIFY COLUMN processing_attempt "
            "INTEGER NOT NULL DEFAULT 0, "
            "ADD COLUMN processing_lease_token VARCHAR(64), "
            "ADD COLUMN processing_lease_expires_at DATETIME(6)"
        )
    )
    if shape == "464faa6":
        connection.execute(
            text("ALTER TABLE chat_event ADD COLUMN stream_sequence BIGINT NOT NULL")
        )
    after = {
        table: {column["name"] for column in inspect(connection).get_columns(table)}
        for table in _KNOWN_0012_SHAPE_ADDITIONS[shape]
    }
    assert {table: frozenset(after[table] - before[table]) for table in before} == (
        _KNOWN_0012_SHAPE_ADDITIONS[shape]
    )


def _insert_deployed_validation_assets(connection, *, governed):
    from sqlalchemy import text

    from tap.modules.ai.domain.models import schema_digest, text_digest

    scope = {
        "enterprise_id": "local",
        "project_id": "tapper-demo",
        "actor_id": "tapper-local-user",
        "identity_mode": "validation",
        "identity_origin": "VALIDATION",
    }
    connection.execute(
        text(
            "INSERT INTO ai_agent "
            "(agent_id,display_name,created_at,enterprise_id,project_id,actor_id,"
            "identity_mode,identity_origin) VALUES "
            "('validation-knowledge-agent','Knowledge agent',UTC_TIMESTAMP(6),"
            ":enterprise_id,:project_id,:actor_id,:identity_mode,:identity_origin)"
        ),
        scope,
    )
    system_instruction = "preserve-464-agent"
    output_schema = {"type": "object"}
    agent_content = ",system_instruction,output_schema_json" if governed else ""
    agent_values = ",:system_instruction,:output_schema_json" if governed else ""
    connection.execute(
        text(
            "INSERT INTO ai_agent_revision "
            "(revision_id,agent_id,revision_number,display_name,content_digest,"
            "system_instruction_digest,tool_allowlist,output_schema_digest,status,created_at,"
            "enterprise_id,project_id,actor_id,identity_mode,identity_origin"
            f"{agent_content}) VALUES "
            "('validation-knowledge-agent-v1','validation-knowledge-agent',1,'Knowledge agent',"
            ":content_digest,:instruction_digest,JSON_ARRAY('knowledge.search','knowledge.answer'),"
            ":schema_digest,'enabled',UTC_TIMESTAMP(6),:enterprise_id,:project_id,:actor_id,"
            f":identity_mode,:identity_origin{agent_values})"
        ),
        scope
        | {
            "content_digest": "sha256:" + "4" * 64,
            "instruction_digest": (
                text_digest(system_instruction)
                if governed
                else "sha256:fce11c5d9869cb393bd12b126a667e53f2e38cea27af38891c7f6f08a123bba9"
            ),
            "schema_digest": (schema_digest(output_schema) if governed else "sha256:" + "5" * 64),
            "system_instruction": system_instruction,
            "output_schema_json": json.dumps(output_schema),
        },
    )
    connection.execute(
        text(
            "INSERT INTO skill "
            "(skill_id,display_name,created_at,enterprise_id,project_id,actor_id,"
            "identity_mode,identity_origin) VALUES "
            "('validation-citation-skill','Citation skill',UTC_TIMESTAMP(6),"
            ":enterprise_id,:project_id,:actor_id,:identity_mode,:identity_origin)"
        ),
        scope,
    )
    instruction_template = "preserve-464-skill"
    skill_content = ",instruction_template" if governed else ""
    skill_values = ",:instruction_template" if governed else ""
    connection.execute(
        text(
            "INSERT INTO skill_revision "
            "(revision_id,skill_id,revision_number,display_name,content_digest,"
            "instruction_template_digest,applicable_tasks,status,created_at,enterprise_id,"
            f"project_id,actor_id,identity_mode,identity_origin{skill_content}) VALUES "
            "('validation-citation-skill-v1','validation-citation-skill',1,'Citation skill',"
            ":content_digest,:template_digest,JSON_ARRAY('knowledge.answer'),'enabled',"
            "UTC_TIMESTAMP(6),:enterprise_id,:project_id,:actor_id,:identity_mode,"
            f":identity_origin{skill_values})"
        ),
        scope
        | {
            "content_digest": "sha256:" + "6" * 64,
            "template_digest": (
                text_digest(instruction_template)
                if governed
                else "sha256:a5c26513151960f626d1d756736dac11f529cabe0ea9f43da1d194ed37ef0702"
            ),
            "instruction_template": instruction_template,
        },
    )


@pytest.mark.parametrize(
    ("shape", "expected_stream_sequence"),
    [("a22c55e", 1), ("464faa6", 7)],
)
def test_exact_deployed_0012_shapes_upgrade_without_rewriting_existing_facts(
    owned_project_mysql,
    monkeypatch,
    shape,
    expected_stream_sequence,
):
    import asyncio

    from scripts.migration_support import seed_baseline
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.entrypoints import tapper_runtime
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.application.assets import resolve_skill_selection, validation_asset_seed
    from tap.modules.ai.domain.assets import AssetRevisionRejected
    from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway

    owned_project_mysql.downgrade("0005_projection_lineage")
    sync_engine = create_engine(owned_project_mysql.url)
    try:
        with sync_engine.begin() as connection:
            seed_baseline(connection)
        owned_project_mysql.upgrade("0012_conversations")
        with sync_engine.begin() as connection:
            _apply_known_0012_shape(connection, shape)
            _insert_deployed_validation_assets(connection, governed=shape == "464faa6")
            connection.execute(
                text(
                    "UPDATE chat_turn SET processing_lease_token='preserve-deployed-lease', "
                    "processing_lease_expires_at='2026-09-09 01:02:03.123456' "
                    "WHERE turn_id='legacy-turn'"
                )
            )
            if shape == "464faa6":
                connection.execute(
                    text("UPDATE chat_event SET stream_sequence=:sequence"),
                    {"sequence": expected_stream_sequence},
                )
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0012_conversations"
            )

        owned_project_mysql.upgrade("0012a_conversation_governance")

        with sync_engine.connect() as connection:
            turn_columns = {
                item["name"]: item for item in inspect(connection).get_columns("chat_turn")
            }
            assert turn_columns["processing_attempt"]["default"] in {"'0'", "0", 0}
            assert (
                connection.execute(
                    text("SELECT processing_lease_token FROM chat_turn WHERE turn_id='legacy-turn'")
                ).scalar_one()
                == "preserve-deployed-lease"
            )
            assert (
                connection.execute(
                    text("SELECT stream_sequence FROM chat_event WHERE event_id='legacy-event'")
                ).scalar_one()
                == expected_stream_sequence
            )
            if shape == "464faa6":
                assert (
                    connection.execute(
                        text(
                            "SELECT system_instruction FROM ai_agent_revision "
                            "WHERE revision_id='validation-knowledge-agent-v1'"
                        )
                    ).scalar_one()
                    == "preserve-464-agent"
                )
                output_schema = connection.execute(
                    text(
                        "SELECT output_schema_json FROM ai_agent_revision "
                        "WHERE revision_id='validation-knowledge-agent-v1'"
                    )
                ).scalar_one()
                assert (
                    json.loads(output_schema) if isinstance(output_schema, str) else output_schema
                ) == {"type": "object"}
                assert (
                    connection.execute(
                        text(
                            "SELECT instruction_template FROM skill_revision "
                            "WHERE revision_id='validation-citation-skill-v1'"
                        )
                    ).scalar_one()
                    == "preserve-464-skill"
                )
    finally:
        sync_engine.dispose()

    async def scenario() -> None:
        class Resource:
            async def aclose(self) -> None:
                return None

        async def create_search(_settings, *, audit_sink, owners=None):
            return Resource(), object(), object()

        async def create_database(_settings):
            engine = create_async_engine(
                owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy")
            )
            return engine, tapper_runtime._build_document_repository(
                async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
            )

        model = KnowledgeModelGateway(
            object(),
            scope=VALIDATION_SCOPE,
            redact=tapper_runtime._redact_model_context,
            embedding_alias="tapper-embedding",
            chat_alias="tapper-chat",
            embedding_dimension=1536,
            timeout_seconds=15,
        )
        monkeypatch.setattr(tapper_runtime, "_create_blob", lambda _settings: Resource())
        monkeypatch.setattr(tapper_runtime, "_create_database", create_database)
        monkeypatch.setattr(tapper_runtime, "_create_redis", lambda _settings: Resource())
        monkeypatch.setattr(tapper_runtime, "_create_embeddings", lambda _settings: model)
        monkeypatch.setattr(tapper_runtime, "_create_search", create_search)
        monkeypatch.setattr(tapper_runtime, "_create_models_probe_client", lambda _settings: None)
        monkeypatch.setattr(tapper_runtime, "_create_readiness", lambda **_kwargs: object())
        settings = tapper_runtime.TapperSettings.from_mapping({})
        runtime = await tapper_runtime.create_api_runtime(settings)
        try:
            loaded = await runtime.http_services.conversations.load("legacy-chat")
            assert loaded.turns[0].turn_id == "legacy-turn"
            catalog = runtime.http_services.asset_catalog
            current = validation_asset_seed(VALIDATION_SCOPE)
            assert await catalog.resolve_agent(
                VALIDATION_SCOPE,
                current.agents[-1].revision_id,
                tools=frozenset({"knowledge.answer"}),
                output_schema_digest=current.agents[-1].output_schema_digest,
            )
            assert resolve_skill_selection(
                await catalog.resolve_skill(VALIDATION_SCOPE, current.skills[-1].revision_id),
                task="knowledge.answer",
            )
            if shape == "a22c55e":
                with pytest.raises(AssetRevisionRejected):
                    await catalog.resolve_agent(
                        VALIDATION_SCOPE,
                        "validation-knowledge-agent-v1",
                        tools=frozenset({"knowledge.answer"}),
                        output_schema_digest="sha256:" + "5" * 64,
                    )
                assert (
                    resolve_skill_selection(
                        await catalog.resolve_skill(
                            VALIDATION_SCOPE, "validation-citation-skill-v1"
                        ),
                        task="knowledge.answer",
                    ).instruction_template
                    == "citation-skill-template-v1"
                )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


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


def test_0012a_legacy_conversation_is_readable_through_new_repository(owned_project_mysql):
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
    owned_project_mysql.upgrade("0012a_conversation_governance")

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
