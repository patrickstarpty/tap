"""Unit tests for `AgentTeamService` (Task 7, 2026-10-23 agent team view),
against a fake store and a fake runtime metrics backend -- no MySQL or
Langfuse involved."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.agent_team.adapters.cached_metrics import CachedRuntimeMetrics
from tap.modules.agent_team.application.service import AgentTeamRejected, AgentTeamService
from tap.modules.agent_team.domain.models import (
    MaintenanceEvent,
    MaintenanceKind,
    Period,
    trailing_30_days,
)
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
    last_run_by_key: dict[str, datetime] = field(default_factory=dict)
    last_runs_keys: list[str] = field(default_factory=list)

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
        self.last_runs_keys = list(agent_keys)
        if self.unavailable:
            raise RuntimeMetricsUnavailable("boom")
        return {key: self.last_run_by_key[key] for key in agent_keys if key in self.last_run_by_key}


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
    lag_queries: list[tuple[datetime, timedelta]] = dataclasses.field(default_factory=list)
    resolved: dict[str, ResolvedFeedback] = field(default_factory=dict)
    events: list[MaintenanceEvent] = field(default_factory=list)
    pending_feedback_calls: int = 0
    maintenance_counts_calls: int = 0
    agents_calls: int = 0

    async def agents(self) -> list[AgentRecord]:
        self.agents_calls += 1
        return list(self.agent_records.values())

    async def agent(self, agent_id: str) -> AgentRecord | None:
        return self.agent_records.get(agent_id)

    async def ratings(self, period: Period) -> list[RatingCounts]:
        return list(self.ratings_by_key.values())

    async def pending_feedback(self, agent_key: str | None = None) -> list[PendingFeedback]:
        self.pending_feedback_calls += 1
        if agent_key is None:
            return list(self.pending)
        return [item for item in self.pending if item.agent_key == agent_key]

    async def maintenance_counts(
        self, period: Period
    ) -> list[tuple[str, str, MaintenanceKind, int]]:
        self.maintenance_counts_calls += 1
        return list(self.maintenance_rows)

    async def history(self, agent_id: str, cursor: str | None, limit: int = 50) -> HistoryPage:
        if cursor == "bad-cursor":
            raise HistoryCursorInvalid("bad cursor")
        return HistoryPage(events=(), next_cursor=None)

    async def last_maintenance(self) -> dict[str, datetime]:
        return dict(self.last_maintenance_by_agent)

    async def active_actors(self, period: Period) -> set[str]:
        return set(self.active_actor_set)

    async def oldest_unsynced_age(self, *, now: datetime, settle: timedelta) -> timedelta | None:
        self.lag_queries.append((now, settle))
        return self.lag

    async def actor_exists(self, actor_id: str) -> bool:
        return actor_id in self.existing_actors

    async def set_job_title(self, agent_id: str, job_title: str | None) -> AgentRecord:
        record = self.agent_records[agent_id]
        updated = dataclasses.replace(record, job_title=job_title)
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
        agent_records={
            "agent-1": _agent("agent-1", owner_actor_id="owner-1"),
            "agent-2": _agent("agent-2", owner_actor_id="owner-1", enabled=False),
        },
        ratings_by_key={"agent-1": RatingCounts(agent_key="agent-1", up=8, down=2)},
    )
    metrics = FakeMetrics(unavailable=True)
    service = _service(store, metrics)
    view = await service.members(_PERIOD)
    assert view.runtime_status == "unavailable"
    # tapper_default is always included during an outage -- whether it ran
    # is unknown, so it must not be silently dropped (controller ruling).
    rows_by_key = {row.agent_key: row for row in view.data}
    assert set(rows_by_key) == {"agent-1", "agent-2", "tapper_default"}
    agent_row = rows_by_key["agent-1"]
    # Controller ruling (Task 9 review, fix round 1): every run-derived field
    # is `None` -- never `0` -- during an outage; it is unknown, not zero.
    assert agent_row.runs is None
    assert agent_row.failures is None
    assert agent_row.abstained is None
    assert agent_row.canceled is None
    assert agent_row.latency_total_ms is None
    assert agent_row.latency_p50_ms is None
    assert agent_row.cost_usd is None
    assert agent_row.served_count is None
    assert agent_row.top_served == ()
    assert agent_row.last_run_at is None
    assert agent_row.trend == ()
    assert agent_row.satisfaction == 0.8
    assert agent_row.owner_actor_id == "owner-1"
    # Never claim "idle" during an outage: a non-disabled Agent's status is
    # unknown (`None`), not a false "idle".
    assert agent_row.status is None
    assert rows_by_key["tapper_default"].status is None
    # A disabled Agent's status is known from MySQL regardless of the
    # outage -- it stays "disabled", never `None`.
    assert rows_by_key["agent-2"].status == "disabled"

    summary_view = await service.summary(_PERIOD)
    assert summary_view.runtime_status == "unavailable"
    assert summary_view.data.agent_total == 1  # known from MySQL's `enabled`, outage or not
    assert summary_view.data.active is None
    assert summary_view.data.idle is None
    assert summary_view.data.runs is None
    assert summary_view.data.latency_total_ms is None
    assert summary_view.data.cost_usd is None

    people_view = await service.people(_PERIOD)
    people_by_id = {row.actor_id: row for row in people_view.data}
    owner_row = people_by_id["owner-1"]
    assert owner_row.owned_agents == 2  # a MySQL fact, unaffected by the outage
    assert owner_row.owned_runs is None
    assert owner_row.served_by_agents is None
    assert owner_row.runs is None


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
async def test_sync_lag_asks_store_with_service_clock_and_settle_window():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")}, lag=timedelta(minutes=5))
    service = _service(store, FakeMetrics(), check_sync_lag=True)
    view = await service.summary(_PERIOD)
    # Exactly the 5 minute threshold is not "behind"; and the store is asked
    # with the service clock and the default 60 s settle window.
    assert view.runtime_status == "ok"
    assert store.lag_queries and store.lag_queries[0] == (_NOW, timedelta(seconds=60))


@pytest.mark.asyncio
async def test_sync_lag_none_means_ok():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")}, lag=None)
    service = _service(store, FakeMetrics(), check_sync_lag=True)
    assert (await service.summary(_PERIOD)).runtime_status == "ok"


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
    assert before.data.row.pending_feedback == 1
    calls_after_first_read = metrics.agent_stats_calls

    await service.resolve_feedback("fb-1", "handled")

    after = await service.member("agent-1", _PERIOD)
    assert after.data.row.pending_feedback == 0
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


@pytest.mark.asyncio
async def test_person_served_count_ignores_non_roster_stats():
    # Review fix round 1, finding 4: `served_by_agents` must come from the
    # same roster-restricted stats as `person()`'s own `served` list, so a
    # stray "unknown"-keyed stats entry (never a roster member) does not
    # inflate the count.
    store = FakeStore(
        agent_records={"agent-1": _agent("agent-1", owner_actor_id="owner-1")},
        active_actor_set={"caller-1"},
    )
    trailing = trailing_30_days(_NOW.date())
    stats = [
        _stats("agent-1", runs=2, served=(ServedActor("caller-1", 2),)),
        _stats("unknown", runs=5, served=(ServedActor("caller-1", 5),)),
    ]
    metrics = FakeMetrics(
        stats_by_period={
            (_PERIOD.start, _PERIOD.end): stats,
            (trailing.start, trailing.end): stats,
        }
    )
    service = _service(store, metrics)
    view = await service.person("caller-1", _PERIOD)
    assert [row.agent_key for row in view.data.served] == ["agent-1"]
    assert view.data.person.served_by_agents == 1
    assert view.data.person.runs == 2


@pytest.mark.asyncio
async def test_report_runtime_status_is_worst_of_current_and_previous():
    # Review fix round 1, finding 2: only the previous period's fetch is
    # unavailable -> the combined report is still "unavailable".
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")})
    previous_period = _PERIOD.previous()
    previous_key = (previous_period.start, previous_period.end)

    @dataclass
    class _PreviousPeriodUnavailable(FakeMetrics):
        async def agent_stats(self, project_id: str, period: Period) -> list[AgentRuntimeStats]:
            if (period.start, period.end) == previous_key:
                raise RuntimeMetricsUnavailable("boom")
            return await super().agent_stats(project_id, period)

    metrics = _PreviousPeriodUnavailable()
    service = _service(store, metrics)
    view = await service.report(_PERIOD)
    assert view.runtime_status == "unavailable"
    assert view.data.current.summary is not None
    assert view.data.previous.summary.active is None


@pytest.mark.asyncio
async def test_members_invalid_sort_field_rejected():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")})
    service = _service(store, FakeMetrics())
    with pytest.raises(AgentTeamRejected) as exc_info:
        await service.members(_PERIOD, sort="agent_key")
    assert exc_info.value.code == "sort_invalid"


@pytest.mark.asyncio
async def test_members_invalid_order_rejected():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")})
    service = _service(store, FakeMetrics())
    with pytest.raises(AgentTeamRejected) as exc_info:
        await service.members(_PERIOD, order="sideways")
    assert exc_info.value.code == "sort_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize("field_name", ["job_title", "owner_actor_id", "status", "kind"])
async def test_members_sort_by_personnel_columns_is_deterministic(field_name):
    store = FakeStore(
        agent_records={
            "agent-b": _agent("agent-b", owner_actor_id="owner-2", job_title="QA"),
            "agent-a": _agent("agent-a", owner_actor_id="owner-1", job_title="Dev"),
            "agent-c": _agent("agent-c", owner_actor_id="owner-1", enabled=False),
        }
    )
    service = _service(store, FakeMetrics())
    asc = await service.members(_PERIOD, sort=field_name, order="asc")
    desc = await service.members(_PERIOD, sort=field_name, order="desc")
    keys = [row.agent_key for row in asc.data]
    assert sorted(keys) == ["agent-a", "agent-b", "agent-c"]
    assert keys == [
        row.agent_key for row in (await service.members(_PERIOD, sort=field_name, order="asc")).data
    ]
    if field_name == "job_title":
        assert keys[:2] == ["agent-a", "agent-b"]  # Dev < QA; missing last
        assert [row.agent_key for row in desc.data][-1] == "agent-c"
    if field_name == "owner_actor_id":
        assert keys == ["agent-a", "agent-c", "agent-b"]


@pytest.mark.asyncio
async def test_people_invalid_sort_field_rejected():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")})
    service = _service(store, FakeMetrics())
    with pytest.raises(AgentTeamRejected) as exc_info:
        await service.people(_PERIOD, sort="agent_key")
    assert exc_info.value.code == "sort_invalid"


@pytest.mark.asyncio
async def test_resolve_feedback_note_over_500_chars_rejected():
    store = FakeStore(
        agent_records={"agent-1": _agent("agent-1")},
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
    with pytest.raises(AgentTeamRejected) as exc_info:
        await service.resolve_feedback("fb-1", "x" * 501)
    assert exc_info.value.code == "note_too_long"


@pytest.mark.asyncio
async def test_person_issues_one_round_of_store_queries():
    # Review fix round 1, finding 6: `pending_feedback`/`maintenance_counts`
    # must be queried once per view, not once in member assembly and again
    # in person-detail assembly.
    store = FakeStore(agent_records={"agent-1": _agent("agent-1", owner_actor_id="owner-1")})
    service = _service(store, FakeMetrics())
    await service.person("owner-1", _PERIOD)
    assert store.pending_feedback_calls == 1
    assert store.maintenance_counts_calls == 1


@pytest.mark.asyncio
async def test_each_view_loads_the_roster_once():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")})
    service = _service(store, FakeMetrics())
    await service.summary(_PERIOD)
    assert store.agents_calls == 1


@pytest.mark.asyncio
async def test_report_computed_at_is_the_oldest_cached_fetch():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")})
    ticks = iter(_NOW - timedelta(minutes=n) for n in range(100))
    cached = CachedRuntimeMetrics(FakeMetrics(), now=lambda: next(ticks))
    service = AgentTeamService(store, cached, scope=_scope(), clock=_clock)
    view = await service.report(_PERIOD)
    # Every fetch gets an earlier timestamp than the one before; the report
    # must surface the oldest of the two periods' data, not either period's
    # newest.
    assert view.computed_at == _NOW - timedelta(minutes=5)


def _pending(feedback_id: str, agent_key: str) -> PendingFeedback:
    return PendingFeedback(
        feedback_id=feedback_id,
        agent_key=agent_key,
        turn_id="t",
        conversation_id="c",
        actor_id="bob",
        rating="down",
        comment=None,
        updated_at=_NOW,
    )


@pytest.mark.asyncio
async def test_member_detail_for_custom_agent():
    store = FakeStore(
        agent_records={"agent-1": _agent("agent-1")},
        pending=[_pending("fb-1", "agent-1")],
    )
    served = (ServedActor("bob", 2), ServedActor("carol", 5))
    metrics = FakeMetrics(
        stats_by_period={(_PERIOD.start, _PERIOD.end): [_stats("agent-1", 7, served)]}
    )
    view = await _service(store, metrics).member("agent-1", _PERIOD)

    assert view.data.current_revision_id == "agent-1-rev-1"
    assert [s.actor_id for s in view.data.served] == ["carol", "bob"]
    assert [f.feedback_id for f in view.data.pending_feedback] == ["fb-1"]


@pytest.mark.asyncio
async def test_member_detail_tapper_default_has_no_revision_but_pending_feedback():
    store = FakeStore(pending=[_pending("fb-9", "tapper_default")])
    metrics = FakeMetrics(
        stats_by_period={(_PERIOD.start, _PERIOD.end): [_stats("tapper_default", 1)]}
    )
    view = await _service(store, metrics).member("tapper_default", _PERIOD)

    assert view.data.current_revision_id is None
    assert [f.feedback_id for f in view.data.pending_feedback] == ["fb-9"]


@pytest.mark.asyncio
async def test_member_detail_served_empty_when_runtime_unavailable():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")})
    view = await _service(store, FakeMetrics(unavailable=True)).member("agent-1", _PERIOD)

    assert view.runtime_status == "unavailable"
    assert view.data.served == ()


@pytest.mark.asyncio
async def test_last_run_covers_idle_agents_without_recent_stats():
    old = _NOW - timedelta(days=45)
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")})
    metrics = FakeMetrics(last_run_by_key={"agent-1": old})
    view = await _service(store, metrics).members(_PERIOD)
    row = next(r for r in view.data if r.agent_key == "agent-1")
    assert "agent-1" in metrics.last_runs_keys
    assert row.last_run_at == old


@pytest.mark.asyncio
async def test_runtime_load_timeout_maps_to_unavailable(monkeypatch):
    import asyncio

    from tap.modules.agent_team.application import service as service_module

    monkeypatch.setattr(service_module, "_RUNTIME_LOAD_TIMEOUT_SECONDS", 0.05)

    class Hanging(FakeMetrics):
        async def agent_stats(self, project_id, period):
            await asyncio.sleep(5)
            return []

    store = FakeStore(agent_records={"agent-1": _agent("agent-1")})
    view = await _service(store, Hanging()).members(_PERIOD)
    assert view.runtime_status == "unavailable"


@pytest.mark.asyncio
async def test_blank_job_title_normalized_to_none():
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")})
    record = await _service(store, FakeMetrics()).update_job_title("agent-1", "   ")
    assert record.job_title is None


@pytest.mark.asyncio
async def test_hanging_second_fetch_is_negative_cached_across_views():
    class _HangingTrend(FakeMetrics):
        trend_calls: int = 0

        async def trend(self, project_id, period, granularity):
            self.trend_calls += 1
            await asyncio.sleep(3600)
            return []

    metrics = _HangingTrend()
    store = FakeStore(agent_records={"agent-1": _agent("agent-1")})
    cached = CachedRuntimeMetrics(metrics, now=lambda: _NOW)
    service = AgentTeamService(
        store, cached, scope=_scope(), clock=_clock, runtime_timeout_seconds=0.05
    )

    first = await service.summary(_PERIOD)
    second = await service.summary(_PERIOD)

    # The first two fetches were fast; the third hung and ran out the shared
    # deadline, so its failure is cached and the next view never re-asks.
    assert first.runtime_status == "unavailable"
    assert second.runtime_status == "unavailable"
    assert metrics.trend_calls == 1


@pytest.mark.asyncio
async def test_member_sort_ties_break_by_name_and_status_kind_use_domain_rank():
    store = FakeStore(
        agent_records={
            "agent-c": _agent("agent-c", enabled=False),
            "agent-b": _agent("agent-b", source_kind="built_in"),
            "agent-a": _agent("agent-a"),
            "agent-d": _agent("agent-d"),
        }
    )
    service = _service(store, FakeMetrics())
    # Names follow ids ("Agent agent-a" ...). All four have 0 runs: ties.
    for order in ("asc", "desc"):
        view = await service.members(_PERIOD, sort="runs", order=order)
        assert [r.agent_key for r in view.data if r.kind != "tapper_default"] == [
            "agent-a",
            "agent-b",
            "agent-c",
            "agent-d",
        ]
    # Status: idle (a, b, d have no runs) < disabled (c); names break ties.
    asc = await service.members(_PERIOD, sort="status", order="asc")
    assert [r.agent_key for r in asc.data if r.status == "idle"] == [
        "agent-a",
        "agent-b",
        "agent-d",
    ]
    assert asc.data[-1].agent_key == "agent-c"
    desc = await service.members(_PERIOD, sort="status", order="desc")
    assert desc.data[0].agent_key == "agent-c"
    assert [r.agent_key for r in desc.data if r.status == "idle"] == [
        "agent-a",
        "agent-b",
        "agent-d",
    ]
    # Kind: custom (a, c, d) < built_in (b); names break ties either way.
    for order, expected in (
        ("asc", ["agent-a", "agent-c", "agent-d", "agent-b"]),
        ("desc", ["agent-b", "agent-a", "agent-c", "agent-d"]),
    ):
        by_kind = await service.members(_PERIOD, sort="kind", order=order)
        assert [r.agent_key for r in by_kind.data] == expected


def test_naive_converts_to_utc_before_stripping_tzinfo():
    from datetime import timezone

    from tap.modules.agent_team.adapters.mysql import _naive

    plus8 = timezone(timedelta(hours=8))
    assert _naive(datetime(2026, 10, 23, 8, 0, tzinfo=plus8)) == datetime(2026, 10, 23, 0, 0)
    assert _naive(datetime(2026, 10, 23, 8, 0)) == datetime(2026, 10, 23, 8, 0)
