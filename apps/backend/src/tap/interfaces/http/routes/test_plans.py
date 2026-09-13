"""Project-scoped Test Management API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status

from tap.contracts.http import (
    TestPlanCaseView,
    TestPlanCitationView,
    TestPlanCoverageGapView,
    TestPlanGenerationAccepted,
    TestPlanGenerationRequestBody,
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
    TestCase,
    TestPlanAssumption,
    TestPlanCitation,
    TestPlanCoverageGap,
    TestPlanRevision,
    TestPlanStep,
    TestPlanUnknown,
    TestScenario,
)
from tap.modules.test_management.domain.models import (
    TestPlanGenerationRequest as DomainGenerationRequest,
)

router = APIRouter(prefix="/test-plans", tags=["test-management"])


def _view(revision) -> TestPlanRevisionView:
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
                            )
                            for step in scenario.steps
                        ],
                    )
                    for scenario in case.scenarios
                ],
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
    return TestPlanRevisionPage(items=[_view(item) for item in revisions])


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
    return _view(revision)


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
) -> TestPlanRevisionView:
    service = test_plan_service(request)
    current = await service.get_revision(request.state.project_scope, test_plan_id, revision_id)
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
                            )
                            for step in scenario.steps
                        ),
                    )
                    for scenario in case.scenarios
                ),
            )
            for case in body.cases
        ),
        citations=tuple(
            TestPlanCitation(
                item.citation_id,
                item.source_revision_id,
                item.document_revision_id,
                item.chunk_id,
                item.content_digest,
                item.claim_text,
                CitationOrigin(item.origin),
            )
            for item in body.citations
        ),
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
    updated = await service.replace_draft(
        request.state.project_scope,
        replacement,
        if_match,
        now=datetime.now(timezone.utc),
    )
    return _view(updated)


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
    generation = DomainGenerationRequest.create(
        project_id=scope.project_id,
        conversation_id=body.conversation_id,
        turn_id=body.turn_id,
        input_snapshot_digest=body.input_snapshot_digest,
        answer_evidence_snapshot_digest=body.answer_evidence_snapshot_digest,
        model_alias=body.model_alias,
        agent_revision_id=body.agent_revision_id,
        skill_revision_ids=tuple(body.skill_revision_ids),
        objective=body.objective,
        idempotency_key=key,
    )
    job = await test_plan_service(request).request_generation(
        scope, generation, now=datetime.now(timezone.utc)
    )
    return TestPlanGenerationAccepted(
        job_id=job.request.job_id,
        test_plan_id=job.request.test_plan_id,
        revision_id=job.request.revision_id,
        status=job.status.value,
        deep_link=(
            f"/test-management/{job.request.test_plan_id}/revisions/{job.request.revision_id}"
        ),
    )


@router.get(
    "/generations/{job_id}",
    operation_id="test_plan_get_generation",
    response_model=TestPlanGenerationAccepted,
    dependencies=[Depends(project_authorization("test-plans.read"))],
)
async def get_generation(request: Request, job_id: str) -> TestPlanGenerationAccepted:
    job = await test_plan_service(request).get_generation_job(request.state.project_scope, job_id)
    return TestPlanGenerationAccepted(
        job_id=job.request.job_id,
        test_plan_id=job.request.test_plan_id,
        revision_id=job.request.revision_id,
        status=job.status.value,
        deep_link=(
            f"/test-management/{job.request.test_plan_id}/revisions/{job.request.revision_id}"
        ),
    )


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
    _key: str = Depends(source_command_key),
) -> TestPlanRevisionView:
    revision = await test_plan_service(request).publish(
        request.state.project_scope,
        test_plan_id,
        revision_id,
        if_match,
    )
    return _view(revision)
