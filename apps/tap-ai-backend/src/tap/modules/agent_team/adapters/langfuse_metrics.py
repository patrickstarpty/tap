"""`AgentRuntimeMetrics` backed by the Langfuse Metrics API v1 (3.225.11, verified
2026-10-23). See `global-constraints.md` in the Agent team view SDD folder for the
exact verified request/response shapes this adapter relies on.

Only Turn-root traces (`turn.request`) carry `agent:<key>` and `outcome:<state>`
tags (Task 3); every row returned by the `tags`-dimension queries below therefore
represents a single `(agent, outcome)` (or `(agent, outcome, userId)`) combination,
never a partial trace. Rows without an `agent:` tag -- including the empty
"gap-filled" buckets Langfuse inserts for a `timeDimension` query -- carry no
usable signal and are dropped.

The Metrics API caps any single query at `row_limit` (1000) rows. When a query
hits the cap, the queried period is bisected at its midday date and re-queried as
two independent half-periods, recursively, until each sub-query returns fewer than
1000 rows or the sub-period is a single day (in which case the partial result is
accepted as-is -- a single day cannot be split further). The raw rows from every
sub-query are concatenated before aggregation, so `count`/`latency sum`/`totalCost
sum` combine by plain addition; `latency p50` cannot be averaged this way, so it is
always recomputed as a run-count-weighted mean across whatever rows happen to be
in hand (whether those rows differ by `outcome:` tag within one query, or came
from different date sub-queries).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast

import httpx

from tap.modules.agent_team.domain.models import Period
from tap.modules.agent_team.ports import (
    AgentRuntimeStats,
    RuntimeMetricsUnavailable,
    ServedActor,
    TrendPoint,
)

_ROW_LIMIT = 1000
_METRICS_PATH = "/api/public/metrics"
_TRACES_PATH = "/api/public/traces"
_AGENT_PREFIX = "agent:"
_OUTCOME_PREFIX = "outcome:"
_PROJECT_PREFIX = "project:"
# Malformed response bodies (bad numbers, bad timestamps, non-dict rows) are a
# backend-contract break, not a programming error -- mapped to
# `RuntimeMetricsUnavailable` same as a network failure.
_PARSE_ERRORS = (InvalidOperation, ValueError, AttributeError, TypeError, KeyError)


def _tag_value(tags: Sequence[str], prefix: str) -> str | None:
    for tag in tags:
        if isinstance(tag, str) and tag.startswith(prefix):
            return tag[len(prefix) :]
    return None


def _as_number(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    if isinstance(value, bool):
        return Decimal(0)
    if isinstance(value, int):
        return Decimal(value)
    return Decimal(str(value))


def _as_int(value: Any) -> int:
    return int(_as_number(value))


def _iso(instant: datetime) -> str:
    return instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _split_period(period: Period) -> tuple[Period, Period] | None:
    if period.days <= 1:
        return None
    mid_offset = period.days // 2
    first_end = period.start + timedelta(days=mid_offset - 1)
    second_start = first_end + timedelta(days=1)
    return Period(period.start, first_end), Period(second_start, period.end)


@dataclass(frozen=True, slots=True)
class _StatsAccumulator:
    runs: int = 0
    failures: int = 0
    abstained: int = 0
    canceled: int = 0
    latency_total_ms: int = 0
    latency_weighted_p50: Decimal = Decimal(0)
    cost_usd: Decimal = Decimal(0)


async def _gather[T](*awaitables: Coroutine[Any, Any, T]) -> list[T]:
    """`asyncio.gather` with structured cancellation: the first failure
    cancels the sibling requests (a bare `gather` leaves them running), and
    the failure is re-raised bare so callers' `except RuntimeMetricsUnavailable`
    still matches instead of seeing an `ExceptionGroup`."""
    try:
        async with asyncio.TaskGroup() as group:
            tasks: list[asyncio.Task[T]] = [
                group.create_task(awaitable) for awaitable in awaitables
            ]
    except ExceptionGroup as group_error:
        raise group_error.exceptions[0] from None
    return [task.result() for task in tasks]


class LangfuseRuntimeMetrics:
    """Queries a single Langfuse project's traces/observations for Agent runtime
    metrics. `client` is owned by the caller; this adapter never closes it."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        host: str,
        public_key: str,
        secret_key: str,
        environment: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._client = client
        self._host = host.rstrip("/")
        self._auth = (public_key, secret_key)
        self._environment = environment
        self._timeout_seconds = timeout_seconds

    async def agent_stats(self, project_id: str, period: Period) -> list[AgentRuntimeStats]:
        """Aggregated runtime stats per Agent over `period`.

        `AgentRuntimeStats.latency_p50_ms` is a run-count-weighted mean of
        each `outcome:`/date-segment group's own p50 (see module docstring),
        not a statistically exact p50 over all runs -- Langfuse's Metrics API
        never returns a single p50 spanning multiple `tags` rows or multiple
        date-bisected sub-queries.

        The whole call is bounded by `timeout_seconds` regardless of how many
        underlying HTTP requests the row-limit bisection needs; raises
        `RuntimeMetricsUnavailable` on timeout, transport failure, or a
        malformed response body.
        """
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._agent_stats(project_id, period)
        except TimeoutError as error:
            raise RuntimeMetricsUnavailable() from error
        except _PARSE_ERRORS as error:
            raise RuntimeMetricsUnavailable() from error

    async def _agent_stats(self, project_id: str, period: Period) -> list[AgentRuntimeStats]:
        base_filters = self._base_filters(project_id)

        outcome_rows, served_rows, generation_rows, tool_rows = await _gather(
            self._fetch_rows(
                lambda p: self._metrics_query(
                    view="traces",
                    dimensions=["tags"],
                    metrics=[
                        {"measure": "count", "aggregation": "count"},
                        {"measure": "latency", "aggregation": "sum"},
                        {"measure": "latency", "aggregation": "p50"},
                        {"measure": "totalCost", "aggregation": "sum"},
                    ],
                    filters=base_filters,
                    period=p,
                ),
                period,
            ),
            self._fetch_rows(
                lambda p: self._metrics_query(
                    view="traces",
                    dimensions=["tags", "userId"],
                    metrics=[{"measure": "count", "aggregation": "count"}],
                    filters=base_filters,
                    period=p,
                ),
                period,
            ),
            self._fetch_rows(
                lambda p: self._metrics_query(
                    view="observations",
                    dimensions=["tags", "userId"],
                    metrics=[{"measure": "count", "aggregation": "count"}],
                    filters=[
                        *base_filters,
                        {
                            "column": "type",
                            "operator": "=",
                            "value": "GENERATION",
                            "type": "string",
                        },
                    ],
                    period=p,
                ),
                period,
            ),
            self._fetch_rows(
                lambda p: self._metrics_query(
                    view="observations",
                    dimensions=["tags", "userId"],
                    metrics=[{"measure": "count", "aggregation": "count"}],
                    filters=[
                        *base_filters,
                        {
                            "column": "name",
                            "operator": "starts with",
                            "value": "tool.",
                            "type": "string",
                        },
                    ],
                    period=p,
                ),
                period,
            ),
        )

        accumulators = self._accumulate_outcomes(outcome_rows)
        served = self._accumulate_served(served_rows)
        generation_calls = self._accumulate_counts(generation_rows)
        tool_calls = self._accumulate_counts(tool_rows)

        agent_keys = set(accumulators) | set(served) | set(generation_calls) | set(tool_calls)
        results: list[AgentRuntimeStats] = []
        for agent_key in sorted(agent_keys):
            acc = accumulators.get(agent_key, _StatsAccumulator())
            p50 = _as_int(acc.latency_weighted_p50 / acc.runs) if acc.runs > 0 else None
            results.append(
                AgentRuntimeStats(
                    agent_key=agent_key,
                    runs=acc.runs,
                    failures=acc.failures,
                    abstained=acc.abstained,
                    canceled=acc.canceled,
                    latency_total_ms=acc.latency_total_ms,
                    latency_p50_ms=p50,
                    cost_usd=acc.cost_usd,
                    served=served.get(agent_key, ()),
                    generation_calls=generation_calls.get(agent_key, 0),
                    tool_calls=tool_calls.get(agent_key, 0),
                )
            )
        return results

    async def trend(self, project_id: str, period: Period, granularity: str) -> list[TrendPoint]:
        """Per-Agent, per-bucket runtime trend. Langfuse labels week/month
        buckets by the bucket's own start (the Monday of the week, or the
        1st of the month) even when `fromTimestamp` falls mid-bucket -- so a
        row-limit bisection that splits a period mid-bucket yields two
        sub-queries that both report the same `time_dimension` label for
        that bucket. Grouping by `(bucket_start, agent_key)` before summing
        (rather than concatenating rows into one list of `TrendPoint`s)
        merges those back into a single point instead of double-reporting
        the bucket.
        """
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._trend(project_id, period, granularity)
        except TimeoutError as error:
            raise RuntimeMetricsUnavailable() from error
        except _PARSE_ERRORS as error:
            raise RuntimeMetricsUnavailable() from error

    async def _trend(self, project_id: str, period: Period, granularity: str) -> list[TrendPoint]:
        base_filters = self._base_filters(project_id)
        rows = await self._fetch_rows(
            lambda p: self._metrics_query(
                view="traces",
                dimensions=["tags"],
                metrics=[
                    {"measure": "count", "aggregation": "count"},
                    {"measure": "latency", "aggregation": "sum"},
                    {"measure": "totalCost", "aggregation": "sum"},
                ],
                filters=base_filters,
                period=p,
                time_granularity=granularity,
            ),
            period,
        )
        buckets: dict[tuple[date, str], dict[str, Any]] = {}
        for row in rows:
            tags = row.get("tags")
            if not isinstance(tags, list):
                continue
            agent_key = _tag_value(tags, _AGENT_PREFIX)
            if agent_key is None:
                continue
            time_dimension = row.get("time_dimension")
            if not isinstance(time_dimension, str) or not time_dimension:
                continue
            bucket_start = date.fromisoformat(time_dimension[:10])
            key = (bucket_start, agent_key)
            bucket = buckets.setdefault(
                key,
                {"runs": 0, "failures": 0, "latency_total_ms": 0, "cost_usd": Decimal(0)},
            )
            count = _as_int(row.get("count_count"))
            bucket["runs"] += count
            if _tag_value(tags, _OUTCOME_PREFIX) == "failed":
                bucket["failures"] += count
            bucket["latency_total_ms"] += _as_int(row.get("sum_latency"))
            bucket["cost_usd"] += _as_number(row.get("sum_totalCost"))

        return [
            TrendPoint(
                bucket_start=bucket_start,
                agent_key=agent_key,
                runs=bucket["runs"],
                failures=bucket["failures"],
                latency_total_ms=bucket["latency_total_ms"],
                cost_usd=bucket["cost_usd"],
            )
            for (bucket_start, agent_key), bucket in sorted(buckets.items())
        ]

    async def last_runs(self, project_id: str, agent_keys: Sequence[str]) -> dict[str, datetime]:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._last_runs(project_id, agent_keys)
        except TimeoutError as error:
            raise RuntimeMetricsUnavailable() from error
        except _PARSE_ERRORS as error:
            raise RuntimeMetricsUnavailable() from error

    async def _last_runs(self, project_id: str, agent_keys: Sequence[str]) -> dict[str, datetime]:
        pairs = await _gather(
            *(self._fetch_last_run(project_id, agent_key) for agent_key in agent_keys)
        )
        return {agent_key: timestamp for agent_key, timestamp in pairs if timestamp is not None}

    async def _fetch_last_run(self, project_id: str, agent_key: str) -> tuple[str, datetime | None]:
        try:
            response = await self._client.get(
                self._host + _TRACES_PATH,
                params={
                    "tags": [f"{_PROJECT_PREFIX}{project_id}", f"{_AGENT_PREFIX}{agent_key}"],
                    "orderBy": "timestamp.desc",
                    "limit": "1",
                    "environment": self._environment,
                },
                auth=self._auth,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeMetricsUnavailable() from error
        if not isinstance(body, dict):
            raise RuntimeMetricsUnavailable()
        data = body.get("data")
        if not isinstance(data, list) or not data:
            return agent_key, None
        first = data[0]
        if not isinstance(first, dict):
            raise RuntimeMetricsUnavailable()
        timestamp = first.get("timestamp")
        if not isinstance(timestamp, str):
            raise RuntimeMetricsUnavailable()
        try:
            return agent_key, datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as error:
            raise RuntimeMetricsUnavailable() from error

    def _base_filters(self, project_id: str) -> list[dict[str, Any]]:
        return [
            {
                "column": "tags",
                "operator": "any of",
                "value": [f"{_PROJECT_PREFIX}{project_id}"],
                "type": "arrayOptions",
            },
            {
                "column": "environment",
                "operator": "=",
                "value": self._environment,
                "type": "string",
            },
        ]

    def _metrics_query(
        self,
        *,
        view: str,
        dimensions: Sequence[str],
        metrics: Sequence[Mapping[str, str]],
        filters: Sequence[Mapping[str, Any]],
        period: Period,
        time_granularity: str | None = None,
    ) -> dict[str, Any]:
        from_ts, to_ts = period.utc_bounds()
        query: dict[str, Any] = {
            "view": view,
            "dimensions": [{"field": field} for field in dimensions],
            "metrics": list(metrics),
            "filters": list(filters),
            "fromTimestamp": _iso(from_ts),
            "toTimestamp": _iso(to_ts),
            "config": {"row_limit": _ROW_LIMIT},
        }
        if time_granularity is not None:
            query["timeDimension"] = {"granularity": time_granularity}
        return query

    async def _fetch_rows(
        self, build_query: Callable[[Period], dict[str, Any]], period: Period
    ) -> list[dict[str, Any]]:
        rows = await self._request_metrics(build_query(period))
        if len(rows) < _ROW_LIMIT:
            return rows
        split = _split_period(period)
        if split is None:
            return rows
        first, second = split
        first_rows, second_rows = await _gather(
            self._fetch_rows(build_query, first),
            self._fetch_rows(build_query, second),
        )
        return [*first_rows, *second_rows]

    async def _request_metrics(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            response = await self._client.get(
                self._host + _METRICS_PATH,
                params={"query": json.dumps(query)},
                auth=self._auth,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeMetricsUnavailable() from error
        if not isinstance(body, dict):
            raise RuntimeMetricsUnavailable()
        data = body.get("data")
        if not isinstance(data, list):
            raise RuntimeMetricsUnavailable()
        return cast(list[dict[str, Any]], data)

    def _accumulate_outcomes(self, rows: list[dict[str, Any]]) -> dict[str, _StatsAccumulator]:
        accumulators: dict[str, _StatsAccumulator] = {}
        for row in rows:
            tags = row.get("tags")
            if not isinstance(tags, list):
                continue
            agent_key = _tag_value(tags, _AGENT_PREFIX)
            if agent_key is None:
                continue
            outcome = _tag_value(tags, _OUTCOME_PREFIX)
            count = _as_int(row.get("count_count"))
            latency_sum = _as_int(row.get("sum_latency"))
            p50 = _as_number(row.get("p50_latency"))
            cost = _as_number(row.get("sum_totalCost"))
            current = accumulators.get(agent_key, _StatsAccumulator())
            accumulators[agent_key] = _StatsAccumulator(
                runs=current.runs + count,
                failures=current.failures + (count if outcome == "failed" else 0),
                abstained=current.abstained + (count if outcome == "abstained" else 0),
                canceled=current.canceled + (count if outcome == "canceled" else 0),
                latency_total_ms=current.latency_total_ms + latency_sum,
                latency_weighted_p50=current.latency_weighted_p50 + p50 * count,
                cost_usd=current.cost_usd + cost,
            )
        return accumulators

    def _accumulate_served(self, rows: list[dict[str, Any]]) -> dict[str, tuple[ServedActor, ...]]:
        per_agent: dict[str, dict[str, int]] = {}
        for row in rows:
            tags = row.get("tags")
            if not isinstance(tags, list):
                continue
            agent_key = _tag_value(tags, _AGENT_PREFIX)
            if agent_key is None:
                continue
            actor_id = row.get("userId")
            if not isinstance(actor_id, str) or not actor_id:
                continue
            count = _as_int(row.get("count_count"))
            actors = per_agent.setdefault(agent_key, {})
            actors[actor_id] = actors.get(actor_id, 0) + count

        result: dict[str, tuple[ServedActor, ...]] = {}
        for agent_key, actors in per_agent.items():
            ordered = sorted(actors.items(), key=lambda item: (-item[1], item[0]))
            result[agent_key] = tuple(ServedActor(actor_id, runs) for actor_id, runs in ordered)
        return result

    def _accumulate_counts(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        totals: dict[str, int] = {}
        for row in rows:
            tags = row.get("tags")
            if not isinstance(tags, list):
                continue
            agent_key = _tag_value(tags, _AGENT_PREFIX)
            if agent_key is None:
                continue
            totals[agent_key] = totals.get(agent_key, 0) + _as_int(row.get("count_count"))
        return totals
