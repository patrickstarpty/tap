"""`AgentTeamService`: merges Task 4/5's cached Langfuse runtime metrics with
Task 6's live MySQL store into the Agent team view's read models (spec
sections 1.4, 2.2-2.6, 5, 5.1, 6.3, 6.4). HTTP wiring is Task 8; this module
never touches routes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, TypeVar

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.agent_team.adapters.cached_metrics import CachedRuntimeMetrics
from tap.modules.agent_team.domain import metrics as pure
from tap.modules.agent_team.domain.models import (
    TAPPER_DEFAULT_AGENT_KEY,
    AgentKind,
    MaintenanceKind,
    MemberRow,
    MemberStatus,
    OrgChart,
    Period,
    PersonRow,
    RuntimeStatus,
    TeamSummary,
    TeamView,
    new_event,
    trailing_30_days,
)
from tap.modules.agent_team.ports import (
    AgentRecord,
    AgentRuntimeStats,
    AgentTeamStore,
    FeedbackNotFound,
    HistoryCursorInvalid,
    HistoryPage,
    PendingFeedback,
    RatingCounts,
    ResolvedFeedback,
    RuntimeMetricsUnavailable,
    ServedActor,
    TrendPoint,
)

_MAX_JOB_TITLE_LENGTH = 64
_MAX_NOTE_LENGTH = 500
_SYNC_LAG_THRESHOLD = timedelta(minutes=5)

# Global constraints: closed, explicit allowlists of scalar fields each view
# can be sorted by -- never an open `getattr` on arbitrary client input.
_MEMBER_SORT_FIELDS = frozenset(
    {
        "name",
        "runs",
        "failures",
        "latency_total_ms",
        "latency_p50_ms",
        "cost_usd",
        "served_count",
        "last_run_at",
        "satisfaction",
        "last_maintenance_at",
        "pending_feedback",
        "joined_at",
        "job_title",
        "owner_actor_id",
        "status",
        "kind",
    }
)
_PEOPLE_SORT_FIELDS = frozenset(
    {"actor_id", "owned_agents", "owned_runs", "served_by_agents", "runs", "maintenance"}
)
_SORT_ORDERS = frozenset({"asc", "desc"})

# Spec 5.1: Langfuse unavailable/stale ranks worse than merely behind, which
# ranks worse than caught up -- `report()` combines two periods' statuses by
# taking the worse of the two.
_STATUS_RANK: dict[RuntimeStatus, int] = {"unavailable": 0, "syncing": 1, "ok": 2}


class AgentTeamRejected(Exception):
    """A rejected Agent team query or write; `code` is drawn from Global
    Constraints' closed error vocabulary."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class MemberDetail:
    """`AgentTeamService.member`'s payload (spec 6.2): the roster row plus the
    profile-only parts -- the Agent's current revision (None for
    `tapper_default`), everyone it served in the period (empty when run data
    is unavailable) and its pending feedback items."""

    row: MemberRow
    current_revision_id: str | None
    served: tuple[ServedActor, ...]
    pending_feedback: tuple[PendingFeedback, ...]


@dataclass(frozen=True, slots=True)
class PersonDetail:
    """`AgentTeamService.person`'s payload (spec 2.5/6.3): the person's own
    row, the Agents they own, the Agents that served them, and the pending
    feedback of Agents they currently own ("待我处理的反馈")."""

    person: PersonRow
    owned: tuple[MemberRow, ...]
    served: tuple[MemberRow, ...]
    pending_feedback: tuple[PendingFeedback, ...]


@dataclass(frozen=True, slots=True)
class PeriodReport:
    summary: TeamSummary
    members: tuple[MemberRow, ...]
    people: tuple[PersonRow, ...]


@dataclass(frozen=True, slots=True)
class TeamReport:
    """`AgentTeamService.report`'s payload (spec 6.4): the selected period's
    data alongside the previous period's, for the printable report page."""

    current: PeriodReport
    previous: PeriodReport
    previous_period: Period


