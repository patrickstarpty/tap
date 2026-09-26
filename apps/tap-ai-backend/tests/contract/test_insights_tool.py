from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.ai.adapters.tap_insights import (
    ServiceIdentity,
    TapInsightsAdapter,
    TapInsightsConfig,
)
from tap.modules.ai.application.insights_explanation import (
    ExplanationBudget,
    ExplanationRequest,
    InsightsExplanationService,
    ProposedExplanation,
)
from tap.modules.ai.ports.insights import (
    AuthorizedEvidence,
    AuthorizedInsightsScope,
    FactWatermark,
    InsightsAuthorizationChanged,
    InsightsQueryUnavailable,
    MetricFact,
    MetricQuery,
    MetricResult,
    ReportCoverage,
    StructuredInsightsTool,
)

NOW = datetime(2026, 9, 25, 8, tzinfo=UTC)


def scope(**changes: object) -> AuthorizedInsightsScope:
    values: dict[str, object] = {
        "context": ProjectScopeContext(
            enterprise_id="enterprise-a",
            project_id="project-a",
            actor_id="user-a",
            identity_mode=IdentityMode.PRODUCT,
        ),
        "user_authorization": "delegated-user-token-0001",
        "authorization_version": "authz-7",
        "authorized_resource_refs": ("receipt-a",),
    }
    values.update(changes)
    return AuthorizedInsightsScope(**values)  # type: ignore[arg-type]


def query() -> MetricQuery:
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
        timezone="Asia/Shanghai",
        as_of=NOW,
    )


def result(**changes: object) -> MetricResult:
    values: dict[str, object] = {
        "query_id": "query-a",
        "metric_version": "insights-metrics-v1",
        "query": query(),
        "fact_watermark": FactWatermark("insights-v1", 7),
        "metrics": (
            MetricFact("first_pass_rate", 1, 3, 1 / 3, "complete", (), ("receipt-a",)),
            MetricFact("final_pass_rate", 2, 3, 2 / 3, "complete", (), ("receipt-a",)),
        ),
        "report_coverage": (),
    }
    values.update(changes)
    return MetricResult(**values)  # type: ignore[arg-type]


def test_non_coverage_identifiers_keep_the_narrow_ai_boundary() -> None:
    with pytest.raises(ValueError, match="query_id"):
        result(query_id="ci/github")
    with pytest.raises(ValueError, match="authorization_version"):
        scope(authorization_version="a" * 129)


def response_body(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "queryId": "query-a",
        "metricVersion": "insights-metrics-v1",
        "filters": {
            "sourceIds": ["ci-a"],
            "runIds": [],
            "buildIds": ["build-a"],
            "branches": ["main"],
            "environments": ["qa"],
            "configurations": [],
        },
        "from": "2026-09-24",
        "to": "2026-09-25",
        "timezone": "Asia/Shanghai",
        "asOf": "2026-09-25T08:00:00Z",
        "createdAt": "2026-09-25T08:00:01Z",
        "factWatermark": {
            "projectionVersion": "insights-v1",
            "visibleDataVersion": 7,
        },
        "metrics": [
            {
                "metricId": "first_pass_rate",
                "numerator": 1,
                "denominator": 3,
                "value": 1 / 3,
                "completeness": "complete",
                "missingReasons": [],
                "evidenceRefs": ["receipt-a"],
            },
            {
                "metricId": "final_pass_rate",
                "numerator": 2,
                "denominator": 3,
                "value": 2 / 3,
                "completeness": "complete",
                "missingReasons": [],
                "evidenceRefs": ["receipt-a"],
            },
        ],
        "trends": [],
        "reportCoverage": [],
    }
    value.update(changes)
    return value


def adapter(
    handler: httpx.MockTransport,
    *,
    identity: ServiceIdentity | None = None,
) -> TapInsightsAdapter:
    return TapInsightsAdapter(
        TapInsightsConfig(base_url="https://tap.example"),
        service_identity=lambda: identity
        or ServiceIdentity(
            authorization="service-token-00000001",
            audience="tap-insights",
            expires_at=NOW + timedelta(minutes=5),
        ),
        clock=lambda: NOW,
        client=httpx.AsyncClient(transport=handler),
    )


