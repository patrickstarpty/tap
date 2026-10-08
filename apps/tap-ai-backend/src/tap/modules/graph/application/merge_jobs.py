"""Leased single-row-per-project merge queue: port, claim/lease dataclass, and
an in-memory reference implementation backed by `InMemoryProjectGraphStore`.

One project has at most one outstanding merge request at a time -- a later
`request()` while a merge is already claimed just keeps the project due so
the next `claim()` picks it up again, rather than queuing multiple entries.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal, Protocol
from uuid import uuid4

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.application.project_queries import InMemoryProjectGraphStore
from tap.modules.graph.domain.project import FragmentRecord, ProjectGraphDraft, ProjectGraphVersion
from tap.platform.db.project_scope import require_project_scope

__all__ = [
    "InMemoryProjectMergeQueue",
    "MergeClaim",
    "MergeInputsPort",
    "ProjectMergeLeaseLost",
    "ProjectMergeQueue",
]

_MAX_BACKOFF_SECONDS = 900
_BASE_BACKOFF_SECONDS = 60


def merge_failure_backoff_seconds(attempt: int) -> int:
    return min(_BASE_BACKOFF_SECONDS * 2**attempt, _MAX_BACKOFF_SECONDS)


@dataclass(frozen=True, slots=True)
class MergeClaim:
    project_id: str
    lease_owner: str
    lease_token: str
    lease_expires_at: datetime
    claimed_due_at: datetime
    reason: str
    attempt: int


class ProjectMergeLeaseLost(Exception):
    """The caller no longer owns the claimed project merge lease."""


class ProjectMergeQueue(Protocol):
    async def request(self, scope: ProjectScopeContext, *, reason: str, now: datetime) -> None: ...

    async def claim(
        self,
        scope: ProjectScopeContext,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> MergeClaim | None: ...

    async def renew(
        self,
        scope: ProjectScopeContext,
        claim: MergeClaim,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> MergeClaim: ...

    async def complete(
        self,
        scope: ProjectScopeContext,
        claim: MergeClaim,
        draft: ProjectGraphDraft,
        *,
        now: datetime,
    ) -> ProjectGraphVersion | None: ...

    async def fail(
        self,
        scope: ProjectScopeContext,
        claim: MergeClaim,
        *,
        failure_code: str,
        now: datetime,
    ) -> None: ...

    async def merge_state(
        self, scope: ProjectScopeContext, *, now: datetime
    ) -> Literal["IDLE", "PENDING", "RUNNING", "FAILED"]: ...


class MergeInputsPort(Protocol):
    async def load_fragments(self, scope: ProjectScopeContext) -> tuple[FragmentRecord, ...]: ...


@dataclass(slots=True)
class _QueueRow:
    due_at: datetime | None = None
    last_reason: str | None = None
    claimed_due_at: datetime | None = None
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = 0
    failure_code: str | None = None


class InMemoryProjectMergeQueue:
    """Reference `ProjectMergeQueue`: one queue row per project, in process
    memory, delegating published versions to an `InMemoryProjectGraphStore`."""

    def __init__(self, *, store: InMemoryProjectGraphStore | None = None) -> None:
        self.store = store if store is not None else InMemoryProjectGraphStore()
        self._rows: dict[str, _QueueRow] = {}

    async def request(self, scope: ProjectScopeContext, *, reason: str, now: datetime) -> None:
        scope = require_project_scope(scope)
        row = self._rows.setdefault(scope.project_id, _QueueRow())
        row.due_at = now
        row.last_reason = reason

    async def claim(
        self,
        scope: ProjectScopeContext,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> MergeClaim | None:
        scope = require_project_scope(scope)
        if not worker_id:
            raise ValueError("project merge worker identity must be nonblank")
        row = self._rows.get(scope.project_id)
        if row is None or row.due_at is None or row.due_at > now:
            return None
        if (
            row.lease_owner is not None
            and row.lease_expires_at is not None
            and row.lease_expires_at > now
        ):
            return None
        token = uuid4().hex
        expires = now + lease_duration
        row.lease_owner = worker_id
        row.lease_token = token
        row.lease_expires_at = expires
        row.claimed_due_at = row.due_at
        return MergeClaim(
            project_id=scope.project_id,
            lease_owner=worker_id,
            lease_token=token,
            lease_expires_at=expires,
            claimed_due_at=row.claimed_due_at,
            reason=row.last_reason or "",
            attempt=row.attempt_count,
        )

    def _owned(self, scope: ProjectScopeContext, claim: MergeClaim, now: datetime) -> _QueueRow:
        row = self._rows.get(scope.project_id)
        if (
            row is None
            or row.lease_token != claim.lease_token
            or row.lease_expires_at is None
            or row.lease_expires_at <= now
        ):
            raise ProjectMergeLeaseLost(scope.project_id)
        return row

    async def renew(
        self,
        scope: ProjectScopeContext,
        claim: MergeClaim,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> MergeClaim:
        scope = require_project_scope(scope)
        row = self._owned(scope, claim, now)
        row.lease_expires_at = now + lease_duration
        return replace(claim, lease_expires_at=row.lease_expires_at)

    async def complete(
        self,
        scope: ProjectScopeContext,
        claim: MergeClaim,
        draft: ProjectGraphDraft,
        *,
        now: datetime,
    ) -> ProjectGraphVersion | None:
        scope = require_project_scope(scope)
        row = self._owned(scope, claim, now)
        current = await self.store.get_current(scope)
        version: ProjectGraphVersion | None
        if current is not None and current.fragment_digest == draft.fragment_digest:
            version = None
        else:
            version = await self.store.publish(scope, draft, now=now)
        row.lease_owner = None
        row.lease_token = None
        row.lease_expires_at = None
        row.attempt_count = 0
        row.failure_code = None
        if row.claimed_due_at == row.due_at:
            row.due_at = None
            row.last_reason = None
        row.claimed_due_at = None
        return version

    async def fail(
        self,
        scope: ProjectScopeContext,
        claim: MergeClaim,
        *,
        failure_code: str,
        now: datetime,
    ) -> None:
        scope = require_project_scope(scope)
        if not failure_code or len(failure_code) > 64:
            raise ValueError("project merge failure code must be bounded")
        row = self._owned(scope, claim, now)
        row.failure_code = failure_code
        row.attempt_count = claim.attempt + 1
        row.due_at = now + timedelta(seconds=merge_failure_backoff_seconds(claim.attempt))
        row.last_reason = claim.reason
        row.lease_owner = None
        row.lease_token = None
        row.lease_expires_at = None
        row.claimed_due_at = None

    async def merge_state(
        self, scope: ProjectScopeContext, *, now: datetime
    ) -> Literal["IDLE", "PENDING", "RUNNING", "FAILED"]:
        scope = require_project_scope(scope)
        row = self._rows.get(scope.project_id)
        if row is None:
            return "IDLE"
        lease_active = row.lease_expires_at is not None and row.lease_expires_at > now
        if row.lease_owner is not None and lease_active:
            return "RUNNING"
        if row.failure_code is not None:
            return "FAILED"
        if row.due_at is not None:
            return "PENDING"
        return "IDLE"
