import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from tap.entrypoints.tapper_runtime import TapperSettings
from tap.interfaces.http.insights_explanation_runtime import ConfiguredInsightsExplanation
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.tap_insights import (
    ServiceIdentity,
    TapInsightsAdapter,
    TapInsightsConfig,
)
from tap.modules.ai.ports.insights import (
    FactWatermark,
    InsightsAuthorizationChanged,
    InsightsQueryUnavailable,
    MetricFact,
    MetricQuery,
    MetricResult,
)
from tests.object_settings import S3_SETTINGS

NOW = datetime(2026, 9, 25, 8, tzinfo=UTC)


def test_insights_settings_require_complete_matching_server_delegation() -> None:
    values = S3_SETTINGS | {
        "TAP_INSIGHTS_BASE_URL": "http://127.0.0.1:8001",
        "TAP_INSIGHTS_DELEGATED_USER_TOKEN": "delegated-user-token-0001",
        "TAP_INSIGHTS_SERVICE_TOKEN": "service-token-00000001",
        "TAP_INSIGHTS_DELEGATED_PROJECT_ID": "tapper-demo",
        "TAP_INSIGHTS_DELEGATED_EXPIRES_AT": "2099-01-01T00:00:00+00:00",
        "TAP_INSIGHTS_AUTHORIZATION_VERSION": "authz-1",
        "TAP_INSIGHTS_MAX_MICROS_PER_TOKEN": "100",
    }
    assert TapperSettings.from_mapping(values).insights_base_url == "http://127.0.0.1:8001"
    with pytest.raises(ValueError, match="all server-side"):
        TapperSettings.from_mapping(values | {"TAP_INSIGHTS_SERVICE_TOKEN": ""})
    with pytest.raises(ValueError, match="fixed Tapper project"):
        TapperSettings.from_mapping(values | {"TAP_INSIGHTS_DELEGATED_PROJECT_ID": "other"})


def historical() -> MetricResult:
    return MetricResult(
        query_id="query-a",
        metric_version="insights-metrics-v1",
        query=MetricQuery(
            metric_ids=("first_pass_rate",),
            source_ids=("ci-a",),
            run_ids=(),
            build_ids=(),
            branches=(),
            environments=(),
            configurations=(),
            from_date="2026-09-24",
            to_date="2026-09-25",
            timezone="UTC",
            as_of=NOW,
        ),
        fact_watermark=FactWatermark("insights-v1", 7),
        metrics=(MetricFact("first_pass_rate", 1, 2, 0.5, "complete", (), ("receipt-a",)),),
        report_coverage=(),
    )


@pytest.mark.asyncio
async def test_persisted_result_reauthorization_stalled_port_is_bounded() -> None:
    class StalledInsights:
        async def get_insights(self, _scope, _query_id):
            await asyncio.Future()

    runtime = ConfiguredInsightsExplanation(
        insights=StalledInsights(),
        gateway=None,
        model_alias="qwen-plus",
        max_micros_per_token=1,
        project_id="tapper-demo",
        delegated_user_token="delegated-user-token-0001",
        authorization_version="authz-1",
        reauthorization_timeout_seconds=0.01,
    )
    with pytest.raises(InsightsQueryUnavailable, match="budget"):
        await runtime.reauthorize_result(
            VALIDATION_SCOPE,
            "query-a",
            ("receipt-a",),
            (),
            {"queryId": "query-a"},
        )


