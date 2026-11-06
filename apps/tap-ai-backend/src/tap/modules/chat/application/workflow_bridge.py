"""Chat-owned public boundary for workflow association and fact projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CommittedWorkflowFact:
    task_id: str
    sequence: int
    kind: str
    payload: dict[str, object]
    occurred_at: datetime


class WorkflowChatPort(Protocol):
    async def link_task(self, conversation_id: str, turn_id: str, task_id: str) -> bool:
        """Bind a Turn to its task; return whether cancellation is pending."""
        ...

    async def authorize_dispatch(
        self, conversation_id: str, turn_id: str, task_id: str
    ) -> bool: ...

    async def request_cancel(self, conversation_id: str, turn_id: str) -> str | None: ...

    async def is_workflow_turn(self, conversation_id: str, turn_id: str) -> bool: ...

    async def repair_candidates(
        self, limit: int
    ) -> tuple[tuple[str, str, str | None, bool], ...]: ...

    async def client_request_id(self, conversation_id: str, turn_id: str, task_id: str) -> str: ...

    async def claim_projection(
        self, conversation_id: str, turn_id: str, task_id: str
    ) -> tuple[str, int] | None: ...

    async def project_fact(
        self, conversation_id: str, turn_id: str, token: str, fact: CommittedWorkflowFact
    ) -> None: ...

    async def release_projection(self, conversation_id: str, turn_id: str, token: str) -> None: ...

    async def projection_candidates(self, limit: int) -> tuple[tuple[str, str, str], ...]: ...

    async def projection_candidates_for_task(
        self, task_id: str
    ) -> tuple[tuple[str, str, str], ...]: ...
