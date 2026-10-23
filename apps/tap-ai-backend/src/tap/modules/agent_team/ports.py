"""Ports for runtime (Langfuse-backed) Agent metrics (spec sections 2.2/2.5/6)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from tap.modules.agent_team.domain.models import Period


class RuntimeMetricsUnavailable(Exception):
    """The runtime metrics backend (Langfuse) could not answer the query."""


@dataclass(frozen=True, slots=True)
class ServedActor:
    actor_id: str
    runs: int


@dataclass(frozen=True, slots=True)
class AgentRuntimeStats:
    agent_key: str
    runs: int
    failures: int
    abstained: int
    canceled: int
    latency_total_ms: int
    latency_p50_ms: int | None
    cost_usd: Decimal
    # Sorted by `runs` descending, then `actor_id` ascending.
    served: tuple[ServedActor, ...]
    generation_calls: int
    tool_calls: int


@dataclass(frozen=True, slots=True)
class TrendPoint:
    bucket_start: date
    agent_key: str
    runs: int
    failures: int
    latency_total_ms: int
    cost_usd: Decimal


class AgentRuntimeMetrics(Protocol):
    """Runtime (Langfuse) metrics for the Agent team view. Raises
    `RuntimeMetricsUnavailable` when the backend cannot be queried."""

    async def agent_stats(self, project_id: str, period: Period) -> list[AgentRuntimeStats]: ...

    async def trend(
        self, project_id: str, period: Period, granularity: str
    ) -> list[TrendPoint]: ...

    async def last_runs(
        self, project_id: str, agent_keys: Sequence[str]
    ) -> dict[str, datetime]:
        """The timestamp of the most recent trace for each Agent, regardless of
        period. Agent keys with no trace are absent from the result."""
        ...
