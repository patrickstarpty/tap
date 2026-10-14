"""MysqlLangfuseSyncStore integration tests: cursor semantics, joins, failure roundtrip."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa
from scripts.migration_support import IsolatedMysql
from sqlalchemy.ext.asyncio import AsyncConnection

from tap.modules.access.adapters import mysql as _access_mysql  # noqa: F401  (registers FK targets)
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.mysql_langfuse_sync import MysqlLangfuseSyncStore
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.telemetry.schema import model_call, model_call_content, trace_span
from tests.owned_mysql import owned_project_database_url

_T0 = datetime(2026, 1, 1, 0, 0, 0)
_T1 = datetime(2026, 1, 1, 0, 1, 0)
_T2 = datetime(2026, 1, 1, 0, 2, 0)


def _scope_values() -> dict[str, str]:
    return {
        "enterprise_id": VALIDATION_SCOPE.enterprise_id,
        "project_id": VALIDATION_SCOPE.project_id,
        "actor_id": VALIDATION_SCOPE.actor_id,
        "identity_mode": VALIDATION_SCOPE.identity_mode.value,
        "identity_origin": VALIDATION_SCOPE.identity_mode.value.upper(),
    }


async def _insert_span(
    connection: AsyncConnection,
    *,
    trace_id: str,
    span_id: str,
    recorded_at: datetime,
    parent_span_id: str | None = None,
    turn_id: str | None = None,
    job_id: str | None = None,
    started_at: datetime | None = None,
) -> None:
    await connection.execute(
        sa.insert(trace_span).values(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            turn_id=turn_id,
            job_id=job_id,
            service_name="tap-ai-backend",
            name="turn.execute",
            status="ok",
            status_message=None,
            started_at=started_at or recorded_at,
            duration_ms=10,
            attributes={"tap.attempt": 1},
            recorded_at=recorded_at,
            **_scope_values(),
        )
    )


async def _insert_conversation_and_turn(
    connection: AsyncConnection, *, turn_id: str, chat_id: str
) -> None:
    from tap.modules.chat.adapters.mysql import chat_turn
    from tap.modules.chat.adapters.mysql_conversations import conversation

    await connection.execute(
        sa.insert(conversation).values(
            conversation_id=chat_id,
            title="t",
            created_at=_T0,
            updated_at=_T0,
            **_scope_values(),
        )
    )
    await connection.execute(
        sa.insert(chat_turn).values(
            turn_id=turn_id,
            chat_id=chat_id,
            client_request_id=f"req-{turn_id}",
            message="hi",
            state="completed",
            processing_attempt=0,
            last_sequence=0,
            created_at=_T0,
            traceparent=None,
            **_scope_values(),
        )
    )


def test_scan_traces_respects_cursor_and_cutoff(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_span(
                    connection, trace_id=uuid4().hex, span_id=uuid4().hex[:16], recorded_at=_T0
                )
                await _insert_span(
                    connection, trace_id=uuid4().hex, span_id=uuid4().hex[:16], recorded_at=_T1
                )
                await _insert_span(
                    connection, trace_id=uuid4().hex, span_id=uuid4().hex[:16], recorded_at=_T2
                )

            store = MysqlLangfuseSyncStore(sessions)
            trace_ids, batch_max = await store.scan_traces(cutoff=_T1, limit=200)
            assert len(trace_ids) == 2
            assert batch_max == _T1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_scan_is_not_advanced_until_advance_called(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_span(
                    connection, trace_id=uuid4().hex, span_id=uuid4().hex[:16], recorded_at=_T0
                )

            store = MysqlLangfuseSyncStore(sessions)
            first_ids, first_max = await store.scan_traces(cutoff=_T2, limit=200)
            assert len(first_ids) == 1
            second_ids, second_max = await store.scan_traces(cutoff=_T2, limit=200)
            assert second_ids == first_ids
            assert second_max == first_max
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_row_inserted_behind_cursor_is_not_rescanned(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_span(
                    connection, trace_id=uuid4().hex, span_id=uuid4().hex[:16], recorded_at=_T0
                )
                await _insert_span(
                    connection, trace_id=uuid4().hex, span_id=uuid4().hex[:16], recorded_at=_T1
                )

            store = MysqlLangfuseSyncStore(sessions)
            _, batch_max = await store.scan_traces(cutoff=_T1, limit=200)
            assert batch_max == _T1
            await store.advance("trace", batch_max)

            behind_cursor = _T0 + timedelta(seconds=30)
            async with engine.begin() as connection:
                await _insert_span(
                    connection,
                    trace_id=uuid4().hex,
                    span_id=uuid4().hex[:16],
                    recorded_at=behind_cursor,
                )

            trace_ids, _ = await store.scan_traces(cutoff=_T2, limit=200)
            assert trace_ids == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_load_trace_joins_calls_content_and_conversation(
    owned_project_mysql: IsolatedMysql,
) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            trace_id = uuid4().hex
            root_span_id = uuid4().hex[:16]
            child_span_id = uuid4().hex[:16]
            turn_id = f"turn-{uuid4().hex[:8]}"
            chat_id = f"chat-{uuid4().hex[:8]}"
            call_id = uuid4().hex

            async with engine.begin() as connection:
                await _insert_conversation_and_turn(connection, turn_id=turn_id, chat_id=chat_id)
                await _insert_span(
                    connection,
                    trace_id=trace_id,
                    span_id=root_span_id,
                    recorded_at=_T0,
                    turn_id=turn_id,
                )
                await _insert_span(
                    connection,
                    trace_id=trace_id,
                    span_id=child_span_id,
                    parent_span_id=root_span_id,
                    recorded_at=_T0,
                    turn_id=turn_id,
                    started_at=_T0 + timedelta(seconds=1),
                )
                await connection.execute(
                    sa.insert(model_call).values(
                        call_id=call_id,
                        trace_id=trace_id,
                        span_id=child_span_id,
                        turn_id=turn_id,
                        job_id=None,
                        operation="chat",
                        model_name="gpt-test",
                        upstream_model="gpt-test-upstream",
                        provider="openai",
                        input_tokens=10,
                        output_tokens=20,
                        cost_usd=None,
                        latency_ms=100,
                        attempts=1,
                        status="ok",
                        error_code=None,
                        gateway_call_id=None,
                        provider_request_id=None,
                        created_at=_T0,
                        **_scope_values(),
                    )
                )
                await connection.execute(
                    sa.insert(model_call_content).values(
                        call_id=call_id,
                        request_json='{"messages": []}',
                        response_text="hello",
                        reasoning_text=None,
                        **_scope_values(),
                    )
                )

            store = MysqlLangfuseSyncStore(sessions)
            bundle = await store.load_trace(trace_id)
            assert bundle is not None
            assert bundle.conversation_id == chat_id
            assert bundle.project_id == VALIDATION_SCOPE.project_id
            assert bundle.actor_id == VALIDATION_SCOPE.actor_id
            assert bundle.turn_id == turn_id
            assert len(bundle.spans) == 2
            assert len(bundle.calls) == 1
            assert bundle.calls[0].request_json == '{"messages": []}'
            assert bundle.calls[0].response_text == "hello"

            job_trace_id = uuid4().hex
            async with engine.begin() as connection:
                await _insert_span(
                    connection,
                    trace_id=job_trace_id,
                    span_id=uuid4().hex[:16],
                    recorded_at=_T0,
                    job_id="job-1",
                )
            job_bundle = await store.load_trace(job_trace_id)
            assert job_bundle is not None
            assert job_bundle.conversation_id is None
            assert job_bundle.job_id == "job-1"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_failures_roundtrip(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            store = MysqlLangfuseSyncStore(sessions)
            trace_id = uuid4().hex
            await store.record_failure("trace", trace_id, "boom")
            await store.record_failure("trace", trace_id, "boom again")

            async with engine.connect() as connection:
                from tap.platform.telemetry.schema import langfuse_sync_failure

                row = (
                    (
                        await connection.execute(
                            sa.select(langfuse_sync_failure).where(
                                langfuse_sync_failure.c.subject_id == trace_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            assert row["failure_count"] == 2
            assert row["last_error"] == "boom again"

            pending = await store.failures("trace", 10)
            assert trace_id in pending

            await store.clear_failure("trace", trace_id)
            pending_after_clear = await store.failures("trace", 10)
            assert pending_after_clear == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_migration_backfills_recorded_at_from_started_at(
    owned_project_mysql: IsolatedMysql,
) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            trace_id = uuid4().hex
            span_id = uuid4().hex[:16]
            started_at = datetime(2025, 6, 1, 12, 0, 0)
            # Bypass the fixture helper's own `recorded_at` to exercise the server
            # default the way a pre-migration row (inserted by the exporter before
            # this column existed) would have been created, then backfilled.
            async with engine.begin() as connection:
                await connection.execute(
                    sa.insert(trace_span).values(
                        trace_id=trace_id,
                        span_id=span_id,
                        parent_span_id=None,
                        turn_id=None,
                        job_id=None,
                        service_name="tap-ai-backend",
                        name="turn.execute",
                        status="ok",
                        status_message=None,
                        started_at=started_at,
                        duration_ms=5,
                        attributes={},
                        **_scope_values(),
                    )
                )
                await connection.execute(sa.text("UPDATE trace_span SET recorded_at = started_at"))

            async with engine.connect() as connection:
                row = (
                    (
                        await connection.execute(
                            sa.select(trace_span.c.recorded_at).where(
                                trace_span.c.trace_id == trace_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            assert row["recorded_at"] == started_at
        finally:
            await engine.dispose()

    asyncio.run(scenario())
