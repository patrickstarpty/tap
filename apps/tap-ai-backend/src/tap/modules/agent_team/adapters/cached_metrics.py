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
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from tap.modules.agent_team.domain.models import Period
from tap.modules.agent_team.ports import (
    AgentRuntimeMetrics,
    AgentRuntimeStats,
    RuntimeMetricsUnavailable,
    TrendPoint,
)

_T = TypeVar("_T")

# The third element of a cache key: "stats", "trend:<granularity>", or
# `("last_runs", <sorted agent keys>)`.
_Kind = str | tuple[str, tuple[str, ...]]
# `(project_id, period-as-a-comparable-tuple-or-None, kind)`.
_CacheKey = tuple[str, tuple[int, int, int, int, int, int] | None, _Kind]


def _default_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class _CacheEntry:
    value: object
    computed_at: datetime
    expires_at: float


@dataclass(slots=True)
class _Flight:
    """One shared fetch and how many callers are still waiting on it."""

    task: asyncio.Task[object]
    waiters: int = 0


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
        self._ttl_seconds = ttl_seconds
        self._failure_ttl_seconds = failure_ttl_seconds
        self._fetch_timeout_seconds = fetch_timeout_seconds
        self._clock = clock
        self._now = now
        self._entries: dict[_CacheKey, _CacheEntry] = {}
        self._failures: dict[_CacheKey, tuple[float, RuntimeMetricsUnavailable]] = {}
        self._inflight: dict[_CacheKey, _Flight] = {}

    async def _cached(
        self,
        key: _CacheKey,
        fetch: Callable[[], Awaitable[_T]],
        timeout_seconds: float | None = None,
    ) -> _T:
        entry = self._entries.get(key)
        if entry is not None and self._clock() < entry.expires_at:
            return entry.value  # type: ignore[return-value]
        failure = self._failures.get(key)
        if failure is not None:
            if self._clock() < failure[0]:
                raise failure[1]
            del self._failures[key]
        if timeout_seconds is not None and timeout_seconds <= 0:
            # The caller's view deadline is already spent: this key was never
            # tried, so it is neither fetched nor negative-cached.
            raise RuntimeMetricsUnavailable("view deadline exhausted")
        flight = self._inflight.get(key)
        if flight is None:
            flight = _Flight(task=asyncio.ensure_future(self._fetch_and_store(key, fetch)))
            self._inflight[key] = flight
        flight.waiters += 1
        try:
            # `shield`: one caller's budget running out (or its cancellation)
            # must not cancel the fetch other callers still wait on.
            async with asyncio.timeout(timeout_seconds):
                return await asyncio.shield(flight.task)  # type: ignore[arg-type, return-value]
        except TimeoutError as error:
            raise RuntimeMetricsUnavailable("view deadline exhausted") from error
        finally:
            flight.waiters -= 1
            if flight.waiters == 0 and not flight.task.done():
                flight.task.cancel()
                if self._inflight.get(key) is flight:
                    del self._inflight[key]

    async def _fetch_and_store(self, key: _CacheKey, fetch: Callable[[], Awaitable[_T]]) -> _T:
        """The shared fetch behind `_cached`, bounded by the per-fetch cap
        only. Hitting that cap (or any other `RuntimeMetricsUnavailable`) is
        a backend failure and is negative-cached."""
        try:
            try:
                async with asyncio.timeout(self._fetch_timeout_seconds):
                    value = await fetch()
            except TimeoutError as error:
                raise RuntimeMetricsUnavailable("runtime metrics query timed out") from error
        except RuntimeMetricsUnavailable as exc:
            if self._failure_ttl_seconds > 0:
                self._failures[key] = (self._clock() + self._failure_ttl_seconds, exc)
            raise
        finally:
            flight = self._inflight.get(key)
            if flight is not None and flight.task is asyncio.current_task():
                del self._inflight[key]
        self._entries[key] = _CacheEntry(
            value=value, computed_at=self._now(), expires_at=self._clock() + self._ttl_seconds
        )
        self._evict_expired()
        return value

    def _evict_expired(self) -> None:
        """Drop every entry whose TTL has already elapsed, so a long-lived
        process does not accumulate one stale entry per distinct
        `(project_id, period, kind)` ever queried."""
        now = self._clock()
        expired = [key for key, entry in self._entries.items() if now >= entry.expires_at]
        for key in expired:
            del self._entries[key]
        for key in [k for k, (until, _) in self._failures.items() if now >= until]:
            del self._failures[key]

    async def agent_stats(
        self, project_id: str, period: Period, *, timeout_seconds: float | None = None
    ) -> list[AgentRuntimeStats]:
        key: _CacheKey = (project_id, _period_key(period), "stats")

        async def fetch() -> list[AgentRuntimeStats]:
            return await self._inner.agent_stats(project_id, period)

        return await self._cached(key, fetch, timeout_seconds)

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

        return await self._cached(key, fetch, timeout_seconds)

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

        return await self._cached(key, fetch, timeout_seconds)

    def computed_at(self, project_id: str, period: Period | None, kind: _Kind) -> datetime | None:
        """The UTC wall-clock time the cached value for `(project_id, period,
        kind)` was fetched, or `None` if nothing is cached or the entry has
        expired."""
        key: _CacheKey = (project_id, _period_key(period), kind)
        entry = self._entries.get(key)
        if entry is None or self._clock() >= entry.expires_at:
            return None
        return entry.computed_at