@dataclass(frozen=True, slots=True)
class ExportBundle:
    """`AgentTeamService.export_data`'s payload (spec 5, Task 9): the
    selected period's roster/people report plus the flattened per-Agent
    trend points (each already at `period.granularity`, spec 2.1) the CSV
    export's `trend.csv` needs. Unlike `report()`, this never touches the
    previous period -- the export has no "previous" file."""

    report: PeriodReport
    trend: tuple[TrendPoint, ...]


@dataclass(slots=True)
class _RuntimeData:
    status: RuntimeStatus
    stats_by_key: dict[str, AgentRuntimeStats]
    trailing_stats_by_key: dict[str, AgentRuntimeStats]
    trend_by_key: dict[str, list[TrendPoint]]
    last_run_by_key: dict[str, datetime]
    computed_at: datetime


@dataclass(slots=True)
class _StoreData:
    """Every MySQL-sourced value one view needs, fetched exactly once per
    top-level `AgentTeamService` call (never once inside `_build_members`
    and again inside `_people_rows`/`person`)."""

    records: list[AgentRecord]
    ratings_by_key: dict[str, RatingCounts]
    pending: list[PendingFeedback]
    pending_count_by_key: dict[str, int]
    maintenance_by_agent: dict[str, dict[MaintenanceKind, int]]
    maintenance_by_actor: dict[str, int]
    last_maintenance: dict[str, datetime]
    active_actors: set[str]


def _unavailable(now: datetime) -> _RuntimeData:
    return _RuntimeData(
        status="unavailable",
        stats_by_key={},
        trailing_stats_by_key={},
        trend_by_key={},
        last_run_by_key={},
        computed_at=now,
    )


def _runs_or_zero(stats: AgentRuntimeStats | None) -> int:
    return stats.runs if stats is not None else 0


def _combine_status(a: RuntimeStatus, b: RuntimeStatus) -> RuntimeStatus:
    return a if _STATUS_RANK[a] <= _STATUS_RANK[b] else b


def _validate_sort(sort: str, order: str, allowed: frozenset[str]) -> None:
    if order not in _SORT_ORDERS:
        raise AgentTeamRejected("sort_invalid", f"invalid order {order!r}")
    if sort not in allowed:
        raise AgentTeamRejected("sort_invalid", f"cannot sort by {sort!r}")


_T = TypeVar("_T")

_RUNTIME_LOAD_TIMEOUT_SECONDS = 10.0
# Safety net only: every cached fetch already carries its own deadline-derived
# timeout, which fires first and is negative-cached.
_RUNTIME_LOAD_GRACE_SECONDS = 1.0


