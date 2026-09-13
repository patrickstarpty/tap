"""Create and fork Project-scoped Test Plan drafts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.test_management.application.publish import PublishTestPlan
from tap.modules.test_management.domain.models import (
    RevisionStatus,
    TestPlanGenerationJob,
    TestPlanGenerationRequest,
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

    async def replace_draft(
        self,
        scope: ProjectScopeContext,
        revision: TestPlanRevision,
        expected_version: int,
        *,
        now: datetime,
    ) -> TestPlanRevision:
        return await self._repository.replace_draft(
            require_project_scope(scope), revision, expected_version, now=now
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

    async def get_generation_job(
        self, scope: ProjectScopeContext, job_id: str
    ) -> TestPlanGenerationJob:
        return await self._repository.get_generation_job(require_project_scope(scope), job_id)

    async def publish(
        self,
        scope: ProjectScopeContext,
        test_plan_id: str,
        revision_id: str,
        expected_version: int,
    ) -> TestPlanRevision:
        return await PublishTestPlan(self._repository, self._repository).execute(
            require_project_scope(scope),
            test_plan_id,
            revision_id,
            expected_version,
        )
