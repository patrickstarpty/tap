"""TTL cache with single-flight fetches and negative caching for runtime
(Langfuse-backed) reporting data.

Successful results are cached for `ttl_seconds`. A `RuntimeMetricsUnavailable`
is negative-cached for `failure_ttl_seconds` (default 30) so an outage does not
retry the backend on every view; other exceptions are never stored.

Concurrent callers for the same key share one in-flight fetch (a view issues
several queries at once against a cold cache). The shared fetch is bounded
only by the per-fetch cap (`fetch_timeout_seconds`); each caller waits at most
its own remaining view budget. A caller that runs out of budget gets
`RuntimeMetricsUnavailable` without negative-caching anything -- its budget,
not the backend, ran out -- and when the last waiting caller gives up the
shared fetch is cancelled.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from tap.platform.reporting.runtime import RuntimeMetricsUnavailable

_T = TypeVar("_T")


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


class TtlSingleFlightCache:
    """Caches each key's fetched value for `ttl_seconds` (default 300)."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        failure_ttl_seconds: int = 30,
        fetch_timeout_seconds: float | None = 8.0,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = _default_now,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._failure_ttl_seconds = failure_ttl_seconds
        self._fetch_timeout_seconds = fetch_timeout_seconds
        self._clock = clock
        self._now = now
        self._entries: dict[Hashable, _CacheEntry] = {}
        self._failures: dict[Hashable, tuple[float, RuntimeMetricsUnavailable]] = {}
        self._inflight: dict[Hashable, _Flight] = {}

    async def get(
        self,
        key: Hashable,
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
                return await asyncio.shield(flight.task)  # type: ignore[arg-type]
        except TimeoutError as error:
            raise RuntimeMetricsUnavailable("view deadline exhausted") from error
        finally:
            flight.waiters -= 1
            if flight.waiters == 0 and not flight.task.done():
                flight.task.cancel()
                if self._inflight.get(key) is flight:
                    del self._inflight[key]

    async def _fetch_and_store(self, key: Hashable, fetch: Callable[[], Awaitable[_T]]) -> _T:
        """The shared fetch behind `get`, bounded by the per-fetch cap
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
        process does not accumulate one stale entry per distinct key ever
        queried."""
        now = self._clock()
        expired = [key for key, entry in self._entries.items() if now >= entry.expires_at]
        for key in expired:
            del self._entries[key]
        for key in [k for k, (until, _) in self._failures.items() if now >= until]:
            del self._failures[key]

    def computed_at(self, key: Hashable) -> datetime | None:
        """The UTC wall-clock time the cached value for `key` was fetched, or
        `None` if nothing is cached or the entry has expired."""
        entry = self._entries.get(key)
        if entry is None or self._clock() >= entry.expires_at:
            return None
        return entry.computed_at
