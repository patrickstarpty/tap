"""HTTP contract for the Skill Hub (Task 10, 2026-11-01 Skill Hub phase 1,
spec section 5): library reads, detail, usage trend, summary, the CSV zip
export and the two Skill writes, over the real `SkillHubService` with an
in-memory store, runtime backend and revision catalog."""

from __future__ import annotations

import dataclasses
import io
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.ai.domain.assets import SkillFile, SkillRevision, text_digest
from tap.modules.skill_hub.adapters.cached_metrics import CachedSkillRuntimeMetrics
from tap.modules.skill_hub.application.service import SkillHubService
from tap.modules.skill_hub.domain.models import SkillMaintenanceEvent
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


def _record(skill_id: str, *, source_kind: str = "authored") -> SkillRecord:
    return SkillRecord(
        skill_id=skill_id,
        name=f"Skill {skill_id}",
        description="Does things",
        category="other",
        version="r1",
        latest_revision_id=f"{skill_id}-r1",
        revision_number=1,
        source_kind=source_kind,
        origin_kind="built_in" if source_kind == "built_in" else "authored",
        origin_ref=None,
        origin_imported_at=None,
        origin_actor_id="creator-1",
        owner_actor_id="owner-1",
        owner_assigned_at=_NOW - timedelta(days=3),
        status="enabled",
        applicable_tasks=("knowledge.answer",),
        compatibility=None,
        updated_at=_NOW - timedelta(days=1),
        built_in=source_kind == "built_in",
    )


def _revision(skill_id: str) -> SkillRevision:
    return SkillRevision(
        revision_id=f"{skill_id}-r1",
        asset_id=skill_id,
        display_name=f"Skill {skill_id}",
        scope=VALIDATION_SCOPE,
        content_digest=text_digest("content"),
        instruction_template_digest=text_digest("instructions"),
        instruction_template="instructions",
        applicable_tasks=frozenset({"knowledge.answer"}),
        source_kind="authored",
        files=(
            SkillFile(
                relative_path="SKILL.md",
                size_bytes=12,
                sha256="a" * 64,
                category="skill",
                attached=False,
            ),
        ),
    )


@dataclass
class FakeStore:
    records: dict[str, SkillRecord] = field(default_factory=dict)
    counts: list[ActivationCount] = field(default_factory=list)
    actors: set[str] = field(default_factory=set)
    events: list[SkillMaintenanceEvent] = field(default_factory=list)

    async def skills(self) -> list[SkillRecord]:
        return list(self.records.values())

    async def skill(self, skill_id: str) -> SkillRecord | None:
        return self.records.get(skill_id)

    async def activation_counts(self, period: Period) -> list[ActivationCount]:
        del period
        return list(self.counts)

    async def distinct_users(self, period: Period) -> dict[str, int]:
        del period
        return {"skill-1": 2}

    async def ratings(self, period: Period) -> list[SkillRatings]:
        del period
        return [SkillRatings(skill_id="skill-1", up=9, down=1)]

    async def last_used(self) -> dict[str, datetime]:
        return {"skill-1": _NOW - timedelta(hours=1)}

    async def pinned_by(self) -> dict[str, tuple[str, ...]]:
        return {"skill-1": ("agent-1",)}

    async def usage_trend(self, skill_id: str, period: Period, granularity) -> list[UsagePoint]:
        del granularity
        if skill_id != "skill-1":
            return []
        return [UsagePoint(bucket_start=period.start, mode="manual", runs=3)]

    async def top_agents(self, skill_id: str, period: Period, limit: int = 5) -> list[RankedKey]:
        del skill_id, period, limit
        return [RankedKey(key="agent-1", runs=3)]

    async def top_callers(self, skill_id: str, period: Period, limit: int = 5) -> list[RankedKey]:
        del skill_id, period, limit
        return [RankedKey(key="owner-1", runs=3)]

    async def actor_exists(self, actor_id: str) -> bool:
        return actor_id in self.actors

    async def set_category(self, skill_id: str, category) -> SkillRecord:
        record = dataclasses.replace(self.records[skill_id], category=category)
        self.records[skill_id] = record
        return record

    async def transfer_owner(
        self, skill_id: str, new_owner: str, event: SkillMaintenanceEvent
    ) -> SkillRecord:
        self.events.append(event)
        record = dataclasses.replace(
            self.records[skill_id], owner_actor_id=new_owner, owner_assigned_at=event.occurred_at
        )
        self.records[skill_id] = record
        return record


