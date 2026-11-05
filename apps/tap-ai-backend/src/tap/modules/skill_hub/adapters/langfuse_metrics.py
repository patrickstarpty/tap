"""`SkillRuntimeMetrics` backed by the Langfuse Metrics API v1 (spec 3).

Only Turn-root traces (`turn.request`) carry `skill:<skill_id>` tags (one per
Skill the run used) next to `outcome:<state>`. The `tags`-dimension query
returns each distinct tag array as one row, so a row is split into one
contribution per distinct `skill:` tag in it: a trace that used two Skills
counts once for each. Rows whose tag array carries `outcome:failed` add to
`failures`. Rows without a `skill:` tag carry no Skill signal and are dropped.

The client issues one request per (bisected) sub-period and has no whole-call
timeout: callers must go through `CachedSkillRuntimeMetrics`, whose per-fetch
cap bounds the call.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from tap.modules.skill_hub.ports import SkillRuntimeStats
from tap.platform.reporting.langfuse_metrics_client import LangfuseMetricsClient
from tap.platform.reporting.period import Period
from tap.platform.reporting.runtime import RuntimeMetricsUnavailable

_SKILL_PREFIX = "skill:"
_FAILED_TAG = "outcome:failed"
_METRICS = (
    {"measure": "count", "aggregation": "count"},
    {"measure": "latency", "aggregation": "sum"},
    {"measure": "totalCost", "aggregation": "sum"},
)
# Malformed response bodies are a backend-contract break, mapped to
# `RuntimeMetricsUnavailable` same as a network failure.
_PARSE_ERRORS = (InvalidOperation, ValueError, AttributeError, TypeError, KeyError)


def _as_number(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        return Decimal(0)
    if isinstance(value, int):
        return Decimal(value)
    return Decimal(str(value))


def _skill_ids(tags: Sequence[Any]) -> list[str]:
    """Distinct, non-empty Skill ids from a row's tag array, in tag order."""
    ids: dict[str, None] = {}
    for tag in tags:
        if isinstance(tag, str) and tag.startswith(_SKILL_PREFIX) and len(tag) > len(_SKILL_PREFIX):
            ids.setdefault(tag[len(_SKILL_PREFIX) :], None)
    return list(ids)


class LangfuseSkillRuntimeMetrics:
    def __init__(self, client: LangfuseMetricsClient) -> None:
        self._client = client

    async def skill_stats(self, project_id: str, period: Period) -> list[SkillRuntimeStats]:
        """Per-Skill runs, failures, latency sum (ms) and cost over `period`,
        ordered by `skill_id`. Raises `RuntimeMetricsUnavailable` on a
        transport failure or a malformed response."""
        rows = await self._client.rows(
            project_id=project_id,
            view="traces",
            dimensions=["tags"],
            metrics=_METRICS,
            period=period,
        )
        try:
            return _aggregate(rows)
        except _PARSE_ERRORS as error:
            raise RuntimeMetricsUnavailable() from error


def _aggregate(rows: list[dict[str, Any]]) -> list[SkillRuntimeStats]:
    runs: dict[str, int] = {}
    failures: dict[str, int] = {}
    latency: dict[str, int] = {}
    cost: dict[str, Decimal] = {}
    for row in rows:
        tags = row.get("tags")
        if not isinstance(tags, list):
            continue
        skill_ids = _skill_ids(tags)
        if not skill_ids:
            continue
        count = int(_as_number(row.get("count_count")))
        latency_sum = int(_as_number(row.get("sum_latency")))
        cost_sum = _as_number(row.get("sum_totalCost"))
        failed = _FAILED_TAG in tags
        for skill_id in skill_ids:
            runs[skill_id] = runs.get(skill_id, 0) + count
            failures[skill_id] = failures.get(skill_id, 0) + (count if failed else 0)
            latency[skill_id] = latency.get(skill_id, 0) + latency_sum
            cost[skill_id] = cost.get(skill_id, Decimal(0)) + cost_sum
    return [
        SkillRuntimeStats(
            skill_id=skill_id,
            runs=runs[skill_id],
            failures=failures[skill_id],
            latency_total_ms=latency[skill_id],
            cost_usd=cost[skill_id],
        )
        for skill_id in sorted(runs)
    ]
