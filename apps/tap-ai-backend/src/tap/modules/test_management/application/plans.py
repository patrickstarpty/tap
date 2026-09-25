"""Create and fork Project-scoped Test Plan drafts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.test_management.application.publish import PublishTestPlan
from tap.modules.test_management.domain.models import (
    ReviewDisposition,
    RevisionStatus,
    TestPlanGenerationJob,
    TestPlanGenerationRequest,
    TestPlanReviewSummary,
    TestPlanRevision,
)
from tap.modules.test_management.ports.repository import TestPlanRepository
from tap.platform.db.project_scope import require_project_scope


class TestPlans:
    def __init__(self, repository: TestPlanRepository) -> None:
        self._repository = repository

    async def create_draft(
        self,
        scope: ProjectScopeContext,
        revision: TestPlanRevision,
        *,
        now: datetime,
    ) -> TestPlanRevision:
        scope = require_project_scope(scope)
        if revision.status is not RevisionStatus.DRAFT:
            raise ValueError("only Draft Test Plan revisions can be created")
        return await self._repository.create_draft(scope, revision, now=now)

    async def fork_revision(
        self,
        scope: ProjectScopeContext,
        test_plan_id: str,
        source_revision_id: str,
        *,
        revision_id: str,
        version: int,
        now: datetime,
    ) -> TestPlanRevision:
        scope = require_project_scope(scope)
        source = await self._repository.get_revision(scope, test_plan_id, source_revision_id)
        draft = source.fork(revision_id, version)
        return await self._repository.create_draft(scope, draft, now=now)


class TestPlanApplication:
    """HTTP-facing orchestration over one Project-bound durable repository."""

    def __init__(self, repository: Any) -> None:
        self._repository = repository
        self.scope = repository.scope

    async def list_revisions(self, scope: ProjectScopeContext) -> tuple[TestPlanRevision, ...]:
        return await self._repository.list_revisions(require_project_scope(scope))

    async def get_revision(
        self, scope: ProjectScopeContext, test_plan_id: str, revision_id: str
    ) -> TestPlanRevision:
        return await self._repository.get_revision(
            require_project_scope(scope), test_plan_id, revision_id
        )

    async def get_evidence_preview(
        self,
        scope: ProjectScopeContext,
        test_plan_id: str,
        revision_id: str,
        citation_id: str,
    ) -> dict[str, object]:
        return await self._repository.get_evidence_preview(
            require_project_scope(scope), test_plan_id, revision_id, citation_id
        )

    async def replace_draft(
        self,
        scope: ProjectScopeContext,
        revision: TestPlanRevision,
        expected_version: int,
        *,
        idempotency_key: str,
        now: datetime,
    ) -> TestPlanRevision:
        return await self._repository.replace_draft(
            require_project_scope(scope),
            revision,
            expected_version,
            idempotency_key=idempotency_key,
            now=now,
        )

    async def request_generation(
        self,
        scope: ProjectScopeContext,
        request: TestPlanGenerationRequest,
        *,
        now: datetime,
    ) -> TestPlanGenerationJob:
        return await self._repository.request_generation(
            require_project_scope(scope), request, now=now
        )

    async def request_generation_from_turn(
        self,
        scope: ProjectScopeContext,
        *,
        conversation_id: str,
        turn_id: str,
        objective: str,
        idempotency_key: str,
        now: datetime,
    ) -> TestPlanGenerationJob:
        return await self._repository.request_generation_from_turn(
            require_project_scope(scope),
            conversation_id=conversation_id,
            turn_id=turn_id,
            objective=objective,
            idempotency_key=idempotency_key,
            now=now,
        )

    async def get_generation_job(
        self, scope: ProjectScopeContext, job_id: str
    ) -> TestPlanGenerationJob:
        return await self._repository.get_generation_job(require_project_scope(scope), job_id)

    async def cancel_generation(
        self,
        scope: ProjectScopeContext,
        job_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        now: datetime,
    ) -> TestPlanGenerationJob:
        return await self._repository.cancel_generation(
            require_project_scope(scope),
            job_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            now=now,
        )

    async def retry_generation(
        self,
        scope: ProjectScopeContext,
        job_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        now: datetime,
    ) -> TestPlanGenerationJob:
        return await self._repository.retry_generation(
            require_project_scope(scope),
            job_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            now=now,
        )

    async def publish(
        self,
        scope: ProjectScopeContext,
        test_plan_id: str,
        revision_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> TestPlanRevision:
        return await PublishTestPlan(self._repository, self._repository).execute(
            require_project_scope(scope),
            test_plan_id,
            revision_id,
            expected_version,
            idempotency_key,
        )

    async def fork_revision(
        self,
        scope: ProjectScopeContext,
        test_plan_id: str,
        source_revision_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        now: datetime,
    ) -> TestPlanRevision:
        return await self._repository.fork_revision(
            require_project_scope(scope),
            test_plan_id,
            source_revision_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            now=now,
        )

    async def review(
        self,
        scope: ProjectScopeContext,
        test_plan_id: str,
        revision_id: str,
        *,
        disposition: ReviewDisposition,
        reason: str,
        expected_version: int,
        idempotency_key: str,
        now: datetime,
    ) -> TestPlanRevision:
        return await self._repository.record_review(
            require_project_scope(scope),
            test_plan_id,
            revision_id,
            disposition=disposition,
            reason=reason,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            now=now,
        )

    async def review_summary(self, scope: ProjectScopeContext) -> TestPlanReviewSummary:
        return await self._repository.review_summary(require_project_scope(scope))

    async def mark_knowledge_sources_changed(
        self,
        source_revision_ids: tuple[str, ...],
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> tuple[str, ...]:
        impacted: set[str] = set()
        for source_revision_id in source_revision_ids:
            impacted.update(
                await self._repository.mark_source_changed(
                    self.scope,
                    source_revision_id,
                    reason=reason,
                    idempotency_key=f"{idempotency_key}:{source_revision_id}",
                    now=now,
                )
            )
        return tuple(sorted(impacted))