@dataclass
class FakeRuntime:
    unavailable: bool = False

    async def skill_stats(self, project_id: str, period: Period) -> list[SkillRuntimeStats]:
        del project_id, period
        if self.unavailable:
            raise RuntimeMetricsUnavailable("langfuse down")
        return [
            SkillRuntimeStats(
                skill_id="skill-1",
                runs=3,
                failures=1,
                latency_total_ms=900,
                cost_usd=Decimal("0.30"),
            )
        ]


@dataclass
class FakeCatalog:
    async def list_revisions(self, kind: str, asset_id: str) -> tuple[SkillRevision, ...]:
        assert kind == "skill"
        return (_revision(asset_id),)


class _Authority:
    def __init__(self, allowed: frozenset[str] | None = None) -> None:
        self._allowed = allowed or frozenset({"ai.skill_hub.read", "ai.skills.write"})

    async def current(self, _facts):
        return VALIDATION_SCOPE

    async def authorize(self, _scope, action, _resource):
        if action not in self._allowed:
            return AuthorizationDecision(False, "action-not-allowed")
        return AuthorizationDecision(True, "validation-allowed")


ORIGIN = "http://127.0.0.1:15176"
BASE = "/api/v1/projects/tapper-demo/ai"
PERIOD_QS = "from=2026-10-03&to=2026-11-01"


def _seeded_store() -> FakeStore:
    return FakeStore(
        records={
            "skill-1": _record("skill-1"),
            "builtin-1": _record("builtin-1", source_kind="built_in"),
        },
        counts=[
            ActivationCount("skill-1", "manual", 3),
            ActivationCount("skill-1", "auto", 1),
        ],
        actors={"owner-1", "owner-2"},
    )


def _client(
    *,
    allowed: frozenset[str] | None = None,
    store: FakeStore | None = None,
    runtime: FakeRuntime | None = None,
) -> TestClient:
    authority = _Authority(allowed)
    service = SkillHubService(
        store or _seeded_store(),
        CachedSkillRuntimeMetrics(runtime or FakeRuntime(), TtlSingleFlightCache(now=lambda: _NOW)),
        asset_catalog=FakeCatalog(),
        scope=VALIDATION_SCOPE,
        clock=lambda: _NOW,
    )
    app = create_app(
        HttpServices(
            skill_hub=service,
            scope=VALIDATION_SCOPE,
            scope_provider=authority,
            authorization_policy=authority,
        ),
        allowed_origins=frozenset({ORIGIN}),
    )
    return TestClient(app, headers={"Origin": ORIGIN})


# -- reads --------------------------------------------------------------


def test_get_catalog_returns_library_view() -> None:
    response = _client().get(f"{BASE}/skill-hub/catalog?{PERIOD_QS}&sort=activations&order=desc")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runtimeStatus"] == "ok"
    assert body["computedAt"] == "2026-11-01T12:00:00Z"
    assert body["timezone"] == "UTC"
    assert body["period"] == {"from": "2026-10-03", "to": "2026-11-01"}
    assert body["granularity"] == "day"
    first = body["data"][0]
    assert first["skill"]["skillId"] == "skill-1"
    assert first["skill"]["latestRevisionId"] == "skill-1-r1"
    assert first["activations"] == 4
    assert first["byMode"] == {"manual": 3, "pinned": 0, "auto": 1}


def test_get_catalog_filters_by_source() -> None:
    response = _client().get(f"{BASE}/skill-hub/catalog?{PERIOD_QS}&source=built_in")

    assert response.status_code == 200, response.text
    assert [item["skill"]["skillId"] for item in response.json()["data"]] == ["builtin-1"]