@pytest.mark.asyncio
async def test_structured_tool_rejects_model_project_scope_and_has_no_write_actions() -> None:
    """Adding a model-controlled project or write action would widen TAP authority."""

    class Port:
        async def query_insights(self, _scope, _query):
            raise AssertionError("invalid input reached TAP")

        async def get_insights(self, _scope, _query_id):
            raise AssertionError("invalid input reached TAP")

    tool = StructuredInsightsTool(Port())

    with pytest.raises(ValueError, match="closed"):
        await tool.invoke(
            scope(),
            {
                "projectId": "other-project",
                "metricIds": ["first_pass_rate"],
                "from": "2026-09-24",
                "to": "2026-09-25",
                "timezone": "UTC",
                "asOf": "2026-09-25T08:00:00Z",
                "filters": {},
            },
        )

    assert tool.read_only is True
    assert tool.name == "insights.query"
    assert not any(hasattr(tool, name) for name in ("publish", "rerun", "create_ticket"))


@pytest.mark.asyncio
async def test_http_adapter_uses_scope_project_and_dual_identity() -> None:
    """Dropping either delegated user or service auth would break dual authorization."""
    observed: httpx.Request | None = None

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal observed
        observed = request
        return httpx.Response(201, json=response_body())

    client = adapter(httpx.MockTransport(handle))
    value = await client.query_insights(scope(), query())

    assert value == result()
    assert observed is not None
    assert observed.url.path == "/api/v1/projects/project-a/insights/queries"
    assert observed.headers["authorization"] == "Bearer delegated-user-token-0001"
    assert observed.headers["x-tap-service-authorization"] == "Bearer service-token-00000001"
    assert "projectId" not in observed.content.decode()
    await client.aclose()


@pytest.mark.asyncio
async def test_http_adapter_rejects_expired_service_identity_before_network() -> None:
    called = False

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201, json=response_body())

    client = adapter(
        httpx.MockTransport(handle),
        identity=ServiceIdentity(
            authorization="service-token-00000001",
            audience="tap-insights",
            expires_at=NOW,
        ),
    )

    with pytest.raises(InsightsAuthorizationChanged, match="service identity expired"):
        await client.query_insights(scope(), query())
    assert called is False
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "failure"),
    [
        (403, InsightsAuthorizationChanged),
        (422, InsightsQueryUnavailable),
        (503, InsightsQueryUnavailable),
        (504, InsightsQueryUnavailable),
    ],
)
async def test_http_adapter_fails_closed_without_database_fallback(
    status: int, failure: type[Exception]
) -> None:
    calls = 0

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"detail": "unavailable"})

    client = adapter(httpx.MockTransport(handle))

    with pytest.raises(failure):
        await client.query_insights(scope(), query())
    assert calls == 1
    assert not hasattr(client, "database")
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        {"queryId": ""},
        {"metricVersion": ""},
        {"factWatermark": {"projectionVersion": "", "visibleDataVersion": 7}},
    ],
)
async def test_http_adapter_rejects_missing_or_changed_response_binding(
    changed: dict[str, object],
) -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=response_body(**changed))

    client = adapter(httpx.MockTransport(handle))
    with pytest.raises(InsightsQueryUnavailable, match="contract"):
        await client.query_insights(scope(), query())
    await client.aclose()


