"""Explicit human publication of validated Test Plan revisions."""

from __future__ import annotations

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.test_management.domain.models import RevisionStatus, TestPlanRevision
from tap.modules.test_management.domain.validation import RevisionImmutable, validate_publishable
from tap.modules.test_management.ports.repository import (
    TestPlanCitationAuthority,
    TestPlanRepository,
)
from tap.platform.db.project_scope import require_project_scope


class PublishTestPlan:
    def __init__(
        self, repository: TestPlanRepository, citation_authority: TestPlanCitationAuthority
    ) -> None:
        self._repository = repository
        self._citation_authority = citation_authority

    async def execute(
        self,
        scope: ProjectScopeContext,
        test_plan_id: str,
        draft_revision_id: str,
        expected_version: int,
    ) -> TestPlanRevision:
        scope = require_project_scope(scope)
        revision = await self._repository.get_revision(scope, test_plan_id, draft_revision_id)
        if revision.status not in {RevisionStatus.DRAFT, RevisionStatus.VALIDATING}:
            raise RevisionImmutable("published and superseded revisions are immutable")
        validation_digest = validate_publishable(revision)
        for citation in revision.citations:
            if not await self._citation_authority.is_authorized(scope, citation):
                raise ValueError("test plan citation is not currently authorized")
        return await self._repository.publish_revision(
            scope, draft_revision_id, expected_version, validation_digest
        )