def test_get_catalog_detail_returns_skill_detail() -> None:
    response = _client().get(f"{BASE}/skill-hub/catalog/skill-1?{PERIOD_QS}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runtimeStatus"] == "ok"
    data = body["data"]
    assert data["skill"]["skillId"] == "skill-1"
    assert data["skill"]["originActorId"] == "creator-1"
    assert data["usage"]["byMode"] == {"manual": 3, "pinned": 0, "auto": 1}
    assert data["usage"]["users"] == 2
    assert data["usage"]["satisfaction"] == 0.9
    assert data["usage"]["satisfactionSample"] == "ok"
    assert data["usage"]["pinnedByAgents"] == 1
    assert data["usage"]["runtime"] == {
        "runs": 3,
        "failures": 1,
        "latencyTotalMs": 900,
        "costUsd": "0.30",
    }
    assert [revision["revisionId"] for revision in data["revisions"]] == ["skill-1-r1"]
    assert data["revisions"][0]["isLatest"] is True
    assert [file["relativePath"] for file in data["files"]] == ["SKILL.md"]
    assert data["pinnedBy"] == ["agent-1"]
    assert data["topAgents"] == [{"key": "agent-1", "runs": 3}]
    assert data["topCallers"] == [{"key": "owner-1", "runs": 3}]


def test_get_catalog_usage_returns_trend() -> None:
    response = _client().get(f"{BASE}/skill-hub/catalog/skill-1/usage?{PERIOD_QS}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runtimeStatus"] == "ok"
    assert body["data"]["points"] == [{"bucketStart": "2026-10-03", "mode": "manual", "runs": 3}]
    assert body["data"]["byMode"] == {"manual": 3, "pinned": 0, "auto": 0}


def test_get_summary_returns_library_summary() -> None:
    response = _client().get(f"{BASE}/skill-hub/summary?{PERIOD_QS}&preset=last30")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runtimeStatus"] == "ok"
    data = body["data"]
    assert data["skillTotal"] == 2
    assert data["enabled"] == 2
    assert data["usedInPeriod"] == 1
    assert data["unused"] == 1
    assert data["autoShare"] == 0.25
    assert data["candidateCount"] == 2
    assert data["candidateLimit"] == 60


def test_export_returns_a_csv_zip() -> None:
    response = _client().get(f"{BASE}/skill-hub/export?{PERIOD_QS}")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        'attachment; filename="skill-hub-2026-10-03-2026-11-01.zip"'
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["library.csv", "usage.csv"]


# -- writes ---------------------------------------------------------------


