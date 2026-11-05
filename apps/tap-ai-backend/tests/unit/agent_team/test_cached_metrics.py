"""Unit tests for `CachedRuntimeMetrics` (Task 5, 2026-10-23 agent team view)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tap.modules.agent_team.adapters.cached_metrics import CachedRuntimeMetrics
from tap.modules.agent_team.domain.models import Period
from tap.modules.agent_team.ports import AgentRuntimeStats, RuntimeMetricsUnavailable


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value


class _CountingInner:
    """Records every call and either returns a canned value or raises."""

    def __init__(self) -> None:
        self.agent_stats_calls = 0
        self.trend_calls = 0
        self.last_runs_calls = 0
        self.raise_next = False

    async def agent_stats(self, project_id: str, period: Period) -> list[AgentRuntimeStats]:
        self.agent_stats_calls += 1
        if self.raise_next:
            self.raise_next = False
            raise RuntimeMetricsUnavailable("boom")
        return [
            AgentRuntimeStats(
                agent_key="agent-1",
                runs=self.agent_stats_calls,
                failures=0,
                abstained=0,
                canceled=0,
                latency_total_ms=0,
                latency_p50_ms=None,
                cost_usd=Decimal("0"),
                served=(),
                generation_calls=0,
                tool_calls=0,
            )
        ]

    async def trend(self, project_id: str, period: Period, granularity: str) -> list[object]:
        self.trend_calls += 1
        return [self.trend_calls]

    async def last_runs(self, project_id: str, agent_keys: Sequence[str]) -> dict[str, datetime]:
        self.last_runs_calls += 1
        return {key: datetime(2026, 10, 23, tzinfo=UTC) for key in agent_keys}


def _period() -> Period:
    return Period.parse(datetime(2026, 10, 1).date(), datetime(2026, 10, 23).date())


@pytest.mark.asyncio
async def test_second_call_within_ttl_hits_cache() -> None:
    inner = _CountingInner()
    clock = _FakeClock()
    cached = CachedRuntimeMetrics(inner, ttl_seconds=300, clock=clock)

    first = await cached.agent_stats("proj1", _period())
    clock.value += 100
    second = await cached.agent_stats("proj1", _period())

    assert inner.agent_stats_calls == 1
    assert first == second


@pytest.mark.asyncio
async def test_call_after_ttl_refetches() -> None:
    inner = _CountingInner()
    clock = _FakeClock()
    cached = CachedRuntimeMetrics(inner, ttl_seconds=300, clock=clock)

    await cached.agent_stats("proj1", _period())
    clock.value += 300
    await cached.agent_stats("proj1", _period())

    assert inner.agent_stats_calls == 2


@pytest.mark.asyncio
async def test_unavailable_is_negative_cached_for_failure_ttl() -> None:
    inner = _CountingInner()
    inner.raise_next = True
    clock = _FakeClock()
    cached = CachedRuntimeMetrics(inner, ttl_seconds=300, failure_ttl_seconds=30, clock=clock)

    with pytest.raises(RuntimeMetricsUnavailable):
        await cached.agent_stats("proj1", _period())
    clock.value += 10
    with pytest.raises(RuntimeMetricsUnavailable):
        await cached.agent_stats("proj1", _period())
    assert inner.agent_stats_calls == 1  # outage not retried within 30 s

    clock.value += 20
    result = await cached.agent_stats("proj1", _period())
    assert inner.agent_stats_calls == 2
    assert result[0].runs == 2
    assert cached.computed_at("proj1", _period(), "stats") is not None


@pytest.mark.asyncio
async def test_cache_key_includes_period_and_kind() -> None:
    inner = _CountingInner()
    clock = _FakeClock()
    cached = CachedRuntimeMetrics(inner, ttl_seconds=300, clock=clock)

    period_a = Period.parse(datetime(2026, 10, 1).date(), datetime(2026, 10, 23).date())
    period_b = Period.parse(datetime(2026, 9, 1).date(), datetime(2026, 9, 30).date())

    await cached.agent_stats("proj1", period_a)
    await cached.agent_stats("proj1", period_b)
    assert inner.agent_stats_calls == 2

    await cached.trend("proj1", period_a, "day")
    assert inner.trend_calls == 1
    # Different project -- a separate cache entry, even with an identical period.
    await cached.agent_stats("proj2", period_a)
    assert inner.agent_stats_calls == 3

    # `agent_stats` and `trend` share a project/period but not a `kind`, so
    # neither call above touched the other's cache entry.
    await cached.agent_stats("proj1", period_a)
    await cached.trend("proj1", period_a, "day")
    assert inner.agent_stats_calls == 3
    assert inner.trend_calls == 1

    await cached.last_runs("proj1", ["agent-1", "agent-2"])
    await cached.last_runs("proj1", ["agent-2", "agent-1"])
    assert inner.last_runs_calls == 1  # same keys, different order -> same cache key

    assert cached.computed_at("proj1", period_a, "stats") is not None
    assert cached.computed_at("proj1", period_b, "stats") is not None
    assert cached.computed_at("proj1", period_a, "trend:day") is not None
    assert cached.computed_at("proj1", None, ("last_runs", ("agent-1", "agent-2"))) is not None
    assert cached.computed_at("proj1", period_a, "trend:week") is None


@pytest.mark.asyncio
async def test_hanging_inner_is_negative_cached_after_fetch_timeout() -> None:
    class _Hanging(_CountingInner):
        async def agent_stats(self, project_id: str, period: Period) -> list[AgentRuntimeStats]:
            self.agent_stats_calls += 1
            await asyncio.sleep(3600)
            return []

    inner = _Hanging()
    clock = _FakeClock()
    cached = CachedRuntimeMetrics(
        inner, ttl_seconds=300, failure_ttl_seconds=30, fetch_timeout_seconds=0.01, clock=clock
    )

    with pytest.raises(RuntimeMetricsUnavailable):
        await cached.agent_stats("proj1", _period())
    clock.value += 10
    with pytest.raises(RuntimeMetricsUnavailable):
        await cached.agent_stats("proj1", _period())
    assert inner.agent_stats_calls == 1  # the hang is not retried within 30 s


@pytest.mark.asyncio
async def test_expired_entries_are_evicted_on_next_store() -> None:
    inner = _CountingInner()
    clock = _FakeClock()
    cached = CachedRuntimeMetrics(inner, ttl_seconds=300, clock=clock)
    period_a = Period.parse(datetime(2026, 10, 1).date(), datetime(2026, 10, 23).date())
    period_b = Period.parse(datetime(2026, 9, 1).date(), datetime(2026, 9, 30).date())

    await cached.agent_stats("proj1", period_a)
    assert len(cached._entries) == 1
    clock.value += 301
    await cached.agent_stats("proj1", period_b)  # stores b, evicts the expired a

    assert len(cached._entries) == 1
    assert cached.computed_at("proj1", period_a, "stats") is None
    assert cached.computed_at("proj1", period_b, "stats") is not None


@pytest.mark.asyncio
async def test_exhausted_deadline_neither_fetches_nor_negative_caches() -> None:
    inner = _CountingInner()
    clock = _FakeClock()
    cached = CachedRuntimeMetrics(inner, ttl_seconds=300, failure_ttl_seconds=30, clock=clock)

    with pytest.raises(RuntimeMetricsUnavailable):
        await cached.agent_stats("proj1", _period(), timeout_seconds=0.0)
    assert inner.agent_stats_calls == 0

    # No failure entry was written: the next call with budget really fetches.
    result = await cached.agent_stats("proj1", _period(), timeout_seconds=5.0)
    assert inner.agent_stats_calls == 1
    assert result[0].runs == 1


# -- review fix 6: in-flight dedupe and the view-budget rule -----------------


class _Gated(_CountingInner):
    """`agent_stats` blocks until `release` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def agent_stats(self, project_id: str, period: Period) -> list[AgentRuntimeStats]:
        self.agent_stats_calls += 1
        await self.release.wait()
        return []


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_in_flight_fetch() -> None:
    inner = _Gated()
    cached = CachedRuntimeMetrics(inner, clock=_FakeClock())

    callers = [
        asyncio.create_task(cached.agent_stats("proj1", _period(), timeout_seconds=5.0))
        for _ in range(5)
    ]
    await asyncio.sleep(0)
    inner.release.set()
    results = await asyncio.gather(*callers)

    assert inner.agent_stats_calls == 1
    assert results == [[]] * 5
    assert cached._inflight == {}


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_failure() -> None:
    class _FailingGated(_Gated):
        async def agent_stats(self, project_id: str, period: Period) -> list[AgentRuntimeStats]:
            self.agent_stats_calls += 1
            await self.release.wait()
            raise RuntimeMetricsUnavailable("boom")

    inner = _FailingGated()
    cached = CachedRuntimeMetrics(inner, clock=_FakeClock())

    callers = [asyncio.create_task(cached.agent_stats("proj1", _period())) for _ in range(3)]
    await asyncio.sleep(0)
    inner.release.set()
    results = await asyncio.gather(*callers, return_exceptions=True)

    assert inner.agent_stats_calls == 1
    assert all(isinstance(result, RuntimeMetricsUnavailable) for result in results)