@pytest.mark.asyncio
async def test_runtime_verifies_historical_query_and_receipt_before_returning_facts() -> None:
    seen: list[str] = []

    class Insights:
        async def get_insights(self, scope, query_id):
            assert scope.context == VALIDATION_SCOPE
            assert scope.user_authorization == "delegated-user-token-0001"
            assert query_id == "query-a"
            seen.append("query")
            return historical()

        async def get_evidence(self, scope, receipt_id, *, max_bytes):
            assert scope.context == VALIDATION_SCOPE
            assert receipt_id == "receipt-a"
            assert max_bytes <= 20_000
            seen.append("receipt")
            return b"<testsuite><failure>Assertion failed</failure></testsuite>"

    class Gateway:
        async def generate_structured(self, request):
            assert request.scope == VALIDATION_SCOPE
            assert request.allow_retries is False
            assert "receipt-a" in request.context
            seen.append("model")
            return SimpleNamespace(
                output={
                    "hypotheses": [
                        {
                            "text": "The report records an assertion failure.",
                            "citationId": "receipt-a",
                            "evidenceQuote": "Assertion failed",
                        }
                    ],
                    "missingInformation": ["Application logs were not supplied."],
                }
            )

    runtime = ConfiguredInsightsExplanation(
        insights=Insights(),
        gateway=Gateway(),
        model_alias="qwen-plus",
        max_micros_per_token=1,
        project_id="tapper-demo",
        delegated_user_token="delegated-user-token-0001",
        authorization_version="authz-1",
    )
    result = await runtime.explain(
        VALIDATION_SCOPE,
        "query-a",
        ("receipt-a",),
        "Why failed?",
        conversation_id="conversation-a",
        turn_id="turn-a",
    )

    assert seen == ["query", "receipt", "query", "model", "receipt"]
    assert result["facts"][0]["value"] == 0.5
    assert result["queryId"] == "query-a"
    assert "receipt-a" in result["hypotheses"][0]
    assert result["stopReason"] == "completed"


@pytest.mark.asyncio
async def test_empty_report_narrative_cannot_be_rendered_as_evidence() -> None:
    class Insights:
        async def get_insights(self, _scope, _query_id):
            return historical()

        async def get_evidence(self, _scope, _receipt_id, *, max_bytes):
            assert max_bytes <= 20_000
            return b"<testsuite/>"

    class Gateway:
        async def generate_structured(self, _request):
            raise AssertionError("empty report narrative must not reach the model")

    runtime = ConfiguredInsightsExplanation(
        insights=Insights(),
        gateway=Gateway(),
        model_alias="qwen-plus",
        max_micros_per_token=1,
        project_id="tapper-demo",
        delegated_user_token="delegated-user-token-0001",
        authorization_version="authz-1",
    )
    result = await runtime.explain(
        VALIDATION_SCOPE,
        "query-a",
        ("receipt-a",),
        "Why failed?",
        conversation_id="conversation-a",
        turn_id="turn-a",
    )

    assert result["facts"][0]["value"] == 0.5
    assert result["hypotheses"] == []
    assert result["evidenceExcerpts"] == []
    assert result["missingInformation"] == [
        "No report narrative or approved knowledge excerpt was available; "
        "provide failure logs or select an approved knowledge source."
    ]


@pytest.mark.asyncio
async def test_runtime_rejects_receipts_that_tap_does_not_authorize() -> None:
    class Insights:
        async def get_insights(self, _scope, _query_id):
            return historical()

        async def get_evidence(self, _scope, _receipt_id, *, max_bytes):
            del max_bytes
            raise InsightsAuthorizationChanged("TAP denied receipt")

    runtime = ConfiguredInsightsExplanation(
        insights=Insights(),
        gateway=None,
        model_alias="qwen-plus",
        max_micros_per_token=1,
        project_id="tapper-demo",
        delegated_user_token="delegated-user-token-0001",
        authorization_version="authz-1",
    )
    with pytest.raises(InsightsAuthorizationChanged):
        await runtime.explain(
            VALIDATION_SCOPE,
            "query-a",
            ("receipt-a",),
            "Why failed?",
            conversation_id="conversation-a",
            turn_id="turn-a",
        )


@pytest.mark.asyncio
async def test_runtime_rejects_unrelated_project_receipt_before_evidence_or_model_call() -> None:
    class Insights:
        async def get_insights(self, _scope, _query_id):
            return historical()

        async def get_evidence(self, _scope, _receipt_id, *, max_bytes):
            del max_bytes
            raise AssertionError("unrelated receipt must not be fetched")

    class Gateway:
        async def generate_structured(self, _request):
            raise AssertionError("unrelated receipt must not reach model")

    runtime = ConfiguredInsightsExplanation(
        insights=Insights(),
        gateway=Gateway(),
        model_alias="qwen-plus",
        project_id="tapper-demo",
        delegated_user_token="delegated-user-token-0001",
        authorization_version="authz-1",
        max_micros_per_token=1,
    )
    with pytest.raises(InsightsAuthorizationChanged, match="historical query"):
        await runtime.explain(
            VALIDATION_SCOPE,
            "query-a",
            ("receipt-other",),
            "Why failed?",
            conversation_id="conversation-a",
            turn_id="turn-a",
        )


