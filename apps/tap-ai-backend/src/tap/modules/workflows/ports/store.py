"""Transactional task and event store contract."""

from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.domain.commands import ControlCommand
from tap.modules.workflows.domain.models import TaskSnapshot, WorkflowEvent
from tap.modules.workflows.ports.runtime import RecoveryEpisode, RuntimeInspection


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

    async def submit_command(
        self, scope: ProjectScopeContext, command: ControlCommand
    ) -> dict[str, object]: ...

    async def command(
        self, scope: ProjectScopeContext, task_id: str, command_id: str
    ) -> ControlCommand | None: ...

    async def pending_commands(
        self, scope: ProjectScopeContext, task_id: str
    ) -> tuple[ControlCommand, ...]: ...

    async def ack_command(
        self,
        scope: ProjectScopeContext,
        command_id: str,
        lease_token: str,
        receipt: dict[str, object],
    ) -> dict[str, object]: ...

    async def claim_task(
        self, scope: ProjectScopeContext, task_id: str, owner: str
    ) -> str | None: ...

    async def mark_lease_lost(
        self, scope: ProjectScopeContext, task_id: str
    ) -> RecoveryEpisode | str: ...

    async def resolve_recovery(
        self, scope: ProjectScopeContext, task_id: str, inspection: RuntimeInspection
    ) -> str: ...

    async def claim_control(
        self, scope: ProjectScopeContext, task_id: str, owner: str
    ) -> str | None: ...

    async def ready_nodes(self, scope: ProjectScopeContext, task_id: str) -> tuple[str, ...]: ...

    async def rerun_task(
        self, scope: ProjectScopeContext, task_id: str, idempotency_key: str
    ) -> TaskSnapshot: ...
