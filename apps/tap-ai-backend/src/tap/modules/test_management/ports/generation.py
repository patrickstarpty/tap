"""Provider-neutral Test Plan generation boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.test_management.domain.models import (
    TestPlanGenerationJob,
    TestPlanGenerationRequest,
    TestPlanRevision,
)


@dataclass(frozen=True, slots=True)
class TestDesignContext:
    scope: ProjectScopeContext
    request: TestPlanGenerationRequest
    input_snapshot: Mapping[str, object]
    answer_evidence_snapshot: Mapping[str, object]


class TestDesignGenerator(Protocol):
    async def generate(self, context: TestDesignContext) -> TestPlanRevision: ...


@dataclass(frozen=True, slots=True)
class ClaimedTestDesignJob:
    job: TestPlanGenerationJob
    lease_token: str


class TestDesignJobStore(Protocol):
    async def claim_generation_jobs(
        self,
        scope: ProjectScopeContext,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[ClaimedTestDesignJob, ...]: ...

    async def generation_context(
        self, scope: ProjectScopeContext, claim: ClaimedTestDesignJob
    ) -> TestDesignContext: ...

    async def complete_generation(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedTestDesignJob,
        revision: TestPlanRevision,
        *,
        now: datetime,
    ) -> TestPlanRevision: ...

    async def fail_generation(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedTestDesignJob,
        *,
        failure_code: str,
        now: datetime,
    ) -> None: ...
