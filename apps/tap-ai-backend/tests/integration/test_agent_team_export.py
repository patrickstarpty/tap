"""Integration test for the Agent team CSV zip export (Task 9, spec section
5): the full route -> `AgentTeamService.export_data` -> `build_export` path,
exercised through a real HTTP download, confirming a Chinese Agent name
round-trips through the zip/CSV/BOM pipeline without mangling.

Uses fakes for the MySQL store and Langfuse metrics (same style as the
Task 8 HTTP contract test) -- no real MySQL or Langfuse involved; this test
is about the pipeline wiring, not persistence.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.agent_team.adapters.cached_metrics import CachedRuntimeMetrics
from tap.modules.agent_team.application.service import AgentTeamService
from tap.modules.agent_team.domain.models import MaintenanceKind, Period
from tap.modules.agent_team.ports import (
    AgentRecord,
    AgentRuntimeStats,
    FeedbackNotFound,
    HistoryPage,
    PendingFeedback,
    RatingCounts,
    ServedActor,
    TrendPoint,
)

_NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
_AGENT_NAME = "客服小助手"


def _agent(agent_id: str) -> AgentRecord:
    return AgentRecord(
        agent_id=agent_id,
        name=_AGENT_NAME,
        job_title="QA 负责人",
        owner_actor_id="owner-1",
        owner_assigned_at=_NOW - timedelta(days=10),
        created_at=_NOW - timedelta(days=100),
        source_kind="authored",
        enabled=True,
        current_revision_id=f"{agent_id}-rev-1",
        current_revision_number=1,
    )


def _stats(agent_key: str) -> AgentRuntimeStats:
    return AgentRuntimeStats(
        agent_key=agent_key,
        runs=5,
        failures=1,
        abstained=0,
        canceled=0,
        latency_total_ms=500,
        latency_p50_ms=100,
        cost_usd=Decimal("5.00"),
        served=(ServedActor(actor_id="owner-1", runs=5),),
        generation_calls=0,
        tool_calls=0,
    )


@dataclass
class FakeMetrics:
    stats: list[AgentRuntimeStats] = field(default_factory=list)

    async def agent_stats(self, project_id: str, period: Period) -> list[AgentRuntimeStats]:
        del project_id, period
        return list(self.stats)

    async def trend(self, project_id: str, period: Period, granularity: str) -> list[TrendPoint]:
        del project_id, granularity
        return [
            TrendPoint(
                bucket_start=period.start,
                agent_key=stat.agent_key,
                runs=stat.runs,
                failures=stat.failures,
                latency_total_ms=stat.latency_total_ms,
                cost_usd=stat.cost_usd,
            )
            for stat in self.stats
        ]

    async def last_runs(self, project_id: str, agent_keys: Sequence[str]) -> dict[str, datetime]:
        del project_id, agent_keys
        return {}


@dataclass
class FakeStore:
    agent_records: dict[str, AgentRecord] = field(default_factory=dict)

    async def agents(self) -> list[AgentRecord]:
        return list(self.agent_records.values())

    async def agent(self, agent_id: str) -> AgentRecord | None:
        return self.agent_records.get(agent_id)

    async def ratings(self, period: Period) -> list[RatingCounts]:
        del period
        return []

    async def pending_feedback(self, agent_key: str | None = None) -> list[PendingFeedback]:
        del agent_key
        return []

    async def maintenance_counts(
        self, period: Period
    ) -> list[tuple[str, str, MaintenanceKind, int]]:
        del period
        return [("agent-1", "owner-1", "revised", 2)]

    async def history(self, agent_id: str, cursor: str | None, limit: int = 50) -> HistoryPage:
        del agent_id, cursor, limit
        return HistoryPage(events=(), next_cursor=None)

    async def last_maintenance(self) -> dict[str, datetime]:
        return {"agent-1": _NOW}

    async def active_actors(self, period: Period) -> set[str]:
        del period
        return set()

    async def sync_lag(self) -> timedelta | None:
        return None

    async def actor_exists(self, actor_id: str) -> bool:
        return True

    async def set_job_title(self, agent_id: str, job_title: str | None) -> AgentRecord:
        raise NotImplementedError

    async def transfer_owner(self, agent_id: str, new_owner: str, event) -> AgentRecord:
        raise NotImplementedError

    async def resolve_feedback(self, feedback_id, actor_id, note, event_factory):
        raise FeedbackNotFound(feedback_id)


class _Authority:
    async def current(self, _facts):
        return VALIDATION_SCOPE

    async def authorize(self, _scope, action, _resource):
        del action
        return AuthorizationDecision(True, "validation-allowed")


ORIGIN = "http://127.0.0.1:15176"
BASE = "/api/v1/projects/tapper-demo/ai"


def _client() -> TestClient:
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")})
    metrics = FakeMetrics(stats=[_stats("agent-1")])
    authority = _Authority()
    service = AgentTeamService(
        store,
        CachedRuntimeMetrics(metrics, now=lambda: _NOW),
        scope=VALIDATION_SCOPE,
        clock=lambda: _NOW,
    )
    app = create_app(
        HttpServices(
            agent_team=service,
            scope=VALIDATION_SCOPE,
            scope_provider=authority,
            authorization_policy=authority,
        ),
        allowed_origins=frozenset({ORIGIN}),
    )
    return TestClient(app, headers={"Origin": ORIGIN})


def test_downloaded_zip_round_trips_chinese_agent_name() -> None:
    client = _client()

    response = client.get(f"{BASE}/agent-team/export?from=2026-10-01&to=2026-10-23")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        'attachment; filename="agent-team-2026-10-01-2026-10-23.zip"'
    )

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"roster.csv", "people.csv", "trend.csv"}

        roster_raw = archive.read("roster.csv")
        assert roster_raw.startswith(b"\xef\xbb\xbf")
        roster_rows = list(csv.reader(io.StringIO(roster_raw.decode("utf-8-sig"))))
        header, row = roster_rows[0], roster_rows[1]
        assert row[header.index("name")] == _AGENT_NAME
        assert row[header.index("job_title")] == "QA 负责人"

        trend_text = archive.read("trend.csv").decode("utf-8-sig")
        trend_lines = trend_text.splitlines()
        assert trend_lines[0] == "# granularity=day"
        # The trend point `FakeMetrics.trend` returns for `agent-1` must
        # actually reach `trend.csv`'s data rows, not just its header.
        trend_rows = list(csv.reader(trend_lines[1:]))
        trend_header, trend_data = trend_rows[0], trend_rows[1:]
        assert [row[trend_header.index("agent_key")] for row in trend_data] == ["agent-1"]
        assert trend_data[0][trend_header.index("runs")] == "5"
