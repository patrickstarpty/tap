"""Turn traceparent propagation: write on append/create, carry through claim_queued."""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.chat.domain.conversations import TurnInput
from tap.platform.telemetry import bind_trace, extract_traceparent, span, trace_id_of
from tests.owned_mysql import owned_project_database_url


def _input(message="Trace this"):
    return TurnInput(
        message=message,
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        agent_revision_id="validation-knowledge-agent-v1",
        agent_revision_digest="sha256:" + "a" * 64,
        skill_revision_ids=("validation-citation-skill-v1",),
        skill_revision_digests=("sha256:" + "b" * 64,),
        retrieval_policy_digest="sha256:" + "c" * 64,
    )


async def _fresh_service(owned_project_mysql):
    url = owned_project_database_url(owned_project_mysql)
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owned = (
        "outbox",
        "turn_artifact_link",
        "turn_answer_evidence_snapshot",
        "turn_input_snapshot",
        "turn_snapshot",
        "chat_event",
        "chat_turn",
        "conversation",
    )
    async with engine.begin() as connection:
        await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in owned:
            await connection.execute(text(f"DELETE FROM {table}"))
        await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    service = ConversationService(
        MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
        scope=VALIDATION_SCOPE,
    )
    return engine, service


def test_append_writes_traceparent_of_request_span(owned_project_mysql, span_recorder):
    async def scenario():
        engine, service = await _fresh_service(owned_project_mysql)
        try:
            await service.create("conversation-1", "turn-1", "request-1", _input())
            with span("turn.request") as request_span:
                trace_id = format(request_span.get_span_context().trace_id, "032x")
                appended = await service.append(
                    "conversation-1", "turn-2", "request-2", _input("second turn")
                )
            loaded = await service.load("conversation-1")
            persisted = next(t for t in loaded.turns if t.turn_id == appended.turn_id)
            assert trace_id_of(persisted.traceparent) == trace_id
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_claimed_turn_carries_traceparent(owned_project_mysql, span_recorder):
    async def scenario():
        engine, service = await _fresh_service(owned_project_mysql)
        try:
            with span("turn.request"):
                queued = await service.create("conversation-1", "turn-1", "request-1", _input())
            persisted = (await service.load("conversation-1")).turns[0]
            claimed = (await service.repository.claim_queued(limit=1))[0][1]
            assert claimed.turn_id == queued.turn_id
            assert claimed.traceparent == persisted.traceparent
            assert claimed.traceparent is not None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_worker_execute_span_joins_request_trace(owned_project_mysql, span_recorder):
    async def scenario():
        engine, service = await _fresh_service(owned_project_mysql)
        try:
            with span("turn.request") as request_span:
                request_span_id = request_span.get_span_context().span_id
                request_trace_id = request_span.get_span_context().trace_id
                await service.create("conversation-1", "turn-1", "request-1", _input())
            claimed = (await service.repository.claim_queued(limit=1))[0][1]
            with bind_trace(
                scope=VALIDATION_SCOPE,
                turn_id=claimed.turn_id,
                attempt=claimed.attempt,
                fresh_usage=True,
            ):
                with span(
                    "turn.execute", context=extract_traceparent(claimed.traceparent)
                ) as execute_span:
                    execute_context = execute_span.get_span_context()
                    execute_parent = execute_span.parent
        finally:
            await engine.dispose()
        assert execute_context.trace_id == request_trace_id
        assert execute_parent is not None
        assert execute_parent.span_id == request_span_id
        finished = {s.name: s for s in span_recorder.get_finished_spans()}
        assert finished["turn.execute"].attributes["tap.attempt"] == 1

    asyncio.run(scenario())


def test_reclaimed_turn_reuses_trace_with_new_attempt(owned_project_mysql, span_recorder):
    async def scenario():
        engine, service = await _fresh_service(owned_project_mysql)
        try:
            with span("turn.request"):
                await service.create("conversation-1", "turn-1", "request-1", _input())
            first_claim = (await service.repository.claim_queued(limit=1))[0][1]
            assert first_claim.attempt == 1
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE chat_turn SET processing_lease_expires_at=UTC_TIMESTAMP(6) "
                        "- INTERVAL 1 SECOND WHERE turn_id='turn-1'"
                    )
                )
            second_claim = (await service.repository.claim_queued(limit=1))[0][1]
            assert second_claim.attempt == 2
            assert second_claim.traceparent == first_claim.traceparent
        finally:
            await engine.dispose()

    asyncio.run(scenario())
