"""Transactional task and event store contract."""

from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.domain.models import TaskSnapshot, WorkflowEvent


class WorkflowStore(Protocol):
    async def create_task(
        self, scope: ProjectScopeContext, request: dict[str, object], idempotency_key: str
    ) -> TaskSnapshot: ...

    async def snapshot(self, scope: ProjectScopeContext, task_id: str) -> dict[str, object]: ...

    async def append_event(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        lease_token: str | None,
        event_id: str,
        kind: str,
        payload: dict[str, object],
    ) -> WorkflowEvent: ...

    async def events_after(
        self, scope: ProjectScopeContext, task_id: str, sequence: int, limit: int
    ) -> tuple[WorkflowEvent, ...]: ...