@pytest.mark.asyncio
async def test_http_adapter_rejects_internally_inconsistent_metric_values() -> None:
    body = response_body()
    metrics = body["metrics"]
    assert isinstance(metrics, list)
    assert isinstance(metrics[0], dict)
    metrics[0]["value"] = 0.9

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=body)

    client = adapter(httpx.MockTransport(handle))
    with pytest.raises(InsightsQueryUnavailable, match="contract response"):
        await client.query_insights(scope(), query())
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("completeness", ["partial", "unknown"])
async def test_http_adapter_preserves_report_coverage_without_inventing_metrics(
    completeness: str,
) -> None:
    coverage = {
        "sourceId": "ci-a",
        "externalRunId": "run-a",
        "reportBatchId": "batch-a",
        "expectedShards": 2 if completeness == "partial" else None,
        "receivedShards": 1,
        "completeness": completeness,
        "missingReasons": [
            "report-shards-missing" if completeness == "partial" else "expected-shards-unknown"
        ],
    }
    body = response_body(reportCoverage=[coverage])
    for metric in body["metrics"]:
        metric["numerator"] = None
        metric["denominator"] = None
        metric["value"] = None
        metric["completeness"] = "unavailable"
        metric["missingReasons"] = coverage["missingReasons"]

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = adapter(httpx.MockTransport(handle))
    created = await client.query_insights(scope(), query())
    historical = await client.get_insights(scope(), "query-a")
    assert created == historical
    assert created.report_coverage == (
        ReportCoverage(
            source_id="ci-a",
            external_run_id="run-a",
            report_batch_id="batch-a",
            expected_shards=coverage["expectedShards"],
            received_shards=1,
            completeness=completeness,
            missing_reasons=tuple(coverage["missingReasons"]),
        ),
    )
    assert all(fact.value is None for fact in created.metrics)
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "coverage_change",
    [
        {"receivedShards": -1},
        {"expectedShards": 0},
        {"completeness": "future"},
        {"completeness": "complete", "missingReasons": ["report-shards-missing"]},
        {"completeness": "partial", "missingReasons": []},
        {
            "completeness": "partial",
            "expectedShards": 2,
            "missingReasons": ["report-shards-missing"],
        },
        {"unexpected": True},
        {"sourceId": "a" * 257},
        {"reportBatchId": "a" * 257},
        {"externalRunId": "a" * 257},
        {"sourceId": "ci github"},
        {"reportBatchId": "ci\tgithub"},
        {"externalRunId": "ci\ngithub"},
        {"sourceId": "ci?github"},
        {"reportBatchId": "ci/githubé"},
        {"sourceId": "/ci-github"},
    ],
)
async def test_http_adapter_rejects_tampered_report_coverage(
    coverage_change: dict[str, object],
) -> None:
    coverage = {
        "sourceId": "ci-a",
        "externalRunId": "run-a",
        "reportBatchId": "batch-a",
        "expectedShards": 1,
        "receivedShards": 1,
        "completeness": "complete",
        "missingReasons": [],
        **coverage_change,
    }

    client = adapter(
        httpx.MockTransport(
            lambda _request: httpx.Response(200, json=response_body(reportCoverage=[coverage]))
        )
    )
    with pytest.raises(InsightsQueryUnavailable, match="contract"):
        await client.query_insights(scope(), query())
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing-coverage", "unknown-root-field"])
async def test_http_adapter_rejects_missing_coverage_or_unknown_root_field(mutation: str) -> None:
    body = response_body()
    if mutation == "missing-coverage":
        del body["reportCoverage"]
    else:
        body["serverOnly"] = True
    client = adapter(httpx.MockTransport(lambda _request: httpx.Response(200, json=body)))

    with pytest.raises(InsightsQueryUnavailable, match="contract response"):
        await client.query_insights(scope(), query())
    await client.aclose()


@pytest.mark.asyncio
async def test_historical_query_rejects_a_different_query_id() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body(queryId="query-other"))

    client = adapter(httpx.MockTransport(handle))
    with pytest.raises(InsightsQueryUnavailable, match="query ID"):
        await client.get_insights(scope(), "query-a")
    await client.aclose()


class StubInsights:
    def __init__(self, value: MetricResult | Exception):
        self.value = value
        self.calls = 0

    async def query_insights(self, _scope, _query):
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return self.value

    async def get_insights(self, _scope, _query_id):
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class StubKnowledge:
    def __init__(
        self,
        evidence: tuple[AuthorizedEvidence, ...],
        *,
        deliverable: bool = True,
    ):
        self.evidence = evidence
        self.deliverable = deliverable
        self.calls = 0

    async def retrieve(self, _scope, resource_refs, _question):
        self.calls += 1
        assert tuple(resource_refs) == ("receipt-a",)
        return self.evidence

    async def reauthorize(self, _scope, evidence):
        assert evidence == self.evidence
        return self.deliverable


@pytest.mark.asyncio
async def test_handoff_requeries_the_historical_query_without_browser_metric_filters() -> None:
    insights = StubInsights(result())

    async def generate(_request, _metrics, _evidence, max_cost_micros):
        assert max_cost_micros == 500
        return ProposedExplanation(
            "query-a",
            "insights-metrics-v1",
            FactWatermark("insights-v1", 7),
            NOW,
            result().metrics,
            (),
            (),
            (),
            0,
        )

    delivery = await InsightsExplanationService(
        insights=insights,
        knowledge=StubKnowledge(()),
        generate=generate,
        authorize=lambda _scope, _refs: True,
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
    ).explain(
        scope(),
        ExplanationRequest("conversation-a", "turn-a", "graph-a", "Explain", None, "query-a", ()),
        ExplanationBudget(1, 1, 30, 500),
    )

    assert delivery.query_id == "query-a"
    assert delivery.facts == result().metrics
    assert insights.calls == 1


