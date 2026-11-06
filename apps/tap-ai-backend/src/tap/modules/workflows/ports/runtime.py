"""Runtime evidence required before a lost workflow can advance."""

from dataclasses import dataclass
from typing import Literal, Protocol

from tap.modules.access.domain.context import ProjectScopeContext


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