@pytest.mark.asyncio
async def test_one_caller_giving_up_does_not_cancel_the_shared_fetch() -> None:
    inner = _Gated()
    cached = CachedRuntimeMetrics(inner, clock=_FakeClock())

    patient = asyncio.create_task(cached.agent_stats("proj1", _period(), timeout_seconds=5.0))
    with pytest.raises(RuntimeMetricsUnavailable):
        await cached.agent_stats("proj1", _period(), timeout_seconds=0.01)
    inner.release.set()

    assert await patient == []
    assert inner.agent_stats_calls == 1


@pytest.mark.asyncio
async def test_budget_shorter_than_cap_times_out_without_negative_caching() -> None:
    inner = _Gated()
    clock = _FakeClock()
    cached = CachedRuntimeMetrics(
        inner, failure_ttl_seconds=30, fetch_timeout_seconds=8.0, clock=clock
    )

    with pytest.raises(RuntimeMetricsUnavailable):
        await cached.agent_stats("proj1", _period(), timeout_seconds=0.01)
    assert cached._failures == {}
    assert cached._inflight == {}  # the abandoned fetch was cancelled

    inner.release.set()
    assert await cached.agent_stats("proj1", _period(), timeout_seconds=5.0) == []
    assert inner.agent_stats_calls == 2


@pytest.mark.asyncio
async def test_cap_shorter_than_budget_times_out_and_is_negative_cached() -> None:
    inner = _Gated()
    clock = _FakeClock()
    cached = CachedRuntimeMetrics(
        inner, failure_ttl_seconds=30, fetch_timeout_seconds=0.01, clock=clock
    )

    with pytest.raises(RuntimeMetricsUnavailable):
        await cached.agent_stats("proj1", _period(), timeout_seconds=5.0)
    with pytest.raises(RuntimeMetricsUnavailable):
        await cached.agent_stats("proj1", _period(), timeout_seconds=5.0)

    assert inner.agent_stats_calls == 1
