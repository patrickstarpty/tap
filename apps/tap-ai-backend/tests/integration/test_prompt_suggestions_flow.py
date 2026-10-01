"""End-to-end prompt suggestion flow against real MySQL: publish/read/refresh
wired together the way the runtime composes them (Task 2-6 pieces), minus the
real Milvus-backed AnswerService — grounding is faked to always ground, since
exercising abstention itself is Task 5's job, not this wiring test's."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, update

from tap.entrypoints.prompt_suggestion_knowledge import KnowledgeSuggestionSources
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.litellm import LiteLLMModelGatewayConfig
from tap.modules.ai.adapters.litellm_catalog import ModelRoles
from tap.modules.chat.adapters.model_gateway_suggestions import ModelGatewaySuggestionGenerator
from tap.modules.chat.adapters.mysql_suggestion_usage import MysqlSuggestionUsage
from tap.modules.chat.adapters.mysql_suggestions import (
    MysqlSuggestionStore,
    prompt_suggestion_refresh,
)
from tap.modules.chat.application.suggestions import (
    PromptSuggestionService,
    SuggestionRefreshWorker,
)
from tap.modules.chat.domain.suggestions import RefreshReason, SuggestionKey
from tap.modules.knowledge.adapters.mysql_documents import knowledge_source
from tap.platform.db.project_scope import scope_predicates
from tap.testing.deterministic_model_gateway import DeterministicModelGateway
from tests.integration.test_prompt_suggestion_inputs_mysql import (
    DIGEST_B,
    _run,
    _seed_document,
    _seed_revision,
    _src,
)

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)


class _AlwaysGrounded:
    """Grounding via a real AnswerService is Task 5's own coverage; this test
    wires the refresh/read loop end-to-end and fakes the grounding boundary."""

    async def is_grounded(self, actor_id, question, source_ids):
        del actor_id, question, source_ids
        return True


async def _redact(text: str) -> str:
    return text


def _gateway() -> DeterministicModelGateway:
    config = LiteLLMModelGatewayConfig(
        base_url="https://litellm.example",
        api_key="private-provider-key",
        roles=ModelRoles("qwen-plus", "text-embedding-v4", None),
        embedding_dimension=2,
    )
    return DeterministicModelGateway(config, scope=VALIDATION_SCOPE, redact=_redact)


async def _refresh_row(sessions, key: SuggestionKey):
    async with sessions() as session:
        return (
            (
                await session.execute(
                    select(
                        prompt_suggestion_refresh.c.due_at,
                        prompt_suggestion_refresh.c.last_reason,
                    ).where(
                        *scope_predicates(prompt_suggestion_refresh, VALIDATION_SCOPE),
                        prompt_suggestion_refresh.c.actor_id == key.actor_id,
                        prompt_suggestion_refresh.c.locale == key.locale,
                    )
                )
            )
            .mappings()
            .one()
        )


def test_publish_generate_verify_and_read(owned_project_mysql):
    async def scenario(sessions, engine):
        del engine
        async with sessions() as session, session.begin():
            await _seed_document(
                session,
                source_id=_src("flow-a"),
                document_id="doc-flow-a",
                revision_id="rev-flow-a",
                name="Source A",
            )
            await _seed_document(
                session,
                source_id=_src("flow-b"),
                document_id="doc-flow-b",
                revision_id="rev-flow-b",
                name="Source B",
            )

        store = MysqlSuggestionStore(sessions, scope=VALIDATION_SCOPE)
        knowledge = KnowledgeSuggestionSources(sessions, scope=VALIDATION_SCOPE)
        usage = MysqlSuggestionUsage(sessions, scope=VALIDATION_SCOPE)
        generator = ModelGatewaySuggestionGenerator(
            _gateway(), scope=VALIDATION_SCOPE, alias="qwen-plus", timeout_seconds=5.0
        )
        service = PromptSuggestionService(
            store=store,
            knowledge=knowledge,
            usage=usage,
            generator=generator,
            grounding=_AlwaysGrounded(),
            id_factory=lambda: uuid4().hex,
            clock=lambda: NOW,
        )
        worker = SuggestionRefreshWorker(
            store=store, service=service, worker_id="flow-worker", clock=lambda: NOW
        )
        key = SuggestionKey(actor_id=VALIDATION_SCOPE.actor_id, locale="en")

        # 1. First read has never refreshed: empty, and a MISSING refresh is queued.
        first = await service.list(key)
        assert first == ()
        row = await _refresh_row(sessions, key)
        assert row["last_reason"] == RefreshReason.MISSING.value
        assert row["due_at"] is not None

        # 2. Worker claims the due refresh and generates one question per ready source.
        processed = await worker.run_once(limit=10)
        assert processed == 1

        # 3. Read returns one question per ready source.
        second = await service.list(key)
        assert {item.question for item in second} == {
            "What does Source A cover?",
            "What does Source B cover?",
        }

        # 4. Deleting a source filters its suggestion out of subsequent reads
        #    and queues a FILTERED refresh.
        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_source)
                .where(
                    *scope_predicates(knowledge_source, VALIDATION_SCOPE),
                    knowledge_source.c.source_id == _src("flow-b"),
                )
                .values(deleted_at=NOW.replace(tzinfo=None))
            )
        third = await service.list(key)
        assert {item.question for item in third} == {"What does Source A cover?"}
        row_after_delete = await _refresh_row(sessions, key)
        assert row_after_delete["last_reason"] == RefreshReason.FILTERED.value

        # 5. A new ready revision of the remaining source changes its opaque
        #    version, so the old cached suggestion for it is filtered too.
        async with sessions() as session, session.begin():
            await _seed_revision(
                session,
                source_id=_src("flow-a"),
                document_id="doc-flow-a",
                revision_id="rev-flow-a-2",
                source_content_hash=DIGEST_B,
                set_current=True,
            )
        fourth = await service.list(key)
        assert fourth == ()

    _run(owned_project_mysql, scenario)
