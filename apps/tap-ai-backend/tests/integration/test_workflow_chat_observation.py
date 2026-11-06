"""Observe real direct Chat model calls through the durable Turn worker."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.interfaces.http.insights_explanation_runtime import ConfiguredInsightsExplanation
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.litellm import LiteLLMModelGatewayConfig
from tap.modules.ai.adapters.litellm_catalog import ModelRoles
from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
from tap.modules.ai.domain.assets import AiAgentRevision, text_digest
from tap.modules.ai.domain.models import schema_digest
from tap.modules.ai.ports.insights import InsightsQueryUnavailable
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.chat.application.plan_answer import AnswerPlanner
from tap.modules.chat.domain.conversations import TurnInput, content_digest
from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway
from tap.modules.workflows.adapters.mysql import MysqlWorkflowStore
from tap.modules.workflows.adapters.mysql_observations import MysqlAgentObservations
from tap.modules.workflows.adapters.observation_bindings import ChatObservationBinding
from tap.modules.workflows.adapters.schema import workflow_task
from tap.modules.workflows.application.observations import ObservedModelGateway
from tap.testing.deterministic_model_gateway import DeterministicModelGateway
from tests.contract.test_insights_explanation_runtime import historical
from tests.conversation_execution_compat import KnowledgeHttpService
from tests.integration.test_agent_loop_mysql import (
    _FIRST_PASS_QUESTION,
    _SECOND_HIT_KEYWORD,
    _frozen,
    _run_until_completed,
    _setup,
)
from tests.integration.test_conversation_persistence import _input
from tests.integration.test_insights_generation_persistence import _NoOrdinaryAnswer
from tests.owned_mysql import owned_project_database_url


class _Scoped:
    scope = VALIDATION_SCOPE


class _CountingGateway:
    def __init__(self, delegate, *, hold: bool = False) -> None:
        self.delegate = delegate
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not hold:
            self.release.set()

    async def chat(self, request):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return await self.delegate.chat(request)

    async def generate_structured(self, request):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return await self.delegate.generate_structured(request)

    async def chat_stream(self, request):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        async for event in self.delegate.chat_stream(request):
            yield event

    def __getattr__(self, name):
        return getattr(self.delegate, name)


async def _observed_chat_task_count(sessions, turn_id: str) -> int:
    async with sessions() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(workflow_task)
                .where(
                    workflow_task.c.enterprise_id == VALIDATION_SCOPE.enterprise_id,
                    workflow_task.c.project_id == VALIDATION_SCOPE.project_id,
                    workflow_task.c.request_json["origin"].as_string() == "observed",
                    workflow_task.c.request_json["source_kind"].as_string() == "chat",
                    workflow_task.c.request_json["source_key"].as_string() == turn_id,
                )
            )
        )


def _direct_input(message: str, revision=None) -> TurnInput:
    selected = (
        {}
        if revision is None
        else {
            "agent_revision_id": revision.revision_id,
            "agent_revision_digest": revision.content_digest,
            "agent_label": revision.display_name,
            "agent_system_instruction": revision.system_instruction,
            "agent_system_instruction_digest": revision.system_instruction_digest,
            "agent_output_schema_json": revision.output_schema_json,
            "agent_output_schema_digest": revision.output_schema_digest,
            "agent_tool_allowlist": tuple(sorted(revision.tool_allowlist)),
        }
    )
    return TurnInput(
        message,
        VALIDATION_SCOPE.actor_id,
        "validation",
        "qwen-plus",
        acl_digest=content_digest({"mode": "model-only", "resources": []}),
        retrieval_policy_digest=content_digest({"mode": "model-only", "retrieval": "not-selected"}),
        **selected,
    )


def _knowledge_model(
    counting: _CountingGateway, *, planner: AnswerPlanner | None = None
) -> KnowledgeHttpService:
    async def redact(text: str) -> str:
        return text

    models = KnowledgeModelGateway(
        ObservedModelGateway(counting),
        scope=VALIDATION_SCOPE,
        redact=redact,
        embedding_alias="text-embedding-v4",
        chat_alias="qwen-plus",
        embedding_dimension=2,
        timeout_seconds=5,
    )
    return KnowledgeHttpService(
        documents=_Scoped(),
        answers=_Scoped(),
        citations=_Scoped(),
        models=models,
        answer_planner=planner,
    )


def _counting_gateway(*, hold: bool = False) -> _CountingGateway:
    config = LiteLLMModelGatewayConfig(
        base_url="https://litellm.example",
        api_key="x" * 20,
        roles=ModelRoles("qwen-plus", "text-embedding-v4", None),
        embedding_dimension=2,
    )

    async def redact(text: str) -> str:
        return text

    return _CountingGateway(
        DeterministicModelGateway(
            config, scope=VALIDATION_SCOPE, redact=redact, answer_delay_seconds=0
        ),
        hold=hold,
    )


@pytest.mark.asyncio
async def test_default_chat_observation_starts_at_actual_model_and_finishes_with_turn(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
        conversations = ConversationService(repository, scope=VALIDATION_SCOPE)
        observer = MysqlAgentObservations(sessions, MysqlWorkflowStore(sessions))
        gateway = _counting_gateway(hold=True)
        worker = GenerationWorker(
            conversations,
            _knowledge_model(gateway),
            model_observation=ChatObservationBinding(
                observer, object(), repository, VALIDATION_SCOPE
            ),
        )
        await conversations.create(
            "chat-observed-default",
            "turn-observed-default",
            "request-observed-default",
            _direct_input("你好"),
        )
        running = asyncio.create_task(worker.run_once(limit=1))
        try:
            await asyncio.wait_for(gateway.entered.wait(), timeout=15)
            task_id = await repository.observed_task_id(
                "chat-observed-default", "turn-observed-default"
            )
            assert task_id is not None
            before = await observer.snapshot(VALIDATION_SCOPE, task_id)
            assert before["status"] == "running"
            assert len(before["nodes"]) == 1
            assert (await conversations.load("chat-observed-default")).turns[0].state == "running"
        finally:
            gateway.release.set()
        assert await running == 1
        assert gateway.calls == 1
        assert (await conversations.load("chat-observed-default")).turns[0].state == "completed"
        after = await observer.snapshot(VALIDATION_SCOPE, task_id)
        assert after["status"] == "succeeded"
        assert len(after["nodes"]) == 1
        assert after["nodes"][0]["agent_revision_id"] is None
        assert after["request_json"]["display_name"] == "Tapper"
        assert await _observed_chat_task_count(sessions, "turn-observed-default") == 1
        assert await worker.run_once(limit=1) == 0
        assert gateway.calls == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_custom_chat_uses_frozen_historical_revision_after_disable(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        }
        instruction = "Answer directly in the user's language."
        revision = AiAgentRevision(
            revision_id="chat-observed-agent-v1",
            asset_id="chat-observed-agent",
            display_name="Original specialist",
            scope=VALIDATION_SCOPE,
            content_digest=text_digest("chat-observed-agent-v1"),
            system_instruction_digest=text_digest(instruction),
            system_instruction=instruction,
            tool_allowlist=frozenset({"knowledge.answer"}),
            output_schema_digest=schema_digest(schema),
            output_schema_json='{"additionalProperties":false,"properties":{"answer":{"type":"string"}},"required":["answer"],"type":"object"}',
            source_kind="authored",
        )
        await catalog.insert_agent_revision(revision, base_revision_id=None)
        repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
        conversations = ConversationService(repository, scope=VALIDATION_SCOPE)
        observer = MysqlAgentObservations(sessions, MysqlWorkflowStore(sessions))
        gateway = _counting_gateway()
        worker = GenerationWorker(
            conversations,
            _knowledge_model(gateway),
            model_observation=ChatObservationBinding(
                observer, catalog, repository, VALIDATION_SCOPE
            ),
        )
        await conversations.create(
            "chat-observed-custom",
            "turn-observed-custom",
            "request-observed-custom",
            _direct_input("你好", revision),
        )
        newer = replace(
            revision,
            revision_id="chat-observed-agent-v2",
            display_name="Renamed specialist",
            content_digest=text_digest("chat-observed-agent-v2"),
        )
        await catalog.insert_agent_revision(newer, base_revision_id=revision.revision_id)
        await catalog.disable_agent(revision.revision_id)
        assert await worker.run_once(limit=1) == 1
        assert gateway.calls == 1
        task_id = await repository.observed_task_id("chat-observed-custom", "turn-observed-custom")
        assert task_id is not None
        loaded = await conversations.load("chat-observed-custom")
        assert loaded.turns[0].state == "completed", [
            event.payload.get("problem")
            for event in loaded.events
            if event.event_type == "turn.failed"
        ]
        snapshot = await observer.snapshot(VALIDATION_SCOPE, task_id)
        assert snapshot["status"] == "succeeded"
        assert len(snapshot["nodes"]) == 1
        assert snapshot["nodes"][0]["agent_revision_id"] == revision.revision_id
        assert snapshot["request_json"]["display_name"] == revision.display_name
        assert await _observed_chat_task_count(sessions, "turn-observed-custom") == 1
        assert (await conversations.load("chat-observed-custom")).turns[0].state == "completed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_chat_tool_loop_keeps_one_observed_agent_for_multiple_model_calls(
    owned_project_mysql,
) -> None:
    setup, engine = await _setup(owned_project_mysql)
    try:
        repository = setup.conversations.repository
        observer = MysqlAgentObservations(setup.sessions, MysqlWorkflowStore(setup.sessions))
        setup.worker.model_observation = ChatObservationBinding(
            observer, object(), repository, VALIDATION_SCOPE
        )
        models = setup.knowledge.conversation_answers._models
        gateway = _CountingGateway(models.gateway)
        models.gateway = ObservedModelGateway(gateway)
        question = f"{_FIRST_PASS_QUESTION} [e2e-tool:knowledge.search:{_SECOND_HIT_KEYWORD}]"
        await setup.conversations.create(
            "chat-observed-tool-loop",
            "turn-observed-tool-loop",
            "request-observed-tool-loop",
            _frozen(question),
        )
        loaded = await _run_until_completed(setup, "chat-observed-tool-loop")
        assert loaded.turns[0].state == "completed"
        assert gateway.calls >= 2
        task_id = await repository.observed_task_id(
            "chat-observed-tool-loop", "turn-observed-tool-loop"
        )
        assert task_id is not None
        snapshot = await observer.snapshot(VALIDATION_SCOPE, task_id)
        assert snapshot["status"] == "succeeded"
        assert len(snapshot["nodes"]) == 1
        assert snapshot["request_json"]["display_name"] == "Tapper"
        assert await _observed_chat_task_count(setup.sessions, "turn-observed-tool-loop") == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_chat_local_final_and_rejected_frozen_authority_create_no_observation(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
        conversations = ConversationService(repository, scope=VALIDATION_SCOPE)
        observer = MysqlAgentObservations(sessions, MysqlWorkflowStore(sessions))
        gateway = _counting_gateway()
        worker = GenerationWorker(
            conversations,
            _knowledge_model(gateway, planner=AnswerPlanner()),
            model_observation=ChatObservationBinding(
                observer, object(), repository, VALIDATION_SCOPE
            ),
        )

        await conversations.create(
            "chat-observed-local",
            "turn-observed-local",
            "request-observed-local",
            _direct_input("这个方案如何？"),
        )
        assert await worker.run_once(limit=1) == 1
        local = await conversations.load("chat-observed-local")
        assert local.turns[0].state == "completed"
        assert any(event.event_type == "turn.completed" for event in local.events)
        assert (
            await repository.observed_task_id("chat-observed-local", "turn-observed-local") is None
        )
        assert await _observed_chat_task_count(sessions, "turn-observed-local") == 0
        assert gateway.calls == 0

        await conversations.create(
            "chat-observed-denied",
            "turn-observed-denied",
            "request-observed-denied",
            replace(_direct_input("你好"), acl_digest="sha256:" + "0" * 64),
        )
        assert await worker.run_once(limit=1) == 1
        denied = await conversations.load("chat-observed-denied")
        assert denied.turns[0].state == "failed"
        assert (
            await repository.observed_task_id("chat-observed-denied", "turn-observed-denied")
            is None
        )
        assert await _observed_chat_task_count(sessions, "turn-observed-denied") == 0
        assert await observer.pending_source(VALIDATION_SCOPE, "chat", 10) == ()
        assert gateway.calls == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_insights_authorization_and_local_fallbacks_create_no_observation(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
        conversations = ConversationService(repository, scope=VALIDATION_SCOPE)
        observer = MysqlAgentObservations(sessions, MysqlWorkflowStore(sessions))
        binding = ChatObservationBinding(observer, object(), repository, VALIDATION_SCOPE)
        gateway = _counting_gateway()

        class Insights:
            def __init__(self, *, metrics_unavailable: bool = False) -> None:
                self.calls = 0
                self.metrics_unavailable = metrics_unavailable

            async def get_insights(self, _scope, _query_id):
                self.calls += 1
                if self.metrics_unavailable and self.calls > 1:
                    raise InsightsQueryUnavailable("metrics unavailable after historical lookup")
                return historical()

            async def get_evidence(self, _scope, _receipt_id, *, max_bytes):
                assert max_bytes == 20_000
                return b"<testsuite><failure>Assertion failed</failure></testsuite>"

        async def run_case(
            suffix: str,
            *,
            report_ref: str,
            max_cost_micros: int,
            metrics_unavailable: bool = False,
        ) -> object:
            chat_id = f"chat-insights-{suffix}"
            turn_id = f"turn-insights-{suffix}"
            await conversations.create(
                chat_id,
                turn_id,
                f"request-insights-{suffix}",
                replace(
                    _input("Explain the timeout"),
                    insights_query_id="query-a",
                    insights_report_refs=(report_ref,),
                ),
            )
            explanation = ConfiguredInsightsExplanation(
                insights=Insights(metrics_unavailable=metrics_unavailable),
                gateway=ObservedModelGateway(gateway),
                model_alias="qwen-plus",
                project_id=VALIDATION_SCOPE.project_id,
                delegated_user_token="x" * 16,
                authorization_version="insights-v1",
                max_micros_per_token=1,
                max_cost_micros=max_cost_micros,
            )
            worker = GenerationWorker(
                conversations,
                _NoOrdinaryAnswer(),
                insights_explanation=explanation,
                model_observation=binding,
            )
            assert await worker.run_once(limit=1) == 1
            assert await repository.observed_task_id(chat_id, turn_id) is None
            assert await _observed_chat_task_count(sessions, turn_id) == 0
            assert gateway.calls == 0
            return await conversations.load(chat_id)

        denied = await run_case("denied", report_ref="receipt-other", max_cost_micros=1)
        assert denied.turns[0].state == "failed"
        budget = await run_case("budget", report_ref="receipt-a", max_cost_micros=1)
        assert budget.turns[0].state == "completed"
        assert budget.turns[0].answer_snapshot.value.insights_explanation["stopReason"] == (
            "budget-exhausted"
        )
        unavailable = await run_case(
            "metrics", report_ref="receipt-a", max_cost_micros=1, metrics_unavailable=True
        )
        assert unavailable.turns[0].state == "failed"
        assert await observer.pending_source(VALIDATION_SCOPE, "chat", 10) == ()
    finally:
        await engine.dispose()