class AgentTeamService:
    """`check_sync_lag` should be `True` only when the runtime adapter is the
    Langfuse-backed one with `LANGFUSE_HOST` configured (Task 8's HTTP
    wiring decides this); `turn_table` mode and an unconfigured Langfuse
    never report `"syncing"`."""

    def __init__(
        self,
        store: AgentTeamStore,
        metrics: CachedRuntimeMetrics,
        *,
        scope: ProjectScopeContext,
        clock: Callable[[], datetime],
        check_sync_lag: bool = False,
        sync_settle_seconds: int = 60,
        runtime_timeout_seconds: float | None = None,
    ) -> None:
        self._store = store
        self._metrics = metrics
        self._scope = scope
        self._clock = clock
        self._check_sync_lag = check_sync_lag
        self._sync_settle = timedelta(seconds=sync_settle_seconds)
        self._runtime_timeout = (
            _RUNTIME_LOAD_TIMEOUT_SECONDS
            if runtime_timeout_seconds is None
            else runtime_timeout_seconds
        )

    # ------------------------------------------------------------------
    # Runtime loading
    # ------------------------------------------------------------------

    async def _load_runtime(self, period: Period, records: Sequence[AgentRecord]) -> _RuntimeData:
        try:
            async with asyncio.timeout(self._runtime_timeout + _RUNTIME_LOAD_GRACE_SECONDS):
                return await self._load_runtime_unbounded(period, records)
        except TimeoutError:
            return _unavailable(self._clock())

    async def _load_runtime_unbounded(
        self, period: Period, records: Sequence[AgentRecord]
    ) -> _RuntimeData:
        project_id = self._scope.project_id
        today = self._clock().date()
        trailing = trailing_30_days(today)
        # One deadline for the whole view: each query may use what is left.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._runtime_timeout

        def remaining() -> float:
            return max(deadline - loop.time(), 0.0)

        try:
            stats = await self._metrics.agent_stats(project_id, period, timeout_seconds=remaining())
            trailing_stats = await self._metrics.agent_stats(
                project_id, trailing, timeout_seconds=remaining()
            )
            trend = await self._metrics.trend(
                project_id, period, period.granularity, timeout_seconds=remaining()
            )
        except RuntimeMetricsUnavailable:
            return _unavailable(self._clock())

        stats_by_key = {s.agent_key: s for s in stats}
        trailing_stats_by_key = {s.agent_key: s for s in trailing_stats}
        # "最近运行" is not limited to the period (spec 2.2): ask for every
        # roster key, not only those with runs in the two stats windows.
        roster_keys = {record.agent_id for record in records}
        roster_keys.add(TAPPER_DEFAULT_AGENT_KEY)
        agent_keys = sorted(set(stats_by_key) | set(trailing_stats_by_key) | roster_keys)
        try:
            last_runs = await self._metrics.last_runs(
                project_id, agent_keys, timeout_seconds=remaining()
            )
        except RuntimeMetricsUnavailable:
            return _unavailable(self._clock())

        trend_by_key: dict[str, list[TrendPoint]] = {}
        for point in trend:
            trend_by_key.setdefault(point.agent_key, []).append(point)

        status: RuntimeStatus = "ok"
        if self._check_sync_lag:
            lag = await self._store.oldest_unsynced_age(now=self._clock(), settle=self._sync_settle)
            if lag is not None and lag > _SYNC_LAG_THRESHOLD:
                status = "syncing"

        candidates = [
            self._metrics.computed_at(project_id, period, "stats"),
            self._metrics.computed_at(project_id, trailing, "stats"),
            self._metrics.computed_at(project_id, period, f"trend:{period.granularity}"),
            self._metrics.computed_at(project_id, None, ("last_runs", tuple(agent_keys))),
        ]
        known = [value for value in candidates if value is not None]
        computed_at = min(known) if known else self._clock()

        return _RuntimeData(
            status=status,
            stats_by_key=stats_by_key,
            trailing_stats_by_key=trailing_stats_by_key,
            trend_by_key=trend_by_key,
            last_run_by_key=last_runs,
            computed_at=computed_at,
        )

    async def _load_store_data(self, period: Period) -> _StoreData:
        """Every MySQL query a view needs, issued exactly once."""
        records = await self._store.agents()
        ratings_by_key = {rating.agent_key: rating for rating in await self._store.ratings(period)}

        pending = await self._store.pending_feedback()
        pending_count_by_key: dict[str, int] = {}
        for item in pending:
            pending_count_by_key[item.agent_key] = pending_count_by_key.get(item.agent_key, 0) + 1

        maintenance_by_agent: dict[str, dict[MaintenanceKind, int]] = {}
        maintenance_by_actor: dict[str, int] = {}
        for agent_id, actor_id, kind, count in await self._store.maintenance_counts(period):
            bucket = maintenance_by_agent.setdefault(agent_id, {})
            bucket[kind] = bucket.get(kind, 0) + count
            maintenance_by_actor[actor_id] = maintenance_by_actor.get(actor_id, 0) + count

        last_maintenance = await self._store.last_maintenance()
        active_actors = await self._store.active_actors(period)

        return _StoreData(
            records=records,
            ratings_by_key=ratings_by_key,
            pending=pending,
            pending_count_by_key=pending_count_by_key,
            maintenance_by_agent=maintenance_by_agent,
            maintenance_by_actor=maintenance_by_actor,
            last_maintenance=last_maintenance,
            active_actors=active_actors,
        )

    # ------------------------------------------------------------------
    # Roster / member assembly (pure dict/list work -- no I/O, so these are
    # plain sync methods over data `_load_runtime`/`_load_store_data` already
    # fetched).
    # ------------------------------------------------------------------

    def _build_members(self, runtime: _RuntimeData, store_data: _StoreData) -> list[MemberRow]:
        records_by_id = {record.agent_id: record for record in store_data.records}
        runtime_available = runtime.status != "unavailable"
        keys = pure.roster_agent_keys(
            store_agent_keys=[record.agent_id for record in store_data.records],
            tapper_default_runs_in_period=_runs_or_zero(
                runtime.stats_by_key.get(TAPPER_DEFAULT_AGENT_KEY)
            ),
            tapper_default_runs_trailing_30d=_runs_or_zero(
                runtime.trailing_stats_by_key.get(TAPPER_DEFAULT_AGENT_KEY)
            ),
            runtime_available=runtime_available,
        )

        rows: list[MemberRow] = []
        for key in sorted(keys):
            record = records_by_id.get(key)
            if record is None:
                kind: AgentKind = "tapper_default"
                name = "Tapper 默认"
                job_title: str | None = None
                owner_actor_id: str | None = None
                joined_at = None
                disabled = False
            else:
                kind = "built_in" if record.source_kind == "built_in" else "custom"
                name = record.name
                job_title = record.job_title
                owner_actor_id = record.owner_actor_id
                joined_at = record.created_at
                disabled = not record.enabled
            stats = runtime.stats_by_key.get(key)
            trailing_stats = runtime.trailing_stats_by_key.get(key)
            # `None` (unknown) only when the outage itself makes the
            # trailing count unknown; a legitimate zero (runtime available,
            # no runs) is still `0`, which correctly yields "idle".
            trailing_runs = (
                trailing_stats.runs if trailing_stats else (0 if runtime_available else None)
            )
            rating = store_data.ratings_by_key.get(key)
            up, down = (rating.up, rating.down) if rating else (0, 0)
            rows.append(
                pure.build_member_row(
                    agent_key=key,
                    name=name,
                    kind=kind,
                    job_title=job_title,
                    owner_actor_id=owner_actor_id,
                    joined_at=joined_at,
                    disabled=disabled,
                    trailing_30d_runs=trailing_runs,
                    stats=stats,
                    last_run_at=runtime.last_run_by_key.get(key),
                    up=up,
                    down=down,
                    last_maintenance_at=store_data.last_maintenance.get(key),
                    pending_feedback=store_data.pending_count_by_key.get(key, 0),
                    maintenance=store_data.maintenance_by_agent.get(key, {}),
                    trend=tuple(runtime.trend_by_key.get(key, ())),
                    runtime_available=runtime_available,
                )
            )
        return rows

    @staticmethod
    def _member_stats(
        members: Sequence[MemberRow], runtime: _RuntimeData
    ) -> dict[str, AgentRuntimeStats]:
        """Runtime stats restricted to this view's roster -- never an
        `unknown`/dropped key bleeding into summary totals or per-person
        counts (spec 2.6; this is also why `person()`'s `served` list and
        `build_person_row`'s `served_by_agents` always agree)."""
        member_keys = {row.agent_key for row in members}
        return {key: value for key, value in runtime.stats_by_key.items() if key in member_keys}

    def _people_rows(
        self, members: Sequence[MemberRow], runtime: _RuntimeData, store_data: _StoreData
    ) -> list[PersonRow]:
        owners = {row.owner_actor_id for row in members if row.owner_actor_id is not None}
        scope_ids = pure.people_scope(owners=owners, active_actors=store_data.active_actors)
        stats_by_key = self._member_stats(members, runtime)
        return [
            pure.build_person_row(
                actor_id=actor_id,
                owned_agent_keys=[
                    row.agent_key for row in members if row.owner_actor_id == actor_id
                ],
                stats_by_key=stats_by_key,
                maintenance_count=store_data.maintenance_by_actor.get(actor_id, 0),
                runtime_available=runtime.status != "unavailable",
            )
            for actor_id in sorted(scope_ids)
        ]

    async def _period_report(self, period: Period) -> tuple[PeriodReport, RuntimeStatus, datetime]:
        store_data = await self._load_store_data(period)
        runtime = await self._load_runtime(period, store_data.records)
        members = self._build_members(runtime, store_data)
        people = self._people_rows(members, runtime, store_data)
        stats_by_key = self._member_stats(members, runtime)
        kinds_by_key = {row.agent_key: row.kind for row in members}
        statuses_by_key = {row.agent_key: row.status for row in members}
        maintenance_total = sum(sum(row.maintenance.values()) for row in members)
        summary = pure.team_summary(
            stats_by_key=stats_by_key,
            kinds_by_key=kinds_by_key,
            statuses_by_key=statuses_by_key,
            person_rows=people,
            maintenance_total=maintenance_total,
            runtime_available=runtime.status != "unavailable",
        )
        report = PeriodReport(summary=summary, members=tuple(members), people=tuple(people))
        return report, runtime.status, runtime.computed_at

    def _view(
        self, period: Period, status: RuntimeStatus, computed_at: datetime, data: _T
    ) -> TeamView[_T]:
        return TeamView(
            runtime_status=status,
            computed_at=computed_at,
            timezone="UTC",
            period=period,
            granularity=period.granularity,
            data=data,
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def summary(self, period: Period) -> TeamView[TeamSummary]:
        report, status, computed_at = await self._period_report(period)
        return self._view(period, status, computed_at, report.summary)

    async def members(
        self,
        period: Period,
        *,
        owner: str | None = None,
        status: MemberStatus | None = None,
        kind: Literal["custom", "built_in", "tapper_default"] | None = None,
        sort: str = "runs",
        order: str = "desc",
    ) -> TeamView[tuple[MemberRow, ...]]:
        _validate_sort(sort, order, _MEMBER_SORT_FIELDS)
        store_data = await self._load_store_data(period)
        runtime = await self._load_runtime(period, store_data.records)
        rows = self._build_members(runtime, store_data)
        if owner is not None:
            rows = [row for row in rows if row.owner_actor_id == owner]
        if status is not None:
            rows = [row for row in rows if row.status == status]
        if kind is not None:
            rows = [row for row in rows if row.kind == kind]
        sorted_rows = _sorted(rows, sort, order)
        return self._view(period, runtime.status, runtime.computed_at, tuple(sorted_rows))

    async def member(self, agent_key: str, period: Period) -> TeamView[MemberDetail]:
        store_data = await self._load_store_data(period)
        runtime = await self._load_runtime(period, store_data.records)
        rows = self._build_members(runtime, store_data)
        for row in rows:
            if row.agent_key == agent_key:
                record = await self._store.agent(agent_key)
                stats = runtime.stats_by_key.get(agent_key)
                served = (
                    ()
                    if stats is None or runtime.status == "unavailable"
                    else tuple(sorted(stats.served, key=lambda item: (-item.runs, item.actor_id)))
                )
                pending = await self._store.pending_feedback(agent_key)
                detail = MemberDetail(
                    row=row,
                    current_revision_id=None if record is None else record.current_revision_id,
                    served=served,
                    pending_feedback=tuple(pending),
                )
                return self._view(period, runtime.status, runtime.computed_at, detail)
        raise AgentTeamRejected("agent_not_found", f"no Agent {agent_key!r} in this Project")

    async def member_row(self, agent_id: str) -> MemberRow:
        """The Agent's roster row over the trailing 30 days ending today by
        this service's clock -- what the personnel writes return."""
        view = await self.member(agent_id, trailing_30_days(self._clock().date()))
        return view.data.row

    async def history(self, agent_id: str, cursor: str | None) -> HistoryPage:
        record = await self._store.agent(agent_id)
        if record is None and agent_id != TAPPER_DEFAULT_AGENT_KEY:
            raise AgentTeamRejected("agent_not_found", f"no Agent {agent_id!r} in this Project")
        try:
            return await self._store.history(agent_id, cursor)
        except HistoryCursorInvalid as exc:
            raise AgentTeamRejected("cursor_invalid", str(exc)) from exc

    async def people(
        self, period: Period, sort: str = "runs", order: str = "desc"
    ) -> TeamView[tuple[PersonRow, ...]]:
        _validate_sort(sort, order, _PEOPLE_SORT_FIELDS)
        report, status, computed_at = await self._period_report(period)
        rows = _sorted(list(report.people), sort, order)
        return self._view(period, status, computed_at, tuple(rows))

    async def person(self, actor_id: str, period: Period) -> TeamView[PersonDetail]:
        resolved = self._scope.actor_id if actor_id == "me" else actor_id
        store_data = await self._load_store_data(period)
        runtime = await self._load_runtime(period, store_data.records)
        members = self._build_members(runtime, store_data)
        people = self._people_rows(members, runtime, store_data)
        runtime_available = runtime.status != "unavailable"
        person_row = next(
            (row for row in people if row.actor_id == resolved),
            PersonRow(
                actor_id=resolved,
                owned_agents=0,
                owned_runs=0 if runtime_available else None,
                served_by_agents=0 if runtime_available else None,
                runs=0 if runtime_available else None,
                maintenance=0,
            ),
        )
        owned = tuple(row for row in members if row.owner_actor_id == resolved)
        served = tuple(
            row
            for row in members
            if _actor_served(runtime.stats_by_key.get(row.agent_key), resolved)
        )
        owned_keys = {row.agent_key for row in owned}
        pending_feedback = tuple(
            item for item in store_data.pending if item.agent_key in owned_keys
        )
        data = PersonDetail(
            person=person_row, owned=owned, served=served, pending_feedback=pending_feedback
        )
        return self._view(period, runtime.status, runtime.computed_at, data)

    async def org_chart(self, period: Period) -> TeamView[OrgChart]:
        store_data = await self._load_store_data(period)
        runtime = await self._load_runtime(period, store_data.records)
        members = self._build_members(runtime, store_data)
        served_by_key = {
            row.agent_key: (
                runtime.stats_by_key[row.agent_key].served
                if row.agent_key in runtime.stats_by_key
                else ()
            )
            for row in members
        }
        data = pure.build_org_chart(members=members, served_by_key=served_by_key)
        return self._view(period, runtime.status, runtime.computed_at, data)

    async def report(self, period: Period) -> TeamView[TeamReport]:
        current, current_status, current_computed_at = await self._period_report(period)
        previous_period = period.previous()
        previous, previous_status, previous_computed_at = await self._period_report(previous_period)
        status = _combine_status(current_status, previous_status)
        computed_at = min(current_computed_at, previous_computed_at)
        data = TeamReport(current=current, previous=previous, previous_period=previous_period)
        return self._view(period, status, computed_at, data)

    async def export_data(self, period: Period) -> TeamView[ExportBundle]:
        """Task 9's CSV zip export source data: the selected period's roster/
        people report (reusing `_period_report`, so this never re-queries
        anything `report()`/`members()`/`people()` don't already) plus every
        Agent's trend points for that period, flattened in roster order.
        Each `MemberRow` already carries its own `trend` tuple at
        `period.granularity` (spec 2.1), so this needs no extra Langfuse
        call beyond what `_load_runtime` already made."""
        report, status, computed_at = await self._period_report(period)
        trend = tuple(point for row in report.members for point in row.trend)
        data = ExportBundle(report=report, trend=trend)
        return self._view(period, status, computed_at, data)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def _require_writable_agent(self, agent_id: str) -> AgentRecord:
        if agent_id == TAPPER_DEFAULT_AGENT_KEY:
            raise AgentTeamRejected("agent_read_only", "Tapper 默认无法编辑人事字段")
        record = await self._store.agent(agent_id)
        if record is None:
            raise AgentTeamRejected("agent_not_found", f"no Agent {agent_id!r} in this Project")
        if record.source_kind == "built_in":
            raise AgentTeamRejected("agent_read_only", "built-in Agents are read-only")
        return record

    async def update_job_title(self, agent_id: str, job_title: str | None) -> AgentRecord:
        await self._require_writable_agent(agent_id)
        if job_title is not None:
            job_title = job_title.strip() or None
        if job_title is not None and len(job_title) > _MAX_JOB_TITLE_LENGTH:
            raise AgentTeamRejected("job_title_too_long", "job_title must be <= 64 characters")
        return await self._store.set_job_title(agent_id, job_title)

    async def transfer_owner(self, agent_id: str, new_owner: str) -> AgentRecord:
        record = await self._require_writable_agent(agent_id)
        if record.owner_actor_id == new_owner:
            return record
        if not await self._store.actor_exists(new_owner):
            raise AgentTeamRejected("actor_not_found", f"no active actor {new_owner!r}")
        event = new_event(self._scope, agent_id, "owner_transferred", now=self._clock())
        return await self._store.transfer_owner(agent_id, new_owner, event)

    async def resolve_feedback(self, feedback_id: str, note: str | None) -> ResolvedFeedback:
        if note is not None and len(note) > _MAX_NOTE_LENGTH:
            raise AgentTeamRejected("note_too_long", "note must be <= 500 characters")

        def event_factory(agent_id: str):
            return new_event(
                self._scope,
                agent_id,
                "feedback_resolved",
                feedback_id=feedback_id,
                note=note,
                now=self._clock(),
            )

        try:
            return await self._store.resolve_feedback(
                feedback_id, self._scope.actor_id, note, event_factory
            )
        except FeedbackNotFound as exc:
            raise AgentTeamRejected("feedback_not_found", str(exc)) from exc


def _actor_served(stats: AgentRuntimeStats | None, actor_id: str) -> bool:
    if stats is None:
        return False
    return any(served.actor_id == actor_id and served.runs > 0 for served in stats.served)


_STATUS_RANK_ORDER = {"active": 0, "idle": 1, "disabled": 2}
_KIND_RANK_ORDER = {"custom": 0, "built_in": 1, "tapper_default": 2}


def _sort_value(row, sort: str):
    value = getattr(row, sort)
    if sort == "status" and value is not None:
        return _STATUS_RANK_ORDER[value]
    if sort == "kind" and value is not None:
        return _KIND_RANK_ORDER[value]
    return value


def _sorted(rows: list, sort: str, order: str) -> list:
    """Sorts by the `sort` attribute, descending unless `order == "asc"`.
    Rows whose sort attribute is `None` (e.g. `last_run_at`) always sort
    last, regardless of direction. `status`/`kind` sort by their domain rank
    (active < idle < disabled; custom < built_in < tapper_default), not
    alphabetically. Ties keep name order (spec 6.1) in both directions: rows
    are first ordered by name ascending, and Python's stable sort -- which
    also keeps equal keys in input order under `reverse=True` -- does the
    rest. Callers must validate `sort`/`order` against a closed allowlist
    first (`_validate_sort`) -- this helper trusts its inputs."""
    reverse = order != "asc"
    by_name = sorted(
        rows,
        key=lambda row: (
            getattr(row, "name", None) or getattr(row, "actor_id", ""),
            getattr(row, "agent_key", ""),
        ),
    )
    with_value = [row for row in by_name if getattr(row, sort) is not None]
    without_value = [row for row in by_name if getattr(row, sort) is None]
    with_value.sort(key=lambda row: _sort_value(row, sort), reverse=reverse)
    return with_value + without_value
