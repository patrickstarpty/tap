"""Deterministic metric-first workflow for grounded Insights explanations."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from tap.modules.ai.ports.insights import (
    AuthorizedEvidence,
    AuthorizedInsightsScope,
    FactWatermark,
    InsightsAuthorizationChanged,
    InsightsPort,
    InsightsQueryUnavailable,
    MetricFact,
    MetricQuery,
)


@dataclass(frozen=True, slots=True)
class ExplanationBudget:
    max_tool_calls: int
    max_model_calls: int
    max_seconds: int
    max_cost_micros: int

    def __post_init__(self) -> None:
        if (
            type(self.max_tool_calls) is not int
            or type(self.max_model_calls) is not int
            or type(self.max_seconds) is not int
            or type(self.max_cost_micros) is not int
            or self.max_tool_calls < 1
            or not 0 <= self.max_model_calls <= 3
            or not 1 <= self.max_seconds <= 300
            or self.max_cost_micros < 0
        ):
            raise ValueError("explanation budget requires fixed calls, time, and cost")


@dataclass(frozen=True, slots=True)
class ExplanationRequest:
    conversation_id: str
    turn_id: str
    graph_run_id: str
    question: str
    metric_query: MetricQuery | None
    query_id: str | None
    resource_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.conversation_id, self.turn_id, self.graph_run_id, self.question):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("explanation request identities and question are required")
        if not isinstance(self.resource_refs, tuple) or len(self.resource_refs) > 20:
            raise ValueError("explanation resource refs must be bounded")
        if self.query_id is None and self.metric_query is None:
            raise ValueError("an Insights query ID or metric query is required")


@dataclass(frozen=True, slots=True)
class ProposedExplanation:
    query_id: str
    metric_version: str
    fact_watermark: FactWatermark
    as_of: datetime
    metric_claims: tuple[MetricFact, ...]
    hypotheses: tuple[str, ...]
    citation_ids: tuple[str, ...]
    missing_information: tuple[str, ...]
    cost_micros: int

    def __post_init__(self) -> None:
        if (
            self.as_of.utcoffset() is None
            or type(self.cost_micros) is not int
            or self.cost_micros < 0
        ):
            raise ValueError("proposal time and cost must be bounded")
        if len(self.hypotheses) > 10 or len(self.citation_ids) > 20:
            raise ValueError("proposal content must be bounded")
        if len(self.hypotheses) != len(self.citation_ids):
            raise ValueError("every hypothesis requires one citation")


@dataclass(frozen=True, slots=True)
class ExplanationDelivery:
    query_id: str | None
    metric_version: str | None
    fact_watermark: FactWatermark | None
    as_of: datetime | None
    facts: tuple[MetricFact, ...]
    hypotheses: tuple[str, ...]
    missing_information: tuple[str, ...]
    audit: dict[str, str | None]
    stop_reason: Literal["completed", "budget-exhausted", "insights-unavailable"]


class KnowledgeEvidencePort(Protocol):
    async def retrieve(
        self,
        scope: AuthorizedInsightsScope,
        resource_refs: tuple[str, ...],
        question: str,
    ) -> tuple[AuthorizedEvidence, ...]: ...


GenerateExplanation = Callable[
    [ExplanationRequest, tuple[MetricFact, ...], tuple[AuthorizedEvidence, ...]],
    Awaitable[ProposedExplanation],
]
Authorize = Callable[[AuthorizedInsightsScope, tuple[str, ...]], bool]


class InsightsExplanationService:
    """Use a fixed metric→knowledge→model workflow; never expose a SQL fallback."""

    def __init__(
        self,
        *,
        insights: InsightsPort,
        knowledge: KnowledgeEvidencePort,
        generate: GenerateExplanation,
        authorize: Authorize,
        clock: Callable[[], datetime],
        monotonic: Callable[[], float],
    ) -> None:
        self._insights = insights
        self._knowledge = knowledge
        self._generate = generate
        self._authorize = authorize
        self._clock = clock
        self._monotonic = monotonic

    async def explain(
        self,
        scope: AuthorizedInsightsScope,
        request: ExplanationRequest,
        budget: ExplanationBudget,
    ) -> ExplanationDelivery:
        started = self._monotonic()
        refs = request.resource_refs
        if not set(refs) <= set(scope.authorized_resource_refs) or not self._authorize(scope, refs):
            raise InsightsAuthorizationChanged("Insights explanation authorization changed")
        try:
            if request.query_id is not None:
                metrics = await self._insights.get_insights(scope, request.query_id)
            elif request.metric_query is not None:
                metrics = await self._insights.query_insights(scope, request.metric_query)
            else:  # guarded by ExplanationRequest; retain a fail-closed type boundary.
                raise InsightsQueryUnavailable("Insights query binding is missing")
        except InsightsQueryUnavailable:
            return self._delivery(
                request,
                query_id=None,
                metric_version=None,
                watermark=None,
                as_of=None,
                facts=(),
                hypotheses=(),
                missing=("Insights metrics are unavailable.",),
                stop_reason="insights-unavailable",
            )
        if request.query_id is not None and metrics.query_id != request.query_id:
            raise InsightsQueryUnavailable("historical Insights query ID changed")
        if request.query_id is None and metrics.query != request.metric_query:
            raise InsightsQueryUnavailable("Insights query scope changed")

        facts = metrics.metrics
        if (
            self._exhausted(started, budget)
            or budget.max_model_calls == 0
            or budget.max_cost_micros == 0
        ):
            return self._delivery(
                request,
                query_id=metrics.query_id,
                metric_version=metrics.metric_version,
                watermark=metrics.fact_watermark,
                as_of=metrics.query.as_of,
                facts=facts,
                hypotheses=(),
                missing=("Explanation budget was exhausted after metric verification.",),
                stop_reason="budget-exhausted",
            )

        evidence: tuple[AuthorizedEvidence, ...] = ()
        tool_calls = 1
        if refs:
            if tool_calls >= budget.max_tool_calls:
                return self._delivery(
                    request,
                    query_id=metrics.query_id,
                    metric_version=metrics.metric_version,
                    watermark=metrics.fact_watermark,
                    as_of=metrics.query.as_of,
                    facts=facts,
                    hypotheses=(),
                    missing=("Explanation budget was exhausted before knowledge retrieval.",),
                    stop_reason="budget-exhausted",
                )
            if not self._authorize(scope, refs):
                raise InsightsAuthorizationChanged("knowledge retrieval authorization changed")
            evidence = await self._knowledge.retrieve(scope, refs, request.question)
            tool_calls += 1
            if any(item.resource_ref not in refs for item in evidence):
                raise InsightsAuthorizationChanged("knowledge retrieval widened resource scope")

        if self._exhausted(started, budget):
            return self._delivery(
                request,
                query_id=metrics.query_id,
                metric_version=metrics.metric_version,
                watermark=metrics.fact_watermark,
                as_of=metrics.query.as_of,
                facts=facts,
                hypotheses=(),
                missing=("Explanation budget was exhausted after evidence retrieval.",),
                stop_reason="budget-exhausted",
            )
        proposal = await self._generate(request, facts, evidence)
        if proposal.cost_micros > budget.max_cost_micros or self._exhausted(started, budget):
            return self._delivery(
                request,
                query_id=metrics.query_id,
                metric_version=metrics.metric_version,
                watermark=metrics.fact_watermark,
                as_of=metrics.query.as_of,
                facts=facts,
                hypotheses=(),
                missing=("Explanation budget was exhausted after metric verification.",),
                stop_reason="budget-exhausted",
            )

        snapshot_matches = (
            proposal.query_id == metrics.query_id
            and proposal.metric_version == metrics.metric_version
            and proposal.fact_watermark == metrics.fact_watermark
            and proposal.as_of == metrics.query.as_of
            and proposal.metric_claims == facts
        )
        missing: tuple[str, ...]
        if not snapshot_matches:
            missing = ("The generated explanation did not match the authorized metric snapshot.",)
            hypotheses: tuple[str, ...] = ()
        else:
            evidence_by_id = {item.citation_id: item for item in evidence}
            if any(citation not in evidence_by_id for citation in proposal.citation_ids):
                raise InsightsAuthorizationChanged("explanation cited unauthorized evidence")
            uncertainty = re.compile(
                r"\b(?:may|might|could|possibly|possible|possibility|plausible|"
                r"associated|association|correlat(?:e|ed|ion))\b|(?:可能|或许|相关)",
                re.IGNORECASE,
            )
            asserts_causality = any(
                re.search(
                    r"\b(?:caused|causes|resulted in|led to|the root cause (?:is|was))\b|"
                    r"(?:导致|根因是|根本原因是)",
                    text,
                    re.IGNORECASE,
                )
                or uncertainty.search(text) is None
                for text in proposal.hypotheses
            )
            if asserts_causality:
                hypotheses = ()
                missing = (
                    "The generated explanation asserted causality that the evidence did not prove.",
                )
            else:
                hypotheses = tuple(
                    f"{text} [{citation}]"
                    for text, citation in zip(
                        proposal.hypotheses, proposal.citation_ids, strict=True
                    )
                )
                missing = proposal.missing_information
        if not self._authorize(scope, refs):
            raise InsightsAuthorizationChanged(
                "Insights explanation delivery authorization changed"
            )
        return self._delivery(
            request,
            query_id=metrics.query_id,
            metric_version=metrics.metric_version,
            watermark=metrics.fact_watermark,
            as_of=metrics.query.as_of,
            facts=facts,
            hypotheses=hypotheses,
            missing=missing,
            stop_reason="completed",
        )

    def _exhausted(self, started: float, budget: ExplanationBudget) -> bool:
        return self._monotonic() - started >= budget.max_seconds

    @staticmethod
    def _delivery(
        request: ExplanationRequest,
        *,
        query_id: str | None,
        metric_version: str | None,
        watermark: FactWatermark | None,
        as_of: datetime | None,
        facts: tuple[MetricFact, ...],
        hypotheses: tuple[str, ...],
        missing: tuple[str, ...],
        stop_reason: Literal["completed", "budget-exhausted", "insights-unavailable"],
    ) -> ExplanationDelivery:
        return ExplanationDelivery(
            query_id=query_id,
            metric_version=metric_version,
            fact_watermark=watermark,
            as_of=as_of,
            facts=facts,
            hypotheses=hypotheses,
            missing_information=missing,
            audit={
                "conversationId": request.conversation_id,
                "turnId": request.turn_id,
                "graphRunId": request.graph_run_id,
                "tool": "insights.query",
                "queryId": query_id,
            },
            stop_reason=stop_reason,
        )
