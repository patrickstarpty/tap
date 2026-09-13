"""Create and fork Project-scoped Test Plan drafts."""

from __future__ import annotations

from datetime import datetime

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.test_management.domain.models import RevisionStatus, TestPlanRevision
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
