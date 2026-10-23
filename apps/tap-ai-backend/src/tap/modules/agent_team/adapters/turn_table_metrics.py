"""`AgentRuntimeMetrics` backed directly by the `chat_turn` / `turn_input_snapshot`
/ `ai_agent_revision` tables, for e2e tests that run without a Langfuse
instance. One Turn is one run, attributed to an Agent exactly as
`MysqlLangfuseSyncStore.load_trace` attributes a trace (Task 3): read
`turn_input_snapshot.snapshot["agent_revision_id"]` in Python, resolve it to
`ai_agent_revision.agent_id`; an empty/missing revision id means no Agent was
selected (`TAPPER_DEFAULT_AGENT_KEY`); a revision id that resolves to no row
means `UNKNOWN_AGENT_KEY`.

`chat_turn` has no completion timestamp column. `agent_stats` and `trend`
therefore only consider Turns in a terminal state (`completed`, `abstained`,
`canceled`, `failed`) -- the same set the Langfuse sync only ever tags with an
`outcome:` (Task 3's `_TERMINAL_TURN_STATES`), so "a run" means the same thing
in both adapters. Each terminal Turn's completion time is the `occurred_at` of
its terminal `chat_event` row (`turn.completed` / `turn.abstained` /
`turn.canceled` / `turn.failed`); latency is that minus `chat_turn.created_at`.
A Turn still in flight (`queued`, `running`, `stopping`) has no outcome yet and
is excluded from `agent_stats`/`trend`, matching Langfuse (which never tags an
unfinished trace with `outcome:`).

`last_runs` has no such restriction -- per the brief, it is "each agent_key's
last Turn's creation time", regardless of whether that Turn has finished.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.agent_team.domain.models import (
    TAPPER_DEFAULT_AGENT_KEY,
    UNKNOWN_AGENT_KEY,
    Period,
)
from tap.modules.agent_team.ports import AgentRuntimeStats, ServedActor, TrendPoint
from tap.modules.ai.adapters.mysql import ai_agent_revision
from tap.modules.chat.adapters.mysql import chat_event, chat_turn
from tap.modules.chat.adapters.mysql_conversations import turn_input_snapshot

_TERMINAL_EVENT_TYPES = ("turn.completed", "turn.abstained", "turn.canceled", "turn.failed")
_TERMINAL_STATES = frozenset({"completed", "abstained", "canceled", "failed"})


@dataclass(slots=True)
class _TurnRow:
    turn_id: str
    state: str
    actor_id: str
    created_at: datetime
    agent_key: str


def _naive_utc(value: datetime) -> datetime:
    """Strip tzinfo from an aware UTC datetime; `chat_turn.created_at` is a
    naive `DATETIME` column that is always UTC."""
    if value.tzinfo is None:
        return value
    return value.replace(tzinfo=None)


def _bucket_start(day: date, granularity: str) -> date:
    if granularity == "day":
        return day
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    if granularity == "month":
        return day.replace(day=1)
    raise ValueError(f"unknown granularity: {granularity!r}")


class TurnTableRuntimeMetrics:
    """`AgentRuntimeMetrics` over the `chat_turn` tables, scoped to one
    Project. e2e-only: intended as a Langfuse-free stand-in, not the
    production adapter (see `LangfuseRuntimeMetrics`)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def _terminal_turns(
        self, session: AsyncSession, project_id: str, period: Period
    ) -> list[_TurnRow]:
        start, end_exclusive = period.utc_bounds()
        start, end_exclusive = _naive_utc(start), _naive_utc(end_exclusive)
        rows = (
            (
                await session.execute(
                    select(
                        chat_turn.c.turn_id,
                        chat_turn.c.state,
                        chat_turn.c.actor_id,
                        chat_turn.c.created_at,
                        turn_input_snapshot.c.snapshot,
                    )
                    .select_from(
                        chat_turn.outerjoin(
                            turn_input_snapshot,
                            turn_input_snapshot.c.turn_id == chat_turn.c.turn_id,
                        )
                    )
                    .where(
                        chat_turn.c.project_id == project_id,
                        chat_turn.c.state.in_(_TERMINAL_STATES),
                        chat_turn.c.created_at >= start,
                        chat_turn.c.created_at < end_exclusive,
                    )
                )
            )
            .mappings()
            .all()
        )
        agent_keys = await self._resolve_agent_keys(session, project_id, rows)
        return [
            _TurnRow(
                turn_id=row["turn_id"],
                state=row["state"],
                actor_id=row["actor_id"],
                created_at=row["created_at"],
                agent_key=agent_keys[row["turn_id"]],
            )
            for row in rows
        ]

    async def _resolve_agent_keys(
        self, session: AsyncSession, project_id: str, rows: Sequence[RowMapping]
    ) -> dict[str, str]:
        revision_ids = {
            row["snapshot"].get("agent_revision_id")
            for row in rows
            if row["snapshot"] and row["snapshot"].get("agent_revision_id")
        }
        agent_by_revision: dict[str, str] = {}
        if revision_ids:
            found = (
                await session.execute(
                    select(ai_agent_revision.c.revision_id, ai_agent_revision.c.agent_id).where(
                        ai_agent_revision.c.project_id == project_id,
                        ai_agent_revision.c.revision_id.in_(revision_ids),
                    )
                )
            ).all()
            agent_by_revision = {r.revision_id: r.agent_id for r in found}
        result: dict[str, str] = {}
        for row in rows:
            snapshot = row["snapshot"]
            revision_id = snapshot.get("agent_revision_id") if snapshot else None
            if not revision_id:
                result[row["turn_id"]] = TAPPER_DEFAULT_AGENT_KEY
            else:
                result[row["turn_id"]] = agent_by_revision.get(revision_id, UNKNOWN_AGENT_KEY)
        return result

    async def _completion_times(
        self, session: AsyncSession, turn_ids: Sequence[str]
    ) -> dict[str, datetime]:
        if not turn_ids:
            return {}
        rows = (
            await session.execute(
                select(chat_event.c.turn_id, chat_event.c.occurred_at).where(
                    chat_event.c.turn_id.in_(turn_ids),
                    chat_event.c.event_type.in_(_TERMINAL_EVENT_TYPES),
                )
            )
        ).all()
        return {r.turn_id: r.occurred_at for r in rows}

    async def agent_stats(self, project_id: str, period: Period) -> list[AgentRuntimeStats]:
        async with self._sessions() as session:
            turns = await self._terminal_turns(session, project_id, period)
            completion_by_turn = await self._completion_times(
                session, [turn.turn_id for turn in turns]
            )

        latencies_by_agent: dict[str, list[int]] = defaultdict(list)
        failures: dict[str, int] = defaultdict(int)
        abstained: dict[str, int] = defaultdict(int)
        canceled: dict[str, int] = defaultdict(int)
        runs: dict[str, int] = defaultdict(int)
        served: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        for turn in turns:
            runs[turn.agent_key] += 1
            served[turn.agent_key][turn.actor_id] += 1
            if turn.state == "failed":
                failures[turn.agent_key] += 1
            elif turn.state == "abstained":
                abstained[turn.agent_key] += 1
            elif turn.state == "canceled":
                canceled[turn.agent_key] += 1
            completed_at = completion_by_turn.get(turn.turn_id)
            if completed_at is not None:
                latency_ms = int((completed_at - turn.created_at).total_seconds() * 1000)
                latencies_by_agent[turn.agent_key].append(latency_ms)

        return [
            AgentRuntimeStats(
                agent_key=agent_key,
                runs=runs[agent_key],
                failures=failures.get(agent_key, 0),
                abstained=abstained.get(agent_key, 0),
                canceled=canceled.get(agent_key, 0),
                latency_total_ms=sum(latencies_by_agent.get(agent_key, [])),
                latency_p50_ms=(
                    int(statistics.median(latencies_by_agent[agent_key]))
                    if latencies_by_agent.get(agent_key)
                    else None
                ),
                cost_usd=Decimal("0"),
                served=tuple(
                    sorted(
                        (
                            ServedActor(actor_id=actor_id, runs=actor_runs)
                            for actor_id, actor_runs in served[agent_key].items()
                        ),
                        key=lambda served_actor: (-served_actor.runs, served_actor.actor_id),
                    )
                ),
                generation_calls=0,
                tool_calls=0,
            )
            for agent_key in runs
        ]

    async def trend(self, project_id: str, period: Period, granularity: str) -> list[TrendPoint]:
        async with self._sessions() as session:
            turns = await self._terminal_turns(session, project_id, period)
            completion_by_turn = await self._completion_times(
                session, [turn.turn_id for turn in turns]
            )

        runs: dict[tuple[date, str], int] = defaultdict(int)
        failures: dict[tuple[date, str], int] = defaultdict(int)
        latency_total_ms: dict[tuple[date, str], int] = defaultdict(int)

        for turn in turns:
            bucket = _bucket_start(turn.created_at.date(), granularity)
            key = (bucket, turn.agent_key)
            runs[key] += 1
            if turn.state == "failed":
                failures[key] += 1
            completed_at = completion_by_turn.get(turn.turn_id)
            if completed_at is not None:
                latency_total_ms[key] += int(
                    (completed_at - turn.created_at).total_seconds() * 1000
                )

        return [
            TrendPoint(
                bucket_start=bucket,
                agent_key=agent_key,
                runs=count,
                failures=failures.get((bucket, agent_key), 0),
                latency_total_ms=latency_total_ms.get((bucket, agent_key), 0),
                cost_usd=Decimal("0"),
            )
            for (bucket, agent_key), count in runs.items()
        ]

    async def last_runs(self, project_id: str, agent_keys: Sequence[str]) -> dict[str, datetime]:
        requested = set(agent_keys)
        if not requested:
            return {}
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            chat_turn.c.turn_id,
                            chat_turn.c.created_at,
                            turn_input_snapshot.c.snapshot,
                        )
                        .select_from(
                            chat_turn.outerjoin(
                                turn_input_snapshot,
                                turn_input_snapshot.c.turn_id == chat_turn.c.turn_id,
                            )
                        )
                        .where(chat_turn.c.project_id == project_id)
                    )
                )
                .mappings()
                .all()
            )
            agent_keys_by_turn = await self._resolve_agent_keys(session, project_id, rows)

        last_seen: dict[str, datetime] = {}
        for row in rows:
            agent_key = agent_keys_by_turn[row["turn_id"]]
            if agent_key not in requested:
                continue
            created_at = row["created_at"]
            if agent_key not in last_seen or created_at > last_seen[agent_key]:
                last_seen[agent_key] = created_at
        return last_seen
