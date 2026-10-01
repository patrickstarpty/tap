"""HTTP-facing turn trace and model-call read contracts."""

from __future__ import annotations

from typing import Protocol

from tap.contracts.http import ModelCallDetail, TurnTrace
from tap.modules.access.domain.context import ProjectScopeContext


class TraceHttpService(Protocol):
    async def turn_trace(
        self, scope: ProjectScopeContext, conversation_id: str, turn_id: str
    ) -> TurnTrace | None: ...

    async def model_call(
        self, scope: ProjectScopeContext, call_id: str
    ) -> ModelCallDetail | None: ...


__all__ = ["TraceHttpService"]
