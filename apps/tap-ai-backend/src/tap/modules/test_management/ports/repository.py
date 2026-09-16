"""Ports for Test Plan persistence and citation authorization."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.test_management.domain.models import TestPlanCitation, TestPlanRevision


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


class TestPlanCitationAuthority(Protocol):
    async def is_authorized(
        self, scope: ProjectScopeContext, citation: TestPlanCitation
    ) -> bool: ...
