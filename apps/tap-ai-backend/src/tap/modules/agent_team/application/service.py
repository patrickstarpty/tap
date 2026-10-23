"""`AgentTeamService`: merges Task 4/5's cached Langfuse runtime metrics with
Task 6's live MySQL store into the Agent team view's read models (spec
sections 1.4, 2.2-2.6, 5, 5.1, 6.3, 6.4). HTTP wiring is Task 8; this module
never touches routes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.agent_team.adapters.cached_metrics import CachedRuntimeMetrics
from tap.modules.agent_team.domain import metrics as pure
from tap.modules.agent_team.domain.models import (
    TAPPER_DEFAULT_AGENT_KEY,
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
    ResolvedFeedback,
    RuntimeMetricsUnavailable,
    TrendPoint,
)

_MAX_JOB_TITLE_LENGTH = 64
_SYNC_LAG_THRESHOLD = timedelta(minutes=5)


class AgentTeamRejected(Exception):
    """A rejected Agent team query or write; `code` is drawn from Global
    Constraints' closed error vocabulary."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


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


@dataclass(slots=True)
class _RuntimeData:
    status: RuntimeStatus
    stats_by_key: dict[str, AgentRuntimeStats]
    trailing_stats_by_key: dict[str, AgentRuntimeStats]
    trend_by_key: dict[str, list[TrendPoint]]
    last_run_by_key: dict[str, datetime]
    computed_at: datetime


