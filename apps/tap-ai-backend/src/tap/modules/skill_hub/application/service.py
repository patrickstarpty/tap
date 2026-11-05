"""`SkillHubService`: merges the live MySQL Skill Hub store with the cached
runtime (Langfuse) secondary metrics into the Skill Hub's read models (spec
sections 3 and 5), and runs the two Skill Hub writes. HTTP wiring is Task
10; this module never touches routes.

Only `detail` and `export_data` carry runtime data, so only they query the
runtime backend; `catalog`, `summary` and `usage` are MySQL-only and always
report `runtime_status="ok"` computed now.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol, TypeVar, cast

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.assets import SkillRevision
from tap.modules.ai.domain.maintenance import new_skill_event
from tap.modules.ai.domain.skill_metadata import SKILL_CATEGORIES, SkillCategory
from tap.modules.skill_hub.adapters.cached_metrics import CachedSkillRuntimeMetrics
from tap.modules.skill_hub.domain import metrics as pure
from tap.modules.skill_hub.domain.models import (
    CatalogItem,
    HubView,
    LibrarySummary,
    ModeCounts,
    SkillDetail,
    SkillUsage,
    UsageSeries,
)
from tap.modules.skill_hub.ports import (
    SkillHubStore,
    SkillRatings,
    SkillRecord,
    SkillRuntimeStats,
    UsagePoint,
)
from tap.platform.reporting.period import Period
from tap.platform.reporting.runtime import RuntimeMetricsUnavailable, RuntimeStatus, view_deadline

_MAX_QUERY_LENGTH = 128
_SYNC_LAG_THRESHOLD = timedelta(minutes=5)
_RUNTIME_LOAD_TIMEOUT_SECONDS = 10.0
# Safety net only: the cached fetch already carries the deadline-derived
# timeout, which fires first.
_RUNTIME_LOAD_GRACE_SECONDS = 1.0
_RANKING_LIMIT = 5

# Mirrors the applicable-task closed set of `tap.modules.ai.domain.assets`.
_APPLICABLE_TASKS = frozenset({"knowledge.answer", "test-plan.generate", "automation.generate"})
_SOURCES = frozenset({"imported", "authored", "built_in"})
_STATUSES = frozenset({"enabled", "disabled"})
_SORT_FIELDS = frozenset({"name", "last_used_at", "activations", "updated_at"})
_SORT_ORDERS = frozenset({"asc", "desc"})

_T = TypeVar("_T")


class SkillHubRejected(Exception):
    """A rejected Skill Hub query or write; `code` is drawn from the closed
    error vocabulary (`period_invalid`, `filter_invalid`, `sort_invalid`,
    `cursor_invalid`, `actor_not_found`, `skill_not_found`,
    `skill_read_only`)."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class SkillRevisionCatalog(Protocol):
    """The slice of `MysqlAssetCatalog` the detail view reads."""

    async def list_revisions(
        self, kind: Literal["agent", "skill"], asset_id: str
    ) -> tuple[object, ...]: ...


@dataclass(frozen=True, slots=True)
class SkillExportItem:
    record: SkillRecord
    usage: SkillUsage


@dataclass(frozen=True, slots=True)
class SkillExportBundle:
    """`export_data`'s payload, serialised by Task 10's `build_skill_export`:
    one library item per Skill (name order) and the usage trend points of
    every Skill used in the period, as `(skill_id, point)`."""

    items: tuple[SkillExportItem, ...]
    usage: tuple[tuple[str, UsagePoint], ...]


@dataclass(frozen=True, slots=True)
class _Runtime:
    status: RuntimeStatus
    # `None` when the runtime backend is unavailable.
    stats_by_skill: dict[str, SkillRuntimeStats] | None
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class _Usage:
    """Every MySQL usage read model one view needs, fetched once."""

    by_skill: dict[str, ModeCounts]
    users: dict[str, int]
    ratings: dict[str, SkillRatings]
    last_used: dict[str, datetime]
    pinned_by: dict[str, tuple[str, ...]]


def _reject_filters(
    q: str | None, category: str | None, task: str | None, source: str | None, status: str | None
) -> None:
    if q is not None and len(q) > _MAX_QUERY_LENGTH:
        raise SkillHubRejected("filter_invalid", "q must be <= 128 characters")
    if category is not None and category not in SKILL_CATEGORIES:
        raise SkillHubRejected("filter_invalid", f"unknown category {category!r}")
    if task is not None and task not in _APPLICABLE_TASKS:
        raise SkillHubRejected("filter_invalid", f"unknown task {task!r}")
    if source is not None and source not in _SOURCES:
        raise SkillHubRejected("filter_invalid", f"unknown source {source!r}")
    if status is not None and status not in _STATUSES:
        raise SkillHubRejected("filter_invalid", f"unknown status {status!r}")


