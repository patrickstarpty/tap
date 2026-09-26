from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tap_platform.app import create_app
from tap_platform.insights.application.queries import (
    InMemoryQueryHistory,
    InsightsQueryService,
    QueryLimits,
)
from tap_platform.insights.domain.metrics import MetricAttempt
from tap_platform.insights.domain.projection import ProjectionSnapshot


class MutableAuthorizer:
    def __init__(self) -> None:
        self.enabled = True

    def authorize(self, **_: object) -> bool:
        return self.enabled


class FactSource:
    def __init__(self) -> None:
        self.snapshot = ProjectionSnapshot(
            projection_version="insights-v1", visible_data_version=7
        )
        self.available = True

    def query_attempts(
        self,
        *,
        snapshot: ProjectionSnapshot,
        project_id: str,
        limits: QueryLimits,
    ) -> list[MetricAttempt]:
        if not self.available:
            raise ConnectionError("ClickHouse unavailable")
        assert project_id == "project-a"
        assert snapshot == self.snapshot
        return [
            MetricAttempt(
                fact_key="fact-a",
                receipt_id="receipt-a",
                project_id="project-a",
                source_id="ci-a",
                external_run_id="run-a",
                stable_test_id="test-a",
                source_test_identity="Checkout.test",
                data_row=None,
                application_commit="app-1",
                script_commit="script-1",
                environment="qa",
                configuration="browser=chromium",
                attempt=1,
                result="pass",
                duration_seconds=0.4,
                first_attempt_eligible=True,
                missing_reasons=(),
                run_started_at=datetime(2026, 9, 24, 1, tzinfo=UTC),
            )
        ]


def request_body() -> dict[str, Any]:
    return {
        "metricIds": ["first_pass_rate", "final_pass_rate"],
        "filters": {
            "sourceIds": ["ci-a"],
            "runIds": [],
            "environments": ["qa"],
            "configurations": [],
        },
        "from": "2026-09-24",
        "to": "2026-09-25",
        "timezone": "Asia/Shanghai",
        "asOf": "2026-09-25T00:00:00Z",
    }


@pytest.fixture
def query_app():
    facts = FactSource()
    authorizer = MutableAuthorizer()
    service = InsightsQueryService(
        facts=facts,
        snapshots=lambda _as_of: facts.snapshot,
        history=InMemoryQueryHistory(),
        clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
        limits=QueryLimits(
            max_rows_to_read=100,
            max_bytes_to_read=1_000_000,
            max_memory_bytes=1_000_000,
            max_concurrent_queries=1,
            max_output_rows=10,
            timeout_seconds=2,
        ),
    )
    app = create_app(query_service=service, insights_authorizer=authorizer)
    return TestClient(app), facts, authorizer


def test_catalog_and_query_return_versioned_traceable_results(query_app) -> None:
    """An unversioned result cannot prove which formula or watermark produced it."""
    client, _, _ = query_app

    catalog = client.get(
        "/api/v1/projects/project-a/insights/metrics",
        headers={"Authorization": "Bearer test-token"},
    )
    created = client.post(
        "/api/v1/projects/project-a/insights/queries",
        headers={"Authorization": "Bearer test-token"},
        json=request_body(),
    )

    assert catalog.status_code == 200
    assert {item["metricId"] for item in catalog.json()["items"]} >= {
        "first_pass_rate",
        "final_pass_rate",
        "retry_recovery_rate",
    }
    assert created.status_code == 201
    body = created.json()
    assert body["metricVersion"] == "insights-metrics-v1"
    assert body["factWatermark"] == {
        "projectionVersion": "insights-v1",
        "visibleDataVersion": 7,
    }
    assert body["filters"] == request_body()["filters"]
    assert body["timezone"] == "Asia/Shanghai"
    assert body["asOf"] == "2026-09-25T00:00:00Z"
    assert body["metrics"][0] == {
        "metricId": "first_pass_rate",
        "numerator": 1,
        "denominator": 1,
        "value": 1.0,
        "completeness": "complete",
        "missingReasons": [],
        "evidenceRefs": ["receipt-a"],
    }
    assert body["queryId"]


def test_historical_query_is_immutable_and_reauthorized_after_revocation(
    query_app,
) -> None:
    """Recomputing history or skipping auth would mutate or leak old query evidence."""
    client, facts, authorizer = query_app
    created = client.post(
        "/api/v1/projects/project-a/insights/queries",
        headers={"Authorization": "Bearer test-token"},
        json=request_body(),
    ).json()
    facts.snapshot = ProjectionSnapshot(
        projection_version="insights-v2", visible_data_version=99
    )

    historical = client.get(
        f"/api/v1/projects/project-a/insights/queries/{created['queryId']}",
        headers={"Authorization": "Bearer test-token"},
    )
    assert historical.status_code == 200
    assert historical.json() == created

    authorizer.enabled = False
    denied = client.get(
        f"/api/v1/projects/project-a/insights/queries/{created['queryId']}",
        headers={"Authorization": "Bearer test-token"},
    )
    assert denied.status_code == 403