def _unavailable(now: datetime) -> _RuntimeData:
    return _RuntimeData(
        status="unavailable",
        stats_by_key={},
        trailing_stats_by_key={},
        trend_by_key={},
        last_run_by_key={},
        computed_at=now,
    )


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
    ) -> None:
        self._store = store
        self._metrics = metrics
        self._scope = scope
        self._clock = clock
        self._check_sync_lag = check_sync_lag

    # ------------------------------------------------------------------
    # Runtime loading
    # ------------------------------------------------------------------

    async def _load_runtime(self, period: Period) -> _RuntimeData:
        project_id = self._scope.project_id
        today = self._clock().date()
        trailing = trailing_30_days(today)
        try:
            stats = await self._metrics.agent_stats(project_id, period)
            trailing_stats = await self._metrics.agent_stats(project_id, trailing)
            trend = await self._metrics.trend(project_id, period, period.granularity)
        except RuntimeMetricsUnavailable:
            return _unavailable(self._clock())

        stats_by_key = {s.agent_key: s for s in stats}
        trailing_stats_by_key = {s.agent_key: s for s in trailing_stats}
        agent_keys = sorted(set(stats_by_key) | set(trailing_stats_by_key))
        try:
            last_runs = await self._metrics.last_runs(project_id, agent_keys)
        except RuntimeMetricsUnavailable:
            return _unavailable(self._clock())

        trend_by_key: dict[str, list[TrendPoint]] = {}
        for point in trend:
            trend_by_key.setdefault(point.agent_key, []).append(point)

        status: RuntimeStatus = "ok"
        if self._check_sync_lag:
            lag = await self._store.sync_lag()
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

    # ------------------------------------------------------------------
    # Roster / member assembly
    # ------------------------------------------------------------------

    async def _roster_keys(
        self, records: Sequence[AgentRecord], runtime: _RuntimeData
    ) -> list[str]:
        default_stats = runtime.stats_by_key.get(TAPPER_DEFAULT_AGENT_KEY)
        default_trailing = runtime.trailing_stats_by_key.get(TAPPER_DEFAULT_AGENT_KEY)
        keys = pure.roster_agent_keys(
            store_agent_keys=[record.agent_id for record in records],
            tapper_default_runs_in_period=default_stats.runs if default_stats else 0,
            tapper_default_runs_trailing_30d=default_trailing.runs if default_trailing else 0,
        )
        return sorted(keys)

    async def _build_members(self, period: Period, runtime: _RuntimeData) -> list[MemberRow]:
        records = await self._store.agents()
        records_by_id = {record.agent_id: record for record in records}
        keys = await self._roster_keys(records, runtime)

        ratings_by_key = {rating.agent_key: rating for rating in await self._store.ratings(period)}

        pending_count: dict[str, int] = {}
        for item in await self._store.pending_feedback():
            pending_count[item.agent_key] = pending_count.get(item.agent_key, 0) + 1

        maintenance_by_agent: dict[str, dict[MaintenanceKind, int]] = {}
        for agent_id, _actor_id, maintenance_kind, count in await self._store.maintenance_counts(
            period
        ):
            bucket = maintenance_by_agent.setdefault(agent_id, {})
            bucket[maintenance_kind] = bucket.get(maintenance_kind, 0) + count

        last_maintenance = await self._store.last_maintenance()

        rows: list[MemberRow] = []
        for key in keys:
            record = records_by_id.get(key)
            if record is None:
                kind: Literal["custom", "built_in", "tapper_default"] = "tapper_default"
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
            rating = ratings_by_key.get(key)
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
                    trailing_30d_runs=trailing_stats.runs if trailing_stats else 0,
                    stats=stats,
                    last_run_at=runtime.last_run_by_key.get(key),
                    up=up,
                    down=down,
                    last_maintenance_at=last_maintenance.get(key),
                    pending_feedback=pending_count.get(key, 0),
                    maintenance=maintenance_by_agent.get(key, {}),
                    trend=tuple(runtime.trend_by_key.get(key, ())),
                )
            )
        return rows

    async def _people_rows(
        self, period: Period, members: Sequence[MemberRow], runtime: _RuntimeData
    ) -> list[PersonRow]:
        owners = {row.owner_actor_id for row in members if row.owner_actor_id is not None}
        active_actors = await self._store.active_actors(period)
        scope_ids = pure.people_scope(owners=owners, active_actors=active_actors)

        maintenance_by_actor: dict[str, int] = {}
        for _agent_id, actor_id, _kind, count in await self._store.maintenance_counts(period):
            maintenance_by_actor[actor_id] = maintenance_by_actor.get(actor_id, 0) + count

        return [
            pure.build_person_row(
                actor_id=actor_id,
                owned_agent_keys=[
                    row.agent_key for row in members if row.owner_actor_id == actor_id
                ],
                stats_by_key=runtime.stats_by_key,
                maintenance_count=maintenance_by_actor.get(actor_id, 0),
            )
            for actor_id in sorted(scope_ids)
        ]

    async def _period_report(self, period: Period) -> tuple[PeriodReport, RuntimeStatus, datetime]:
        runtime = await self._load_runtime(period)
        members = await self._build_members(period, runtime)
        people = await self._people_rows(period, members, runtime)
        member_keys = {row.agent_key for row in members}
        stats_by_key = {k: v for k, v in runtime.stats_by_key.items() if k in member_keys}
        kinds_by_key = {row.agent_key: row.kind for row in members}
        statuses_by_key = {row.agent_key: row.status for row in members}
        maintenance_total = sum(sum(row.maintenance.values()) for row in members)
        summary = pure.team_summary(
            stats_by_key=stats_by_key,
            kinds_by_key=kinds_by_key,
            statuses_by_key=statuses_by_key,
            person_rows=people,
            maintenance_total=maintenance_total,
        )
        report = PeriodReport(summary=summary, members=tuple(members), people=tuple(people))
        return report, runtime.status, runtime.computed_at

    def _view(self, period: Period, status: RuntimeStatus, computed_at: datetime, data):
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
        order: Literal["asc", "desc"] = "desc",
    ) -> TeamView[tuple[MemberRow, ...]]:
        runtime = await self._load_runtime(period)
        rows = await self._build_members(period, runtime)
        if owner is not None:
            rows = [row for row in rows if row.owner_actor_id == owner]
        if status is not None:
            rows = [row for row in rows if row.status == status]
        if kind is not None:
            rows = [row for row in rows if row.kind == kind]
        rows = _sorted(rows, sort, order)
        return self._view(period, runtime.status, runtime.computed_at, tuple(rows))

    async def member(self, agent_key: str, period: Period) -> TeamView[MemberRow]:
        runtime = await self._load_runtime(period)
        rows = await self._build_members(period, runtime)
        for row in rows:
            if row.agent_key == agent_key:
                return self._view(period, runtime.status, runtime.computed_at, row)
        raise AgentTeamRejected("agent_not_found", f"no Agent {agent_key!r} in this Project")

    async def history(self, agent_id: str, cursor: str | None) -> HistoryPage:
        record = await self._store.agent(agent_id)
        if record is None and agent_id != TAPPER_DEFAULT_AGENT_KEY:
            raise AgentTeamRejected("agent_not_found", f"no Agent {agent_id!r} in this Project")
        try:
            return await self._store.history(agent_id, cursor)
        except HistoryCursorInvalid as exc:
            raise AgentTeamRejected("cursor_invalid", str(exc)) from exc

    async def people(
        self, period: Period, sort: str = "runs", order: Literal["asc", "desc"] = "desc"
    ) -> TeamView[tuple[PersonRow, ...]]:
        report, status, computed_at = await self._period_report(period)
        rows = _sorted(list(report.people), sort, order)
        return self._view(period, status, computed_at, tuple(rows))

    async def person(self, actor_id: str, period: Period) -> TeamView[PersonDetail]:
        resolved = self._scope.actor_id if actor_id == "me" else actor_id
        runtime = await self._load_runtime(period)
        members = await self._build_members(period, runtime)
        people = await self._people_rows(period, members, runtime)
        person_row = next(
            (row for row in people if row.actor_id == resolved),
            PersonRow(
                actor_id=resolved,
                owned_agents=0,
                owned_runs=0,
                served_by_agents=0,
                runs=0,
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
        all_pending = await self._store.pending_feedback()
        pending_feedback = tuple(item for item in all_pending if item.agent_key in owned_keys)
        data = PersonDetail(
            person=person_row, owned=owned, served=served, pending_feedback=pending_feedback
        )
        return self._view(period, runtime.status, runtime.computed_at, data)

    async def org_chart(self, period: Period) -> TeamView[OrgChart]:
        runtime = await self._load_runtime(period)
        members = await self._build_members(period, runtime)
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
        current, status, computed_at = await self._period_report(period)
        previous_period = period.previous()
        previous, _previous_status, _previous_computed_at = await self._period_report(
            previous_period
        )
        data = TeamReport(current=current, previous=previous)
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


def _sorted(rows: list, sort: str, order: Literal["asc", "desc"]) -> list:
    """Sorts by the `sort` attribute, descending unless `order == "asc"`.
    Rows whose sort attribute is `None` (e.g. `last_run_at`) always sort
    last, regardless of direction."""
    reverse = order != "asc"
    with_value = [row for row in rows if getattr(row, sort) is not None]
    without_value = [row for row in rows if getattr(row, sort) is None]
    with_value.sort(key=lambda row: getattr(row, sort), reverse=reverse)
    return with_value + without_value
