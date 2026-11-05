"""Unit tests for `SkillHubService` (Task 9, 2026-11-01 Skill Hub phase 1)
against an in-memory store, a fake runtime backend behind the real
`CachedSkillRuntimeMetrics`/`TtlSingleFlightCache`, and an in-memory revision
catalog -- no MySQL or Langfuse involved."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.ai.domain.assets import SkillFile, SkillRevision, text_digest
from tap.modules.ai.domain.maintenance import SkillMaintenanceEvent
from tap.modules.skill_hub.adapters.cached_metrics import CachedSkillRuntimeMetrics
from tap.modules.skill_hub.application.service import SkillHubRejected, SkillHubService
from tap.modules.skill_hub.ports import (
    ActivationCount,
    RankedKey,
    SkillRatings,
    SkillRecord,
    SkillRuntimeStats,
    UsagePoint,
)
from tap.platform.reporting.period import Period
from tap.platform.reporting.runtime import RuntimeMetricsUnavailable
from tap.platform.reporting.ttl_cache import TtlSingleFlightCache

_NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
_PERIOD = Period(date(2026, 10, 3), date(2026, 11, 1))


def _scope(actor_id: str = "actor-me") -> ProjectScopeContext:
    return ProjectScopeContext(
        enterprise_id="ent-1",
        project_id="proj-1",
        actor_id=actor_id,
        identity_mode=IdentityMode.PRODUCT,
    )


def _record(
    skill_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    category: str = "other",
    source_kind: str = "authored",
    status: str = "enabled",
    applicable_tasks: tuple[str, ...] = ("knowledge.answer",),
    owner_actor_id: str = "owner-1",
    updated_at: datetime = _NOW,
) -> SkillRecord:
    return SkillRecord(
        skill_id=skill_id,
        name=name or skill_id,
        description=description,
        category=category,  # type: ignore[arg-type]
        version="r1",
        latest_revision_id=f"{skill_id}-r1",
        revision_number=1,
        source_kind=source_kind,
        origin_kind="built_in" if source_kind == "built_in" else "authored",
        origin_ref=None,
        origin_imported_at=None,
        origin_actor_id="creator-1",
        owner_actor_id=owner_actor_id,
        owner_assigned_at=_NOW - timedelta(days=3),
        status=status,
        applicable_tasks=applicable_tasks,
        compatibility=None,
        updated_at=updated_at,
        built_in=source_kind == "built_in",
    )


def _revision(skill_id: str, number: int, files: tuple[SkillFile, ...] = ()) -> SkillRevision:
    template = f"instructions {number}"
    return SkillRevision(
        revision_id=f"{skill_id}-r{number}",
        asset_id=skill_id,
        display_name=skill_id,
        scope=_scope(),
        content_digest=text_digest(f"content {number}"),
        instruction_template_digest=text_digest(template),
        instruction_template=template,
        applicable_tasks=frozenset({"knowledge.answer"}),
        source_kind="authored",
        files=files,
    )


def _file(path: str) -> SkillFile:
    return SkillFile(
        relative_path=path, size_bytes=10, sha256="a" * 64, category="reference", attached=True
    )


@dataclass
class FakeStore:
    records: dict[str, SkillRecord] = field(default_factory=dict)
    counts: list[ActivationCount] = field(default_factory=list)
    users: dict[str, int] = field(default_factory=dict)
    rating_rows: list[SkillRatings] = field(default_factory=list)
    last_used_at: dict[str, datetime] = field(default_factory=dict)
    pinned: dict[str, tuple[str, ...]] = field(default_factory=dict)
    trend: dict[str, list[UsagePoint]] = field(default_factory=dict)
    agents: dict[str, list[RankedKey]] = field(default_factory=dict)
    callers: dict[str, list[RankedKey]] = field(default_factory=dict)
    actors: set[str] = field(default_factory=set)
    events: list[SkillMaintenanceEvent] = field(default_factory=list)
    writes: list[tuple[str, str]] = field(default_factory=list)
    trend_calls: list[tuple[str, Period, str]] = field(default_factory=list)

    async def skills(self) -> list[SkillRecord]:
        return list(self.records.values())

    async def skill(self, skill_id: str) -> SkillRecord | None:
        return self.records.get(skill_id)

    async def activation_counts(self, period: Period) -> list[ActivationCount]:
        return list(self.counts)

    async def distinct_users(self, period: Period) -> dict[str, int]:
        return dict(self.users)

    async def ratings(self, period: Period) -> list[SkillRatings]:
        return list(self.rating_rows)

    async def last_used(self) -> dict[str, datetime]:
        return dict(self.last_used_at)

    async def pinned_by(self) -> dict[str, tuple[str, ...]]:
        return dict(self.pinned)

    async def usage_trend(self, skill_id: str, period: Period, granularity) -> list[UsagePoint]:
        self.trend_calls.append((skill_id, period, granularity))
        return list(self.trend.get(skill_id, []))

    async def top_agents(self, skill_id: str, period: Period, limit: int = 5) -> list[RankedKey]:
        return self.agents.get(skill_id, [])[:limit]

    async def top_callers(self, skill_id: str, period: Period, limit: int = 5) -> list[RankedKey]:
        return self.callers.get(skill_id, [])[:limit]

    async def actor_exists(self, actor_id: str) -> bool:
        return actor_id in self.actors

    async def set_category(self, skill_id: str, category) -> SkillRecord:
        self.writes.append(("category", skill_id))
        record = dataclasses.replace(self.records[skill_id], category=category)
        self.records[skill_id] = record
        return record

    async def transfer_owner(
        self, skill_id: str, new_owner: str, event: SkillMaintenanceEvent
    ) -> SkillRecord:
        self.writes.append(("owner", skill_id))
        self.events.append(event)
        record = dataclasses.replace(
            self.records[skill_id], owner_actor_id=new_owner, owner_assigned_at=event.occurred_at
        )
        self.records[skill_id] = record
        return record


@dataclass
class FakeRuntime:
    stats: list[SkillRuntimeStats] = field(default_factory=list)
    unavailable: bool = False

    async def skill_stats(self, project_id: str, period: Period) -> list[SkillRuntimeStats]:
        if self.unavailable:
            raise RuntimeMetricsUnavailable("langfuse down")
        return list(self.stats)


@dataclass
class FakeCatalog:
    revisions: dict[str, tuple[SkillRevision, ...]] = field(default_factory=dict)

    async def list_revisions(self, kind: str, asset_id: str) -> tuple[SkillRevision, ...]:
        assert kind == "skill"
        return self.revisions.get(asset_id, ())


def _service(
    store: FakeStore,
    runtime: FakeRuntime | None = None,
    *,
    catalog: FakeCatalog | None = None,
    sync_lag: timedelta | None = None,
    with_sync_lag: bool = False,
) -> SkillHubService:
    async def lag() -> timedelta | None:
        return sync_lag

    return SkillHubService(
        store,
        CachedSkillRuntimeMetrics(runtime or FakeRuntime(), TtlSingleFlightCache(now=lambda: _NOW)),
        asset_catalog=catalog or FakeCatalog(),
        scope=_scope(),
        clock=lambda: _NOW,
        sync_lag=lag if with_sync_lag else None,
    )


def _store_with_usage() -> FakeStore:
    return FakeStore(
        records={"s1": _record("s1"), "s2": _record("s2")},
        counts=[
            ActivationCount("s1", "manual", 2),
            ActivationCount("s1", "auto", 3),
            ActivationCount("s2", "pinned", 1),
        ],
        users={"s1": 2},
        rating_rows=[SkillRatings("s1", up=7, down=3)],
        last_used_at={"s1": _NOW - timedelta(hours=1)},
        # `agent-x` pins a Skill that is no longer in the library.
        pinned={"s1": ("agent-a", "agent-b"), "s-gone": ("agent-x",)},
        trend={"s1": [UsagePoint(date(2026, 10, 30), "manual", 2)]},
        agents={"s1": [RankedKey("agent-a", 4), RankedKey("unknown", 1)]},
        callers={"s1": [RankedKey("actor-1", 5)]},
    )


@pytest.mark.asyncio
async def test_unavailable_runtime_keeps_primary_metrics() -> None:
    service = _service(_store_with_usage(), FakeRuntime(unavailable=True))

    view = await service.detail("s1", _PERIOD)

    assert view.runtime_status == "unavailable"
    assert view.computed_at == _NOW
    usage = view.data.usage
    assert usage.runtime is None
    assert usage.by_mode == {"manual": 2, "pinned": 0, "auto": 3}
    assert usage.users == 2
    assert (usage.satisfaction, usage.satisfaction_sample) == (0.7, "ok")
    assert usage.pinned_by_agents == 2

    export = await service.export_data(_PERIOD)
    assert export.runtime_status == "unavailable"
    assert [item.usage.runtime for item in export.data.items] == [None, None]
    assert [item.usage.by_mode["manual"] for item in export.data.items] == [2, 0]


@pytest.mark.asyncio
async def test_available_runtime_fills_secondary_metrics() -> None:
    runtime = FakeRuntime(
        stats=[SkillRuntimeStats("s1", 5, 1, 900, Decimal("0.25"))],
    )
    service = _service(_store_with_usage(), runtime)

    s1 = await service.detail("s1", _PERIOD)
    s2 = await service.detail("s2", _PERIOD)

    assert s1.runtime_status == "ok"
    assert s1.data.usage.runtime == SkillRuntimeStats("s1", 5, 1, 900, Decimal("0.25"))
    # Available but no runs for this Skill: zeroes, not "unknown".
    assert s2.data.usage.runtime == SkillRuntimeStats("s2", 0, 0, 0, Decimal(0))


@pytest.mark.asyncio
async def test_sync_lag_over_5_minutes_reports_syncing() -> None:
    store = _store_with_usage()
    lagging = _service(store, with_sync_lag=True, sync_lag=timedelta(minutes=6))
    caught_up = _service(store, with_sync_lag=True, sync_lag=timedelta(minutes=5))

    assert (await lagging.detail("s1", _PERIOD)).runtime_status == "syncing"
    assert (await caught_up.detail("s1", _PERIOD)).runtime_status == "ok"


@pytest.mark.asyncio
async def test_detail_lists_revisions_files_pinned_and_rankings() -> None:
    catalog = FakeCatalog(
        revisions={"s1": (_revision("s1", 1), _revision("s1", 2, files=(_file("ref/a.md"),)))}
    )
    store = _store_with_usage()
    store.records["s1"] = dataclasses.replace(
        store.records["s1"], latest_revision_id="s1-r2", revision_number=2
    )
    view = await _service(store, catalog=catalog).detail("s1", _PERIOD)

    detail = view.data
    assert detail.record.skill_id == "s1"
    assert [revision.revision_id for revision in detail.revisions] == ["s1-r1", "s1-r2"]
    assert [file.relative_path for file in detail.files] == ["ref/a.md"]
    assert detail.pinned_by == ("agent-a", "agent-b")
    assert detail.top_agents == (RankedKey("agent-a", 4), RankedKey("unknown", 1))
    assert detail.top_callers == (RankedKey("actor-1", 5),)
    assert (view.timezone, view.period, view.granularity) == ("UTC", _PERIOD, "day")


@pytest.mark.asyncio
async def test_catalog_defaults_to_activations_desc_with_name_ties() -> None:
    store = FakeStore(
        records={sid: _record(sid) for sid in ("b", "a", "c")},
        counts=[ActivationCount("c", "auto", 2), ActivationCount("a", "manual", 1)],
    )
    view = await _service(store).catalog(_PERIOD)

    assert [(item.record.skill_id, item.activations) for item in view.data] == [
        ("c", 2),
        ("a", 1),
        ("b", 0),
    ]
    assert view.data[0].by_mode == {"manual": 0, "pinned": 0, "auto": 2}
    assert view.runtime_status == "ok"


@pytest.mark.asyncio
async def test_catalog_filters_by_q_category_task_source_status() -> None:
    store = FakeStore(
        records={
            "alpha": _record("alpha", name="Login Checks", category="test-design"),
            "beta": _record("beta", description="Writes LOGIN docs", category="writing"),
            "gamma": _record(
                "gamma",
                source_kind="imported",
                status="disabled",
                applicable_tasks=("test-plan.generate",),
            ),
            "delta": _record("delta", source_kind="built_in"),
        }
    )
    service = _service(store)

    async def ids(**filters) -> list[str]:
        view = await service.catalog(_PERIOD, sort="name", order="asc", **filters)
        return [item.record.skill_id for item in view.data]

    assert await ids(q="login") == ["beta", "alpha"]
    assert await ids(category="writing") == ["beta"]
    assert await ids(task="test-plan.generate") == ["gamma"]
    assert await ids(source="imported") == ["gamma"]
    assert await ids(source="built_in") == ["delta"]
    assert await ids(status="disabled") == ["gamma"]
    assert await ids(status="enabled", q="LOGIN", category="test-design") == ["alpha"]


@pytest.mark.asyncio
async def test_invalid_category_filter_rejected() -> None:
    service = _service(FakeStore())

    for filters in (
        {"category": "marketing"},
        {"task": "knowledge.search"},
        {"source": "custom"},
        {"status": "archived"},
    ):
        with pytest.raises(SkillHubRejected) as error:
            await service.catalog(_PERIOD, **filters)
        assert error.value.code == "filter_invalid"


@pytest.mark.asyncio
async def test_invalid_sort_rejected() -> None:
    service = _service(FakeStore())

    for sort, order in (("cost_usd", "desc"), ("name", "sideways")):
        with pytest.raises(SkillHubRejected) as error:
            await service.catalog(_PERIOD, sort=sort, order=order)
        assert error.value.code == "sort_invalid"


@pytest.mark.asyncio
async def test_q_longer_than_128_rejected() -> None:
    service = _service(FakeStore(records={"s1": _record("s1")}))

    assert (await service.catalog(_PERIOD, q="x" * 128)).data == ()
    with pytest.raises(SkillHubRejected) as error:
        await service.catalog(_PERIOD, q="x" * 129)
    assert error.value.code == "filter_invalid"


@pytest.mark.asyncio
async def test_sort_by_last_used_puts_never_used_last() -> None:
    store = FakeStore(
        records={sid: _record(sid) for sid in ("never", "old", "recent")},
        last_used_at={"old": _NOW - timedelta(days=9), "recent": _NOW - timedelta(hours=1)},
    )
    service = _service(store)

    async def ids(order: str) -> list[str]:
        view = await service.catalog(_PERIOD, sort="last_used_at", order=order)
        return [item.record.skill_id for item in view.data]

    assert await ids("desc") == ["recent", "old", "never"]
    assert await ids("asc") == ["old", "recent", "never"]


@pytest.mark.asyncio
async def test_catalog_sorts_by_name_and_updated_at() -> None:
    store = FakeStore(
        records={
            "s1": _record("s1", name="beta", updated_at=_NOW - timedelta(days=1)),
            "s2": _record("s2", name="Alpha", updated_at=_NOW),
            "s3": _record("s3", name="gamma", updated_at=_NOW - timedelta(days=2)),
        }
    )
    service = _service(store)

    by_name = await service.catalog(_PERIOD, sort="name", order="asc")
    by_updated = await service.catalog(_PERIOD, sort="updated_at", order="desc")

    assert [item.record.name for item in by_name.data] == ["Alpha", "beta", "gamma"]
    assert [item.record.skill_id for item in by_updated.data] == ["s2", "s1", "s3"]


@pytest.mark.asyncio
async def test_summary_counts_library() -> None:
    store = _store_with_usage()
    store.records["s3"] = _record("s3", status="disabled")

    view = await _service(store).summary(_PERIOD)

    summary = view.data
    assert (summary.skill_total, summary.enabled, summary.used_in_period, summary.unused) == (
        3,
        2,
        2,
        0,
    )
    assert summary.auto_share == 3 / 6
    assert (summary.candidate_count, summary.candidate_limit) == (2, 60)


@pytest.mark.asyncio
async def test_usage_returns_period_trend_and_mode_split() -> None:
    store = _store_with_usage()
    store.trend["s1"] = [
        UsagePoint(date(2026, 10, 30), "manual", 2),
        UsagePoint(date(2026, 10, 31), "auto", 3),
    ]

    view = await _service(store).usage("s1", _PERIOD)

    assert view.data.points == tuple(store.trend["s1"])
    assert view.data.by_mode == {"manual": 2, "pinned": 0, "auto": 3}
    assert store.trend_calls == [("s1", _PERIOD, "day")]


@pytest.mark.asyncio
async def test_export_data_has_items_and_usage_series_per_used_skill() -> None:
    store = _store_with_usage()
    store.records["s3"] = _record("s3")

    view = await _service(store).export_data(_PERIOD)

    assert [item.record.skill_id for item in view.data.items] == ["s1", "s2", "s3"]
    assert view.data.items[0].usage.users == 2
    assert view.data.usage == (("s1", UsagePoint(date(2026, 10, 30), "manual", 2)),)
    # Unused Skills have no trend to fetch.
    assert sorted(call[0] for call in store.trend_calls) == ["s1", "s2"]


@pytest.mark.asyncio
async def test_unknown_skill_rejected() -> None:
    service = _service(_store_with_usage())

    for call in (
        service.detail("missing", _PERIOD),
        service.usage("missing", _PERIOD),
        service.update_profile("missing", "writing"),
        service.transfer_owner("missing", "owner-2"),
    ):
        with pytest.raises(SkillHubRejected) as error:
            await call
        assert error.value.code == "skill_not_found"


@pytest.mark.asyncio
async def test_built_in_profile_and_owner_rejected() -> None:
    store = FakeStore(records={"b": _record("b", source_kind="built_in")}, actors={"owner-2"})
    service = _service(store)

    for call in (service.update_profile("b", "writing"), service.transfer_owner("b", "owner-2")):
        with pytest.raises(SkillHubRejected) as error:
            await call
        assert error.value.code == "skill_read_only"
    assert store.writes == []


@pytest.mark.asyncio
async def test_update_profile_sets_category() -> None:
    store = FakeStore(records={"s1": _record("s1")})

    record = await _service(store).update_profile("s1", "writing")

    assert record.category == "writing"
    assert store.writes == [("category", "s1")]
    assert store.events == []


@pytest.mark.asyncio
async def test_update_profile_rejects_unknown_category() -> None:
    store = FakeStore(records={"s1": _record("s1")})

    with pytest.raises(SkillHubRejected) as error:
        await _service(store).update_profile("s1", "marketing")  # type: ignore[arg-type]
    assert error.value.code == "filter_invalid"
    assert store.writes == []


@pytest.mark.asyncio
async def test_transfer_to_unknown_actor_rejected() -> None:
    store = FakeStore(records={"s1": _record("s1")}, actors={"owner-2"})

    with pytest.raises(SkillHubRejected) as error:
        await _service(store).transfer_owner("s1", "nobody")
    assert error.value.code == "actor_not_found"
    assert store.writes == []


@pytest.mark.asyncio
async def test_transfer_to_current_owner_is_noop() -> None:
    store = FakeStore(records={"s1": _record("s1", owner_actor_id="owner-1")})

    record = await _service(store).transfer_owner("s1", "owner-1")

    assert record == store.records["s1"]
    assert store.writes == []
    assert store.events == []


@pytest.mark.asyncio
async def test_transfer_records_owner_transferred_event() -> None:
    store = FakeStore(records={"s1": _record("s1")}, actors={"owner-2"})

    record = await _service(store).transfer_owner("s1", "owner-2")

    assert record.owner_actor_id == "owner-2"
    (event,) = store.events
    assert (event.kind, event.skill_id, event.actor_id, event.occurred_at) == (
        "owner_transferred",
        "s1",
        "actor-me",
        _NOW,
    )
