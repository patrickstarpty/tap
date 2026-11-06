"""Transactional task and event store contract."""

from typing import Any, Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.domain.commands import ControlCommand
from tap.modules.workflows.domain.models import TaskSnapshot, WorkflowEvent
from tap.modules.workflows.ports.runtime import RecoveryEpisode, RuntimeInspection


class WorkflowStore(Protocol):
    async def create_task(
        self, scope: ProjectScopeContext, request: dict[str, object], idempotency_key: str
    ) -> TaskSnapshot: ...

    async def task_by_key(
        self, scope: ProjectScopeContext, idempotency_key: str
    ) -> TaskSnapshot | None: ...

    async def activate_task(
        self, scope: ProjectScopeContext, task_id: str, origin_turn_id: str
    ) -> None: ...

    async def pending_admissions(
        self, scope: ProjectScopeContext, limit: int
    ) -> tuple[tuple[str, str, str], ...]: ...

    async def graph_lineage(
        self, scope: ProjectScopeContext, task_id: str, node_id: str, attempt: int
    ) -> tuple[str, int]: ...

    async def snapshot(self, scope: ProjectScopeContext, task_id: str) -> dict[str, Any]: ...

    async def task_request(self, scope: ProjectScopeContext, task_id: str) -> dict[str, Any]: ...

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

    async def expire_idle_task(self, scope: ProjectScopeContext, task_id: str) -> bool: ...

    async def claim_task(
        self, scope: ProjectScopeContext, task_id: str, owner: str
    ) -> str | None: ...

    async def retry_evidence(
        self, scope: ProjectScopeContext, task_id: str, node_id: str
    ) -> bool: ...

    async def episode_facts(
        self, scope: ProjectScopeContext, episode: RecoveryEpisode
    ) -> dict[str, object]: ...

    async def mark_lease_lost(
        self, scope: ProjectScopeContext, task_id: str
    ) -> RecoveryEpisode | str: ...

    async def resolve_recovery(
        self, scope: ProjectScopeContext, task_id: str, inspection: RuntimeInspection
    ) -> str: ...

    async def claim_control(
        self, scope: ProjectScopeContext, task_id: str, owner: str
    ) -> str | None: ...

    async def node_running(
        self, scope: ProjectScopeContext, task_id: str, node_id: str
    ) -> bool: ...

    async def active_lease(
        self, scope: ProjectScopeContext, task_id: str, lease_token: str
    ) -> bool: ...

    async def stop_evidence(
        self, scope: ProjectScopeContext, task_id: str, lease_token: str, target_node_id: str | None
    ) -> bool: ...

    async def scan_work(
        self, scope: ProjectScopeContext, limit: int
    ) -> tuple[tuple[str, str], ...]: ...

    async def applied_steers(
        self, scope: ProjectScopeContext, task_id: str, node_id: str
    ) -> tuple[str, ...]: ...

    async def release_control(
        self, scope: ProjectScopeContext, task_id: str, lease_token: str
    ) -> None: ...

    async def ready_nodes(self, scope: ProjectScopeContext, task_id: str) -> tuple[str, ...]: ...

    async def rerun_task(
        self, scope: ProjectScopeContext, task_id: str, idempotency_key: str
    ) -> TaskSnapshot: ...
