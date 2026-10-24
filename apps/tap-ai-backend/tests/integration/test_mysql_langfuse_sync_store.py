"""MysqlLangfuseSyncStore integration tests: cursor semantics, joins, failure roundtrip."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import sqlalchemy as sa
from scripts.migration_support import IsolatedMysql
from sqlalchemy.ext.asyncio import AsyncConnection

from tap.modules.access.adapters import mysql as _access_mysql  # noqa: F401  (registers FK targets)
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.mysql_langfuse_sync import MysqlLangfuseSyncStore
from tap.modules.chat.adapters.mysql_feedback import answer_feedback
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
    name: str = "turn.execute",
) -> None:
    await connection.execute(
        sa.insert(trace_span).values(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            turn_id=turn_id,
            job_id=job_id,
            service_name="tap-ai-backend",
            name=name,
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


async def _insert_turn_input_snapshot(
    connection: AsyncConnection,
    *,
    turn_id: str,
    agent_revision_id: str | None,
) -> None:
    from tap.modules.chat.adapters.mysql_conversations import turn_input_snapshot

    await connection.execute(
        sa.insert(turn_input_snapshot).values(
            snapshot_id=f"snap-{uuid4().hex[:16]}",
            turn_id=turn_id,
            snapshot_digest="sha256:" + uuid4().hex,
            snapshot={"agent_revision_id": agent_revision_id},
            created_at=_T0,
            **_scope_values(),
        )
    )


async def _insert_feedback(
    connection: AsyncConnection,
    *,
    feedback_id: str,
    turn_id: str,
    chat_id: str,
    updated_at: datetime,
    rating: str | None = "up",
) -> None:
    await _insert_conversation_and_turn(connection, turn_id=turn_id, chat_id=chat_id)
    await connection.execute(
        sa.insert(answer_feedback).values(
            feedback_id=feedback_id,
            conversation_id=chat_id,
            turn_id=turn_id,
            trace_id=None,
            rating=rating,
            comment=None,
            created_at=updated_at,
            updated_at=updated_at,
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


def test_scan_traces_full_mixed_page_drops_trailing_tie_until_next_scan(
    owned_project_mysql: IsolatedMysql,
) -> None:
    """The one `_paginate` branch with no prior MySQL-level coverage: a full
    page spanning more than one timestamp (A alone at `_T0`, C and D tied at
    `_T1`, `limit=2`). The first scan must return only A, positioned at `_T0`
    — not advance to `_T1` and not include C or D yet, since a 2-row page
    cannot prove there isn't a third row tied at `_T1` beyond it. The second
    scan (after `advance`) must then return both C and D, positioned at
    `_T1`."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            trace_a, trace_c, trace_d = uuid4().hex, uuid4().hex, uuid4().hex
            async with engine.begin() as connection:
                await _insert_span(
                    connection, trace_id=trace_a, span_id=uuid4().hex[:16], recorded_at=_T0
                )
                await _insert_span(
                    connection, trace_id=trace_c, span_id=uuid4().hex[:16], recorded_at=_T1
                )
                await _insert_span(
                    connection, trace_id=trace_d, span_id=uuid4().hex[:16], recorded_at=_T1
                )

            store = MysqlLangfuseSyncStore(sessions)
            first_ids, first_position = await store.scan_traces(cutoff=_T1, limit=2)
            assert first_ids == [trace_a]
            assert first_position == _T0

            await store.advance("trace", first_position)
            second_ids, second_position = await store.scan_traces(cutoff=_T1, limit=2)
            assert set(second_ids) == {trace_c, trace_d}
            assert second_position == _T1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_scan_feedback_full_mixed_page_drops_trailing_tie_until_next_scan(
    owned_project_mysql: IsolatedMysql,
) -> None:
    """Same shape as `test_scan_traces_full_mixed_page_drops_trailing_tie_until_next_scan`,
    for `scan_feedback`."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            feedback_a, feedback_c, feedback_d = uuid4().hex, uuid4().hex, uuid4().hex
            async with engine.begin() as connection:
                for feedback_id, updated_at in (
                    (feedback_a, _T0),
                    (feedback_c, _T1),
                    (feedback_d, _T1),
                ):
                    await _insert_feedback(
                        connection,
                        feedback_id=feedback_id,
                        turn_id=f"turn-{feedback_id[:8]}",
                        chat_id=f"chat-{feedback_id[:8]}",
                        updated_at=updated_at,
                    )

            store = MysqlLangfuseSyncStore(sessions)
            first_records, first_position = await store.scan_feedback(cutoff=_T1, limit=2)
            assert [record.feedback_id for record in first_records] == [feedback_a]
            assert first_position == _T0

            await store.advance("feedback", first_position)
            second_records, second_position = await store.scan_feedback(cutoff=_T1, limit=2)
            assert {record.feedback_id for record in second_records} == {feedback_c, feedback_d}
            assert second_position == _T1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_scan_traces_tie_at_cut_is_not_lost(owned_project_mysql: IsolatedMysql) -> None:
    """A `LIMIT`-sized page that lands exactly on a group of tied timestamps must
    not advance the cursor until every row sharing that timestamp is accounted
    for, even when the page is smaller than the tied group (here: `limit=1` with
    two traces tied at `_T1`)."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            trace_c, trace_d, trace_e = uuid4().hex, uuid4().hex, uuid4().hex
            async with engine.begin() as connection:
                await _insert_span(
                    connection, trace_id=trace_c, span_id=uuid4().hex[:16], recorded_at=_T1
                )
                await _insert_span(
                    connection, trace_id=trace_d, span_id=uuid4().hex[:16], recorded_at=_T1
                )
                await _insert_span(
                    connection, trace_id=trace_e, span_id=uuid4().hex[:16], recorded_at=_T2
                )

            store = MysqlLangfuseSyncStore(sessions)
            trace_ids, position = await store.scan_traces(cutoff=_T2, limit=1)
            assert set(trace_ids) == {trace_c, trace_d}
            assert position == _T1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_scan_traces_full_page_of_identical_timestamp_refetches_without_limit(
    owned_project_mysql: IsolatedMysql,
) -> None:
    """When every row in a full page shares one timestamp, there may be more rows
    at that timestamp past the `LIMIT`; the store must fetch all of them, not
    just the page, before advancing past that timestamp."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            trace_f, trace_g, trace_h = uuid4().hex, uuid4().hex, uuid4().hex
            async with engine.begin() as connection:
                for trace_id in (trace_f, trace_g, trace_h):
                    await _insert_span(
                        connection, trace_id=trace_id, span_id=uuid4().hex[:16], recorded_at=_T1
                    )

            store = MysqlLangfuseSyncStore(sessions)
            trace_ids, position = await store.scan_traces(cutoff=_T1, limit=2)
            assert set(trace_ids) == {trace_f, trace_g, trace_h}
            assert position == _T1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_scan_traces_repeated_scan_returns_every_trace_at_least_once(
    owned_project_mysql: IsolatedMysql,
) -> None:
    """Interleaved spans (one trace split across two timestamps, another trace
    between them) with a `limit` smaller than the number of distinct traces: a
    repeated scan+advance loop must eventually surface every trace, never stall,
    and never regress its position."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            trace_a, trace_b = uuid4().hex, uuid4().hex
            async with engine.begin() as connection:
                await _insert_span(
                    connection, trace_id=trace_a, span_id=uuid4().hex[:16], recorded_at=_T0
                )
                await _insert_span(
                    connection,
                    trace_id=trace_b,
                    span_id=uuid4().hex[:16],
                    recorded_at=_T0 + timedelta(seconds=30),
                )
                await _insert_span(
                    connection,
                    trace_id=trace_a,
                    span_id=uuid4().hex[:16],
                    recorded_at=_T0 + timedelta(minutes=10),
                    started_at=_T0 + timedelta(minutes=10),
                )

            store = MysqlLangfuseSyncStore(sessions)
            cutoff = _T0 + timedelta(hours=1)
            seen: set[str] = set()
            last_position: datetime | None = None
            for _ in range(10):
                trace_ids, position = await store.scan_traces(cutoff=cutoff, limit=1)
                if not trace_ids:
                    break
                if last_position is not None:
                    assert position is not None
                    assert position > last_position
                last_position = position
                seen.update(trace_ids)
                await store.advance("trace", position)
            else:
                pytest.fail("scan did not converge within 10 iterations")
            assert seen == {trace_a, trace_b}
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_scan_feedback_tie_at_cut_is_not_lost(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            feedback_c, feedback_d, feedback_e = uuid4().hex, uuid4().hex, uuid4().hex
            async with engine.begin() as connection:
                for feedback_id, updated_at in (
                    (feedback_c, _T1),
                    (feedback_d, _T1),
                    (feedback_e, _T2),
                ):
                    await _insert_feedback(
                        connection,
                        feedback_id=feedback_id,
                        turn_id=f"turn-{feedback_id[:8]}",
                        chat_id=f"chat-{feedback_id[:8]}",
                        updated_at=updated_at,
                    )

            store = MysqlLangfuseSyncStore(sessions)
            records, position = await store.scan_feedback(cutoff=_T2, limit=1)
            assert {record.feedback_id for record in records} == {feedback_c, feedback_d}
            assert position == _T1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_scan_feedback_full_page_of_identical_timestamp_refetches_without_limit(
    owned_project_mysql: IsolatedMysql,
) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            feedback_ids = [uuid4().hex for _ in range(3)]
            async with engine.begin() as connection:
                for feedback_id in feedback_ids:
                    await _insert_feedback(
                        connection,
                        feedback_id=feedback_id,
                        turn_id=f"turn-{feedback_id[:8]}",
                        chat_id=f"chat-{feedback_id[:8]}",
                        updated_at=_T1,
                    )

            store = MysqlLangfuseSyncStore(sessions)
            records, position = await store.scan_feedback(cutoff=_T1, limit=2)
            assert {record.feedback_id for record in records} == set(feedback_ids)
            assert position == _T1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_scan_feedback_repeated_scan_returns_every_feedback_at_least_once(
    owned_project_mysql: IsolatedMysql,
) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            feedback_ids = [uuid4().hex for _ in range(3)]
            async with engine.begin() as connection:
                for index, feedback_id in enumerate(feedback_ids):
                    await _insert_feedback(
                        connection,
                        feedback_id=feedback_id,
                        turn_id=f"turn-{feedback_id[:8]}",
                        chat_id=f"chat-{feedback_id[:8]}",
                        updated_at=_T0 + timedelta(minutes=index),
                    )

            store = MysqlLangfuseSyncStore(sessions)
            cutoff = _T0 + timedelta(hours=1)
            seen: set[str] = set()
            last_position: datetime | None = None
            for _ in range(10):
                records, position = await store.scan_feedback(cutoff=cutoff, limit=1)
                if not records:
                    break
                if last_position is not None:
                    assert position is not None
                    assert position > last_position
                last_position = position
                seen.update(record.feedback_id for record in records)
                await store.advance("feedback", position)
            else:
                pytest.fail("scan did not converge within 10 iterations")
            assert seen == set(feedback_ids)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_recorded_at_default_is_utc_regardless_of_session_time_zone(
    owned_project_mysql: IsolatedMysql,
) -> None:
    """`recorded_at`'s server default must use `UTC_TIMESTAMP(6)`, not
    `CURRENT_TIMESTAMP(6)`: the latter would follow the connection's session
    time zone and desync from the naive-UTC `started_at` column and the
    worker's UTC cutoff whenever that session isn't UTC."""

    sync_engine = sa.create_engine(owned_project_mysql.url)
    trace_id = uuid4().hex
    span_id = uuid4().hex[:16]
    try:
        with sync_engine.begin() as connection:
            connection.execute(sa.text("SET time_zone = '+08:00'"))
            connection.execute(
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
                    started_at=datetime(2026, 1, 1),
                    duration_ms=5,
                    attributes={},
                    **_scope_values(),
                )
            )
            row = (
                connection.execute(
                    sa.select(trace_span.c.recorded_at).where(trace_span.c.trace_id == trace_id)
                )
                .mappings()
                .one()
            )
        recorded_at = row["recorded_at"]
        now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        assert abs((recorded_at - now_utc_naive).total_seconds()) < 5
    finally:
        sync_engine.dispose()


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