def _matches(record: SkillRecord, needle: str) -> bool:
    return needle in record.name.casefold() or needle in (record.description or "").casefold()


def _by_name(record: SkillRecord) -> tuple[str, str]:
    return record.name.casefold(), record.skill_id


class SkillHubService:
    """`sync_lag` (wired to `MysqlAgentTeamStore.oldest_unsynced_age` when
    the runtime adapter is Langfuse) turns a runtime view `syncing` when the
    Langfuse sync is more than 5 minutes behind; `None` never reports it."""

    def __init__(
        self,
        store: SkillHubStore,
        metrics: CachedSkillRuntimeMetrics,
        *,
        asset_catalog: SkillRevisionCatalog,
        scope: ProjectScopeContext,
        clock: Callable[[], datetime],
        sync_lag: Callable[[], Awaitable[timedelta | None]] | None = None,
    ) -> None:
        self._store = store
        self._metrics = metrics
        self._asset_catalog = asset_catalog
        self._scope = scope
        self._clock = clock
        self._sync_lag = sync_lag

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    async def _load_runtime(self, period: Period) -> _Runtime:
        """One view's runtime data under its 10 s budget; any backend
        failure or budget overrun degrades to `unavailable`."""
        remaining = view_deadline(_RUNTIME_LOAD_TIMEOUT_SECONDS)
        project_id = self._scope.project_id
        try:
            async with asyncio.timeout(remaining() + _RUNTIME_LOAD_GRACE_SECONDS):
                stats = await self._metrics.skill_stats(
                    project_id, period, timeout_seconds=remaining()
                )
        except (RuntimeMetricsUnavailable, TimeoutError):
            return _Runtime(status="unavailable", stats_by_skill=None, computed_at=self._clock())
        status: RuntimeStatus = "ok"
        if self._sync_lag is not None:
            lag = await self._sync_lag()
            if lag is not None and lag > _SYNC_LAG_THRESHOLD:
                status = "syncing"
        return _Runtime(
            status=status,
            stats_by_skill={item.skill_id: item for item in stats},
            computed_at=self._metrics.computed_at(project_id, period) or self._clock(),
        )

    async def _load_usage(self, period: Period) -> _Usage:
        return _Usage(
            by_skill=pure.modes_by_skill(await self._store.activation_counts(period)),
            users=await self._store.distinct_users(period),
            ratings={item.skill_id: item for item in await self._store.ratings(period)},
            last_used=await self._store.last_used(),
            pinned_by=await self._store.pinned_by(),
        )

    async def _require_skill(self, skill_id: str) -> SkillRecord:
        record = await self._store.skill(skill_id)
        if record is None:
            raise SkillHubRejected("skill_not_found", f"no Skill {skill_id!r} in this Project")
        return record

    async def _require_writable_skill(self, skill_id: str) -> SkillRecord:
        record = await self._require_skill(skill_id)
        if record.built_in:
            raise SkillHubRejected("skill_read_only", "built-in Skills are read-only")
        return record

    @staticmethod
    def _usage_of(skill_id: str, usage: _Usage, runtime: _Runtime) -> SkillUsage:
        stats = None
        if runtime.stats_by_skill is not None:
            stats = runtime.stats_by_skill.get(skill_id) or SkillRuntimeStats(
                skill_id=skill_id, runs=0, failures=0, latency_total_ms=0, cost_usd=Decimal(0)
            )
        return pure.build_usage(
            by_mode=usage.by_skill.get(skill_id, pure.empty_modes()),
            users=usage.users.get(skill_id, 0),
            ratings=usage.ratings.get(skill_id),
            last_used_at=usage.last_used.get(skill_id),
            pinned_by_agents=len(usage.pinned_by.get(skill_id, ())),
            runtime=stats,
        )

    def _view(
        self,
        period: Period,
        data: _T,
        status: RuntimeStatus = "ok",
        computed_at: datetime | None = None,
    ) -> HubView[_T]:
        return HubView(
            runtime_status=status,
            computed_at=computed_at or self._clock(),
            timezone="UTC",
            period=period,
            granularity=period.granularity,
            data=data,
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def catalog(
        self,
        period: Period,
        *,
        q: str | None = None,
        category: str | None = None,
        task: str | None = None,
        source: str | None = None,
        status: str | None = None,
        sort: str = "activations",
        order: str = "desc",
    ) -> HubView[tuple[CatalogItem, ...]]:
        """The library, filtered and sorted (no pagination). Ties keep name
        order in both directions; `last_used_at` puts never-used Skills last
        in both orders."""
        _reject_filters(q, category, task, source, status)
        if sort not in _SORT_FIELDS or order not in _SORT_ORDERS:
            raise SkillHubRejected("sort_invalid", f"cannot sort by {sort!r} {order!r}")
        records = await self._store.skills()
        needle = q.strip().casefold() if q is not None else ""
        records = [
            record
            for record in records
            if (not needle or _matches(record, needle))
            and (category is None or record.category == category)
            and (task is None or task in record.applicable_tasks)
            and (source is None or record.source_kind == source)
            and (status is None or record.status == status)
        ]
        by_skill = pure.modes_by_skill(await self._store.activation_counts(period))
        last_used = await self._store.last_used() if sort == "last_used_at" else {}

        items: list[CatalogItem] = []
        for record in sorted(records, key=_by_name):
            modes = by_skill.get(record.skill_id, pure.empty_modes())
            items.append(
                CatalogItem(record=record, activations=pure.activation_total(modes), by_mode=modes)
            )
        reverse = order == "desc"
        if sort == "last_used_at":
            used = [item for item in items if item.record.skill_id in last_used]
            never = [item for item in items if item.record.skill_id not in last_used]
            used.sort(key=lambda item: last_used[item.record.skill_id], reverse=reverse)
            items = used + never
        elif sort == "activations":
            items.sort(key=lambda item: item.activations, reverse=reverse)
        elif sort == "updated_at":
            items.sort(key=lambda item: item.record.updated_at, reverse=reverse)
        elif reverse:
            items.reverse()
        return self._view(period, tuple(items))

    async def detail(self, skill_id: str, period: Period) -> HubView[SkillDetail]:
        record = await self._require_skill(skill_id)
        usage = await self._load_usage(period)
        runtime = await self._load_runtime(period)
        revisions = cast(
            tuple[SkillRevision, ...],
            await self._asset_catalog.list_revisions("skill", skill_id),
        )
        latest = next(
            (r for r in revisions if r.revision_id == record.latest_revision_id),
            revisions[-1] if revisions else None,
        )
        detail = SkillDetail(
            record=record,
            usage=self._usage_of(skill_id, usage, runtime),
            revisions=revisions,
            files=latest.files if latest is not None else (),
            pinned_by=usage.pinned_by.get(skill_id, ()),
            top_agents=tuple(await self._store.top_agents(skill_id, period, _RANKING_LIMIT)),
            top_callers=tuple(await self._store.top_callers(skill_id, period, _RANKING_LIMIT)),
        )
        return self._view(period, detail, runtime.status, runtime.computed_at)

    async def usage(self, skill_id: str, period: Period) -> HubView[UsageSeries]:
        await self._require_skill(skill_id)
        points = tuple(await self._store.usage_trend(skill_id, period, period.granularity))
        return self._view(period, UsageSeries(points=points, by_mode=pure.mode_totals(points)))

    async def summary(self, period: Period) -> HubView[LibrarySummary]:
        records = await self._store.skills()
        by_skill = pure.modes_by_skill(await self._store.activation_counts(period))
        return self._view(period, pure.library_summary(records, by_skill))

    async def export_data(self, period: Period) -> HubView[SkillExportBundle]:
        records = sorted(await self._store.skills(), key=_by_name)
        usage = await self._load_usage(period)
        runtime = await self._load_runtime(period)
        items = tuple(
            SkillExportItem(record=record, usage=self._usage_of(record.skill_id, usage, runtime))
            for record in records
        )
        series: list[tuple[str, UsagePoint]] = []
        for record in records:
            if pure.activation_total(usage.by_skill.get(record.skill_id, {})) == 0:
                continue
            points = await self._store.usage_trend(record.skill_id, period, period.granularity)
            series.extend((record.skill_id, point) for point in points)
        bundle = SkillExportBundle(items=items, usage=tuple(series))
        return self._view(period, bundle, runtime.status, runtime.computed_at)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def update_profile(self, skill_id: str, category: SkillCategory) -> SkillRecord:
        await self._require_writable_skill(skill_id)
        if category not in SKILL_CATEGORIES:
            raise SkillHubRejected("filter_invalid", f"unknown category {category!r}")
        try:
            return await self._store.set_category(skill_id, category)
        except LookupError as exc:
            raise SkillHubRejected("skill_not_found", str(exc)) from exc

    async def transfer_owner(self, skill_id: str, actor_id: str) -> SkillRecord:
        record = await self._require_writable_skill(skill_id)
        if record.owner_actor_id == actor_id:
            return record
        if not await self._store.actor_exists(actor_id):
            raise SkillHubRejected("actor_not_found", f"no active actor {actor_id!r}")
        event = new_skill_event(self._scope, skill_id, "owner_transferred", now=self._clock())
        try:
            return await self._store.transfer_owner(skill_id, actor_id, event)
        except LookupError as exc:
            raise SkillHubRejected("skill_not_found", str(exc)) from exc
