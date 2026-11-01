"""`AgentRuntimeMetrics` decorator that caches each method's result for
`ttl_seconds` (spec/global-constraints: Langfuse-backed runtime data is cached,
MySQL-backed data is always queried live; this decorator wraps whichever
`AgentRuntimeMetrics` the project is configured with -- Langfuse or
`TurnTableRuntimeMetrics` -- so both pick up the same TTL behavior for free).

Successful results are cached for `ttl_seconds`. A `RuntimeMetricsUnavailable`
is negative-cached for `failure_ttl_seconds` (default 30) so an outage does not
retry the inner adapter on every view; other exceptions are never stored.

Concurrent callers for the same key share one in-flight fetch (the team tab
issues several views at once against a cold cache). The shared fetch is bounded
only by the per-fetch cap (`fetch_timeout_seconds`); each caller waits at most
its own remaining view budget. A caller that runs out of budget gets
`RuntimeMetricsUnavailable` without negative-caching anything -- its budget,
not the backend, ran out -- and when the last waiting caller gives up the
shared fetch is cancelled.

The caching itself is `tap.platform.reporting.ttl_cache.TtlSingleFlightCache`
(shared with Skill Hub); this decorator only builds the per-method keys.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from tap.modules.agent_team.domain.models import Period
from tap.modules.agent_team.ports import (
    AgentRuntimeMetrics,
    AgentRuntimeStats,
    RuntimeMetricsUnavailable,
    TrendPoint,
)
from tap.platform.reporting.ttl_cache import TtlSingleFlightCache

# The third element of a cache key: "stats", "trend:<granularity>", or
# `("last_runs", <sorted agent keys>)`.
_Kind = str | tuple[str, tuple[str, ...]]
# `(project_id, period-as-a-comparable-tuple-or-None, kind)`.
_CacheKey = tuple[str, tuple[int, int, int, int, int, int] | None, _Kind]


def _default_now() -> datetime:
    return datetime.now(UTC)


def _period_key(period: Period | None) -> tuple[int, int, int, int, int, int] | None:
    if period is None:
        return None
    return (
        period.start.year,
        period.start.month,
        period.start.day,
        period.end.year,
        period.end.month,
        period.end.day,
    )


class CachedRuntimeMetrics:
    """Caches `agent_stats`, `trend` and `last_runs` results for `ttl_seconds`
    (default 300, per global-constraints.md), keyed by `(project_id, period or
    None, kind)`."""

    def __init__(
        self,
        inner: AgentRuntimeMetrics,
        *,
        ttl_seconds: int = 300,
        failure_ttl_seconds: int = 30,
        fetch_timeout_seconds: float | None = 8.0,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = _default_now,
    ) -> None:
        self._inner = inner
        # The configuration stays readable here as before; the shared cache
        # below is what applies it.
        self._ttl_seconds = ttl_seconds
        self._failure_ttl_seconds = failure_ttl_seconds
        self._fetch_timeout_seconds = fetch_timeout_seconds
        self._clock = clock
        self._now = now
        self._cache = TtlSingleFlightCache(
            ttl_seconds=ttl_seconds,
            failure_ttl_seconds=failure_ttl_seconds,
            fetch_timeout_seconds=fetch_timeout_seconds,
            clock=clock,
            now=now,
        )

    # The shared cache's internal state, exposed read-only for tests.
    @property
    def _entries(self) -> dict[Any, Any]:
        return self._cache._entries

    @property
    def _failures(self) -> dict[Any, tuple[float, RuntimeMetricsUnavailable]]:
        return self._cache._failures

    @property
    def _inflight(self) -> dict[Any, Any]:
        return self._cache._inflight

    async def agent_stats(
        self, project_id: str, period: Period, *, timeout_seconds: float | None = None
    ) -> list[AgentRuntimeStats]:
        key: _CacheKey = (project_id, _period_key(period), "stats")

        async def fetch() -> list[AgentRuntimeStats]:
            return await self._inner.agent_stats(project_id, period)

        return await self._cache.get(key, fetch, timeout_seconds)

    async def trend(
        self,
        project_id: str,
        period: Period,
        granularity: str,
        *,
        timeout_seconds: float | None = None,
    ) -> list[TrendPoint]:
        key: _CacheKey = (project_id, _period_key(period), f"trend:{granularity}")

        async def fetch() -> list[TrendPoint]:
            return await self._inner.trend(project_id, period, granularity)

        return await self._cache.get(key, fetch, timeout_seconds)

    async def last_runs(
        self,
        project_id: str,
        agent_keys: Sequence[str],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, datetime]:
        sorted_keys = tuple(sorted(agent_keys))
        key: _CacheKey = (project_id, None, ("last_runs", sorted_keys))

        async def fetch() -> dict[str, datetime]:
            return await self._inner.last_runs(project_id, agent_keys)

        return await self._cache.get(key, fetch, timeout_seconds)

    def computed_at(self, project_id: str, period: Period | None, kind: _Kind) -> datetime | None:
        """The UTC wall-clock time the cached value for `(project_id, period,
        kind)` was fetched, or `None` if nothing is cached or the entry has
        expired."""
        key: _CacheKey = (project_id, _period_key(period), kind)
        return self._cache.computed_at(key)
