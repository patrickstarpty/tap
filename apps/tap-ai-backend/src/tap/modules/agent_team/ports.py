"""Ports for runtime (Langfuse-backed) Agent metrics (spec sections 2.2/2.5/6)
and for the MySQL-backed Agent team read models/writes (spec sections 2.3,
2.4, 2.5, 4, 5.1)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from tap.modules.agent_team.domain.models import MaintenanceEvent, MaintenanceKind, Period


class RuntimeMetricsUnavailable(Exception):
    """The runtime metrics backend (Langfuse) could not answer the query."""


@dataclass(frozen=True, slots=True)
class ServedActor:
    actor_id: str
    runs: int


@dataclass(frozen=True, slots=True)
class AgentRuntimeStats:
    agent_key: str
    runs: int
    failures: int
    abstained: int
    canceled: int
    latency_total_ms: int
    # A run-count-weighted mean of each underlying group's p50 (Langfuse never
    # returns one p50 over the whole period -- see `LangfuseRuntimeMetrics.agent_stats`
    # for why), not a statistically exact p50 over all of `runs`.
    latency_p50_ms: int | None
    cost_usd: Decimal
    # Sorted by `runs` descending, then `actor_id` ascending.
    served: tuple[ServedActor, ...]
    generation_calls: int
    tool_calls: int


@dataclass(frozen=True, slots=True)
class TrendPoint:
    bucket_start: date
    agent_key: str
    runs: int
    failures: int
    latency_total_ms: int
    cost_usd: Decimal


class AgentRuntimeMetrics(Protocol):
    """Runtime (Langfuse) metrics for the Agent team view. Raises
    `RuntimeMetricsUnavailable` when the backend cannot be queried."""

    async def agent_stats(self, project_id: str, period: Period) -> list[AgentRuntimeStats]: ...

    async def trend(
        self, project_id: str, period: Period, granularity: str
    ) -> list[TrendPoint]: ...

    async def last_runs(self, project_id: str, agent_keys: Sequence[str]) -> dict[str, datetime]:
        """The timestamp of the most recent trace for each Agent, regardless of
        period. Agent keys with no trace are absent from the result. Returned
        datetimes are timezone-aware UTC."""
        ...


class FeedbackNotFound(Exception):
    """`resolve_feedback` was asked for a `feedback_id` that does not exist in
    this Project (the service layer maps this to `feedback_not_found`)."""


@dataclass(frozen=True, slots=True)
class AgentRecord:
    agent_id: str
    name: str
    job_title: str | None
    owner_actor_id: str
    owner_assigned_at: datetime
    created_at: datetime
    source_kind: str
    enabled: bool
    current_revision_id: str
    current_revision_number: int


@dataclass(frozen=True, slots=True)
class RatingCounts:
    agent_key: str
    up: int
    down: int


@dataclass(frozen=True, slots=True)
class PendingFeedback:
    feedback_id: str
    agent_key: str
    turn_id: str
    conversation_id: str
    actor_id: str
    rating: str | None
    comment: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class HistoryPage:
    events: tuple[MaintenanceEvent, ...]
    next_cursor: str | None


class AgentTeamStore(Protocol):
    """MySQL read models and writes for the Agent team view: Agent roster and
    personnel fields, satisfaction/maintenance (spec 2.3/2.4), per-person
    scope (spec 2.5), Langfuse sync lag, and the three writes (profile,
    owner transfer, feedback resolution). Every query is scoped to the
    Project the store was constructed with. Returned datetimes are
    timezone-aware UTC."""

    async def agents(self) -> list[AgentRecord]: ...

    async def agent(self, agent_id: str) -> AgentRecord | None: ...

    async def ratings(self, period: Period) -> list[RatingCounts]:
        """Up/down counts per `agent_key`, attributed by each Turn's frozen
        Agent and filtered by each Turn's creation time falling in `period`
        (a rating submitted after the period still counts, per spec 2.3)."""
        ...

    async def pending_feedback(self, agent_key: str | None = None) -> list[PendingFeedback]:
        """Feedback that is a downvote or carries a comment (including an
        upvote with a comment) and is not yet resolved."""
        ...

    async def maintenance_counts(
        self, period: Period
    ) -> list[tuple[str, str, MaintenanceKind, int]]:
        """`(agent_id, actor_id, kind, count)` for maintenance events whose
        `occurred_at` falls in `period`."""
        ...

    async def history(self, agent_id: str, cursor: str | None, limit: int = 50) -> HistoryPage:
        """Maintenance events for one Agent, newest first (`occurred_at` desc,
        `event_id` as a deterministic tiebreaker). `cursor` is an opaque,
        base64-encoded page token."""
        ...

    async def last_maintenance(self) -> dict[str, datetime]:
        """Each `agent_id`'s most recent maintenance event time, across all
        time (not period-bound)."""
        ...

    async def active_actors(self, period: Period) -> set[str]:
        """Actors with a Turn or a maintenance event in `period` (spec 2.5's
        personnel scope, minus current owners, which the caller adds)."""
        ...

    async def sync_lag(self) -> timedelta | None:
        """`max(trace_span.recorded_at) - langfuse_sync_cursor['traces']`.
        `None` when there is no `trace_span` row yet to measure against; when
        a row exists but no `traces` cursor row does, lag is unbounded, so
        this returns `timedelta.max`."""
        ...

    async def actor_exists(self, actor_id: str) -> bool:
        """Whether `actor_id` names an enabled identity in this Project's
        Enterprise (`actor_principal`)."""
        ...

    async def set_job_title(self, agent_id: str, job_title: str | None) -> AgentRecord: ...

    async def transfer_owner(
        self, agent_id: str, new_owner: str, event: MaintenanceEvent
    ) -> AgentRecord:
        """Updates `owner_actor_id`/`owner_assigned_at` and writes `event` in
        one transaction."""
        ...

    async def resolve_feedback(
        self,
        feedback_id: str,
        actor_id: str,
        note: str | None,
        event_factory: Callable[[str], MaintenanceEvent],
    ) -> PendingFeedback:
        """Marks `feedback_id` resolved and writes the Turn's frozen Agent's
        `feedback_resolved` event (`event_factory` is called with that
        `agent_id`) in one transaction. Already-resolved feedback is returned
        unchanged and writes no event. Raises `FeedbackNotFound` when
        `feedback_id` does not exist in this Project."""
        ...
