"""Validated workflow control requests and plan-version disposition."""

from dataclasses import dataclass
from typing import Literal

from tap.modules.access.domain.context import require_identifier


class WorkflowDispatchBlocked(Exception):
    """A committed pause/cancel blocks a new external reservation."""


class WorkflowLimitExceeded(ValueError):
    """A hard task deadline or shared call limit prevents new external work."""


ControlKind = Literal["steer", "pause", "resume", "cancel", "retry"]
ReceiptStatus = Literal["accepted", "pending", "applied", "rejected", "stale"]


@dataclass(frozen=True, slots=True)
class ControlCommand:
    command_id: str
    task_id: str
    target_node_id: str | None
    kind: ControlKind
    expected_plan_revision: int
    text: str | None

    def __post_init__(self) -> None:
        require_identifier("command_id", self.command_id)
        require_identifier("task_id", self.task_id)
        if self.target_node_id is not None:
            require_identifier("target_node_id", self.target_node_id)
        if self.kind not in ("steer", "pause", "resume", "cancel", "retry"):
            raise ValueError("unsupported workflow command")
        if type(self.expected_plan_revision) is not int or self.expected_plan_revision < 1:
            raise ValueError("expected_plan_revision must be positive")
        if self.kind == "steer" and (not isinstance(self.text, str) or not self.text.strip()):
            raise ValueError("steer text is required")
        if self.kind != "steer" and self.text is not None:
            raise ValueError("text is only valid for steer")
        if self.kind == "retry" and self.target_node_id is None:
            raise ValueError("retry requires a target node")
        if self.kind == "cancel" and self.target_node_id is not None:
            raise ValueError("cancel targets the task")


def command_disposition(expected: int, actual: int, already_applied: bool) -> str:
    """An applied command keeps its receipt across later plan revisions."""
    if already_applied:
        return "applied"
    if expected < actual:
        return "stale"
    if expected > actual:
        return "rejected"
    return "pending"
