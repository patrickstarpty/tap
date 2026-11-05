"""`SkillRuntimeMetrics` backed directly by `turn_skill_activation` /
`chat_turn` / `chat_event`, for e2e runs without a Langfuse instance
(selected by the same `TAP_AGENT_TEAM_RUNTIME_METRICS=turn_table` switch as
the Agent team view). e2e-only, not the production adapter.

A run is a Turn in a terminal state (`completed`, `abstained`, `canceled`,
`failed`) created in the period -- the same "run" the Agent team view's
`TurnTableRuntimeMetrics` counts, and the only Turns the Langfuse sync tags
with an `outcome:`. Each Skill the Turn used counts that run once; a `failed`
Turn adds a failure. Latency is derived as in `TurnTableRuntimeMetrics`
(`chat_turn` has no duration column): the terminal `chat_event`'s
`occurred_at` minus `chat_turn.created_at`, or 0 when that event is missing.
Cost is always 0.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.chat.adapters.mysql import chat_event, chat_turn
from tap.modules.chat.adapters.mysql_skill_activations import turn_skill_activation
from tap.modules.skill_hub.ports import SkillRuntimeStats
from tap.platform.reporting.period import Period

_TERMINAL_EVENT_TYPES = ("turn.completed", "turn.abstained", "turn.canceled", "turn.failed")
_TERMINAL_STATES = ("completed", "abstained", "canceled", "failed")


class TurnTableSkillRuntimeMetrics:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def skill_stats(self, project_id: str, period: Period) -> list[SkillRuntimeStats]:
        start, end_exclusive = (instant.replace(tzinfo=None) for instant in period.utc_bounds())
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        turn_skill_activation.c.skill_id,
                        chat_turn.c.turn_id,
                        chat_turn.c.state,
                        chat_turn.c.created_at,
                    )
                    .distinct()
                    .select_from(
                        turn_skill_activation.join(
                            chat_turn,
                            (chat_turn.c.turn_id == turn_skill_activation.c.turn_id)
                            & (chat_turn.c.project_id == turn_skill_activation.c.project_id),
                        )
                    )
                    .where(
                        turn_skill_activation.c.project_id == project_id,
                        chat_turn.c.state.in_(_TERMINAL_STATES),
                        chat_turn.c.created_at >= start,
                        chat_turn.c.created_at < end_exclusive,
                    )
                )
            ).all()
            completed_at = await self._completion_times(
                session, project_id, sorted({row.turn_id for row in rows})
            )

        runs: dict[str, int] = {}
        failures: dict[str, int] = {}
        latency: dict[str, int] = {}
        for row in rows:
            runs[row.skill_id] = runs.get(row.skill_id, 0) + 1
            failures[row.skill_id] = failures.get(row.skill_id, 0) + (
                1 if row.state == "failed" else 0
            )
            finished = completed_at.get(row.turn_id)
            elapsed = (
                int((finished - row.created_at).total_seconds() * 1000)
                if finished is not None
                else 0
            )
            latency[row.skill_id] = latency.get(row.skill_id, 0) + elapsed
        return [
            SkillRuntimeStats(
                skill_id=skill_id,
                runs=runs[skill_id],
                failures=failures[skill_id],
                latency_total_ms=latency[skill_id],
                cost_usd=Decimal(0),
            )
            for skill_id in sorted(runs)
        ]

    @staticmethod
    async def _completion_times(
        session: AsyncSession, project_id: str, turn_ids: Sequence[str]
    ) -> dict[str, datetime]:
        if not turn_ids:
            return {}
        rows = (
            await session.execute(
                select(chat_event.c.turn_id, chat_event.c.occurred_at).where(
                    chat_event.c.project_id == project_id,
                    chat_event.c.turn_id.in_(turn_ids),
                    chat_event.c.event_type.in_(_TERMINAL_EVENT_TYPES),
                )
            )
        ).all()
        return {row.turn_id: row.occurred_at for row in rows}
