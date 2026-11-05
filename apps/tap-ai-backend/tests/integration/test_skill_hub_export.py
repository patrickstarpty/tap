"""Skill Hub CSV zip export integration test (Task 10, 2026-11-01 Skill Hub
phase 1): Skills and activations seeded in MySQL, read back by the real
`MysqlSkillHubStore` through `SkillHubService` and downloaded over HTTP,
confirming Chinese Skill names survive the MySQL -> zip/CSV/BOM pipeline.
The runtime backend is a fake (no Langfuse).

Requires `TAP_RUN_MYSQL_INTEGRATION=1`; uses the isolated `owned_project_mysql`
fixture (never the shared default MySQL service)."""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from scripts.migration_support import IsolatedMysql

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.ai.adapters.mysql import skill
from tap.modules.skill_hub.adapters.cached_metrics import CachedSkillRuntimeMetrics
from tap.modules.skill_hub.adapters.mysql import MysqlSkillHubStore
from tap.modules.skill_hub.application.service import SkillHubService
from tap.modules.skill_hub.ports import SkillRuntimeStats
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.reporting.period import Period
from tap.platform.reporting.ttl_cache import TtlSingleFlightCache
from tests.integration.test_agent_team_store import _scope_values
from tests.integration.test_skill_hub_store import _insert_skill_revision, _insert_used_turn
from tests.owned_mysql import owned_project_database_url

_T0 = datetime(2026, 10, 1, 9, 0, 0)
_NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
_SKILL_NAME = "测试用例设计"
_DESCRIPTION = "根据需求生成测试用例"


class _Runtime:
    async def skill_stats(self, project_id: str, period: Period) -> list[SkillRuntimeStats]:
        del project_id, period
        return [
            SkillRuntimeStats(
                skill_id="s-cn", runs=2, failures=0, latency_total_ms=300, cost_usd=Decimal("0.2")
            )
        ]


class _Catalog:
    async def list_revisions(self, kind: str, asset_id: str) -> tuple[object, ...]:
        del kind, asset_id
        return ()


class _Authority:
    async def current(self, _facts):
        return VALIDATION_SCOPE

    async def authorize(self, _scope, action, _resource):
        del action
        return AuthorizationDecision(True, "validation-allowed")


async def _seed(connection) -> None:
    await connection.execute(
        sa.insert(skill).values(
            skill_id="s-cn",
            display_name=_SKILL_NAME,
            created_at=_T0,
            category="test-design",
            owner_actor_id=VALIDATION_SCOPE.actor_id,
            owner_assigned_at=_T0,
            **_scope_values(VALIDATION_SCOPE),
        )
    )
    await _insert_skill_revision(
        connection,
        VALIDATION_SCOPE,
        "s-cn",
        revision_number=1,
        display_name=_SKILL_NAME,
        description=_DESCRIPTION,
    )
    await _insert_used_turn(connection, VALIDATION_SCOPE, "t-cn-1", [("s-cn", "manual")])
    await _insert_used_turn(connection, VALIDATION_SCOPE, "t-cn-2", [("s-cn", "auto")])


def _seed_database(url: str) -> None:
    async def seed() -> None:
        engine, _sessions = create_engine_and_session_factory(url)
        try:
            async with engine.begin() as connection:
                await _seed(connection)
        finally:
            await engine.dispose()

    asyncio.run(seed())


def test_chinese_names_round_trip(owned_project_mysql: IsolatedMysql) -> None:
    url = owned_project_database_url(owned_project_mysql)
    _seed_database(url)
    # The service's engine is only ever used (and disposed) on the TestClient
    # portal's event loop.
    engine, sessions = create_engine_and_session_factory(url)
    service = SkillHubService(
        MysqlSkillHubStore(sessions, scope=VALIDATION_SCOPE),
        CachedSkillRuntimeMetrics(_Runtime(), TtlSingleFlightCache(now=lambda: _NOW)),
        asset_catalog=_Catalog(),
        scope=VALIDATION_SCOPE,
        clock=lambda: _NOW,
    )
    authority = _Authority()
    origin = "http://127.0.0.1:15176"
    app = create_app(
        HttpServices(
            skill_hub=service,
            scope=VALIDATION_SCOPE,
            scope_provider=authority,
            authorization_policy=authority,
        ),
        allowed_origins=frozenset({origin}),
    )
    with TestClient(app, headers={"Origin": origin}) as client:
        try:
            response = client.get(
                "/api/v1/projects/tapper-demo/ai/skill-hub/export?from=2026-10-01&to=2026-10-23"
            )
        finally:
            client.portal.call(engine.dispose)

    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == (
        'attachment; filename="skill-hub-2026-10-01-2026-10-23.zip"'
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        library_raw = archive.read("library.csv")
        usage_text = archive.read("usage.csv").decode("utf-8-sig")
    assert library_raw.startswith(b"\xef\xbb\xbf")
    header, row = list(csv.reader(io.StringIO(library_raw.decode("utf-8-sig"))))
    cells = dict(zip(header, row, strict=True))
    assert cells["name"] == _SKILL_NAME
    assert cells["description"] == _DESCRIPTION
    assert cells["category"] == "test-design"
    assert (cells["activations_manual"], cells["activations_auto"]) == ("1", "1")
    assert cells["cost_usd"] == "0.2"

    usage_lines = usage_text.splitlines()
    assert usage_lines[0] == "# granularity=day"
    usage_header, *usage_rows = csv.reader(usage_lines[1:])
    assert usage_header == ["bucket_start", "bucket_end", "skill_id", "mode", "runs"]
    assert sorted(usage_rows) == [
        ["2026-10-01", "2026-10-01", "s-cn", "auto", "1"],
        ["2026-10-01", "2026-10-01", "s-cn", "manual", "1"],
    ]
