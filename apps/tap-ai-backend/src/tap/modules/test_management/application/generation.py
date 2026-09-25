"""Bounded independently restartable Test Plan generation worker."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.application.interaction_graph import InteractionGraph
from tap.modules.ai.domain.graph_runs import GraphCheckpointUnavailable
from tap.modules.test_management.domain.models import (
    BddKeyword,
    CitationOrigin,
    GapSeverity,
    IdentityOrigin,
    RevisionStatus,
    TestCase,
    TestPlanAssumption,
    TestPlanCitation,
    TestPlanCoverageGap,
    TestPlanRevision,
    TestPlanStep,
    TestPlanUnknown,
    TestScenario,
)
from tap.modules.test_management.ports.generation import (
    GenerationResponseUnknown,
    TestDesignGenerator,
    TestDesignJobStore,
)
from tap.platform.db.project_scope import require_project_scope

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TestDesignWorkerRun:
    claimed: int
    ready: int
    failed: int
    lease_lost: int
    waiting: int = 0


def _revision_checkpoint(revision: TestPlanRevision) -> dict[str, object]:
    return {
        "testPlanId": revision.test_plan_id,
        "revisionId": revision.revision_id,
        "version": revision.version,
        "status": revision.status.value,
        "origin": revision.origin.value,
        "adoptedFromRevisionId": revision.adopted_from_revision_id,
        "contentDigest": revision.content_digest,
        "rowVersion": revision.row_version,
        "validationDigest": revision.validation_digest,
        "createdAt": revision.created_at.isoformat() if revision.created_at else None,
        "publishedAt": revision.published_at.isoformat() if revision.published_at else None,
        "requirementScopeId": revision.requirement_scope_id,
        "requirementScopeVersion": revision.requirement_scope_version,
        "requirementScopeDigest": revision.requirement_scope_digest,
        "requirementIds": list(revision.requirement_ids),
        "approvedKnowledgeRevisionIds": list(revision.approved_knowledge_revision_ids),
        "modelRevisionId": revision.model_revision_id,
        "agentRevisionId": revision.agent_revision_id,
        "skillRevisionIds": list(revision.skill_revision_ids),
        "authorActorId": revision.author_actor_id,
        "strictReviewRequired": revision.strict_review_required,
        "generatedContentDigest": revision.generated_content_digest,
        "needsReview": revision.needs_review,
        "needsReviewReason": revision.needs_review_reason,
        "content": revision.canonical_content(),
    }


def _revision_from_checkpoint(value: object) -> TestPlanRevision:
    if not isinstance(value, dict):
        raise TypeError("test design checkpoint draft is malformed")
    data = cast(dict[str, Any], value)
    content = cast(dict[str, Any], data["content"])
    cases = tuple(
        TestCase(
            item["caseId"],
            item["ordinal"],
            item["title"],
            item["objective"],
            item["critical"],
            tuple(
                TestScenario(
                    scenario["scenarioId"],
                    scenario["ordinal"],
                    scenario["title"],
                    tuple(
                        TestPlanStep(
                            step["stepId"],
                            step["ordinal"],
                            BddKeyword(step["keyword"]),
                            step["text"],
                            step["expectedResult"],
                            step["critical"],
                            tuple(step.get("citationIds", [])),
                            tuple(step.get("unknownIds", [])),
                        )
                        for step in scenario["steps"]
                    ),
                )
                for scenario in item["scenarios"]
            ),
            tuple(item.get("coveredRequirementIds", [])),
        )
        for item in content["cases"]
    )
    citations = tuple(
        TestPlanCitation(
            item["citationId"],
            item["sourceRevisionId"],
            item["documentRevisionId"],
            item["chunkId"],
            item["contentDigest"],
            item["claimText"],
            CitationOrigin(item["origin"]),
            item.get("anchor"),
        )
        for item in content["citations"]
    )
    assumptions = tuple(
        TestPlanAssumption(item["assumptionId"], item["text"], item["graphEdgeId"])
        for item in content["assumptions"]
    )
    unknowns = tuple(
        TestPlanUnknown(item["unknownId"], item["text"]) for item in content["unknowns"]
    )
    coverage_gaps = tuple(
        TestPlanCoverageGap(
            item["gapId"],
            item["requirementRef"],
            item["reason"],
            GapSeverity(item["severity"]),
        )
        for item in content["coverageGaps"]
    )
    return TestPlanRevision(
        data["testPlanId"],
        data["revisionId"],
        data["version"],
        content["title"],
        content["objective"],
        tuple(content["scope"]),
        tuple(content["prerequisites"]),
        tuple(content["risks"]),
        cases,
        citations,
        assumptions,
        unknowns,
        coverage_gaps,
        RevisionStatus(data["status"]),
        IdentityOrigin(data["origin"]),
        data["adoptedFromRevisionId"],
        data["contentDigest"],
        data["rowVersion"],
        data["validationDigest"],
        datetime.fromisoformat(data["createdAt"]) if data["createdAt"] else None,
        datetime.fromisoformat(data["publishedAt"]) if data["publishedAt"] else None,
        data.get("requirementScopeId"),
        data.get("requirementScopeVersion"),
        data.get("requirementScopeDigest"),
        tuple(data.get("requirementIds", [])),
        tuple(data.get("approvedKnowledgeRevisionIds", [])),
        data.get("modelRevisionId"),
        data.get("agentRevisionId"),
        tuple(data.get("skillRevisionIds", [])),
        data.get("authorActorId"),
        bool(data.get("strictReviewRequired", False)),
        data.get("generatedContentDigest"),
        (),
        bool(data.get("needsReview", False)),
        data.get("needsReviewReason"),
    )


class TestDesignWorker:
    def __init__(
        self,
        *,
        jobs: TestDesignJobStore,
        generator: TestDesignGenerator,
        scope: ProjectScopeContext,
        worker_id: str,
        lease_duration: timedelta = timedelta(seconds=60),
        renew_interval_seconds: float = 20.0,
    ) -> None:
        self._jobs = jobs
        self._generator = generator
        self._scope = require_project_scope(scope)
        if not worker_id:
            raise ValueError("test design worker identity must be nonblank")
        if (
            not timedelta(0) < lease_duration <= timedelta(minutes=15)
            or not 0 < renew_interval_seconds < lease_duration.total_seconds()
        ):
            raise ValueError("test design worker lease renewal is invalid")
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._renew_interval_seconds = renew_interval_seconds

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    async def run_once(self, limit: int) -> TestDesignWorkerRun:
        claims = await self._jobs.claim_generation_jobs(
            self._scope,
            worker_id=self._worker_id,
            now=self._now(),
            lease_duration=self._lease_duration,
            limit=limit,
        )
        ready = failed = lease_lost = waiting = 0
        for claim in claims:
            graph_ready = False
            try:
                checkpointer = self._jobs.graph_checkpointer(claim)

                async def authorized() -> bool:
                    try:
                        await self._jobs.generation_context(self._scope, claim)
                    except Exception:
                        return False
                    return True

                async def classify(_state):
                    return {"reasoning_mode": "workflow"}

                async def admit(_state):
                    await self._jobs.generation_context(self._scope, claim)
                    waiting_reason = await self._jobs.generation_waiting_reason(self._scope, claim)
                    return {
                        "admitted": waiting_reason is None,
                        "waiting_reason": waiting_reason,
                    }

                async def execute(_state):
                    context = await self._jobs.generation_context(self._scope, claim)
                    generated = await self._generator.generate(context)
                    return {"result": {"draft": _revision_checkpoint(generated)}}

                graph = InteractionGraph(
                    graph_version="test-design-generation-v1",
                    state_schema_version=1,
                    checkpointer=checkpointer,
                    classify=classify,
                    admit=admit,
                    execute=execute,
                    authorize=authorized,
                )

                async def run_graph():
                    return (
                        await graph.resume(run_id=claim.job.request.job_id)
                        if await graph.has_checkpoint(run_id=claim.job.request.job_id)
                        else await graph.start(
                            run_id=claim.job.request.job_id,
                            payload={
                                "jobId": claim.job.request.job_id,
                                "requestDigest": claim.job.request.request_digest,
                            },
                            execution_mode="durable",
                        )
                    )

                running = asyncio.create_task(run_graph())
                try:
                    while True:
                        done, _ = await asyncio.wait(
                            {running}, timeout=self._renew_interval_seconds
                        )
                        if done:
                            state = await running
                            break
                        await self._jobs.renew_generation_job(
                            self._scope,
                            claim,
                            now=self._now(),
                            lease_duration=self._lease_duration,
                        )
                except BaseException:
                    if not running.done():
                        running.cancel()
                        await asyncio.gather(running, return_exceptions=True)
                    raise
                waiting_reason = state.get("waiting_reason")
                if waiting_reason:
                    await self._jobs.wait_generation(
                        self._scope,
                        claim,
                        reason=waiting_reason,
                        now=self._now(),
                    )
                    waiting += 1
                    continue
                draft = _revision_from_checkpoint(state.get("result", {}).get("draft"))
                graph_ready = True
                await self._jobs.complete_generation(self._scope, claim, draft, now=self._now())
                ready += 1
            except GenerationResponseUnknown:
                try:
                    await self._jobs.wait_generation(
                        self._scope,
                        claim,
                        reason="provider-response-unknown",
                        now=self._now(),
                    )
                    waiting += 1
                except Exception:
                    lease_lost += 1
            except GraphCheckpointUnavailable:
                lease_lost += 1
            except Exception:
                if graph_ready:
                    lease_lost += 1
                    continue
                logger.exception(
                    "test-design generation failed",
                    extra={"job_id": claim.job.request.job_id},
                )
                try:
                    await self._jobs.fail_generation(
                        self._scope,
                        claim,
                        failure_code="test-design-generation-failed",
                        now=self._now(),
                    )
                    failed += 1
                except Exception:
                    lease_lost += 1
        return TestDesignWorkerRun(len(claims), ready, failed, lease_lost, waiting)