def test_standalone_execute_root_gets_no_agent_tag(owned_project_mysql: IsolatedMysql) -> None:
    """`test_load_trace_joins_calls_content_and_conversation` already inserts a
    Turn trace whose root span is named `turn.execute` (the default) -- per
    spec 2.2, only a `turn.request`-rooted trace is tagged. This asserts that
    directly: `agent_key`/`turn_outcome` must stay `None`."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            trace_id = uuid4().hex
            turn_id = f"turn-{uuid4().hex[:8]}"
            chat_id = f"chat-{uuid4().hex[:8]}"
            async with engine.begin() as connection:
                await _insert_conversation_and_turn(connection, turn_id=turn_id, chat_id=chat_id)
                await _insert_span(
                    connection,
                    trace_id=trace_id,
                    span_id=uuid4().hex[:16],
                    recorded_at=_T0,
                    turn_id=turn_id,
                    name="turn.execute",
                )

            store = MysqlLangfuseSyncStore(sessions)
            bundle = await store.load_trace(trace_id)
            assert bundle is not None
            assert bundle.agent_key is None
            assert bundle.turn_outcome is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_load_trace_resolves_agent_id_from_snapshot(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
        from tap.modules.ai.application.assets import validation_asset_seed

        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            catalog_engine = create_async_engine(
                owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy")
            )
            try:
                catalog = MysqlAssetCatalog(
                    async_sessionmaker(catalog_engine, expire_on_commit=False),
                    scope=VALIDATION_SCOPE,
                )
                seed = validation_asset_seed(VALIDATION_SCOPE)
                await catalog.seed(seed)
            finally:
                await catalog_engine.dispose()
            agent_revision_id = seed.agents[0].revision_id
            agent_id = seed.agents[0].asset_id

            trace_id = uuid4().hex
            turn_id = f"turn-{uuid4().hex[:8]}"
            chat_id = f"chat-{uuid4().hex[:8]}"
            async with engine.begin() as connection:
                await _insert_conversation_and_turn(connection, turn_id=turn_id, chat_id=chat_id)
                await _insert_turn_input_snapshot(
                    connection, turn_id=turn_id, agent_revision_id=agent_revision_id
                )
                await _insert_span(
                    connection,
                    trace_id=trace_id,
                    span_id=uuid4().hex[:16],
                    recorded_at=_T0,
                    turn_id=turn_id,
                    name="turn.request",
                )

            store = MysqlLangfuseSyncStore(sessions)
            bundle = await store.load_trace(trace_id)
            assert bundle is not None
            assert bundle.agent_key == agent_id
            # `_insert_conversation_and_turn` creates the Turn with state
            # "completed" -- a terminal state, so `turn_outcome` is set too.
            assert bundle.turn_outcome == "completed"

            # A `turn_input_snapshot` row whose `agent_revision_id` is empty
            # (no Agent was selected for this Turn) -> falls back to the
            # Tapper default agent.
            default_trace_id = uuid4().hex
            default_turn_id = f"turn-{uuid4().hex[:8]}"
            default_chat_id = f"chat-{uuid4().hex[:8]}"
            async with engine.begin() as connection:
                await _insert_conversation_and_turn(
                    connection, turn_id=default_turn_id, chat_id=default_chat_id
                )
                await _insert_turn_input_snapshot(
                    connection, turn_id=default_turn_id, agent_revision_id=None
                )
                await _insert_span(
                    connection,
                    trace_id=default_trace_id,
                    span_id=uuid4().hex[:16],
                    recorded_at=_T0,
                    turn_id=default_turn_id,
                    name="turn.request",
                )
            default_bundle = await store.load_trace(default_trace_id)
            assert default_bundle is not None
            assert default_bundle.agent_key == "tapper_default"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_unknown_revision_tags_unknown(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            trace_id = uuid4().hex
            turn_id = f"turn-{uuid4().hex[:8]}"
            chat_id = f"chat-{uuid4().hex[:8]}"
            async with engine.begin() as connection:
                await _insert_conversation_and_turn(connection, turn_id=turn_id, chat_id=chat_id)
                await _insert_turn_input_snapshot(
                    connection, turn_id=turn_id, agent_revision_id="no-such-revision"
                )
                await _insert_span(
                    connection,
                    trace_id=trace_id,
                    span_id=uuid4().hex[:16],
                    recorded_at=_T0,
                    turn_id=turn_id,
                    name="turn.request",
                )

            store = MysqlLangfuseSyncStore(sessions)
            bundle = await store.load_trace(trace_id)
            assert bundle is not None
            assert bundle.agent_key == "unknown"
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
    """At `0032_answer_feedback`, `trace_span` has no `recorded_at` column yet —
    insert a row there (the way the exporter would have, pre-migration), then
    upgrade to `0033_langfuse_sync` and assert the new column was backfilled
    from `started_at`, not left at the migration's own run time."""
    owned_project_mysql.rebuild("0032_answer_feedback")
    sync_engine = sa.create_engine(owned_project_mysql.url)
    trace_id = uuid4().hex
    span_id = uuid4().hex[:16]
    started_at = datetime(2025, 6, 1, 12, 0, 0)
    try:
        with sync_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO trace_span "
                    "(trace_id, span_id, parent_span_id, turn_id, job_id, service_name, "
                    "name, status, status_message, started_at, duration_ms, attributes, "
                    "enterprise_id, project_id, actor_id, identity_mode, identity_origin) "
                    "VALUES (:trace_id, :span_id, NULL, NULL, NULL, 'tap-ai-backend', "
                    "'turn.execute', 'ok', NULL, :started_at, 5, '{}', "
                    ":enterprise_id, :project_id, :actor_id, :identity_mode, :identity_origin)"
                ),
                {
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "started_at": started_at,
                    **_scope_values(),
                },
            )

        owned_project_mysql.upgrade("0033_langfuse_sync")

        with sync_engine.connect() as connection:
            row = (
                connection.execute(
                    sa.text("SELECT recorded_at FROM trace_span WHERE trace_id = :trace_id"),
                    {"trace_id": trace_id},
                )
                .mappings()
                .one()
            )
        assert row["recorded_at"] == started_at
    finally:
        sync_engine.dispose()
