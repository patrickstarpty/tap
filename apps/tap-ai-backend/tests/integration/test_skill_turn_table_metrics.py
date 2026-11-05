"""`TurnTableSkillRuntimeMetrics` integration test (Task 9, 2026-11-01 Skill
Hub phase 1): per-Skill runs, failures and latency straight off
`turn_skill_activation` / `chat_turn` / `chat_event` -- no Langfuse involved.

Requires `TAP_RUN_MYSQL_INTEGRATION=1`; uses the isolated `owned_project_mysql`
fixture (never the shared default MySQL service)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from scripts.migration_support import IsolatedMysql

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.skill_hub.adapters.turn_table_metrics import TurnTableSkillRuntimeMetrics
from tap.modules.skill_hub.ports import SkillRuntimeStats
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.reporting.period import Period
from tests.integration.test_skill_hub_store import _insert_activation, _insert_skill
from tests.integration.test_turn_table_metrics import _T0, _insert_turn
from tests.owned_mysql import owned_project_database_url


def test_turn_table_counts_runs_and_failures_per_skill(
    owned_project_mysql: IsolatedMysql,
) -> None:
    async def used_turn(
        connection,
        turn_id: str,
        skill_ids: list[str],
        *,
        state: str,
        offset: timedelta,
        latency: timedelta | None,
    ) -> None:
        created_at = _T0 + offset
        await _insert_turn(
            connection,
            turn_id=turn_id,
            chat_id=f"chat-{uuid4().hex[:8]}",
            state=state,
            created_at=created_at,
            agent_revision_id=None,
            terminal_event_type=None if latency is None else f"turn.{state}",
            completed_at=None if latency is None else created_at + latency,
        )
        for skill_id in skill_ids:
            await _insert_activation(
                connection,
                VALIDATION_SCOPE,
                turn_id=turn_id,
                skill_id=skill_id,
                mode="auto",
                activated_at=created_at,
            )

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                for skill_id in ("s-a", "s-b", "s-idle"):
                    await _insert_skill(connection, VALIDATION_SCOPE, skill_id)
                # One run using two Skills counts once for each.
                await used_turn(
                    connection,
                    "t-both",
                    ["s-a", "s-b"],
                    state="completed",
                    offset=timedelta(0),
                    latency=timedelta(milliseconds=1500),
                )
                await used_turn(
                    connection,
                    "t-failed",
                    ["s-a"],
                    state="failed",
                    offset=timedelta(minutes=1),
                    latency=timedelta(milliseconds=500),
                )
                # Still running: no outcome yet, not a run.
                await used_turn(
                    connection,
                    "t-running",
                    ["s-a"],
                    state="running",
                    offset=timedelta(minutes=2),
                    latency=None,
                )
                # Outside the period.
                await used_turn(
                    connection,
                    "t-next-day",
                    ["s-b"],
                    state="failed",
                    offset=timedelta(days=1),
                    latency=timedelta(milliseconds=100),
                )

            metrics = TurnTableSkillRuntimeMetrics(sessions)
            stats = await metrics.skill_stats(
                VALIDATION_SCOPE.project_id, Period.parse(_T0.date(), _T0.date())
            )

            assert stats == [
                SkillRuntimeStats("s-a", 2, 1, 2000, Decimal(0)),
                SkillRuntimeStats("s-b", 1, 0, 1500, Decimal(0)),
            ]
            other_project = await metrics.skill_stats(
                "other-project", Period.parse(_T0.date(), _T0.date())
            )
            assert other_project == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())
