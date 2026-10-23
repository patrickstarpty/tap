"""Explicitly opted-in smoke for `LangfuseRuntimeMetrics` against a real local
Langfuse instance. Exists to catch API-shape drift after a Langfuse upgrade
(see `global-constraints.md` in the 2026-10-23 agent team view SDD folder for
the behaviour this adapter was verified against on 3.225.11).

Requires `TAP_RUN_LANGFUSE_SMOKE=1` plus `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY` and `TAP_LANGFUSE_SMOKE_PROJECT_ID` (the Langfuse project
id to query; it must have some runtime traffic with `agent:` tags in the
trailing 30 days). `TAP_LANGFUSE_ENVIRONMENT` is optional, default `local`.
"""

from __future__ import annotations

import os
from datetime import date

import httpx
import pytest

from tap.modules.agent_team.adapters.langfuse_metrics import LangfuseRuntimeMetrics
from tap.modules.agent_team.domain.models import trailing_30_days


@pytest.mark.asyncio
async def test_agent_stats_and_last_runs_against_real_langfuse() -> None:
    if os.environ.get("TAP_RUN_LANGFUSE_SMOKE") != "1":
        pytest.skip("real Langfuse smoke requires explicit opt-in")

    host = os.environ["LANGFUSE_HOST"]
    public_key = os.environ["LANGFUSE_PUBLIC_KEY"]
    secret_key = os.environ["LANGFUSE_SECRET_KEY"]
    environment = os.environ.get("TAP_LANGFUSE_ENVIRONMENT", "local")
    project_id = os.environ["TAP_LANGFUSE_SMOKE_PROJECT_ID"]

    async with httpx.AsyncClient() as client:
        metrics = LangfuseRuntimeMetrics(
            client,
            host=host,
            public_key=public_key,
            secret_key=secret_key,
            environment=environment,
        )

        period = trailing_30_days(date.today())
        stats = await metrics.agent_stats(project_id, period)
        assert stats, "expected at least one agent: key with traffic in this period"
        for agent_stat in stats:
            assert agent_stat.runs >= agent_stat.failures >= 0
            assert agent_stat.runs >= agent_stat.abstained >= 0
            assert agent_stat.runs >= agent_stat.canceled >= 0
            assert agent_stat.latency_total_ms >= 0

        last_runs = await metrics.last_runs(project_id, [stats[0].agent_key])
        assert stats[0].agent_key in last_runs

        trend = await metrics.trend(project_id, period, period.granularity)
        assert any(point.agent_key == stats[0].agent_key for point in trend), (
            "expected at least one trend point for the Agent with traffic"
        )
