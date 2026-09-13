"""Bounded independently restartable Test Plan generation worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.test_management.ports.generation import (
    TestDesignGenerator,
    TestDesignJobStore,
)
from tap.platform.db.project_scope import require_project_scope


@dataclass(frozen=True, slots=True)
class TestDesignWorkerRun:
    claimed: int
    ready: int
    failed: int
    lease_lost: int


class TestDesignWorker:
    def __init__(
        self,
        *,
        jobs: TestDesignJobStore,
        generator: TestDesignGenerator,
        scope: ProjectScopeContext,
        worker_id: str,
    ) -> None:
        self._jobs = jobs
        self._generator = generator
        self._scope = require_project_scope(scope)
        if not worker_id:
            raise ValueError("test design worker identity must be nonblank")
        self._worker_id = worker_id

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    async def run_once(self, limit: int) -> TestDesignWorkerRun:
        claims = await self._jobs.claim_generation_jobs(
            self._scope,
            worker_id=self._worker_id,
            now=self._now(),
            lease_duration=timedelta(seconds=60),
            limit=limit,
        )
        ready = failed = lease_lost = 0
        for claim in claims:
            try:
                context = await self._jobs.generation_context(self._scope, claim)
                draft = await self._generator.generate(context)
                await self._jobs.complete_generation(self._scope, claim, draft, now=self._now())
                ready += 1
            except Exception:
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
        return TestDesignWorkerRun(len(claims), ready, failed, lease_lost)
