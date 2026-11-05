"""The only model-provider boundary available to V1 application code."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.models import (
    ModelDescriptor,
    ModelRequest,
    ModelResult,
    ModelStreamEvent,
)


@runtime_checkable
class ModelGateway(Protocol):
    async def catalog(self, scope: ProjectScopeContext) -> tuple[ModelDescriptor, ...]: ...
    async def chat(self, request: ModelRequest) -> ModelResult: ...
    def chat_stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]: ...
    async def embed(self, request: ModelRequest) -> ModelResult: ...
    async def generate_structured(self, request: ModelRequest) -> ModelResult: ...
