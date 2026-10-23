"""Unit tests for `AgentTeamService` (Task 7, 2026-10-23 agent team view),
against a fake store and a fake runtime metrics backend -- no MySQL or
Langfuse involved."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.agent_team.adapters.cached_metrics import CachedRuntimeMetrics
from tap.modules.agent_team.application.service import AgentTeamRejected, AgentTeamService
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
    ServedActor,
    TrendPoint,
)

_PROJECT_ID = "proj-1"
_NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def _scope(actor_id: str = "actor-me") -> ProjectScopeContext:
    return ProjectScopeContext(
        enterprise_id="ent-1",
        project_id=_PROJECT_ID,
        actor_id=actor_id,
        identity_mode=IdentityMode.PRODUCT,
    )


def _clock() -> datetime:
    return _NOW


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


def _stats(agent_key: str, runs: int, served: tuple[ServedActor, ...] = ()) -> AgentRuntimeStats:
    return AgentRuntimeStats(
        agent_key=agent_key,
        runs=runs,
        failures=0,
        abstained=0,
        canceled=0,
        latency_total_ms=runs * 10,
        latency_p50_ms=None,
        cost_usd=Decimal("1.00") * runs,
        served=served,
        generation_calls=0,
        tool_calls=0,
    )


@dataclass
class FakeMetrics:
    stats_by_period: dict[tuple[date, date], list[AgentRuntimeStats]] = field(default_factory=dict)
    unavailable: bool = False
    agent_stats_calls: int = 0

    async def agent_stats(self, project_id: str, period: Period) -> list[AgentRuntimeStats]:
        self.agent_stats_calls += 1
        if self.unavailable:
            raise RuntimeMetricsUnavailable("boom")
        return self.stats_by_period.get((period.start, period.end), [])

    async def trend(self, project_id: str, period: Period, granularity: str) -> list[TrendPoint]:
        if self.unavailable:
            raise RuntimeMetricsUnavailable("boom")
        return []

    async def last_runs(self, project_id: str, agent_keys: Sequence[str]) -> dict[str, datetime]:
        if self.unavailable:
            raise RuntimeMetricsUnavailable("boom")
        return {}


@dataclass
class FakeStore:
    agent_records: dict[str, AgentRecord] = field(default_factory=dict)
    ratings_by_key: dict[str, RatingCounts] = field(default_factory=dict)
    pending: list[PendingFeedback] = field(default_factory=list)
    maintenance_rows: list[tuple[str, str, MaintenanceKind, int]] = field(default_factory=list)
    last_maintenance_by_agent: dict[str, datetime] = field(default_factory=dict)
    active_actor_set: set[str] = field(default_factory=set)
    existing_actors: set[str] = field(default_factory=set)
    lag: timedelta | None = None
    resolved: dict[str, ResolvedFeedback] = field(default_factory=dict)
    events: list[MaintenanceEvent] = field(default_factory=list)

    async def agents(self) -> list[AgentRecord]:
        return list(self.agent_records.values())

    async def agent(self, agent_id: str) -> AgentRecord | None:
        return self.agent_records.get(agent_id)

    async def ratings(self, period: Period) -> list[RatingCounts]:
        return list(self.ratings_by_key.values())

    async def pending_feedback(self, agent_key: str | None = None) -> list[PendingFeedback]:
        if agent_key is None:
            return list(self.pending)
        return [item for item in self.pending if item.agent_key == agent_key]

    async def maintenance_counts(
        self, period: Period
    ) -> list[tuple[str, str, MaintenanceKind, int]]:
        return list(self.maintenance_rows)

    async def history(self, agent_id: str, cursor: str | None, limit: int = 50) -> HistoryPage:
        if cursor == "bad-cursor":
            raise HistoryCursorInvalid("bad cursor")
        return HistoryPage(events=(), next_cursor=None)

    async def last_maintenance(self) -> dict[str, datetime]:
        return dict(self.last_maintenance_by_agent)

    async def active_actors(self, period: Period) -> set[str]:
        return set(self.active_actor_set)

    async def sync_lag(self) -> timedelta | None:
        return self.lag

    async def actor_exists(self, actor_id: str) -> bool:
        return actor_id in self.existing_actors

    async def set_job_title(self, agent_id: str, job_title: str | None) -> AgentRecord:
        record = self.agent_records[agent_id]
        updated = AgentRecord(**{**record.__dict__, "job_title": job_title})
        self.agent_records[agent_id] = updated
        return updated

    async def transfer_owner(
        self, agent_id: str, new_owner: str, event: MaintenanceEvent
    ) -> AgentRecord:
        record = self.agent_records[agent_id]
        updated = AgentRecord(**{**record.__dict__, "owner_actor_id": new_owner})
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


_PERIOD = Period(date(2026, 10, 1), date(2026, 10, 23))


def _service(
    store: FakeStore, metrics: FakeMetrics, *, check_sync_lag: bool = False
) -> AgentTeamService:
    cached = CachedRuntimeMetrics(metrics, now=lambda: _NOW)
    return AgentTeamService(
        store, cached, scope=_scope(), clock=_clock, check_sync_lag=check_sync_lag
    )


@pytest.mark.asyncio
async def test_unavailable_runtime_keeps_roster_and_satisfaction():
    store = FakeStore(
        agent_records={"agent-1": _agent("agent-1")},
        ratings_by_key={"agent-1": RatingCounts(agent_key="agent-1", up=8, down=2)},
    )
    metrics = FakeMetrics(unavailable=True)
    service = _service(store, metrics)
    view = await service.members(_PERIOD)
    assert view.runtime_status == "unavailable"
    assert len(view.data) == 1
    row = view.data[0]
    assert row.runs == 0
    assert row.satisfaction == 0.8
    assert row.owner_actor_id == "owner-1"


@pytest.mark.asyncio
async def test_transfer_to_current_owner_is_noop():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1", owner_actor_id="owner-1")})
    service = _service(store, FakeMetrics())
    record = await service.transfer_owner("agent-1", "owner-1")
    assert record.owner_actor_id == "owner-1"
    assert store.events == []


@pytest.mark.asyncio
async def test_transfer_to_unknown_actor_rejected():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1", owner_actor_id="owner-1")})
    service = _service(store, FakeMetrics())
    with pytest.raises(AgentTeamRejected) as exc_info:
        await service.transfer_owner("agent-1", "ghost")
    assert exc_info.value.code == "actor_not_found"


@pytest.mark.asyncio
async def test_builtin_profile_edit_rejected():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1", source_kind="built_in")})
    service = _service(store, FakeMetrics())
    with pytest.raises(AgentTeamRejected) as exc_info:
        await service.update_job_title("agent-1", "回归测试分析员")
    assert exc_info.value.code == "agent_read_only"


@pytest.mark.asyncio
async def test_unknown_agent_rejected():
    store = FakeStore()
    service = _service(store, FakeMetrics())
    with pytest.raises(AgentTeamRejected) as exc_info:
        await service.update_job_title("ghost", "x")
    assert exc_info.value.code == "agent_not_found"


@pytest.mark.asyncio
async def test_job_title_over_64_rejected():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")})
    service = _service(store, FakeMetrics())
    with pytest.raises(AgentTeamRejected) as exc_info:
        await service.update_job_title("agent-1", "x" * 65)
    assert exc_info.value.code == "job_title_too_long"


@pytest.mark.asyncio
async def test_me_resolves_to_scope_actor():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1", owner_actor_id="actor-me")})
    service = _service(store, FakeMetrics())
    view = await service.person("me", _PERIOD)
    assert view.data.person.actor_id == "actor-me"
    assert [row.agent_key for row in view.data.owned] == ["agent-1"]


@pytest.mark.asyncio
async def test_report_includes_previous_period():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")})
    service = _service(store, FakeMetrics())
    view = await service.report(_PERIOD)
    assert view.data.current.summary is not None
    assert view.data.previous.summary is not None


@pytest.mark.asyncio
async def test_sync_lag_over_5_minutes_is_syncing():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")}, lag=timedelta(minutes=6))
    service = _service(store, FakeMetrics(), check_sync_lag=True)
    view = await service.summary(_PERIOD)
    assert view.runtime_status == "syncing"


@pytest.mark.asyncio
async def test_writes_bypass_runtime_cache():
    store = FakeStore(
        agent_records={"agent-1": _agent("agent-1", owner_actor_id="owner-1")},
        pending=[
            PendingFeedback(
                feedback_id="fb-1",
                agent_key="agent-1",
                turn_id="turn-1",
                conversation_id="conv-1",
                actor_id="caller-1",
                rating="down",
                comment=None,
                updated_at=_NOW,
            )
        ],
    )
    metrics = FakeMetrics()
    service = _service(store, metrics)

    before = await service.member("agent-1", _PERIOD)
    assert before.data.pending_feedback == 1
    calls_after_first_read = metrics.agent_stats_calls

    await service.resolve_feedback("fb-1", "handled")

    after = await service.member("agent-1", _PERIOD)
    assert after.data.pending_feedback == 0
    # The store (MySQL) is always re-queried live; the cached Langfuse
    # metrics backend is not hit again within its TTL.
    assert metrics.agent_stats_calls == calls_after_first_read


@pytest.mark.asyncio
async def test_builtin_pending_feedback_not_in_anyones_queue():
    store = FakeStore(
        agent_records={
            "agent-1": _agent("agent-1", owner_actor_id="owner-1", source_kind="built_in"),
        },
        pending=[
            PendingFeedback(
                feedback_id="fb-1",
                agent_key="agent-1",
                turn_id="turn-1",
                conversation_id="conv-1",
                actor_id="caller-1",
                rating="down",
                comment=None,
                updated_at=_NOW,
            )
        ],
    )
    service = _service(store, FakeMetrics())
    view = await service.person("owner-1", _PERIOD)
    assert view.data.pending_feedback == ()
