from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tap_platform.access import (
    AccessPrincipal,
    InsightsResource,
    authorize_insights_request,
)


NOW = datetime(2026, 9, 23, 8, tzinfo=UTC)
METRICS = InsightsResource(
    project_id="synthetic-commerce-project",
    kind="metric-query",
    resource_id="query-001",
)


def user(**changes) -> AccessPrincipal:
    return replace(
        AccessPrincipal(
            project_id="synthetic-commerce-project",
            principal_id="synthetic-insights-reader",
            principal_type="user",
            audience="tap",
            expires_at=NOW + timedelta(minutes=10),
            actions=frozenset({"insights.metrics.read", "insights.evidence.read"}),
            enabled=True,
        ),
        **changes,
    )


def service(**changes) -> AccessPrincipal:
    return replace(
        AccessPrincipal(
            project_id="synthetic-commerce-project",
            principal_id="synthetic-tap-ai-service",
            principal_type="service",
            audience="tap-insights",
            expires_at=NOW + timedelta(minutes=5),
            actions=frozenset({"insights.invoke"}),
            enabled=True,
        ),
        **changes,
    )


def test_insights_query_requires_current_user_and_service_authority():
    decision = authorize_insights_request(
        user(),
        service(),
        "insights.metrics.read",
        METRICS,
        expected_user_audience="tap",
        expected_service_audience="tap-insights",
        now=NOW,
    )

    assert decision.allowed
    assert decision.reason == "insights-action-allowed"


@pytest.mark.parametrize(
    "changed_user,changed_service,expected",
    [
        ({"audience": "tap-ai"}, {}, "user-audience-mismatch"),
        ({"project_id": "other-project"}, {}, "user-scope-mismatch"),
        ({"enabled": False}, {}, "user-disabled"),
        ({"expires_at": NOW}, {}, "user-expired"),
        ({}, {"audience": "tap"}, "service-audience-mismatch"),
        ({}, {"project_id": "other-project"}, "service-scope-mismatch"),
        ({}, {"enabled": False}, "service-disabled"),
        ({}, {"expires_at": NOW}, "service-expired"),
        ({}, {"actions": frozenset()}, "service-action-not-allowed"),
    ],
)
def test_insights_query_fails_closed_when_either_authority_is_invalid(
    changed_user, changed_service, expected
):
    decision = authorize_insights_request(
        user(**changed_user),
        service(**changed_service),
        "insights.metrics.read",
        METRICS,
        expected_user_audience="tap",
        expected_service_audience="tap-insights",
        now=NOW,
    )

    assert not decision.allowed
    assert decision.reason == expected


def test_evidence_download_cannot_reuse_metric_or_service_authority():
    evidence = replace(METRICS, kind="evidence", resource_id="evidence-001")
    assert authorize_insights_request(
        user(),
        service(),
        "insights.evidence.read",
        evidence,
        expected_user_audience="tap",
        expected_service_audience="tap-insights",
        now=NOW,
    ).allowed

    bypass = authorize_insights_request(
        user(actions=frozenset({"insights.metrics.read"})),
        service(),
        "insights.evidence.read",
        evidence,
        expected_user_audience="tap",
        expected_service_audience="tap-insights",
        now=NOW,
    )
    assert not bypass.allowed
    assert bypass.reason == "user-action-not-allowed"
