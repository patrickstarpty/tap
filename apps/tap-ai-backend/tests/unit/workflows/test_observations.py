"""A legacy Agent is observed only at a real authorized model boundary."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from tap.modules.workflows.application.observations import (
    ObservedModelGateway,
    model_observation_scope,
)


@dataclass
class _Gateway:
    calls: int = 0

    async def chat(self, request):
        self.calls += 1
        return request

    async def generate_structured(self, request):
        self.calls += 1
        return request

    async def embed(self, request):
        self.calls += 1
        return request

    async def catalog(self, scope):
        return ()

    async def chat_stream(self, request):
        self.calls += 1
        yield request


@pytest.mark.asyncio
async def test_actual_chat_and_consumed_stream_start_once_but_embed_does_not() -> None:
    gateway = _Gateway()
    observed = ObservedModelGateway(gateway)
    started = []

    async def authorize():
        return None

    async def start():
        started.append(True)

    await observed.chat("outside")
    async with model_observation_scope(authorize, start):
        await observed.embed("embedding")
        assert started == []
        stream = observed.chat_stream("answer")
        assert started == []
        assert [item async for item in stream] == ["answer"]
        await observed.chat("second round")
    assert gateway.calls == 4
    assert started == [True]


@pytest.mark.asyncio
async def test_final_authorization_denial_starts_no_observation_or_model() -> None:
    gateway = _Gateway()
    observed = ObservedModelGateway(gateway)
    started = []

    async def deny():
        raise PermissionError("source lease lost")

    async def start():
        started.append(True)

    async with model_observation_scope(deny, start):
        with pytest.raises(PermissionError):
            await observed.chat(SimpleNamespace())
    assert started == []
    assert gateway.calls == 0
