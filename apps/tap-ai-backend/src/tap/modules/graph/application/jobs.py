"""Graph job port and in-memory lease-fencing reference implementation."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Protocol
from uuid import uuid4

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.application.queries import InMemoryGraphStore
from tap.modules.graph.domain.jobs import (
    ClaimedGraphJob,
    GraphJob,
    GraphJobLeaseLost,
    GraphJobRequest,
    GraphJobStatus,
)
from tap.modules.graph.domain.models import GraphSnapshot, GraphSnapshotDraft
from tap.platform.db.project_scope import require_project_scope


class GraphJobStore(Protocol):
    async def request(
        self, scope: ProjectScopeContext, request: GraphJobRequest, *, now: datetime
    ) -> GraphJob: ...

    async def claim(
        self,
        scope: ProjectScopeContext,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[ClaimedGraphJob, ...]: ...

    async def complete(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedGraphJob,
        draft: GraphSnapshotDraft,
        *,
        now: datetime,
    ) -> GraphJob: ...

    async def fail(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedGraphJob,
        *,
        failure_code: str,
        now: datetime,
    ) -> GraphJob: ...


class InMemoryGraphJobStore:
    def __init__(self) -> None:
        self._jobs: dict[tuple[str, str], GraphJob] = {}
        self._requests: dict[tuple[str, str], str] = {}
        self._graph = InMemoryGraphStore()

    async def request(
        self, scope: ProjectScopeContext, request: GraphJobRequest, *, now: datetime
    ) -> GraphJob:
        scope = require_project_scope(scope)
        if request.snapshot.project_id != scope.project_id:
            raise ValueError("graph job request is outside Project scope")
        request_key = (scope.project_id, request.request_digest)
        existing_id = self._requests.get(request_key)
        if existing_id is not None:
            return self._jobs[(scope.project_id, existing_id)]
        job = GraphJob(
            request.job_id,
            request.revision_id,
            request.snapshot,
            request.chunks_locator,
            request.extraction_profile_digest,
            request.request_digest,
            request.model_alias,
            GraphJobStatus.PENDING,
            now,
            now,
        )
        self._jobs[(scope.project_id, job.job_id)] = job
        self._requests[request_key] = job.job_id
        return job

    async def claim(
        self,
        scope: ProjectScopeContext,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[ClaimedGraphJob, ...]:
        scope = require_project_scope(scope)
        if not worker_id or not timedelta(0) < lease_duration <= timedelta(minutes=15):
            raise ValueError("graph worker lease is invalid")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("graph job claim limit must be between 1 and 50")
        claimed: list[ClaimedGraphJob] = []
        for key, job in tuple(self._jobs.items()):
            if key[0] != scope.project_id or len(claimed) >= limit:
                continue
            expires = getattr(job, "lease_expires_at", None)
            eligible = job.status is GraphJobStatus.PENDING or (
                job.status is GraphJobStatus.RUNNING and expires is not None and expires <= now
            )
            if not eligible:
                continue
            claim = ClaimedGraphJob(
                job.job_id,
                job.revision_id,
                job.snapshot,
                job.chunks_locator,
                job.extraction_profile_digest,
                job.request_digest,
                job.model_alias,
                GraphJobStatus.RUNNING,
                job.created_at,
                now,
                job.failure_code,
                worker_id,
                uuid4().hex,
                now + lease_duration,
            )
            self._jobs[key] = claim
            claimed.append(claim)
        return tuple(claimed)

    def _owned(
        self, scope: ProjectScopeContext, claim: ClaimedGraphJob, now: datetime
    ) -> ClaimedGraphJob:
        scope = require_project_scope(scope)
        current = self._jobs.get((scope.project_id, claim.job_id))
        if (
            not isinstance(current, ClaimedGraphJob)
            or current.status is not GraphJobStatus.RUNNING
            or current.lease_token != claim.lease_token
            or current.lease_expires_at is None
            or current.lease_expires_at <= now
        ):
            raise GraphJobLeaseLost(claim.job_id)
        return current

    async def complete(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedGraphJob,
        draft: GraphSnapshotDraft,
        *,
        now: datetime,
    ) -> GraphJob:
        current = self._owned(scope, claim, now)
        if draft.snapshot != current.snapshot:
            raise ValueError("graph draft does not match claimed snapshot")
        ready_snapshot = await self._graph.publish(scope, draft)
        ready = GraphJob(
            current.job_id,
            current.revision_id,
            ready_snapshot,
            current.chunks_locator,
            current.extraction_profile_digest,
            current.request_digest,
            current.model_alias,
            GraphJobStatus.READY,
            current.created_at,
            now,
        )
        self._jobs[(scope.project_id, current.job_id)] = ready
        return ready

    async def fail(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedGraphJob,
        *,
        failure_code: str,
        now: datetime,
    ) -> GraphJob:
        current = self._owned(scope, claim, now)
        if not failure_code or len(failure_code) > 64:
            raise ValueError("graph failure code must be bounded")
        failed = GraphJob(
            current.job_id,
            current.revision_id,
            replace(current.snapshot, status="FAILED"),
            current.chunks_locator,
            current.extraction_profile_digest,
            current.request_digest,
            current.model_alias,
            GraphJobStatus.FAILED,
            current.created_at,
            now,
            failure_code,
        )
        self._jobs[(scope.project_id, current.job_id)] = failed
        return failed

    async def list_jobs(self, scope: ProjectScopeContext) -> tuple[GraphJob, ...]:
        scope = require_project_scope(scope)
        return tuple(
            job for (project_id, _), job in self._jobs.items() if project_id == scope.project_id
        )

    async def get_job(self, scope: ProjectScopeContext, job_id: str) -> GraphJob:
        scope = require_project_scope(scope)
        return self._jobs[(scope.project_id, job_id)]

    async def active_snapshot(
        self, scope: ProjectScopeContext, source_ids: tuple[str, ...]
    ) -> GraphSnapshot | None:
        return await self._graph.active_snapshot(scope, source_ids)
