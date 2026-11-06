"""Runtime evidence required before a lost workflow can advance."""

from dataclasses import dataclass
from typing import Literal, Protocol

from tap.modules.access.domain.context import ProjectScopeContext


@dataclass(frozen=True, slots=True)
class RuntimeInspection:
    outcome: Literal["running", "stopped", "unknown"]
    checkpoint_compatible: bool
    side_effects: Literal["clear", "present", "unknown"]


class NativeWorkflowRuntime(Protocol):
    async def inspect(self, scope: ProjectScopeContext, task_id: str) -> RuntimeInspection: ...
