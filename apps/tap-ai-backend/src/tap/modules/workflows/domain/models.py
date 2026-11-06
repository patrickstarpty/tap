"""Durable workflow projections, independent of graph-run status."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal


class WorkflowStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_DEPENDENCY = "waiting_dependency"
    AWAITING_INPUT = "awaiting_input"
    PAUSING = "pausing"
    PAUSED = "paused"
    RECOVERING = "recovering"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELING = "canceling"
    CANCELED = "canceled"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_id: str
    project_id: str
    conversation_id: str | None
    plan_revision: int
    status: WorkflowStatus
    last_sequence: int


@dataclass(frozen=True, slots=True)
class NodeSnapshot:
    node_id: str
    task_id: str
    parent_node_id: str | None
    agent_revision_id: str | None
    kind: Literal["agent", "service", "approval"]
    status: WorkflowStatus
    attempt: int


@dataclass(frozen=True, slots=True)
class WorkflowEvent:
    event_id: str
    task_id: str
    sequence: int
    node_id: str | None
    attempt: int
    kind: str
    payload: dict[str, object]
    occurred_at: datetime
