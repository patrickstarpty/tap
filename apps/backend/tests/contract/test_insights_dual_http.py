from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tap_platform.access import AccessPrincipal
from tap_platform.app import create_app
from tap_platform.insights.http import DualBearerInsightsAuthorizer


NOW = datetime.now(UTC)


def principal(kind: str, **changes: object) -> AccessPrincipal:
    values: dict[str, object] = {
        "project_id": "project-a",
        "principal_id": f"{kind}-a",
        "principal_type": kind,
        "audience": "tap" if kind == "user" else "tap-insights",
        "expires_at": NOW + timedelta(minutes=5),
        "actions": (
            frozenset({"insights.metrics.read"})
            if kind == "user"
            else frozenset({"insights.invoke"})
        ),
        "enabled": True,
    }
    values.update(changes)
    return AccessPrincipal(**values)  # type: ignore[arg-type]


def client(*, service: AccessPrincipal | None = None) -> TestClient:
    return TestClient(
        create_app(
            insights_authorizer=DualBearerInsightsAuthorizer(
                user_token="delegated-user-token-0001",
                user=principal("user"),
                service_token="service-token-00000001",
                service=service or principal("service"),
                expected_user_audience="tap",
                expected_service_audience="tap-insights",
            )
        )
    )


def test_ai_insights_http_requires_user_and_service_identity() -> None:
    headers = {
        "Authorization": "Bearer delegated-user-token-0001",
        "X-TAP-Service-Authorization": "Bearer service-token-00000001",
        "X-TAP-Authorization-Version": "authz-1",
    }

    allowed = client().get(
        "/api/v1/projects/project-a/insights/metrics", headers=headers
    )
    missing_service = client().get(
        "/api/v1/projects/project-a/insights/metrics",
        headers={"Authorization": headers["Authorization"]},
    )
    stale_authorization = client().get(
        "/api/v1/projects/project-a/insights/metrics",
        headers={**headers, "X-TAP-Authorization-Version": "authz-old"},
    )

    assert allowed.status_code == 200
    assert missing_service.status_code == 403
    assert stale_authorization.status_code == 403


def test_ai_insights_http_rejects_expired_service_and_other_project() -> None:
    headers = {
        "Authorization": "Bearer delegated-user-token-0001",
        "X-TAP-Service-Authorization": "Bearer service-token-00000001",
        "X-TAP-Authorization-Version": "authz-1",
    }
    expired = client(
        service=principal("service", expires_at=NOW - timedelta(seconds=1))
    )

    assert (
        expired.get(
            "/api/v1/projects/project-a/insights/metrics", headers=headers
        ).status_code
        == 403
    )
    assert (
        client()
        .get("/api/v1/projects/project-b/insights/metrics", headers=headers)
        .status_code
        == 403
    )


def test_dual_authorizer_hides_credentials() -> None:
    authorizer = DualBearerInsightsAuthorizer(
        user_token="delegated-user-token-0001",
        user=principal("user"),
        service_token="service-token-00000001",
        service=principal("service"),
        expected_user_audience="tap",
        expected_service_audience="tap-insights",
    )

    assert "delegated-user-token-0001" not in repr(authorizer)
    assert "service-token-00000001" not in repr(authorizer)


def test_ai_service_identity_cannot_authorize_report_writes_or_retries() -> None:
    authorizer = DualBearerInsightsAuthorizer(
        user_token="delegated-user-token-0001",
        user=principal(
            "user",
            actions=frozenset(
                {
                    "insights.metrics.read",
                    "insights.reports.create",
                }
            ),
        ),
        service_token="service-token-00000001",
        service=principal("service"),
        expected_user_audience="tap",
        expected_service_audience="tap-insights",
    )
    credentials = {
        "bearer_token": "delegated-user-token-0001",
        "project_id": "project-a",
        "resource_kind": "report",
        "resource_id": "receipt-a",
        "service_bearer_token": "service-token-00000001",
        "authorization_version": "authz-1",
    }

    assert (
        authorizer.authorize(action="insights.reports.create", **credentials) is False
    )