@pytest.mark.asyncio
async def test_explanation_uses_real_tap_adapter_and_current_report_authority() -> None:
    seen: list[str] = []

    def tap(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer delegated-user-token-0001"
        assert request.headers["x-tap-service-authorization"] == "Bearer service-token-00000001"
        assert request.headers["x-tap-authorization-version"] == "authz-1"
        seen.append(request.url.path)
        if request.url.path.endswith("/evidence/receipt-a"):
            return httpx.Response(
                200, content=b"<testsuite><failure>Assertion failed</failure></testsuite>"
            )
        if request.url.path.endswith("/queries/query-a"):
            return httpx.Response(
                200,
                json={
                    "queryId": "query-a",
                    "metricVersion": "insights-metrics-v1",
                    "filters": {
                        "sourceIds": ["ci-a"],
                        "runIds": [],
                        "buildIds": [],
                        "branches": [],
                        "environments": [],
                        "configurations": [],
                    },
                    "from": "2026-09-24",
                    "to": "2026-09-25",
                    "timezone": "UTC",
                    "asOf": "2026-09-25T08:00:00Z",
                    "createdAt": "2026-09-25T08:00:01Z",
                    "factWatermark": {"projectionVersion": "insights-v1", "visibleDataVersion": 7},
                    "metrics": [
                        {
                            "metricId": "first_pass_rate",
                            "numerator": 1,
                            "denominator": 2,
                            "value": 0.5,
                            "completeness": "complete",
                            "missingReasons": [],
                            "evidenceRefs": ["receipt-a"],
                        }
                    ],
                    "trends": [],
                    "reportCoverage": [],
                },
            )
        return httpx.Response(404)

    class Gateway:
        async def generate_structured(self, request):
            assert "Assertion failed" in request.context
            assert "first_pass_rate" in request.context
            return SimpleNamespace(
                output={
                    "hypotheses": [
                        {
                            "text": "The report records an assertion failure.",
                            "citationId": "receipt-a",
                            "evidenceQuote": "Assertion failed",
                        }
                    ],
                    "missingInformation": ["The token issue time is unknown."],
                }
            )

    adapter = TapInsightsAdapter(
        TapInsightsConfig(base_url="http://127.0.0.1:8001"),
        service_identity=lambda: ServiceIdentity(
            authorization="service-token-00000001",
            audience="tap-insights",
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        ),
        clock=lambda: NOW,
        client=httpx.AsyncClient(transport=httpx.MockTransport(tap)),
    )
    runtime = ConfiguredInsightsExplanation(
        insights=adapter,
        gateway=Gateway(),
        model_alias="qwen-plus",
        project_id="tapper-demo",
        delegated_user_token="delegated-user-token-0001",
        authorization_version="authz-1",
        max_micros_per_token=1,
    )
    result = await runtime.explain(
        VALIDATION_SCOPE,
        "query-a",
        ("receipt-a",),
        "Why failed?",
        conversation_id="conversation-a",
        turn_id="turn-a",
    )

    assert result["facts"][0]["value"] == 0.5
    assert result["evidenceExcerpts"] == [
        {
            "citationId": "receipt-a",
            "text": "Assertion failed",
            "evidenceVersion": result["evidenceExcerpts"][0]["evidenceVersion"],
        }
    ]
    assert "assertion failure" in result["hypotheses"][0]
    assert result["missingInformation"] == ["The token issue time is unknown."]
    assert seen == [
        "/api/v1/projects/tapper-demo/insights/queries/query-a",
        "/api/v1/projects/tapper-demo/insights/evidence/receipt-a",
        "/api/v1/projects/tapper-demo/insights/queries/query-a",
        "/api/v1/projects/tapper-demo/insights/evidence/receipt-a",
    ]
