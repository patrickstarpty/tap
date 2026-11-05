"""Unit tests for `LangfuseSkillRuntimeMetrics` (Task 9, 2026-11-01 Skill Hub
phase 1). Langfuse HTTP calls are faked with `httpx.MockTransport`; the
Metrics API returns each row's whole `tags` array, which the adapter splits
into one count per `skill:<skill_id>` tag."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from tap.modules.skill_hub.adapters.langfuse_metrics import LangfuseSkillRuntimeMetrics
from tap.modules.skill_hub.ports import SkillRuntimeStats
from tap.platform.reporting.langfuse_metrics_client import LangfuseMetricsClient
from tap.platform.reporting.period import Period
from tap.platform.reporting.runtime import RuntimeMetricsUnavailable

_HOST = "https://langfuse.example.test"
_PROJECT_ID = "proj1"
_DAY = Period(date(2026, 11, 1), date(2026, 11, 1))


def _row(tags: list[str], *, count: int, latency: int, cost: float) -> dict[str, Any]:
    return {
        "tags": tags,
        "count_count": count,
        "sum_latency": latency,
        "sum_totalCost": cost,
    }


def _adapter(client: httpx.AsyncClient) -> LangfuseSkillRuntimeMetrics:
    return LangfuseSkillRuntimeMetrics(
        LangfuseMetricsClient(
            client, host=_HOST, public_key="pk", secret_key="sk", environment="local"
        )
    )


def _serving(rows: list[dict[str, Any]], captured: list[dict[str, Any]]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.url.params["query"]))
        return httpx.Response(200, json={"data": rows})

    return handler


@pytest.mark.asyncio
async def test_skill_stats_split_tag_arrays_per_skill() -> None:
    project = f"project:{_PROJECT_ID}"
    rows = [
        _row([project, "agent:a", "outcome:completed", "skill:s1"], count=3, latency=300, cost=0.3),
        _row([project, "outcome:failed", "skill:s1"], count=1, latency=100, cost=0.1),
        _row([project, "outcome:completed", "skill:s2"], count=2, latency=40, cost=0.05),
        # No `skill:` tag: carries no Skill signal and is dropped.
        _row([project, "agent:a", "outcome:failed"], count=5, latency=500, cost=0.5),
    ]
    captured: list[dict[str, Any]] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_serving(rows, captured))) as client:
        stats = await _adapter(client).skill_stats(_PROJECT_ID, _DAY)

    assert stats == [
        SkillRuntimeStats("s1", runs=4, failures=1, latency_total_ms=400, cost_usd=Decimal("0.4")),
        SkillRuntimeStats("s2", runs=2, failures=0, latency_total_ms=40, cost_usd=Decimal("0.05")),
    ]
    (query,) = captured
    assert query["view"] == "traces"
    assert query["dimensions"] == [{"field": "tags"}]
    assert query["metrics"] == [
        {"measure": "count", "aggregation": "count"},
        {"measure": "latency", "aggregation": "sum"},
        {"measure": "totalCost", "aggregation": "sum"},
    ]
    assert query["filters"][0]["value"] == [project]


@pytest.mark.asyncio
async def test_trace_with_two_skills_counts_once_for_each() -> None:
    rows = [
        _row(
            [f"project:{_PROJECT_ID}", "outcome:failed", "skill:s1", "skill:s2", "skill:s1"],
            count=2,
            latency=50,
            cost=0.02,
        ),
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(_serving(rows, []))) as client:
        stats = await _adapter(client).skill_stats(_PROJECT_ID, _DAY)

    assert stats == [
        SkillRuntimeStats("s1", runs=2, failures=2, latency_total_ms=50, cost_usd=Decimal("0.02")),
        SkillRuntimeStats("s2", runs=2, failures=2, latency_total_ms=50, cost_usd=Decimal("0.02")),
    ]


@pytest.mark.asyncio
async def test_timeout_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeMetricsUnavailable):
            await _adapter(client).skill_stats(_PROJECT_ID, _DAY)


@pytest.mark.asyncio
async def test_malformed_row_raises_unavailable() -> None:
    rows = [{"tags": ["skill:s1"], "count_count": "not-a-number"}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(_serving(rows, []))) as client:
        with pytest.raises(RuntimeMetricsUnavailable):
            await _adapter(client).skill_stats(_PROJECT_ID, _DAY)
