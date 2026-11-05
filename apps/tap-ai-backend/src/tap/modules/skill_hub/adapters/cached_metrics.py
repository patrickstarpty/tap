"""`SkillRuntimeMetrics` decorator caching `skill_stats` in a
`TtlSingleFlightCache` keyed `(project_id, period)` -- the same TTL,
negative-caching, single-flight and per-fetch-cap behavior as the Agent team
view's `CachedRuntimeMetrics`, for whichever runtime adapter is configured
(Langfuse or the e2e turn-table one)."""

from __future__ import annotations

from datetime import datetime

from tap.modules.skill_hub.ports import SkillRuntimeMetrics, SkillRuntimeStats
from tap.platform.reporting.period import Period
from tap.platform.reporting.ttl_cache import TtlSingleFlightCache


class CachedSkillRuntimeMetrics:
    def __init__(self, inner: SkillRuntimeMetrics, cache: TtlSingleFlightCache) -> None:
        self._inner = inner
        self._cache = cache

    async def skill_stats(
        self, project_id: str, period: Period, *, timeout_seconds: float | None = None
    ) -> list[SkillRuntimeStats]:
        """`timeout_seconds` is the caller's remaining view budget; running
        out raises `RuntimeMetricsUnavailable` without negative-caching."""

        async def fetch() -> list[SkillRuntimeStats]:
            return await self._inner.skill_stats(project_id, period)

        return await self._cache.get((project_id, period), fetch, timeout_seconds)

    def computed_at(self, project_id: str, period: Period) -> datetime | None:
        """When the cached `skill_stats` for `(project_id, period)` was
        fetched, or `None` if nothing (unexpired) is cached."""
        return self._cache.computed_at((project_id, period))
