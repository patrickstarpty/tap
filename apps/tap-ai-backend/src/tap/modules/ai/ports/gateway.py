"""The only model-provider boundary available to V1 application code."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.models import (
    ChatMessage,
    ModelDescriptor,
    ModelRequest,
    ModelResult,
    ModelStreamEvent,
    ToolSpec,
    ToolTurn,
)


@runtime_checkable
class ModelGateway(Protocol):
    async def catalog(self, scope: ProjectScopeContext) -> tuple[ModelDescriptor, ...]: ...
    async def chat(self, request: ModelRequest) -> ModelResult: ...
    async def embed(self, request: ModelRequest) -> ModelResult: ...
    async def generate_structured(self, request: ModelRequest) -> ModelResult: ...
    def stream_structured(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        """Yield text deltas, then exactly one final `StreamCompleted`."""
        ...

    async def chat_with_tools(
        self,
        request: ModelRequest,
        messages: tuple[ChatMessage, ...],
        tools: tuple[ToolSpec, ...],
    ) -> ToolTurn:
        """One chat turn that may request tool calls.

        `messages` is the whole conversation and starts with the system `request.prompt` and
        the user `request.context`; an empty `tools` sends no tool choice.
        """
        ...
