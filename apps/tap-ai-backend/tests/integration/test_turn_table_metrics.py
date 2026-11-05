"""`TurnTableRuntimeMetrics` integration test (Task 5, 2026-10-23 agent team
view): two Agent-attributed Turns and one default (no-Agent) Turn, counted per
`agent_key` straight off `chat_turn` / `turn_input_snapshot` / `ai_agent_revision`
-- no Langfuse involved."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa
from scripts.migration_support import IsolatedMysql

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.agent_team.adapters.turn_table_metrics import TurnTableRuntimeMetrics
from tap.modules.agent_team.domain.models import TAPPER_DEFAULT_AGENT_KEY, Period
from tap.modules.ai.adapters.mysql import ai_agent, ai_agent_revision
from tap.modules.chat.adapters.mysql import chat_event, chat_turn
from tap.modules.chat.adapters.mysql_conversations import conversation, turn_input_snapshot
from tap.platform.db.session import create_engine_and_session_factory
from tests.owned_mysql import owned_project_database_url

_T0 = datetime(2026, 10, 1, 0, 0, 0)


def _scope_values() -> dict[str, str]:
    return {
        "enterprise_id": VALIDATION_SCOPE.enterprise_id,
        "project_id": VALIDATION_SCOPE.project_id,
        "actor_id": VALIDATION_SCOPE.actor_id,
        "identity_mode": VALIDATION_SCOPE.identity_mode.value,
        "identity_origin": VALIDATION_SCOPE.identity_mode.value.upper(),
    }


async def _insert_agent(connection, *, agent_id: str, revision_id: str) -> None:
    await connection.execute(
        sa.insert(ai_agent).values(
            agent_id=agent_id,
            display_name=agent_id,
            created_at=_T0,
            job_title=None,
            owner_actor_id=VALIDATION_SCOPE.actor_id,
            owner_assigned_at=_T0,
            **_scope_values(),
        )
    )
    await connection.execute(
        sa.insert(ai_agent_revision).values(
            revision_id=revision_id,
            agent_id=agent_id,
            revision_number=1,
            display_name=agent_id,
            description=None,
            source_kind="custom",
            content_digest="sha256:" + uuid4().hex,
            system_instruction_digest="sha256:" + uuid4().hex,
            system_instruction="be helpful",
            tool_allowlist=[],
            output_schema_digest="sha256:" + uuid4().hex,
            output_schema_json=None,
            skill_revision_ids=None,
            default_model_alias=None,
            status="active",
            adopted_from_revision_id=None,
            created_at=_T0,
            **_scope_values(),
        )
    )


async def _insert_turn(
    connection,
    *,
    turn_id: str,
    chat_id: str,
    state: str,
    created_at: datetime,
    agent_revision_id: str | None,
    terminal_event_type: str | None = None,
    completed_at: datetime | None = None,
    with_snapshot: bool = True,
) -> None:
    await connection.execute(
        sa.insert(conversation).values(
            conversation_id=chat_id,
            title="t",
            created_at=created_at,
            updated_at=created_at,
            **_scope_values(),
        )
    )
    await connection.execute(
        sa.insert(chat_turn).values(
            turn_id=turn_id,
            chat_id=chat_id,
            client_request_id=f"req-{turn_id}",
            message="hi",
            state=state,
            processing_attempt=0,
            last_sequence=1,
            created_at=created_at,
            traceparent=None,
            **_scope_values(),
        )
    )
    if with_snapshot:
        await connection.execute(
            sa.insert(turn_input_snapshot).values(
                snapshot_id=f"snap-{uuid4().hex[:16]}",
                turn_id=turn_id,
                snapshot_digest="sha256:" + uuid4().hex,
                snapshot={"agent_revision_id": agent_revision_id},
                created_at=created_at,
                **_scope_values(),
            )
        )
    if terminal_event_type is not None:
        assert completed_at is not None
        await connection.execute(
            sa.insert(chat_event).values(
                event_id=f"evt-{uuid4().hex[:16]}",
                turn_id=turn_id,
                sequence=1,
                stream_sequence=1,
                event_type=terminal_event_type,
                payload={},
                schema_version=1,
                occurred_at=completed_at,
                **_scope_values(),
            )
        )


def test_turn_table_counts_runs_per_agent_key(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            agent_a_turn = f"turn-{uuid4().hex[:8]}"
            agent_b_turn = f"turn-{uuid4().hex[:8]}"
            default_turn = f"turn-{uuid4().hex[:8]}"

            async with engine.begin() as connection:
                await _insert_agent(connection, agent_id="agent-a", revision_id="rev-a")
                await _insert_agent(connection, agent_id="agent-b", revision_id="rev-b")

                await _insert_turn(
                    connection,
                    turn_id=agent_a_turn,
                    chat_id=f"chat-{uuid4().hex[:8]}",
                    state="completed",
                    created_at=_T0,
                    agent_revision_id="rev-a",
                    terminal_event_type="turn.completed",
                    completed_at=_T0 + timedelta(milliseconds=1500),
                )
                await _insert_turn(
                    connection,
                    turn_id=agent_b_turn,
                    chat_id=f"chat-{uuid4().hex[:8]}",
                    state="failed",
                    created_at=_T0 + timedelta(minutes=1),
                    agent_revision_id="rev-b",
                    terminal_event_type="turn.failed",
                    completed_at=_T0 + timedelta(minutes=1, milliseconds=500),
                )
                await _insert_turn(
                    connection,
                    turn_id=default_turn,
                    chat_id=f"chat-{uuid4().hex[:8]}",
                    state="completed",
                    created_at=_T0 + timedelta(minutes=2),
                    agent_revision_id=None,
                    terminal_event_type="turn.completed",
                    completed_at=_T0 + timedelta(minutes=2, milliseconds=2000),
                    with_snapshot=False,
                )

            metrics = TurnTableRuntimeMetrics(sessions)
            period = Period.parse(_T0.date(), _T0.date())
            stats = {
                row.agent_key: row
                for row in await metrics.agent_stats(VALIDATION_SCOPE.project_id, period)
            }

            assert stats.keys() == {"agent-a", "agent-b", TAPPER_DEFAULT_AGENT_KEY}
            assert stats["agent-a"].runs == 1
            assert stats["agent-a"].failures == 0
            assert stats["agent-a"].latency_total_ms == 1500
            assert stats["agent-a"].latency_p50_ms == 1500
            assert len(stats["agent-a"].served) == 1
            assert stats["agent-a"].served[0].actor_id == VALIDATION_SCOPE.actor_id
            assert stats["agent-a"].served[0].runs == 1

            assert stats["agent-b"].runs == 1
            assert stats["agent-b"].failures == 1
            assert stats["agent-b"].latency_total_ms == 500

            assert stats[TAPPER_DEFAULT_AGENT_KEY].runs == 1
            assert stats[TAPPER_DEFAULT_AGENT_KEY].latency_total_ms == 2000

            last_runs = await metrics.last_runs(
                VALIDATION_SCOPE.project_id, ["agent-a", "agent-b", TAPPER_DEFAULT_AGENT_KEY]
            )
            for value in last_runs.values():
                assert value.tzinfo == UTC
            assert last_runs["agent-a"] == _T0.replace(tzinfo=UTC)
            assert last_runs["agent-b"] == (_T0 + timedelta(minutes=1)).replace(tzinfo=UTC)
            assert last_runs[TAPPER_DEFAULT_AGENT_KEY] == (_T0 + timedelta(minutes=2)).replace(
                tzinfo=UTC
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_trend_buckets_by_day_and_week_and_excludes_non_runs(
    owned_project_mysql: IsolatedMysql,
) -> None:
    """One Agent, four terminal Turns split across two consecutive days in the
    same ISO week (2026-10-01 is a Thursday, 2026-10-02 a Friday; both bucket
    to Monday 2026-09-28), plus a Turn outside the queried period and a Turn
    still in flight (no terminal state, no terminal event) -- both of which
    must be excluded from every bucket."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            day1_completed = f"turn-{uuid4().hex[:8]}"
            day1_failed = f"turn-{uuid4().hex[:8]}"
            day2_abstained = f"turn-{uuid4().hex[:8]}"
            day2_canceled = f"turn-{uuid4().hex[:8]}"
            out_of_period = f"turn-{uuid4().hex[:8]}"
            in_flight = f"turn-{uuid4().hex[:8]}"

            day1 = datetime(2026, 10, 1, 0, 0, 0)
            day2 = datetime(2026, 10, 2, 0, 0, 0)

            async with engine.begin() as connection:
                await _insert_agent(connection, agent_id="agent-a", revision_id="rev-a")

                await _insert_turn(
                    connection,
                    turn_id=day1_completed,
                    chat_id=f"chat-{uuid4().hex[:8]}",
                    state="completed",
                    created_at=day1,
                    agent_revision_id="rev-a",
                    terminal_event_type="turn.completed",
                    completed_at=day1 + timedelta(milliseconds=1000),
                )
                await _insert_turn(
                    connection,
                    turn_id=day1_failed,
                    chat_id=f"chat-{uuid4().hex[:8]}",
                    state="failed",
                    created_at=day1 + timedelta(hours=1),
                    agent_revision_id="rev-a",
                    terminal_event_type="turn.failed",
                    completed_at=day1 + timedelta(hours=1, milliseconds=2000),
                )
                await _insert_turn(
                    connection,
                    turn_id=day2_abstained,
                    chat_id=f"chat-{uuid4().hex[:8]}",
                    state="abstained",
                    created_at=day2,
                    agent_revision_id="rev-a",
                    terminal_event_type="turn.abstained",
                    completed_at=day2 + timedelta(milliseconds=500),
                )
                await _insert_turn(
                    connection,
                    turn_id=day2_canceled,
                    chat_id=f"chat-{uuid4().hex[:8]}",
                    state="canceled",
                    created_at=day2 + timedelta(hours=1),
                    agent_revision_id="rev-a",
                    terminal_event_type="turn.canceled",
                    completed_at=day2 + timedelta(hours=1, milliseconds=300),
                )
                # Outside the [2026-10-01, 2026-10-02] period queried below.
                await _insert_turn(
                    connection,
                    turn_id=out_of_period,
                    chat_id=f"chat-{uuid4().hex[:8]}",
                    state="completed",
                    created_at=datetime(2026, 10, 8),
                    agent_revision_id="rev-a",
                    terminal_event_type="turn.completed",
                    completed_at=datetime(2026, 10, 8, 0, 0, 1),
                )
                # Still running: no terminal state, no terminal event.
                await _insert_turn(
                    connection,
                    turn_id=in_flight,
                    chat_id=f"chat-{uuid4().hex[:8]}",
                    state="running",
                    created_at=day1 + timedelta(hours=12),
                    agent_revision_id="rev-a",
                )

            metrics = TurnTableRuntimeMetrics(sessions)
            period = Period.parse(day1.date(), day2.date())

            by_day = await metrics.trend(VALIDATION_SCOPE.project_id, period, "day")
            assert len(by_day) == 2
            day_points = {point.bucket_start: point for point in by_day}
            assert day_points[day1.date()].runs == 2
            assert day_points[day1.date()].failures == 1
            assert day_points[day1.date()].latency_total_ms == 3000
            assert day_points[day2.date()].runs == 2
            assert day_points[day2.date()].failures == 0
            assert day_points[day2.date()].latency_total_ms == 800

            by_week = await metrics.trend(VALIDATION_SCOPE.project_id, period, "week")
            assert len(by_week) == 1
            week_point = by_week[0]
            assert week_point.bucket_start == day1.date() - timedelta(days=day1.weekday())
            assert week_point.runs == 4
            assert week_point.failures == 1
            assert week_point.latency_total_ms == 3800
        finally:
            await engine.dispose()

    asyncio.run(scenario())