@pytest.mark.asyncio
async def test_explanation_preserves_partial_report_coverage_and_unavailable_facts() -> None:
    coverage = ReportCoverage(
        source_id="ci-a",
        external_run_id="run-a",
        report_batch_id="batch-a",
        expected_shards=2,
        received_shards=1,
        completeness="partial",
        missing_reasons=("report-shards-missing",),
    )
    facts = (
        MetricFact(
            "first_pass_rate", None, None, None, "unavailable", coverage.missing_reasons, ()
        ),
        MetricFact(
            "final_pass_rate", None, None, None, "unavailable", coverage.missing_reasons, ()
        ),
    )
    verified = result(metrics=facts, report_coverage=(coverage,))

    async def generate(_request, model_facts, _evidence, _max_cost_micros):
        assert model_facts == facts
        return ProposedExplanation(
            "query-a",
            "insights-metrics-v1",
            FactWatermark("insights-v1", 7),
            NOW,
            facts,
            (),
            (),
            (),
            0,
        )

    delivery = await InsightsExplanationService(
        insights=StubInsights(verified),
        knowledge=StubKnowledge(()),
        generate=generate,
        authorize=lambda _scope, _refs: True,
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
    ).explain(
        scope(),
        ExplanationRequest("conversation-a", "turn-a", "graph-a", "Explain", query(), None, ()),
        ExplanationBudget(1, 1, 30, 500),
    )

    assert delivery.report_coverage == (coverage,)
    assert all(fact.value is None for fact in delivery.facts)
    assert any("report-shards-missing" in item for item in delivery.missing_information)


@pytest.mark.asyncio
async def test_explanation_delivers_verified_facts_and_audited_hypotheses() -> None:
    knowledge = StubKnowledge(
        (
            AuthorizedEvidence(
                "citation-a", "receipt-a", "revision-1", "Retry passed after cache clear."
            ),
        )
    )

    async def generate(_request, _metrics, _evidence, _max_cost_micros):
        return ProposedExplanation(
            query_id="query-a",
            metric_version="insights-metrics-v1",
            fact_watermark=FactWatermark("insights-v1", 7),
            as_of=NOW,
            metric_claims=(
                MetricFact("first_pass_rate", 1, 3, 1 / 3, "complete", (), ("receipt-a",)),
                MetricFact("final_pass_rate", 2, 3, 2 / 3, "complete", (), ("receipt-a",)),
            ),
            hypotheses=("Cache state may be associated with the recovered retry.",),
            citation_ids=("citation-a",),
            missing_information=("Application logs were not supplied.",),
            cost_micros=200,
        )

    service = InsightsExplanationService(
        insights=StubInsights(result()),
        knowledge=knowledge,
        generate=generate,
        authorize=lambda _scope, _refs: True,
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
    )
    delivery = await service.explain(
        scope(),
        ExplanationRequest(
            conversation_id="conversation-a",
            turn_id="turn-a",
            graph_run_id="graph-a",
            question="Why did the retry recover?",
            metric_query=query(),
            query_id=None,
            resource_refs=("receipt-a",),
        ),
        ExplanationBudget(max_tool_calls=2, max_model_calls=1, max_seconds=30, max_cost_micros=500),
    )

    assert [(fact.numerator, fact.denominator) for fact in delivery.facts] == [(1, 3), (2, 3)]
    assert delivery.hypotheses == (
        "The cited evidence suggests a possible association worth investigating; "
        "causality is not established. [citation-a]",
    )
    assert delivery.missing_information == (
        "Additional information is required to evaluate the possible association.",
    )
    assert delivery.query_id == "query-a"
    assert delivery.as_of == NOW
    assert delivery.fact_watermark == FactWatermark("insights-v1", 7)
    assert delivery.audit == {
        "conversationId": "conversation-a",
        "turnId": "turn-a",
        "graphRunId": "graph-a",
        "tool": "insights.query",
        "queryId": "query-a",
    }
    assert knowledge.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proposal",
    [
        ProposedExplanation(
            "query-a",
            "insights-metrics-v1",
            FactWatermark("insights-v1", 7),
            NOW,
            (MetricFact("final_pass_rate", 3, 3, 1.0, "complete", (), ("receipt-a",)),),
            ("The cache caused the failure.",),
            ("citation-a",),
            (),
            10,
        ),
        ProposedExplanation(
            "query-a",
            "insights-metrics-v1",
            FactWatermark("insights-v1", 6),
            NOW,
            result().metrics,
            ("A stale conclusion.",),
            ("citation-a",),
            (),
            10,
        ),
    ],
)
async def test_tampered_numbers_or_old_watermark_cannot_cross_delivery(
    proposal: ProposedExplanation,
) -> None:
    async def generate(_request, _metrics, _evidence, _max_cost_micros):
        return proposal

    service = InsightsExplanationService(
        insights=StubInsights(result()),
        knowledge=StubKnowledge(
            (AuthorizedEvidence("citation-a", "receipt-a", "revision-1", "A relevant source."),)
        ),
        generate=generate,
        authorize=lambda _scope, _refs: True,
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
    )

    delivery = await service.explain(
        scope(),
        ExplanationRequest(
            "conversation-a", "turn-a", "graph-a", "Explain", query(), None, ("receipt-a",)
        ),
        ExplanationBudget(2, 1, 30, 500),
    )

    assert delivery.facts == result().metrics
    assert delivery.hypotheses == ()
    assert (
        "The generated explanation did not match the authorized metric snapshot."
        in delivery.missing_information
    )