def test_run_and_failure_pages_use_the_persisted_query_scope(query_app) -> None:
    """Accepting fresh filters on drilldown could diverge cards from details."""
    client, _, _ = query_app
    query_id = client.post(
        "/api/v1/projects/project-a/insights/queries",
        headers={"Authorization": "Bearer test-token"},
        json=request_body(),
    ).json()["queryId"]

    runs = client.get(
        "/api/v1/projects/project-a/insights/runs",
        params={"queryId": query_id, "limit": 10},
        headers={"Authorization": "Bearer test-token"},
    )
    failures = client.get(
        "/api/v1/projects/project-a/insights/failures",
        params={"queryId": query_id, "limit": 10},
        headers={"Authorization": "Bearer test-token"},
    )

    assert runs.status_code == 200
    assert runs.json() == {
        "queryId": query_id,
        "items": [
            {
                "runId": "run-a",
                "sourceId": "ci-a",
                "environment": "qa",
                "configuration": "browser=chromium",
                "startedAt": "2026-09-24T01:00:00Z",
                "instanceCount": 1,
                "evidenceRefs": ["receipt-a"],
            }
        ],
        "nextCursor": None,
    }
    assert failures.status_code == 200
    assert failures.json() == {
        "queryId": query_id,
        "items": [],
        "nextCursor": None,
    }
    attempts = client.get(
        "/api/v1/projects/project-a/insights/runs/run-a/attempts",
        params={"queryId": query_id, "limit": 10},
        headers={"Authorization": "Bearer test-token"},
    )
    assert attempts.status_code == 200
    assert attempts.json() == {
        "queryId": query_id,
        "runId": "run-a",
        "items": [
            {
                "factKey": "fact-a",
                "stableTestId": "test-a",
                "sourceTestIdentity": "Checkout.test",
                "dataRow": None,
                "attempt": 1,
                "result": "pass",
                "durationSeconds": 0.4,
                "evidenceRefs": ["receipt-a"],
            }
        ],
        "nextCursor": None,
    }
    assert (
        client.get(
            "/api/v1/projects/project-a/insights/runs",
            params={"queryId": "unknown", "limit": 10},
            headers={"Authorization": "Bearer test-token"},
        ).status_code
        == 404
    )


def test_unavailable_query_dependency_returns_explicit_failure_not_partial_result(
    query_app,
) -> None:
    """Returning an empty metric array on ClickHouse failure looks like valid no-data."""
    client, facts, _ = query_app
    facts.available = False

    response = client.post(
        "/api/v1/projects/project-a/insights/queries",
        headers={"Authorization": "Bearer test-token"},
        json=request_body(),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "insights query unavailable"
    assert "metrics" not in response.json()


def test_query_output_limit_returns_explicit_failure_not_truncated_data() -> None:
    """Silently truncating facts would publish a partial denominator."""

    class TooManyFacts(FactSource):
        def query_attempts(self, **kwargs: object) -> list[MetricAttempt]:
            return super().query_attempts(**kwargs) * 2

    large = TooManyFacts()
    service = InsightsQueryService(
        facts=large,
        snapshots=lambda _as_of: large.snapshot,
        history=InMemoryQueryHistory(),
        clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
        limits=QueryLimits(
            max_rows_to_read=100,
            max_bytes_to_read=1_000_000,
            max_memory_bytes=1_000_000,
            max_concurrent_queries=1,
            max_output_rows=1,
            timeout_seconds=2,
        ),
    )
    client = TestClient(
        create_app(query_service=service, insights_authorizer=MutableAuthorizer())
    )

    response = client.post(
        "/api/v1/projects/project-a/insights/queries",
        headers={"Authorization": "Bearer test-token"},
        json=request_body(),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "insights query limit exceeded"
    assert "metrics" not in response.json()


def test_final_serialized_output_byte_limit_fails_closed() -> None:
    """Per-SQL limits alone must not permit an oversized assembled response."""
    facts = FactSource()
    service = InsightsQueryService(
        facts=facts,
        snapshots=lambda _as_of: facts.snapshot,
        history=InMemoryQueryHistory(),
        clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
        limits=QueryLimits(
            max_rows_to_read=100,
            max_bytes_to_read=1_000_000,
            max_memory_bytes=1_000_000,
            max_concurrent_queries=1,
            max_output_rows=10,
            max_output_bytes=10,
            timeout_seconds=2,
        ),
    )
    client = TestClient(
        create_app(query_service=service, insights_authorizer=MutableAuthorizer())
    )

    response = client.post(
        "/api/v1/projects/project-a/insights/queries",
        headers={"Authorization": "Bearer test-token"},
        json=request_body(),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "insights query limit exceeded"


def test_query_timeout_returns_gateway_timeout_without_partial_metrics() -> None:
    """A timed-out scan must not serialize facts read before cancellation."""

    class TimedOutFacts(FactSource):
        def query_attempts(self, **_: object) -> list[MetricAttempt]:
            raise TimeoutError("deadline exceeded")

    facts = TimedOutFacts()
    service = InsightsQueryService(
        facts=facts,
        snapshots=lambda _as_of: facts.snapshot,
        history=InMemoryQueryHistory(),
        clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
        limits=QueryLimits(
            max_rows_to_read=100,
            max_bytes_to_read=1_000_000,
            max_memory_bytes=1_000_000,
            max_concurrent_queries=1,
            max_output_rows=10,
            timeout_seconds=2,
        ),
    )
    client = TestClient(
        create_app(query_service=service, insights_authorizer=MutableAuthorizer())
    )

    response = client.post(
        "/api/v1/projects/project-a/insights/queries",
        headers={"Authorization": "Bearer test-token"},
        json=request_body(),
    )

    assert response.status_code == 504
    assert response.json()["detail"] == "insights query timed out"
    assert "metrics" not in response.json()


def test_unknown_metric_is_rejected_by_the_server_allowlist(query_app) -> None:
    """Accepting arbitrary metric names would create an unreviewed query template path."""
    client, _, _ = query_app
    body = request_body()
    body["metricIds"] = ["arbitrary_sql_metric"]

    response = client.post(
        "/api/v1/projects/project-a/insights/queries",
        headers={"Authorization": "Bearer test-token"},
        json=body,
    )

    assert response.status_code == 422
