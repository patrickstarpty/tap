"""Project-scoped Test Management API."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request, status

from tap.contracts.http import (
    TestPlanCaseView,
    TestPlanCitationView,
    TestPlanCoverageGapView,
    TestPlanEvidencePreview,
    TestPlanGenerationAccepted,
    TestPlanGenerationRequestBody,
    TestPlanReviewDecisionView,
    TestPlanReviewRequest,
    TestPlanReviewSummaryView,
    TestPlanRevisionPage,
    TestPlanRevisionUpdate,
    TestPlanRevisionView,
    TestPlanScenarioView,
    TestPlanStepView,
    TestPlanTextFactView,
)
from tap.interfaces.http.dependencies import source_command_key, test_plan_service
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.modules.test_management.domain.models import (
    BddKeyword,
    CitationOrigin,
    GapSeverity,
    GenerationJobStatus,
    ReviewDisposition,
    TestCase,
    TestPlanAssumption,
    TestPlanCitation,
    TestPlanCoverageGap,
    TestPlanRevision,
    TestPlanStep,
    TestPlanUnknown,
    TestScenario,
)

router = APIRouter(prefix="/test-plans", tags=["test-management"])

_GenerationProgress = Literal["queued", "running", "waiting", "completed", "failed", "canceled"]
_GENERATION_PROGRESS: dict[GenerationJobStatus, _GenerationProgress] = {
    GenerationJobStatus.PENDING: "queued",
    GenerationJobStatus.RUNNING: "running",
    GenerationJobStatus.WAITING: "waiting",
    GenerationJobStatus.DRAFT_READY: "completed",
    GenerationJobStatus.FAILED: "failed",
    GenerationJobStatus.CANCELED: "canceled",
}


def _generation_view(job) -> TestPlanGenerationAccepted:
    return TestPlanGenerationAccepted(
        job_id=job.request.job_id,
        test_plan_id=job.request.test_plan_id,
        revision_id=job.request.revision_id,
        status=job.status.value,
        progress=_GENERATION_PROGRESS[job.status],
        failure_code=job.failure_code,
        deep_link=(
            f"/test-management/{job.request.test_plan_id}/revisions/{job.request.revision_id}"
        ),
        row_version=job.row_version,
    )


def _view(revision, project_id: str) -> TestPlanRevisionView:
    return TestPlanRevisionView(
        test_plan_id=revision.test_plan_id,
        revision_id=revision.revision_id,
        version=revision.version,
        row_version=revision.row_version,
        title=revision.title,
        objective=revision.objective,
        scope_items=list(revision.scope_items),
        prerequisites=list(revision.prerequisites),
        risks=list(revision.risks),
        status=revision.status.value,
        origin=revision.origin.value,
        adopted_from_revision_id=revision.adopted_from_revision_id,
        content_digest=revision.content_digest,
        validation_digest=revision.validation_digest,
        cases=[
            TestPlanCaseView(
                case_id=case.case_id,
                ordinal=case.ordinal,
                title=case.title,
                objective=case.objective,
                critical=case.critical,
                scenarios=[
                    TestPlanScenarioView(
                        scenario_id=scenario.scenario_id,
                        ordinal=scenario.ordinal,
                        title=scenario.title,
                        steps=[
                            TestPlanStepView(
                                step_id=step.step_id,
                                ordinal=step.ordinal,
                                keyword=step.keyword.value,
                                text=step.text,
                                expected_result=step.expected_result,
                                critical=step.critical,
                                citation_ids=list(step.citation_ids),
                                unknown_ids=list(step.unknown_ids),
                            )
                            for step in scenario.steps
                        ],
                    )
                    for scenario in case.scenarios
                ],
                covered_requirement_ids=list(case.covered_requirement_ids),
            )
            for case in revision.cases
        ],
        citations=[
            TestPlanCitationView(
                citation_id=item.citation_id,
                source_revision_id=item.source_revision_id,
                document_revision_id=item.document_revision_id,
                chunk_id=item.chunk_id,
                content_digest=item.content_digest,
                claim_text=item.claim_text,
                origin=item.origin.value,
                evidence_preview_url=(
                    f"/api/v1/projects/{project_id}/test-plans/{revision.test_plan_id}"
                    f"/revisions/{revision.revision_id}/evidence/{item.citation_id}"
                ),
                anchor=item.anchor,
            )
            for item in revision.citations
        ],
        assumptions=[
            TestPlanTextFactView(
                fact_id=item.assumption_id,
                text=item.text,
                graph_edge_id=item.graph_edge_id,
            )
            for item in revision.assumptions
        ],
        unknowns=[
            TestPlanTextFactView(fact_id=item.unknown_id, text=item.text)
            for item in revision.unknowns
        ],
        coverage_gaps=[
            TestPlanCoverageGapView(
                gap_id=item.gap_id,
                requirement_ref=item.requirement_ref,
                reason=item.reason,
                severity=item.severity.value,
            )
            for item in revision.coverage_gaps
        ],
        requirement_scope_id=revision.requirement_scope_id,
        requirement_scope_version=revision.requirement_scope_version,
        requirement_scope_digest=revision.requirement_scope_digest,
        requirement_ids=list(revision.requirement_ids),
        covered_requirement_ids=list(revision.covered_requirement_ids),
        coverage_denominator=revision.coverage_denominator,
        covered_requirement_count=revision.covered_requirement_count,
        approved_knowledge_revision_ids=list(revision.approved_knowledge_revision_ids),
        model_revision_id=revision.model_revision_id,
        agent_revision_id=revision.agent_revision_id,
        skill_revision_ids=list(revision.skill_revision_ids),
        author_actor_id=revision.author_actor_id,
        strict_review_required=revision.strict_review_required,
        generated_content_digest=revision.generated_content_digest,
        review_decisions=[
            TestPlanReviewDecisionView(
                decision_id=item.decision_id,
                disposition=item.disposition.value,
                reason=item.reason,
                actor_id=item.actor_id,
                reviewed_content_digest=item.reviewed_content_digest,
                created_at=item.created_at,
            )
            for item in revision.review_decisions
        ],
        needs_review=revision.needs_review,
        needs_review_reason=revision.needs_review_reason,
        deep_link=f"/test-management/{revision.test_plan_id}/revisions/{revision.revision_id}",
    )


@router.get(
    "",
    operation_id="test_plan_list_revisions",
    response_model=TestPlanRevisionPage,
    dependencies=[Depends(project_authorization("test-plans.read"))],
)
async def list_revisions(request: Request) -> TestPlanRevisionPage:
    revisions = await test_plan_service(request).list_revisions(request.state.project_scope)
    return TestPlanRevisionPage(
        items=[_view(item, request.state.project_scope.project_id) for item in revisions]
    )


@router.get(
    "/{test_plan_id}/revisions/{revision_id}",
    operation_id="test_plan_get_revision",
    response_model=TestPlanRevisionView,
    dependencies=[Depends(project_authorization("test-plans.read"))],
)
async def get_revision(
    request: Request, test_plan_id: str, revision_id: str
) -> TestPlanRevisionView:
    revision = await test_plan_service(request).get_revision(
        request.state.project_scope, test_plan_id, revision_id
    )
    return _view(revision, request.state.project_scope.project_id)


@router.get(
    "/{test_plan_id}/revisions/{revision_id}/evidence/{citation_id}",
    operation_id="test_plan_get_evidence_preview",
    response_model=TestPlanEvidencePreview,
    dependencies=[Depends(project_authorization("test-plans.read"))],
)
async def get_evidence_preview(
    request: Request,
    test_plan_id: str,
    revision_id: str,
    citation_id: str,
) -> TestPlanEvidencePreview:
    value = await test_plan_service(request).get_evidence_preview(
        request.state.project_scope, test_plan_id, revision_id, citation_id
    )
    return TestPlanEvidencePreview(
        citation_id=str(value["citation_id"]),
        source_revision_id=str(value["source_revision_id"]),
        document_revision_id=str(value["document_revision_id"]),
        chunk_id=str(value["chunk_id"]),
        content_digest=str(value["content_digest"]),
        claim_text=str(value["claim_text"]),
        anchor=value["anchor"] if isinstance(value["anchor"], dict) else {},
    )


@router.patch(
    "/{test_plan_id}/revisions/{revision_id}",
    operation_id="test_plan_replace_draft",
    response_model=TestPlanRevisionView,
    dependencies=[Depends(project_authorization("test-plans.write"))],
)
async def replace_draft(
    request: Request,
    test_plan_id: str,
    revision_id: str,
    body: TestPlanRevisionUpdate,
    if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    key: str = Depends(source_command_key),
) -> TestPlanRevisionView:
    service = test_plan_service(request)
    current = await service.get_revision(request.state.project_scope, test_plan_id, revision_id)
    submitted_citations = tuple(
        TestPlanCitation(
            item.citation_id,
            item.source_revision_id,
            item.document_revision_id,
            item.chunk_id,
            item.content_digest,
            item.claim_text,
            CitationOrigin(item.origin),
            item.anchor,
        )
        for item in body.citations
    )
    if submitted_citations != current.citations:
        raise ValueError("frozen evidence citations cannot be edited")
    replacement = TestPlanRevision.create(
        test_plan_id=test_plan_id,
        revision_id=revision_id,
        version=current.version,
        title=body.title,
        objective=body.objective,
        scope_items=tuple(body.scope_items),
        prerequisites=tuple(body.prerequisites),
        risks=tuple(body.risks),
        cases=tuple(
            TestCase(
                case.case_id,
                case.ordinal,
                case.title,
                case.objective,
                case.critical,
                tuple(
                    TestScenario(
                        scenario.scenario_id,
                        scenario.ordinal,
                        scenario.title,
                        tuple(
                            TestPlanStep(
                                step.step_id,
                                step.ordinal,
                                BddKeyword(step.keyword),
                                step.text,
                                step.expected_result,
                                step.critical,
                                tuple(step.citation_ids),
                                tuple(step.unknown_ids),
                            )
                            for step in scenario.steps
                        ),
                    )
                    for scenario in case.scenarios
                ),
                tuple(case.covered_requirement_ids),
            )
            for case in body.cases
        ),
        citations=current.citations,
        assumptions=tuple(
            TestPlanAssumption(item.fact_id, item.text, item.graph_edge_id)
            for item in body.assumptions
        ),
        unknowns=tuple(TestPlanUnknown(item.fact_id, item.text) for item in body.unknowns),
        coverage_gaps=tuple(
            TestPlanCoverageGap(
                item.gap_id,
                item.requirement_ref,
                item.reason,
                GapSeverity(item.severity),
            )
            for item in body.coverage_gaps
        ),
        origin=current.origin,
        adopted_from_revision_id=current.adopted_from_revision_id,
    )
    replacement = replace(
        replacement,
        requirement_scope_id=current.requirement_scope_id,
        requirement_scope_version=current.requirement_scope_version,
        requirement_scope_digest=current.requirement_scope_digest,
        requirement_ids=current.requirement_ids,
        author_actor_id=current.author_actor_id,
        strict_review_required=current.strict_review_required,
        approved_knowledge_revision_ids=current.approved_knowledge_revision_ids,
        model_revision_id=current.model_revision_id,
        agent_revision_id=current.agent_revision_id,
        skill_revision_ids=current.skill_revision_ids,
        generated_content_digest=current.generated_content_digest,
    )
    replacement = replace(replacement, content_digest=replacement.compute_content_digest())
    updated = await service.replace_draft(
        request.state.project_scope,
        replacement,
        if_match,
        idempotency_key=key,
        now=datetime.now(timezone.utc),
    )
    return _view(updated, request.state.project_scope.project_id)


@router.post(
    "/generations",
    operation_id="test_plan_request_generation",
    response_model=TestPlanGenerationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(project_authorization("test-plans.write"))],
    responses={422: problem_response_metadata("Request validation failed")},
)
async def request_generation(
    request: Request,
    body: TestPlanGenerationRequestBody,
    key: str = Depends(source_command_key),
) -> TestPlanGenerationAccepted:
    scope = request.state.project_scope
    job = await test_plan_service(request).request_generation_from_turn(
        scope,
        conversation_id=body.conversation_id,
        turn_id=body.turn_id,
        objective=body.objective,
        idempotency_key=key,
        now=datetime.now(timezone.utc),
    )
    return _generation_view(job)


@router.get(
    "/generations/{job_id}",
    operation_id="test_plan_get_generation",
    response_model=TestPlanGenerationAccepted,
    dependencies=[Depends(project_authorization("test-plans.read"))],
)
async def get_generation(request: Request, job_id: str) -> TestPlanGenerationAccepted:
    job = await test_plan_service(request).get_generation_job(request.state.project_scope, job_id)
    return _generation_view(job)


@router.post(
    "/generations/{job_id}/cancel",
    operation_id="test_plan_cancel_generation",
    response_model=TestPlanGenerationAccepted,
    dependencies=[Depends(project_authorization("test-plans.write"))],
)
async def cancel_generation(
    request: Request,
    job_id: str,
    if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    key: str = Depends(source_command_key),
) -> TestPlanGenerationAccepted:
    job = await test_plan_service(request).cancel_generation(
        request.state.project_scope,
        job_id,
        expected_version=if_match,
        idempotency_key=key,
        now=datetime.now(timezone.utc),
    )
    return _generation_view(job)


@router.post(
    "/generations/{job_id}/retry",
    operation_id="test_plan_retry_generation",
    response_model=TestPlanGenerationAccepted,
    dependencies=[Depends(project_authorization("test-plans.write"))],
)
async def retry_generation(
    request: Request,
    job_id: str,
    if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    key: str = Depends(source_command_key),
) -> TestPlanGenerationAccepted:
    job = await test_plan_service(request).retry_generation(
        request.state.project_scope,
        job_id,
        expected_version=if_match,
        idempotency_key=key,
        now=datetime.now(timezone.utc),
    )
    return _generation_view(job)


@router.get(
    "/reviews/summary",
    operation_id="test_plan_review_summary",
    response_model=TestPlanReviewSummaryView,
    dependencies=[Depends(project_authorization("test-plans.read"))],
)
async def review_summary(request: Request) -> TestPlanReviewSummaryView:
    summary = await test_plan_service(request).review_summary(request.state.project_scope)
    return TestPlanReviewSummaryView(
        reviewed_count=summary.reviewed_count,
        unchanged_count=summary.unchanged_count,
        modified_count=summary.modified_count,
        rejected_count=summary.rejected_count,
        unchanged_adoption_rate=summary.unchanged_adoption_rate,
        total_adoption_rate=summary.total_adoption_rate,
    )


@router.post(
    "/{test_plan_id}/revisions/{revision_id}/reviews",
    operation_id="test_plan_review_revision",
    response_model=TestPlanRevisionView,
    dependencies=[Depends(project_authorization("test-plans.review"))],
)
async def review_revision(
    request: Request,
    test_plan_id: str,
    revision_id: str,
    body: TestPlanReviewRequest,
    if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    key: str = Depends(source_command_key),
) -> TestPlanRevisionView:
    revision = await test_plan_service(request).review(
        request.state.project_scope,
        test_plan_id,
        revision_id,
        disposition=ReviewDisposition(body.disposition),
        reason=body.reason,
        expected_version=if_match,
        idempotency_key=key,
        now=datetime.now(timezone.utc),
    )
    return _view(revision, request.state.project_scope.project_id)


@router.post(
    "/{test_plan_id}/revisions/{revision_id}/fork",
    operation_id="test_plan_fork_revision",
    response_model=TestPlanRevisionView,
    dependencies=[Depends(project_authorization("test-plans.write"))],
)
async def fork_revision(
    request: Request,
    test_plan_id: str,
    revision_id: str,
    if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    key: str = Depends(source_command_key),
) -> TestPlanRevisionView:
    revision = await test_plan_service(request).fork_revision(
        request.state.project_scope,
        test_plan_id,
        revision_id,
        expected_version=if_match,
        idempotency_key=key,
        now=datetime.now(timezone.utc),
    )
    return _view(revision, request.state.project_scope.project_id)


@router.post(
    "/{test_plan_id}/revisions/{revision_id}/publish",
    operation_id="test_plan_publish_revision",
    response_model=TestPlanRevisionView,
    dependencies=[Depends(project_authorization("test-plans.publish"))],
)
async def publish_revision(
    request: Request,
    test_plan_id: str,
    revision_id: str,
    if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    key: str = Depends(source_command_key),
) -> TestPlanRevisionView:
    revision = await test_plan_service(request).publish(
        request.state.project_scope,
        test_plan_id,
        revision_id,
        if_match,
        key,
    )
    return _view(revision, request.state.project_scope.project_id)
