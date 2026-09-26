from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from tap_platform.access import AccessPrincipal
from tap_platform.app import create_app
from tap_platform.insights.application.queries import (
    InMemoryQueryHistory,
    InsightsQueryService,
    QueryLimits,
)
from tap_platform.insights.domain.metrics import MetricAttempt
from tap_platform.insights.domain.projection import ProjectionSnapshot
from tap_platform.insights.http import DualBearerInsightsAuthorizer

from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.ai.adapters.tap_insights import (
    ServiceIdentity,
    TapInsightsAdapter,
    TapInsightsConfig,
)
from tap.modules.ai.ports.insights import AuthorizedInsightsScope, MetricQuery

NOW = datetime(2026, 9, 25, 8, tzinfo=UTC)


class OwnedFacts:
    snapshot = ProjectionSnapshot(projection_version="insights-v1", visible_data_version=41)

    def query_attempts(self, **_: object) -> list[MetricAttempt]:
        base = dict(
            receipt_id="receipt-a",
            project_id="project-a",
            source_id="ci-a",
            external_run_id="run-a",
            stable_test_id="checkout-a",
            source_test_identity="checkout::a",
            data_row=None,
            application_commit="app-a",
            script_commit="script-a",
            environment="qa",
            configuration="browser=chromium",
            duration_seconds=1.0,
            first_attempt_eligible=True,
            missing_reasons=(),
            run_started_at=datetime(2026, 9, 24, 1, tzinfo=UTC),
            build_id="build-a",
            branch="main",
        )
        return [
            MetricAttempt(fact_key="fact-1", attempt=1, result="fail", **base),
            MetricAttempt(
                fact_key="fact-2",
                attempt=2,
                result="pass",
                **{**base, "first_attempt_eligible": False},
            ),
        ]


def _principal(kind: str) -> AccessPrincipal:
    return AccessPrincipal(
        project_id="project-a",
        principal_id=f"{kind}-a",
        principal_type=kind,  # type: ignore[arg-type]
        audience="tap" if kind == "user" else "tap-insights",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        actions=(
            frozenset({"insights.metrics.read"})
            if kind == "user"
            else frozenset({"insights.invoke"})
        ),
        enabled=True,
    )


def _tap_app(service: InsightsQueryService):
    return create_app(
        query_service=service,
        insights_authorizer=DualBearerInsightsAuthorizer(
            user_token="delegated-user-token-0001",
            user=_principal("user"),
            service_token="service-token-00000001",
            service=_principal("service"),
            expected_user_audience="tap",
            expected_service_audience="tap-insights",
        ),
    )


def _scope() -> AuthorizedInsightsScope:
    return AuthorizedInsightsScope(
        context=ProjectScopeContext(
            enterprise_id="enterprise-a",
            project_id="project-a",
            actor_id="user-a",
            identity_mode=IdentityMode.PRODUCT,
        ),
        user_authorization="delegated-user-token-0001",
        authorization_version="authz-1",
        authorized_resource_refs=("receipt-a",),
    )


def _query() -> MetricQuery:
    return MetricQuery(
        metric_ids=("first_pass_rate", "final_pass_rate"),
        source_ids=("ci-a",),
        run_ids=(),
        build_ids=("build-a",),
        branches=("main",),
        environments=("qa",),
        configurations=(),
        from_date="2026-09-24",
        to_date="2026-09-25",
        timezone="UTC",
        as_of=NOW,
    )


def _service() -> InsightsQueryService:
    facts = OwnedFacts()
    return InsightsQueryService(
        facts=facts,
        snapshots=lambda _as_of: facts.snapshot,
        history=InMemoryQueryHistory(),
        clock=lambda: NOW + timedelta(seconds=1),
        limits=QueryLimits(
            max_rows_to_read=10,
            max_bytes_to_read=1_000_000,
            max_memory_bytes=1_000_000,
            max_concurrent_queries=1,
            max_output_rows=100,
            timeout_seconds=2,
        ),
    )


def _adapter(app) -> TapInsightsAdapter:
    return TapInsightsAdapter(
        TapInsightsConfig(base_url="http://127.0.0.1"),
        service_identity=lambda: ServiceIdentity(
            "service-token-00000001",
            "tap-insights",
            datetime.now(UTC) + timedelta(minutes=5),
        ),
        clock=lambda: datetime.now(UTC),
        client=httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ),
    )


@pytest.mark.asyncio
async def test_owned_tap_http_keeps_page_and_ai_query_parity_across_app_restart() -> None:
    service = _service()
    first = _adapter(_tap_app(service))

    created = await first.query_insights(_scope(), _query())
    page_read = await first.get_insights(_scope(), created.query_id)
    await first.aclose()

    restarted = _adapter(_tap_app(service))
    after_restart = await restarted.get_insights(_scope(), created.query_id)
    await restarted.aclose()

    assert created == page_read == after_restart
    assert [(item.numerator, item.denominator) for item in created.metrics] == [
        (0, 1),
        (1, 1),
    ]
    assert created.fact_watermark.visible_data_version == 41
    assert created.query.as_of == NOW
