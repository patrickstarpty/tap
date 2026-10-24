"""HTTP contract for the Agent team view (Task 8, spec section 5/5.1)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.agent_team.adapters.cached_metrics import CachedRuntimeMetrics
from tap.modules.agent_team.application.service import AgentTeamService
from tap.modules.agent_team.domain.models import MaintenanceEvent, MaintenanceKind, Period
from tap.modules.agent_team.ports import (
    AgentRecord,
    AgentRuntimeStats,
    FeedbackNotFound,
    HistoryCursorInvalid,
    HistoryPage,
    PendingFeedback,
    RatingCounts,
    ResolvedFeedback,
    RuntimeMetricsUnavailable,
)

_NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def _agent(
    agent_id: str,
    *,
    owner_actor_id: str = "owner-1",
    source_kind: str = "authored",
    enabled: bool = True,
    job_title: str | None = None,
) -> AgentRecord:
    return AgentRecord(
        agent_id=agent_id,
        name=f"Agent {agent_id}",
        job_title=job_title,
        owner_actor_id=owner_actor_id,
        owner_assigned_at=_NOW - timedelta(days=10),
        created_at=_NOW - timedelta(days=100),
        source_kind=source_kind,
        enabled=enabled,
        current_revision_id=f"{agent_id}-rev-1",
        current_revision_number=1,
    )


def _stats(agent_key: str, runs: int = 3) -> AgentRuntimeStats:
    return AgentRuntimeStats(
        agent_key=agent_key,
        runs=runs,
        failures=0,
        abstained=0,
        canceled=0,
        latency_total_ms=runs * 10,
        latency_p50_ms=10,
        cost_usd=Decimal("1.00") * runs,
        served=(),
        generation_calls=0,
        tool_calls=0,
    )


@dataclass
class FakeMetrics:
    stats: list[AgentRuntimeStats] = field(default_factory=list)
    unavailable: bool = False

    async def agent_stats(self, project_id: str, period: Period) -> list[AgentRuntimeStats]:
        del project_id, period
        if self.unavailable:
            raise RuntimeMetricsUnavailable("boom")
        return list(self.stats)

    async def trend(self, project_id: str, period: Period, granularity: str) -> list:
        del project_id, period, granularity
        if self.unavailable:
            raise RuntimeMetricsUnavailable("boom")
        return []

    async def last_runs(self, project_id: str, agent_keys) -> dict:
        del project_id, agent_keys
        if self.unavailable:
            raise RuntimeMetricsUnavailable("boom")
        return {}


@dataclass
class FakeStore:
    agent_records: dict[str, AgentRecord] = field(default_factory=dict)
    pending: list[PendingFeedback] = field(default_factory=list)
    existing_actors: set[str] = field(default_factory=set)
    resolved: dict[str, ResolvedFeedback] = field(default_factory=dict)
    events: list[MaintenanceEvent] = field(default_factory=list)

    async def agents(self) -> list[AgentRecord]:
        return list(self.agent_records.values())

    async def agent(self, agent_id: str) -> AgentRecord | None:
        return self.agent_records.get(agent_id)

    async def ratings(self, period: Period) -> list[RatingCounts]:
        del period
        return []

    async def pending_feedback(self, agent_key: str | None = None) -> list[PendingFeedback]:
        if agent_key is None:
            return list(self.pending)
        return [item for item in self.pending if item.agent_key == agent_key]

    async def maintenance_counts(
        self, period: Period
    ) -> list[tuple[str, str, MaintenanceKind, int]]:
        del period
        return []

    async def history(self, agent_id: str, cursor: str | None, limit: int = 50) -> HistoryPage:
        del agent_id, limit
        if cursor == "bad-cursor":
            raise HistoryCursorInvalid("bad cursor")
        return HistoryPage(events=(), next_cursor=None)

    async def last_maintenance(self) -> dict[str, datetime]:
        return {}

    async def active_actors(self, period: Period) -> set[str]:
        del period
        return set()

    async def sync_lag(self) -> timedelta | None:
        return None

    async def actor_exists(self, actor_id: str) -> bool:
        return actor_id in self.existing_actors

    async def set_job_title(self, agent_id: str, job_title: str | None) -> AgentRecord:
        record = self.agent_records[agent_id]
        updated = replace(record, job_title=job_title)
        self.agent_records[agent_id] = updated
        return updated

    async def transfer_owner(
        self, agent_id: str, new_owner: str, event: MaintenanceEvent
    ) -> AgentRecord:
        record = self.agent_records[agent_id]
        updated = replace(record, owner_actor_id=new_owner)
        self.agent_records[agent_id] = updated
        self.events.append(event)
        return updated

    async def resolve_feedback(
        self, feedback_id, actor_id, note, event_factory
    ) -> ResolvedFeedback:
        known = {item.feedback_id for item in self.pending}
        if feedback_id not in known and feedback_id not in self.resolved:
            raise FeedbackNotFound(feedback_id)
        if feedback_id in self.resolved:
            return self.resolved[feedback_id]
        item = next(i for i in self.pending if i.feedback_id == feedback_id)
        event = event_factory(item.agent_key)
        self.events.append(event)
        self.pending = [i for i in self.pending if i.feedback_id != feedback_id]
        resolved = ResolvedFeedback(
            feedback_id=item.feedback_id,
            agent_key=item.agent_key,
            turn_id=item.turn_id,
            conversation_id=item.conversation_id,
            actor_id=item.actor_id,
            rating=item.rating,
            comment=item.comment,
            updated_at=item.updated_at,
            resolved_at=_NOW,
            resolved_by=actor_id,
            resolution_note=note,
        )
        self.resolved[feedback_id] = resolved
        return resolved


class _Authority:
    def __init__(self, allowed: frozenset[str] | None = None) -> None:
        self._allowed = allowed or frozenset({"ai.agent_team.read", "ai.agents.write"})

    async def current(self, _facts):
        return VALIDATION_SCOPE

    async def authorize(self, _scope, action, _resource):
        if action not in self._allowed:
            return AuthorizationDecision(False, "action-not-allowed")
        return AuthorizationDecision(True, "validation-allowed")


ORIGIN = "http://127.0.0.1:15176"
BASE = "/api/v1/projects/tapper-demo/ai"


def _app(
    *,
    allowed: frozenset[str] | None = None,
    store: FakeStore | None = None,
    metrics: FakeMetrics | None = None,
    check_sync_lag: bool = False,
) -> tuple[TestClient, FakeStore, FakeMetrics]:
    store = store or FakeStore()
    metrics = metrics or FakeMetrics()
    authority = _Authority(allowed)
    service = AgentTeamService(
        store,
        CachedRuntimeMetrics(metrics, now=lambda: _NOW),
        scope=VALIDATION_SCOPE,
        clock=lambda: _NOW,
        check_sync_lag=check_sync_lag,
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
    client = TestClient(app, headers={"Origin": ORIGIN})
    return client, store, metrics


def _seeded_store() -> FakeStore:
    return FakeStore(
        agent_records={
            "agent-1": _agent("agent-1", owner_actor_id="owner-1"),
            "builtin-1": _agent("builtin-1", owner_actor_id="owner-1", source_kind="built_in"),
        },
        existing_actors={"owner-1", "owner-2"},
        pending=[
            PendingFeedback(
                feedback_id="fb-1",
                agent_key="agent-1",
                turn_id="turn-1",
                conversation_id="conv-1",
                actor_id="owner-2",
                rating="down",
                comment=None,
                updated_at=_NOW,
            )
        ],
    )


PERIOD_QS = "from=2026-10-01&to=2026-10-23"


# -- reads --------------------------------------------------------------


def test_get_summary_returns_team_view_envelope() -> None:
    client, _store, _metrics = _app(
        store=_seeded_store(), metrics=FakeMetrics(stats=[_stats("agent-1")])
    )

    response = client.get(f"{BASE}/agent-team/summary?{PERIOD_QS}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runtimeStatus"] == "ok"
    assert body["period"] == {"from": "2026-10-01", "to": "2026-10-23"}
    assert body["data"]["agentTotal"] == 2


def test_list_members_returns_roster() -> None:
    client, _store, _metrics = _app(
        store=_seeded_store(), metrics=FakeMetrics(stats=[_stats("agent-1")])
    )

    response = client.get(f"{BASE}/agent-team/members?{PERIOD_QS}")

    assert response.status_code == 200, response.text
    keys = {row["agentKey"] for row in response.json()["data"]}
    assert "agent-1" in keys


def test_get_member_returns_member_row() -> None:
    client, _store, _metrics = _app(
        store=_seeded_store(), metrics=FakeMetrics(stats=[_stats("agent-1")])
    )

    response = client.get(f"{BASE}/agent-team/members/agent-1?{PERIOD_QS}")

    assert response.status_code == 200, response.text
    detail = response.json()["data"]
    assert detail["currentRevisionId"] is not None
    assert isinstance(detail["served"], list)
    assert isinstance(detail["pendingFeedback"], list)
    data = detail["row"]
    assert data["agentKey"] == "agent-1"
    # Spec 2.2 secondary metrics: present on every member row.
    assert data["generationCalls"] == 0
    assert data["toolCalls"] == 0
    assert data["callsPerRun"] == 0.0


def test_get_member_history_returns_page() -> None:
    client, _store, _metrics = _app(store=_seeded_store())

    response = client.get(f"{BASE}/agent-team/members/agent-1/history")

    assert response.status_code == 200, response.text
    assert response.json() == {"events": [], "nextCursor": None}


def test_list_people_returns_rows() -> None:
    client, _store, _metrics = _app(
        store=_seeded_store(), metrics=FakeMetrics(stats=[_stats("agent-1")])
    )

    response = client.get(f"{BASE}/agent-team/people?{PERIOD_QS}")

    assert response.status_code == 200, response.text
    assert isinstance(response.json()["data"], list)


def test_get_person_returns_detail() -> None:
    client, _store, _metrics = _app(
        store=_seeded_store(), metrics=FakeMetrics(stats=[_stats("agent-1")])
    )

    response = client.get(f"{BASE}/agent-team/people/owner-1?{PERIOD_QS}")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["person"]["actorId"] == "owner-1"


def test_get_org_chart_returns_nodes_and_edges() -> None:
    client, _store, _metrics = _app(
        store=_seeded_store(), metrics=FakeMetrics(stats=[_stats("agent-1")])
    )

    response = client.get(f"{BASE}/agent-team/org-chart?{PERIOD_QS}")

    assert response.status_code == 200, response.text
    assert "agent-1" in response.json()["data"]["agents"]


def test_get_report_returns_current_and_previous() -> None:
    client, _store, _metrics = _app(
        store=_seeded_store(), metrics=FakeMetrics(stats=[_stats("agent-1")])
    )

    response = client.get(f"{BASE}/agent-team/report?{PERIOD_QS}")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert "current" in data and "previous" in data
    # 2026-10-01..2026-10-23 is month-start to a non-month-end: same span of last month.
    assert data["previousPeriod"] == {"from": "2026-09-01", "to": "2026-09-23"}


def test_export_returns_a_csv_zip() -> None:
    import zipfile
    from io import BytesIO

    client, _store, _metrics = _app(
        store=_seeded_store(), metrics=FakeMetrics(stats=[_stats("agent-1")])
    )

    response = client.get(f"{BASE}/agent-team/export?{PERIOD_QS}")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        'attachment; filename="agent-team-2026-10-01-2026-10-23.zip"'
    )
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"roster.csv", "people.csv", "trend.csv"}


# -- writes ---------------------------------------------------------------


def test_update_agent_profile_returns_member_row() -> None:
    client, _store, _metrics = _app(store=_seeded_store())

    response = client.put(f"{BASE}/agents/agent-1/profile", json={"jobTitle": "QA 负责人"})

    assert response.status_code == 200, response.text
    assert response.json()["jobTitle"] == "QA 负责人"


def test_transfer_agent_owner_returns_member_row() -> None:
    client, _store, _metrics = _app(store=_seeded_store())

    response = client.post(f"{BASE}/agents/agent-1/owner", json={"newOwnerActorId": "owner-2"})

    assert response.status_code == 200, response.text
    assert response.json()["ownerActorId"] == "owner-2"


def test_resolve_feedback_returns_resolved_feedback() -> None:
    client, _store, _metrics = _app(store=_seeded_store())

    response = client.post(f"{BASE}/feedback/fb-1/resolve", json={"note": "已跟进"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resolvedBy"] == VALIDATION_SCOPE.actor_id
    assert body["resolutionNote"] == "已跟进"


# -- errors (spec 5.1 / controller rulings) -------------------------------


def test_reversed_period_returns_period_invalid() -> None:
    client, _store, _metrics = _app(store=_seeded_store())

    response = client.get(f"{BASE}/agent-team/summary?from=2026-10-23&to=2026-10-01")

    assert response.status_code == 400
    assert response.json()["code"] == "period_invalid"


def test_transfer_to_missing_actor_returns_actor_not_found() -> None:
    client, _store, _metrics = _app(store=_seeded_store())

    response = client.post(f"{BASE}/agents/agent-1/owner", json={"newOwnerActorId": "ghost"})

    assert response.status_code == 400
    assert response.json()["code"] == "actor_not_found"


def test_builtin_profile_returns_agent_read_only() -> None:
    client, _store, _metrics = _app(store=_seeded_store())

    response = client.put(f"{BASE}/agents/builtin-1/profile", json={"jobTitle": "x"})

    assert response.status_code == 403
    assert response.json()["code"] == "agent_read_only"


def test_feedback_not_found_returns_404() -> None:
    client, _store, _metrics = _app(store=_seeded_store())

    response = client.post(f"{BASE}/feedback/ghost/resolve", json={})

    assert response.status_code == 404
    assert response.json()["code"] == "feedback_not_found"


def test_reads_require_agent_team_read() -> None:
    client, _store, _metrics = _app(allowed=frozenset({"ai.agents.write"}), store=_seeded_store())

    response = client.get(f"{BASE}/agent-team/summary?{PERIOD_QS}")

    assert response.status_code == 403


def test_writes_require_agents_write() -> None:
    client, _store, _metrics = _app(
        allowed=frozenset({"ai.agent_team.read"}), store=_seeded_store()
    )

    response = client.put(f"{BASE}/agents/agent-1/profile", json={"jobTitle": "x"})

    assert response.status_code == 403


def test_langfuse_down_returns_200_unavailable() -> None:
    client, _store, _metrics = _app(store=_seeded_store(), metrics=FakeMetrics(unavailable=True))

    response = client.get(f"{BASE}/agent-team/summary?{PERIOD_QS}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runtimeStatus"] == "unavailable"
    # Controller ruling (Task 9 review, fix round 1): run-derived summary
    # fields are `null` -- never `0` -- over the wire during an outage.
    assert body["data"]["active"] is None
    assert body["data"]["idle"] is None
    assert body["data"]["runs"] is None
    assert body["data"]["latencyTotalMs"] is None
    assert body["data"]["costUsd"] is None


def test_langfuse_down_leaves_member_run_fields_null() -> None:
    client, _store, _metrics = _app(store=_seeded_store(), metrics=FakeMetrics(unavailable=True))

    response = client.get(f"{BASE}/agent-team/members/agent-1?{PERIOD_QS}")

    assert response.status_code == 200, response.text
    data = response.json()["data"]["row"]
    for key in (
        "runs",
        "failures",
        "abstained",
        "canceled",
        "latencyTotalMs",
        "latencyP50Ms",
        "costUsd",
        "servedCount",
        "lastRunAt",
        "generationCalls",
        "toolCalls",
        "callsPerRun",
    ):
        assert data[key] is None, key
    assert data["topServed"] == []
    assert data["trend"] == []


def test_history_bad_cursor_returns_cursor_invalid() -> None:
    client, _store, _metrics = _app(store=_seeded_store())

    response = client.get(f"{BASE}/agent-team/members/agent-1/history?cursor=bad-cursor")

    assert response.status_code == 400
    assert response.json()["code"] == "cursor_invalid"


def test_history_unknown_agent_returns_agent_not_found() -> None:
    client, _store, _metrics = _app(store=_seeded_store())

    response = client.get(f"{BASE}/agent-team/members/ghost/history")

    assert response.status_code == 404
    assert response.json()["code"] == "agent_not_found"


def test_get_person_me_resolves_to_the_caller() -> None:
    client, _store, _metrics = _app(store=_seeded_store())

    response = client.get(f"{BASE}/agent-team/people/me?{PERIOD_QS}")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["person"]["actorId"] == VALIDATION_SCOPE.actor_id
