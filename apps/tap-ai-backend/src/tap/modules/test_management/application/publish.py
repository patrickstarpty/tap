"""Explicit human publication of validated Test Plan revisions."""

from __future__ import annotations

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.test_management.domain.models import (
    GapSeverity,
    ReviewDisposition,
    RevisionStatus,
    TestPlanRevision,
)
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
        idempotency_key: str,
    ) -> TestPlanRevision:
        scope = require_project_scope(scope)
        revision = await self._repository.get_revision(scope, test_plan_id, draft_revision_id)
        if revision.status not in {RevisionStatus.DRAFT, RevisionStatus.VALIDATING}:
            raise RevisionImmutable("published and superseded revisions are immutable")
        validation_digest = validate_publishable(revision)
        decision = revision.current_review_decision
        if (
            decision is None
            or decision.disposition
            not in {
                ReviewDisposition.ACCEPTED_UNCHANGED,
                ReviewDisposition.ACCEPTED_MODIFIED,
            }
            or decision.reviewed_content_digest != revision.content_digest
        ):
            raise ValueError("current content requires an explicit human review")
        if revision.strict_review_required and (
            revision.author_actor_id is None or decision.actor_id == revision.author_actor_id
        ):
            raise ValueError("strict Test Plans require non-author business review")
        if revision.needs_review:
            raise ValueError("source changes require review before publication")
        if revision.unknowns or any(
            gap.severity is GapSeverity.CRITICAL for gap in revision.coverage_gaps
        ):
            raise ValueError("unresolved Test Plan blockers prevent publication")
        for citation in revision.citations:
            if not await self._citation_authority.is_authorized(scope, citation):
                raise ValueError("test plan citation is not currently authorized")
        if not await self._citation_authority.is_requirement_scope_current(scope, revision):
            raise ValueError("test plan requirement scope is no longer current")
        if not await self._citation_authority.are_knowledge_versions_current(scope, revision):
            raise ValueError("test plan knowledge versions are no longer current")
        return await self._repository.publish_revision(
            scope,
            draft_revision_id,
            expected_version,
            validation_digest,
            idempotency_key,
        )
