"""Persist control requests separately from fenced execution acknowledgment."""

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.domain.commands import ControlCommand
from tap.modules.workflows.domain.models import TaskSnapshot
from tap.modules.workflows.ports.store import WorkflowStore


class WorkflowService:
    def __init__(self, store: WorkflowStore) -> None:
        self._store = store

    async def submit(
        self, scope: ProjectScopeContext, command: ControlCommand
    ) -> dict[str, object]:
        return await self._store.submit_command(scope, command)

    async def ack_command(
        self,
        scope: ProjectScopeContext,
        command_id: str,
        lease_token: str,
        receipt: dict[str, object],
    ) -> dict[str, object]:
        return await self._store.ack_command(scope, command_id, lease_token, receipt)

    async def rerun(
        self, scope: ProjectScopeContext, task_id: str, idempotency_key: str
    ) -> TaskSnapshot:
        return await self._store.rerun_task(scope, task_id, idempotency_key)