@pytest.mark.asyncio
async def test_model_text_cannot_present_tampered_numbers_or_confirmed_causality() -> None:
    async def generate(_request, _metrics, _evidence, _max_cost_micros):
        return ProposedExplanation(
            "query-a",
            "insights-metrics-v1",
            FactWatermark("insights-v1", 7),
            NOW,
            result().metrics,
            ("Cache definitely triggered 99% of failures; it may recur.",),
            ("citation-a",),
            (),
            10,
        )

    service = InsightsExplanationService(
        insights=StubInsights(result()),
        knowledge=StubKnowledge(
            (
                AuthorizedEvidence(
                    "citation-a", "receipt-a", "revision-1", "Cache clear preceded retry."
                ),
            )
        ),
        generate=generate,
        authorize=lambda _scope, _refs: True,
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
    )

    delivery = await service.explain(
        scope(),
        ExplanationRequest(
            "conversation-a",
            "turn-a",
            "graph-a",
            "Explain",
            query(),
            None,
            ("receipt-a",),
        ),
        ExplanationBudget(2, 1, 30, 500),
    )

    assert delivery.hypotheses == (
        "The cited evidence suggests a possible association worth investigating; "
        "causality is not established. [citation-a]",
    )
    assert "99" not in delivery.hypotheses[0]


@pytest.mark.asyncio
async def test_metric_evidence_outside_current_resource_scope_is_rejected() -> None:
    unauthorized = result(
        metrics=tuple(
            MetricFact(
                fact.metric_id,
                fact.numerator,
                fact.denominator,
                fact.value,
                fact.completeness,
                fact.missing_reasons,
                ("receipt-other",),
            )
            for fact in result().metrics
        )
    )

    async def generate(*_args):
        raise AssertionError("unauthorized facts reached the model")

    service = InsightsExplanationService(
        insights=StubInsights(unauthorized),
        knowledge=StubKnowledge(()),
        generate=generate,
        authorize=lambda _scope, _refs: True,
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
    )

    with pytest.raises(InsightsAuthorizationChanged, match="unauthorized evidence"):
        await service.explain(
            scope(),
            ExplanationRequest("conversation-a", "turn-a", "graph-a", "Explain", query(), None, ()),
            ExplanationBudget(1, 1, 30, 500),
        )


@pytest.mark.asyncio
async def test_budget_exhaustion_preserves_verified_facts_and_stops() -> None:
    generated = False

    async def generate(_request, _metrics, _evidence, _max_cost_micros):
        nonlocal generated
        generated = True
        raise AssertionError("model should not be called")

    service = InsightsExplanationService(
        insights=StubInsights(result()),
        knowledge=StubKnowledge(()),
        generate=generate,
        authorize=lambda _scope, _refs: True,
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
    )

    delivery = await service.explain(
        scope(),
        ExplanationRequest("conversation-a", "turn-a", "graph-a", "Explain", query(), None, ()),
        ExplanationBudget(1, 0, 30, 0),
    )

    assert delivery.facts == result().metrics
    assert delivery.hypotheses == ()
    assert delivery.stop_reason == "budget-exhausted"
    assert generated is False


