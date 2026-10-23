"""Unit tests for `LangfuseRuntimeMetrics` (Task 4, 2026-10-23 agent team view).

All Langfuse HTTP calls are faked with `httpx.MockTransport`; see
`global-constraints.md` in the SDD folder for the verified request/response
shapes these tests assert against.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from tap.modules.agent_team.adapters.langfuse_metrics import LangfuseRuntimeMetrics
from tap.modules.agent_team.domain.models import Period
from tap.modules.agent_team.ports import RuntimeMetricsUnavailable

_HOST = "https://langfuse.example.test"
_PROJECT_ID = "proj1"
_ENVIRONMENT = "local"


def _row(
    tags: list[str],
    *,
    user_id: str | None = None,
    count: int = 0,
    latency_sum: int | None = None,
    latency_p50: int | None = None,
    cost_sum: float | None = None,
    time_dimension: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {"tags": tags, "count_count": count}
    if user_id is not None:
        row["userId"] = user_id
    if latency_sum is not None:
        row["sum_latency"] = latency_sum
    if latency_p50 is not None:
        row["p50_latency"] = latency_p50
    if cost_sum is not None:
        row["sum_totalCost"] = cost_sum
    if time_dimension is not None:
        row["time_dimension"] = time_dimension
    return row


def _metrics_response(rows: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(200, json={"data": rows})


def _query_of(request: httpx.Request) -> dict[str, Any]:
    raw = request.url.params["query"]
    return json.loads(raw)


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _adapter(client: httpx.AsyncClient) -> LangfuseRuntimeMetrics:
    return LangfuseRuntimeMetrics(
        client,
        host=_HOST,
        public_key="pk",
        secret_key="sk",
        environment=_ENVIRONMENT,
    )


def _is_observations_filtered(query: dict[str, Any], column: str, value: str) -> bool:
    return any(
        f.get("column") == column and f.get("value") == value for f in query.get("filters", [])
    )


def _classify(query: dict[str, Any]) -> str:
    dims = [d["field"] for d in query["dimensions"]]
    if query["view"] == "observations":
        if _is_observations_filtered(query, "type", "GENERATION"):
            return "generation"
        if _is_observations_filtered(query, "name", "tool."):
            return "tool"
        raise AssertionError(f"unrecognized observations query: {query}")
    if query["view"] == "traces":
        if "timeDimension" in query:
            return "trend"
        if dims == ["tags", "userId"]:
            return "served"
        if dims == ["tags"]:
            return "stats"
    raise AssertionError(f"unrecognized query: {query}")


@pytest.mark.asyncio
async def test_stats_query_groups_traces_by_tags() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = _query_of(request)
        captured.append(query)
        return _metrics_response([])

    async with _make_client(handler) as client:
        await _adapter(client).agent_stats(_PROJECT_ID, Period(date(2026, 1, 1), date(2026, 1, 1)))

    by_kind = {_classify(q): q for q in captured}
    assert set(by_kind) == {"stats", "served", "generation", "tool"}

    base_filters = [
        {
            "column": "tags",
            "operator": "any of",
            "value": [f"project:{_PROJECT_ID}"],
            "type": "arrayOptions",
        },
        {"column": "environment", "operator": "=", "value": _ENVIRONMENT, "type": "string"},
    ]

    stats_q = by_kind["stats"]
    assert stats_q["view"] == "traces"
    assert stats_q["dimensions"] == [{"field": "tags"}]
    assert stats_q["metrics"] == [
        {"measure": "count", "aggregation": "count"},
        {"measure": "latency", "aggregation": "sum"},
        {"measure": "latency", "aggregation": "p50"},
        {"measure": "totalCost", "aggregation": "sum"},
    ]
    assert stats_q["filters"] == base_filters
    assert stats_q["config"] == {"row_limit": 1000}
    assert stats_q["fromTimestamp"] == "2026-01-01T00:00:00.000Z"
    assert stats_q["toTimestamp"] == "2026-01-02T00:00:00.000Z"

    served_q = by_kind["served"]
    assert served_q["view"] == "traces"
    assert served_q["dimensions"] == [{"field": "tags"}, {"field": "userId"}]
    assert served_q["metrics"] == [{"measure": "count", "aggregation": "count"}]
    assert served_q["filters"] == base_filters

    gen_q = by_kind["generation"]
    assert gen_q["view"] == "observations"
    assert gen_q["dimensions"] == [{"field": "tags"}, {"field": "userId"}]
    assert gen_q["metrics"] == [{"measure": "count", "aggregation": "count"}]
    assert gen_q["filters"] == [
        *base_filters,
        {"column": "type", "operator": "=", "value": "GENERATION", "type": "string"},
    ]

    tool_q = by_kind["tool"]
    assert tool_q["view"] == "observations"
    assert tool_q["filters"] == [
        *base_filters,
        {"column": "name", "operator": "starts with", "value": "tool.", "type": "string"},
    ]


@pytest.mark.asyncio
async def test_parses_agent_keys_and_ignores_project_tags() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = _query_of(request)
        if _classify(query) == "stats":
            return _metrics_response(
                [
                    _row([f"project:{_PROJECT_ID}"], count=5),
                    _row(
                        [f"project:{_PROJECT_ID}", "agent:a", "outcome:completed"],
                        count=3,
                        latency_sum=300,
                        latency_p50=100,
                        cost_sum=0.3,
                    ),
                ]
            )
        return _metrics_response([])

    async with _make_client(handler) as client:
        stats = await _adapter(client).agent_stats(
            _PROJECT_ID, Period(date(2026, 1, 1), date(2026, 1, 1))
        )

    assert [s.agent_key for s in stats] == ["a"]
    assert stats[0].runs == 3


@pytest.mark.asyncio
async def test_outcome_tags_split_failures_abstained_canceled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = _query_of(request)
        if _classify(query) == "stats":
            return _metrics_response(
                [
                    _row(
                        [f"project:{_PROJECT_ID}", "agent:a", "outcome:completed"],
                        count=10,
                        latency_sum=1000,
                        latency_p50=90,
                        cost_sum=1.0,
                    ),
                    _row(
                        [f"project:{_PROJECT_ID}", "agent:a", "outcome:failed"],
                        count=2,
                        latency_sum=200,
                        latency_p50=110,
                        cost_sum=0.2,
                    ),
                    _row(
                        [f"project:{_PROJECT_ID}", "agent:a", "outcome:abstained"],
                        count=1,
                        latency_sum=50,
                        latency_p50=50,
                        cost_sum=0.05,
                    ),
                    _row(
                        [f"project:{_PROJECT_ID}", "agent:a", "outcome:canceled"],
                        count=1,
                        latency_sum=20,
                        latency_p50=20,
                        cost_sum=0.0,
                    ),
                ]
            )
        return _metrics_response([])

    async with _make_client(handler) as client:
        stats = await _adapter(client).agent_stats(
            _PROJECT_ID, Period(date(2026, 1, 1), date(2026, 1, 1))
        )

    assert len(stats) == 1
    s = stats[0]
    assert s.runs == 14
    assert s.failures == 2
    assert s.abstained == 1
    assert s.canceled == 1
    assert s.latency_total_ms == 1270
    assert s.cost_usd == Decimal("1.25")


@pytest.mark.asyncio
async def test_split_on_row_limit_merges_without_double_count() -> None:
    period = Period(date(2026, 1, 1), date(2026, 1, 4))
    calls: list[Period] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = _query_of(request)
        if _classify(query) != "stats":
            return _metrics_response([])
        from_ts = query["fromTimestamp"]
        to_ts = query["toTimestamp"]
        calls.append((from_ts, to_ts))
        if from_ts == "2026-01-01T00:00:00.000Z" and to_ts == "2026-01-05T00:00:00.000Z":
            # Full period: pretend the cap was hit.
            return _metrics_response(
                [_row([f"project:{_PROJECT_ID}", "agent:a", "outcome:completed"], count=1)] * 1000
            )
        if from_ts == "2026-01-01T00:00:00.000Z" and to_ts == "2026-01-03T00:00:00.000Z":
            return _metrics_response(
                [_row([f"project:{_PROJECT_ID}", "agent:a", "outcome:completed"], count=400)]
            )
        if from_ts == "2026-01-03T00:00:00.000Z" and to_ts == "2026-01-05T00:00:00.000Z":
            return _metrics_response(
                [_row([f"project:{_PROJECT_ID}", "agent:a", "outcome:completed"], count=600)]
            )
        raise AssertionError(f"unexpected sub-period {from_ts}..{to_ts}")

    async with _make_client(handler) as client:
        stats = await _adapter(client).agent_stats(_PROJECT_ID, period)

    assert len(calls) == 3
    assert stats[0].runs == 1000


@pytest.mark.asyncio
async def test_single_day_at_row_limit_returns_partial() -> None:
    period = Period(date(2026, 1, 1), date(2026, 1, 1))
    stats_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal stats_calls
        query = _query_of(request)
        if _classify(query) != "stats":
            return _metrics_response([])
        stats_calls += 1
        return _metrics_response(
            [_row([f"project:{_PROJECT_ID}", "agent:a", "outcome:completed"], count=1)] * 1000
        )

    async with _make_client(handler) as client:
        stats = await _adapter(client).agent_stats(_PROJECT_ID, period)

    assert stats_calls == 1
    assert stats[0].runs == 1000


@pytest.mark.asyncio
async def test_timeout_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    async with _make_client(handler) as client:
        with pytest.raises(RuntimeMetricsUnavailable):
            await _adapter(client).agent_stats(
                _PROJECT_ID, Period(date(2026, 1, 1), date(2026, 1, 1))
            )


@pytest.mark.asyncio
async def test_http_5xx_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    async with _make_client(handler) as client:
        with pytest.raises(RuntimeMetricsUnavailable):
            await _adapter(client).agent_stats(
                _PROJECT_ID, Period(date(2026, 1, 1), date(2026, 1, 1))
            )


@pytest.mark.asyncio
async def test_served_actors_sorted_by_runs_desc() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = _query_of(request)
        kind = _classify(query)
        if kind == "stats":
            return _metrics_response(
                [_row([f"project:{_PROJECT_ID}", "agent:a", "outcome:completed"], count=6)]
            )
        if kind == "served":
            return _metrics_response(
                [
                    _row(
                        [f"project:{_PROJECT_ID}", "agent:a", "outcome:completed"],
                        user_id="u-low",
                        count=1,
                    ),
                    _row(
                        [f"project:{_PROJECT_ID}", "agent:a", "outcome:completed"],
                        user_id="u-high",
                        count=5,
                    ),
                    _row(
                        [f"project:{_PROJECT_ID}", "agent:a", "outcome:completed"],
                        user_id="u-mid-b",
                        count=2,
                    ),
                    _row(
                        [f"project:{_PROJECT_ID}", "agent:a", "outcome:completed"],
                        user_id="u-mid-a",
                        count=2,
                    ),
                ]
            )
        return _metrics_response([])

    async with _make_client(handler) as client:
        stats = await _adapter(client).agent_stats(
            _PROJECT_ID, Period(date(2026, 1, 1), date(2026, 1, 1))
        )

    served = stats[0].served
    assert [s.actor_id for s in served] == ["u-high", "u-mid-a", "u-mid-b", "u-low"]
    assert [s.runs for s in served] == [5, 2, 2, 1]


@pytest.mark.asyncio
async def test_tool_calls_zero_when_no_tool_spans() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = _query_of(request)
        if _classify(query) == "stats":
            return _metrics_response(
                [_row([f"project:{_PROJECT_ID}", "agent:a", "outcome:completed"], count=4)]
            )
        return _metrics_response([])

    async with _make_client(handler) as client:
        stats = await _adapter(client).agent_stats(
            _PROJECT_ID, Period(date(2026, 1, 1), date(2026, 1, 1))
        )

    assert stats[0].tool_calls == 0
    assert stats[0].generation_calls == 0


@pytest.mark.asyncio
async def test_gap_filled_empty_buckets_are_ignored() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = _query_of(request)
        if _classify(query) == "trend":
            return _metrics_response(
                [
                    _row([], count=0, time_dimension="2026-01-01"),
                    _row(
                        [f"project:{_PROJECT_ID}", "agent:a", "outcome:completed"],
                        count=3,
                        latency_sum=90,
                        cost_sum=0.3,
                        time_dimension="2026-01-02",
                    ),
                ]
            )
        return _metrics_response([])

    async with _make_client(handler) as client:
        points = await _adapter(client).trend(
            _PROJECT_ID, Period(date(2026, 1, 1), date(2026, 1, 2)), "day"
        )

    assert len(points) == 1
    assert points[0].bucket_start == date(2026, 1, 2)
    assert points[0].agent_key == "a"
    assert points[0].runs == 3


@pytest.mark.asyncio
async def test_last_runs_queries_traces_api_per_agent() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        agent_tag = next(
            (v for v in request.url.params.get_list("tags") if v.startswith("agent:")), None
        )
        if agent_tag == "agent:a":
            return httpx.Response(
                200, json={"data": [{"timestamp": "2026-10-23T05:57:24.024Z"}]}
            )
        return httpx.Response(200, json={"data": []})

    async with _make_client(handler) as client:
        result = await _adapter(client).last_runs(_PROJECT_ID, ["a", "b"])

    assert len(captured) == 2
    for request in captured:
        tags = request.url.params.get_list("tags")
        assert tags[0] == f"project:{_PROJECT_ID}"
        assert tags[1].startswith("agent:")
        assert request.url.params["orderBy"] == "timestamp.desc"
        assert request.url.params["limit"] == "1"

    assert result == {"a": datetime(2026, 10, 23, 5, 57, 24, 24000, tzinfo=UTC)}
    assert "b" not in result
