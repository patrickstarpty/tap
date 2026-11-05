"""Langfuse Metrics API v1 client (3.225.11, verified 2026-10-23) shared by
reporting views. See `global-constraints.md` in the Agent team view SDD folder
for the exact verified request/response shapes this client relies on.

Every query is scoped to one TAP Project (`project:<id>` trace tag) and one
Langfuse environment. The Metrics API caps any single query at `row_limit`
(1000) rows. When a query hits the cap, the queried period is bisected at its
midday date and re-queried as two independent half-periods, recursively,
until each sub-query returns fewer than 1000 rows or the sub-period is a
single day (in which case the partial result is accepted as-is -- a single
day cannot be split further). The raw rows from every sub-query are
concatenated, so additive aggregates combine by plain addition.

`client` is owned by the caller; this client never closes it. Each HTTP
request is bounded by `timeout_seconds`; callers bound a whole multi-request
call (bisection included) themselves.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx

from tap.platform.reporting.period import Period
from tap.platform.reporting.runtime import RuntimeMetricsUnavailable, gather_runtime

_ROW_LIMIT = 1000
_METRICS_PATH = "/api/public/metrics"
_PROJECT_PREFIX = "project:"
_TIMESTAMP_RESOLUTION = timedelta(milliseconds=1)


def _iso(instant: datetime) -> str:
    return instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _split_period(period: Period) -> tuple[Period, Period] | None:
    if period.days <= 1:
        return None
    mid_offset = period.days // 2
    first_end = period.start + timedelta(days=mid_offset - 1)
    second_start = first_end + timedelta(days=1)
    return Period(period.start, first_end), Period(second_start, period.end)


class LangfuseMetricsClient:
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

    async def rows(
        self,
        *,
        project_id: str,
        view: str,
        dimensions: Sequence[str],
        metrics: Sequence[Mapping[str, str]],
        period: Period,
        extra_filters: Sequence[Mapping[str, Any]] = (),
        granularity: str | None = None,
    ) -> list[dict[str, Any]]:
        """Every raw row of one Metrics API query over `period`, filtered to
        `project_id`'s tag and the environment, then `extra_filters` (ANDed),
        bisecting by date whenever a (sub-)query hits the row limit."""
        filters = [*self.base_filters(project_id), *extra_filters]
        return await self._fetch_rows(
            lambda p: self.query(
                view=view,
                dimensions=dimensions,
                metrics=metrics,
                filters=filters,
                period=p,
                time_granularity=granularity,
            ),
            period,
        )

    def base_filters(self, project_id: str, *, tags: Sequence[str] = ()) -> list[dict[str, Any]]:
        """Project and environment filters; each of `tags` adds a filter
        narrowing to traces carrying that tag (filters are ANDed)."""
        filters: list[dict[str, Any]] = [
            {
                "column": "tags",
                "operator": "any of",
                "value": [f"{_PROJECT_PREFIX}{project_id}"],
                "type": "arrayOptions",
            },
        ]
        for tag in tags:
            filters.append(
                {
                    "column": "tags",
                    "operator": "any of",
                    "value": [tag],
                    "type": "arrayOptions",
                }
            )
        filters.append(
            {
                "column": "environment",
                "operator": "=",
                "value": self._environment,
                "type": "string",
            }
        )
        return filters

    def query(
        self,
        *,
        view: str,
        dimensions: Sequence[str],
        metrics: Sequence[Mapping[str, str]],
        filters: Sequence[Mapping[str, Any]],
        period: Period,
        time_granularity: str | None = None,
    ) -> dict[str, Any]:
        from_ts, end_exclusive = period.utc_bounds()
        # Langfuse applies `fromTimestamp` as `>=` but `toTimestamp` as `<=`
        # (inclusive; QueryBuilder, verified in the Langfuse source
        # 2026-10-27). Timestamps are millisecond precision, so the last
        # included instant is 1 ms before the exclusive day boundary -- a
        # trace at exactly 00:00:00.000 is then counted in one adjacent
        # period/half only.
        to_ts = end_exclusive - _TIMESTAMP_RESOLUTION
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
        rows = await self.request(build_query(period))
        if len(rows) < _ROW_LIMIT:
            return rows
        split = _split_period(period)
        if split is None:
            return rows
        first, second = split
        first_rows, second_rows = await gather_runtime(
            self._fetch_rows(build_query, first),
            self._fetch_rows(build_query, second),
        )
        return [*first_rows, *second_rows]

    async def request(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        """One Metrics API request; raises `RuntimeMetricsUnavailable` on a
        transport failure or a malformed response envelope."""
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