@pytest.mark.asyncio
async def test_model_call_is_canceled_at_the_fixed_time_budget() -> None:
    canceled = False

    async def generate(_request, _metrics, _evidence, _max_cost_micros):
        nonlocal canceled
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            canceled = True
            raise
        raise AssertionError("unreachable")

    service = InsightsExplanationService(
        insights=StubInsights(result()),
        knowledge=StubKnowledge(()),
        generate=generate,
        authorize=lambda _scope, _refs: True,
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
    )

    delivery = await service.explain(
        scope(),
        ExplanationRequest("conversation-a", "turn-a", "graph-a", "Explain", query(), None, ()),
        ExplanationBudget(1, 1, 1, 500),
    )

    assert canceled is True
    assert delivery.facts == result().metrics
    assert delivery.stop_reason == "budget-exhausted"


@pytest.mark.asyncio
async def test_withdrawn_citation_and_delivery_reauthorization_fail_closed() -> None:
    async def generate(_request, _metrics, _evidence, _max_cost_micros):
        return ProposedExplanation(
            "query-a",
            "insights-metrics-v1",
            FactWatermark("insights-v1", 7),
            NOW,
            result().metrics,
            ("A source-backed possibility.",),
            ("citation-a",),
            (),
            10,
        )

    service = InsightsExplanationService(
        insights=StubInsights(result()),
        knowledge=StubKnowledge(
            (AuthorizedEvidence("citation-a", "receipt-a", "revision-1", "Withdrawn source."),),
            deliverable=False,
        ),
        generate=generate,
        authorize=lambda _scope, _refs: True,
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
    )

    with pytest.raises(InsightsAuthorizationChanged, match="withdrawn or changed version"):
        await service.explain(
            scope(),
            ExplanationRequest(
                "conversation-a", "turn-a", "graph-a", "Explain", query(), "query-a", ("receipt-a",)
            ),
            ExplanationBudget(2, 1, 30, 500),
        )


@pytest.mark.asyncio
async def test_metric_evidence_is_reauthorized_again_before_delivery() -> None:
    checks = 0

    def authorize(_scope, refs):
        nonlocal checks
        checks += 1
        return checks < 3 or refs == ()

    async def generate(_request, _metrics, _evidence, _max_cost_micros):
        return ProposedExplanation(
            "query-a",
            "insights-metrics-v1",
            FactWatermark("insights-v1", 7),
            NOW,
            result().metrics,
            (),
            (),
            (),
            10,
        )

    service = InsightsExplanationService(
        insights=StubInsights(result()),
        knowledge=StubKnowledge(()),
        generate=generate,
        authorize=authorize,
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
    )

    with pytest.raises(InsightsAuthorizationChanged, match="delivery"):
        await service.explain(
            scope(),
            ExplanationRequest("conversation-a", "turn-a", "graph-a", "Explain", query(), None, ()),
            ExplanationBudget(1, 1, 30, 500),
        )


@pytest.mark.asyncio
async def test_unavailable_metric_is_not_zero_and_does_not_block_plain_knowledge_chat() -> None:
    knowledge = StubKnowledge(())
    service = InsightsExplanationService(
        insights=StubInsights(InsightsQueryUnavailable("tap unavailable")),
        knowledge=knowledge,
        generate=lambda *_args: (_ for _ in ()).throw(AssertionError("model called")),
        authorize=lambda _scope, _refs: True,
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
    )

    delivery = await service.explain(
        scope(),
        ExplanationRequest(
            "conversation-a", "turn-a", "graph-a", "Explain", query(), None, ("receipt-a",)
        ),
        ExplanationBudget(2, 1, 30, 500),
    )

    assert delivery.facts == ()
    assert delivery.hypotheses == ()
    assert delivery.query_id is None
    assert delivery.stop_reason == "insights-unavailable"
    assert delivery.missing_information == ("Insights metrics are unavailable.",)
    assert knowledge.calls == 0


def test_budget_requires_fixed_calls_time_and_cost() -> None:
    with pytest.raises((TypeError, ValueError)):
        ExplanationBudget(max_tool_calls=0, max_model_calls=1, max_seconds=30, max_cost_micros=1)
    with pytest.raises((TypeError, ValueError)):
        ExplanationBudget(max_tool_calls=1, max_model_calls=1, max_seconds=0, max_cost_micros=1)
    with pytest.raises((TypeError, ValueError)):
        ExplanationBudget(max_tool_calls=1, max_model_calls=1, max_seconds=30, max_cost_micros=-1)


def test_scope_secrets_are_not_rendered() -> None:
    assert "delegated-user-token-0001" not in repr(scope())
    assert "service-token-00000001" not in repr(
        ServiceIdentity(
            authorization="service-token-00000001",
            audience="tap-insights",
            expires_at=NOW + timedelta(minutes=1),
        )
    )
