"""Runtime evidence required before a lost workflow can advance."""

from dataclasses import dataclass
from typing import Literal, Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.domain.delegation import DelegationRequest


@dataclass(frozen=True, slots=True)
class RecoveryEpisode:
    """Identity of the execution whose lease was lost, retained while recovering."""

    task_id: str
    execution_token: str


@dataclass(frozen=True, slots=True)
class RuntimeInspection:
    """Observed outcome for one specific lost execution token."""

    outcome: Literal["running", "stopped", "unknown"]
    checkpoint_compatible: bool
    side_effects: Literal["clear", "present", "unknown"]
    execution_token: str | None = None


class NativeWorkflowRuntime(Protocol):
    async def inspect(
        self, scope: ProjectScopeContext, episode: RecoveryEpisode
    ) -> RuntimeInspection:
        """Check the execution/attempt bound to the token, never a later task run.

        Echoing the token without inspecting its corresponding execution is not
        evidence of stop. Return unknown when that binding cannot be verified.
        """
        ...


class NativeStatePort(Protocol):
    async def ensure_root(self, scope: ProjectScopeContext, task_id: str, token: str) -> str: ...
    async def delegate(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        token: str,
        parent_id: str,
        request: DelegationRequest,
        call_id: str,
    ) -> str: ...
    async def start_node(
        self, scope: ProjectScopeContext, task_id: str, token: str, node_id: str
    ) -> int: ...
    async def wait_node(
        self, scope: ProjectScopeContext, task_id: str, token: str, node_id: str
    ) -> None: ...
    async def await_input(
        self, scope: ProjectScopeContext, task_id: str, token: str, node_id: str, question: str
    ) -> None: ...
    async def fail_node(
        self, scope: ProjectScopeContext, task_id: str, token: str, node_id: str, reason: str
    ) -> None: ...
    async def finish_node(
        self, scope: ProjectScopeContext, task_id: str, token: str, node_id: str, summary: str
    ) -> None: ...
    async def unblock_parents(
        self, scope: ProjectScopeContext, task_id: str, token: str
    ) -> tuple[str, ...]: ...
    async def release_task(
        self, scope: ProjectScopeContext, task_id: str, token: str, status: str
    ) -> None: ...
    async def fail_task(
        self, scope: ProjectScopeContext, task_id: str, token: str, reason: str
    ) -> None: ...
    async def finish_task(
        self, scope: ProjectScopeContext, task_id: str, token: str, answer: str, outcome: str
    ) -> None: ...
    async def renew_task(self, scope: ProjectScopeContext, task_id: str, token: str) -> None: ...


class WorkflowWakeups(Protocol):
    async def notify(self, scope: ProjectScopeContext, task_id: str, sequence: int) -> None: ...