def test_update_skill_profile_returns_skill() -> None:
    response = _client().put(f"{BASE}/skills/skill-1/profile", json={"category": "test-design"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["skillId"] == "skill-1"
    assert body["category"] == "test-design"


def test_transfer_skill_owner_returns_skill() -> None:
    store = _seeded_store()

    response = _client(store=store).post(
        f"{BASE}/skills/skill-1/owner", json={"newOwnerActorId": "owner-2"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ownerActorId"] == "owner-2"
    assert body["ownerAssignedAt"] == "2026-11-01T12:00:00Z"
    assert [event.kind for event in store.events] == ["owner_transferred"]


def test_unknown_category_is_a_validation_error() -> None:
    response = _client().put(f"{BASE}/skills/skill-1/profile", json={"category": "bogus"})

    assert response.status_code == 422


def test_transfer_to_missing_actor_returns_actor_not_found() -> None:
    response = _client().post(f"{BASE}/skills/skill-1/owner", json={"newOwnerActorId": "ghost"})

    assert response.status_code == 400
    assert response.json()["code"] == "actor_not_found"


# -- errors -----------------------------------------------------------------


def test_reversed_period_returns_period_invalid() -> None:
    response = _client().get(f"{BASE}/skill-hub/summary?from=2026-11-01&to=2026-10-03")

    assert response.status_code == 400
    assert response.json()["code"] == "period_invalid"


def test_unknown_preset_returns_period_invalid() -> None:
    response = _client().get(f"{BASE}/skill-hub/catalog?{PERIOD_QS}&preset=bogus")

    assert response.status_code == 400
    assert response.json()["code"] == "period_invalid"


@pytest.mark.parametrize("query", ["source=bogus", "status=bogus", "category=bogus", "task=bogus"])
def test_catalog_invalid_filter_returns_filter_invalid(query: str) -> None:
    response = _client().get(f"{BASE}/skill-hub/catalog?{PERIOD_QS}&{query}")

    assert response.status_code == 400
    assert response.json()["code"] == "filter_invalid"


@pytest.mark.parametrize("query", ["sort=bogus", "order=sideways"])
def test_catalog_invalid_sort_returns_sort_invalid(query: str) -> None:
    response = _client().get(f"{BASE}/skill-hub/catalog?{PERIOD_QS}&{query}")

    assert response.status_code == 400
    assert response.json()["code"] == "sort_invalid"


@pytest.mark.parametrize(
    "path",
    [
        "skill-hub/catalog",
        "skill-hub/catalog/skill-1",
        "skill-hub/catalog/skill-1/usage",
        "skill-hub/summary",
        "skill-hub/export",
    ],
)
def test_reads_require_skill_hub_read(path: str) -> None:
    client = _client(allowed=frozenset({"ai.skills.read", "ai.skills.write"}))

    response = client.get(f"{BASE}/{path}?{PERIOD_QS}")

    assert response.status_code == 403


def test_profile_and_owner_require_skills_write() -> None:
    client = _client(allowed=frozenset({"ai.skill_hub.read", "ai.skills.read"}))

    profile = client.put(f"{BASE}/skills/skill-1/profile", json={"category": "writing"})
    owner = client.post(f"{BASE}/skills/skill-1/owner", json={"newOwnerActorId": "owner-2"})

    assert profile.status_code == 403
    assert owner.status_code == 403


def test_built_in_profile_returns_skill_read_only() -> None:
    client = _client()

    profile = client.put(f"{BASE}/skills/builtin-1/profile", json={"category": "writing"})
    owner = client.post(f"{BASE}/skills/builtin-1/owner", json={"newOwnerActorId": "owner-2"})

    assert profile.status_code == 403
    assert profile.json()["code"] == "skill_read_only"
    assert owner.status_code == 403
    assert owner.json()["code"] == "skill_read_only"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", f"skill-hub/catalog/ghost?{PERIOD_QS}", None),
        ("GET", f"skill-hub/catalog/ghost/usage?{PERIOD_QS}", None),
        ("PUT", "skills/ghost/profile", {"category": "writing"}),
        ("POST", "skills/ghost/owner", {"newOwnerActorId": "owner-2"}),
    ],
)
def test_missing_skill_returns_skill_not_found(method: str, path: str, body: dict | None) -> None:
    response = _client().request(method, f"{BASE}/{path}", json=body)

    assert response.status_code == 404
    assert response.json()["code"] == "skill_not_found"


def test_langfuse_down_returns_200_unavailable() -> None:
    client = _client(runtime=FakeRuntime(unavailable=True))

    detail = client.get(f"{BASE}/skill-hub/catalog/skill-1?{PERIOD_QS}")
    catalog = client.get(f"{BASE}/skill-hub/catalog?{PERIOD_QS}")
    export = client.get(f"{BASE}/skill-hub/export?{PERIOD_QS}")

    assert detail.status_code == 200, detail.text
    assert detail.json()["runtimeStatus"] == "unavailable"
    assert detail.json()["data"]["usage"]["runtime"] is None
    # MySQL-only usage still answers during the outage.
    assert detail.json()["data"]["usage"]["users"] == 2
    # The library view carries no runtime data, so it stays `ok`.
    assert catalog.json()["runtimeStatus"] == "ok"
    assert export.status_code == 200, export.text
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        library = archive.read("library.csv").decode("utf-8-sig")
    assert library.splitlines()[0] == "# runtime=unavailable"


def test_period_dates_are_required() -> None:
    response = _client().get(f"{BASE}/skill-hub/summary")

    assert response.status_code == 422
