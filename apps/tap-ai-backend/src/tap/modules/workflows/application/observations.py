"""Application boundary for source-owned model observation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.models import ModelRequest, ModelResult, ModelStreamEvent
from tap.modules.ai.ports.gateway import ModelGateway


@dataclass(slots=True)
class _ModelObservation:
    authorize: Callable[[], Awaitable[None]]
    start: Callable[[], Awaitable[None]]
    started: bool = False

    async def before_call(self) -> None:
        await self.authorize()
        if not self.started:
            await self.start()
            self.started = True


_current_model_observation: ContextVar[_ModelObservation | None] = ContextVar(
    "tapper_model_observation", default=None
)


@asynccontextmanager
async def model_observation_scope(
    authorize: Callable[[], Awaitable[None]],
    start: Callable[[], Awaitable[None]],
) -> AsyncIterator[None]:
    token = _current_model_observation.set(_ModelObservation(authorize, start))
    try:
        yield
    finally:
        _current_model_observation.reset(token)


class ObservedModelGateway:
    """Delegates exactly once, activating only for real answer model calls."""

    def __init__(self, delegate: ModelGateway) -> None:
        self._delegate = delegate

    async def catalog(self, scope: ProjectScopeContext):
        return await self._delegate.catalog(scope)

    async def embed(self, request: ModelRequest) -> ModelResult:
        return await self._delegate.embed(request)

    async def chat(self, request: ModelRequest) -> ModelResult:
        bound = _current_model_observation.get()
        if bound is not None:
            await bound.before_call()
        return await self._delegate.chat(request)

    async def generate_structured(self, request: ModelRequest) -> ModelResult:
        bound = _current_model_observation.get()
        if bound is not None:
            await bound.before_call()
        return await self._delegate.generate_structured(request)

    async def chat_stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        bound = _current_model_observation.get()
        if bound is not None:
            await bound.before_call()
        async for event in self._delegate.chat_stream(request):
            yield event

    async def aclose(self) -> None:
        close = getattr(self._delegate, "aclose", None)
        if close is not None:
            await close()
