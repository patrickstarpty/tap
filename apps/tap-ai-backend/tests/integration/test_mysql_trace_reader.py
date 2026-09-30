"""MysqlTraceHttpService integration tests: aggregation, ownership, soft-delete retention."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.mysql_traces import MysqlTraceHttpService, _chat_turn_conversation_join
from tap.modules.chat.adapters.mysql import chat_turn
from tap.modules.chat.adapters.mysql_conversations import conversation
from tap.platform.db.project_scope import scope_values
from tap.platform.telemetry.schema import model_call, model_call_content, trace_span
from tests.owned_mysql import owned_project_database_url

TRACE_ID = "a" * 32


def _traceparent(trace_id: str) -> str:
    return f"00-{trace_id}-{'b' * 16}-01"


async def _fresh_engine(owned_project_mysql):
    url = owned_project_database_url(owned_project_mysql)
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owned = ("model_call_content", "model_call", "trace_span", "chat_turn", "conversation")
    async with engine.begin() as connection:
        await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in owned:
            await connection.execute(text(f"DELETE FROM {table}"))
        await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    return engine, sessions


async def _seed_conversation(sessions, conversation_id, turn_id, traceparent):
    scope = scope_values(VALIDATION_SCOPE)
    now = datetime(2026, 1, 1)
    async with sessions() as session, session.begin():
        await session.execute(
            insert(conversation).values(
                **scope,
                conversation_id=conversation_id,
                title="t",
                created_at=now,
                updated_at=now,
            )
        )
        await session.execute(
            insert(chat_turn).values(
                **scope,
                turn_id=turn_id,
                chat_id=conversation_id,
                client_request_id=f"req-{turn_id}",
                message="hi",
                state="completed",
                created_at=now,
                traceparent=traceparent,
            )
        )


async def _seed_spans_and_calls(sessions, trace_id, turn_id):
    scope = scope_values(VALIDATION_SCOPE)
    started = datetime(2026, 1, 1)
    async with sessions() as session, session.begin():
        await session.execute(
            insert(trace_span).values(
                **scope,
                trace_id=trace_id,
                span_id="1" * 16,
                parent_span_id=None,
                turn_id=turn_id,
                job_id=None,
                service_name="tap-ai-api",
                name="turn.request",
                status="ok",
                status_message=None,
                started_at=started,
                duration_ms=100,
                attributes={"tap.attempt": 1},
            )
        )
        await session.execute(
            insert(trace_span).values(
                **scope,
                trace_id=trace_id,
                span_id="2" * 16,
                parent_span_id="1" * 16,
                turn_id=turn_id,
                job_id=None,
                service_name="tap-ai-worker-generation",
                name="turn.execute",
                status="ok",
                status_message=None,
                started_at=started + timedelta(milliseconds=200),
                duration_ms=150,
                attributes={"tap.attempt": 2},
            )
        )
        calls = [
            ("call-0", "ok", Decimal("0.001200"), "qwen-plus-2025", None),
            ("call-1", "ok", None, "qwen-plus-2025", None),
            ("call-2", "error", None, None, "rate-limited"),
        ]
        for index, (call_id, status, cost, upstream, error_code) in enumerate(calls):
            await session.execute(
                insert(model_call).values(
                    **scope,
                    call_id=call_id,
                    trace_id=trace_id,
                    span_id="1" * 16,
                    turn_id=turn_id,
                    job_id=None,
                    operation="chat",
                    model_name="qwen-plus",
                    upstream_model=upstream,
                    provider="litellm",
                    input_tokens=10 if status == "ok" else None,
                    output_tokens=5 if status == "ok" else None,
                    cost_usd=cost,
                    latency_ms=100,
                    attempts=1,
                    status=status,
                    error_code=error_code,
                    gateway_call_id=None,
                    provider_request_id=None,
                    created_at=started + timedelta(seconds=index),
                )
            )
            await session.execute(
                insert(model_call_content).values(
                    **scope,
                    call_id=call_id,
                    request_json="{}",
                    response_text="ok" if status == "ok" else None,
                    reasoning_text=None,
                )
            )


def test_summary_aggregates_ok_calls(owned_project_mysql):
    async def scenario():
        engine, sessions = await _fresh_engine(owned_project_mysql)
        try:
            await _seed_conversation(sessions, "conv-1", "turn-1", _traceparent(TRACE_ID))
            await _seed_spans_and_calls(sessions, TRACE_ID, "turn-1")
            service = MysqlTraceHttpService(sessions)
            trace = await service.turn_trace(VALIDATION_SCOPE, "conv-1", "turn-1")
            assert trace is not None
            assert trace.trace_id == TRACE_ID
            summary = trace.summary
            assert summary.input_tokens == 20
            assert summary.output_tokens == 10
            assert summary.cost_incomplete is True
            assert summary.cost_usd == Decimal("0.001200")
            assert summary.attempt_count == 2
            assert summary.upstream_models == ["qwen-plus-2025"]
            assert None not in summary.upstream_models
            assert summary.requested_models == ["qwen-plus"]
            assert len(trace.spans) == 2
            assert len(trace.model_calls) == 3
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_turn_without_traceparent_returns_none(owned_project_mysql):
    async def scenario():
        engine, sessions = await _fresh_engine(owned_project_mysql)
        try:
            await _seed_conversation(sessions, "conv-1", "turn-1", None)
            service = MysqlTraceHttpService(sessions)
            assert await service.turn_trace(VALIDATION_SCOPE, "conv-1", "turn-1") is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_turn_of_other_conversation_returns_none(owned_project_mysql):
    async def scenario():
        engine, sessions = await _fresh_engine(owned_project_mysql)
        try:
            await _seed_conversation(sessions, "conv-1", "turn-1", _traceparent(TRACE_ID))
            service = MysqlTraceHttpService(sessions)
            assert await service.turn_trace(VALIDATION_SCOPE, "conv-2", "turn-1") is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_soft_deleted_conversation_trace_rows_remain(owned_project_mysql):
    async def scenario():
        engine, sessions = await _fresh_engine(owned_project_mysql)
        try:
            await _seed_conversation(sessions, "conv-1", "turn-1", _traceparent(TRACE_ID))
            await _seed_spans_and_calls(sessions, TRACE_ID, "turn-1")
            async with sessions() as session, session.begin():
                await session.execute(
                    conversation.update()
                    .where(conversation.c.conversation_id == "conv-1")
                    .values(deleted_at=datetime(2026, 1, 2), deleted_by="tester")
                )
            async with sessions() as session:
                spans = (
                    (
                        await session.execute(
                            select(trace_span).where(trace_span.c.trace_id == TRACE_ID)
                        )
                    )
                    .mappings()
                    .all()
                )
                calls = (
                    (
                        await session.execute(
                            select(model_call).where(model_call.c.trace_id == TRACE_ID)
                        )
                    )
                    .mappings()
                    .all()
                )
                content = (await session.execute(select(model_call_content))).mappings().all()
            assert len(spans) == 2
            assert len(calls) == 3
            assert len(content) == 3

            service = MysqlTraceHttpService(sessions)
            assert await service.turn_trace(VALIDATION_SCOPE, "conv-1", "turn-1") is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_same_ids_in_other_enterprise_do_not_leak():
    """The chat_turn/conversation join must not match a row from another Enterprise.

    A true cross-enterprise row-level exploit cannot be seeded against the real
    schema: `conversation`'s primary key is `(project_id, conversation_id)`
    alone (no `enterprise_id` column in the key -- see
    `modules/chat/adapters/mysql_conversations.py`), so two Enterprises can
    never hold two different rows for the same `(project_id, conversation_id)`
    pair to exercise a live leak through `owned_project_mysql` -- the second
    insert would fail on the existing primary key, not on a missing scope
    check. This asserts the join predicate itself is scoped by
    `enterprise_id` (matching the identical join in
    `MysqlConversationRepository.claim_queued`), so a future schema change
    that drops the `(project_id, conversation_id)` uniqueness invariant does
    not silently reopen a leak here.
    """
    clause = str(_chat_turn_conversation_join().onclause)
    assert "chat_turn.enterprise_id = conversation.enterprise_id" in clause
    assert "chat_turn.project_id = conversation.project_id" in clause
    assert "chat_turn.chat_id = conversation.conversation_id" in clause
