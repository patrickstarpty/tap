"""Reconcile lost workflow leases with authoritative runtime evidence."""

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.ports.runtime import NativeWorkflowRuntime, RecoveryEpisode
from tap.modules.workflows.ports.store import WorkflowStore


class RecoveryService:
    def __init__(self, store: WorkflowStore, runtime: NativeWorkflowRuntime) -> None:
        self._store = store
        self._runtime = runtime

    async def reconcile(self, scope: ProjectScopeContext, task_id: str) -> str:
        state = await self._store.mark_lease_lost(scope, task_id)
        if not isinstance(state, RecoveryEpisode):
            return state
        inspection = await self._runtime.inspect(scope, state)
        return await self._store.resolve_recovery(scope, task_id, inspection)
