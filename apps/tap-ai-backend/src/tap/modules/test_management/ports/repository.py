"""Ports for Test Plan persistence and citation authorization."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.test_management.domain.models import (
    ReviewDisposition,
    TestPlanCitation,
    TestPlanReviewSummary,
    TestPlanRevision,
)


class TestPlanRepository(Protocol):
    async def create_draft(
        self,
        scope: ProjectScopeContext,
        revision: TestPlanRevision,
        *,
        now: datetime,
    ) -> TestPlanRevision: ...

    async def get_revision(
        self, scope: ProjectScopeContext, test_plan_id: str, revision_id: str
    ) -> TestPlanRevision: ...

    async def replace_draft(
        self,
        scope: ProjectScopeContext,
        revision: TestPlanRevision,
        expected_version: int,
        *,
        now: datetime,
    ) -> TestPlanRevision: ...

    async def publish_revision(
        self,
        scope: ProjectScopeContext,
        revision_id: str,
        expected_version: int,
        validation_digest: str,
    ) -> TestPlanRevision: ...

    async def record_review(
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
    ) -> TestPlanRevision: ...

    async def review_summary(self, scope: ProjectScopeContext) -> TestPlanReviewSummary: ...

    async def mark_source_changed(
        self,
        scope: ProjectScopeContext,
        source_revision_id: str,
        *,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> tuple[str, ...]: ...


class TestPlanCitationAuthority(Protocol):
    async def is_authorized(
        self, scope: ProjectScopeContext, citation: TestPlanCitation
    ) -> bool: ...

    async def is_requirement_scope_current(
        self, scope: ProjectScopeContext, revision: TestPlanRevision
    ) -> bool: ...

    async def are_knowledge_versions_current(
        self, scope: ProjectScopeContext, revision: TestPlanRevision
    ) -> bool: ...
