"""The suggestion worker runtime composes the full API graph (grounding needs
a real AnswerService) but polls on a plain timer instead of a Redis stream,
since no aggregate publishes a "suggestion due" wakeup event."""

from __future__ import annotations

import importlib

import pytest

from tests.unit.entrypoints.test_tapper_runtime import valid_settings


def _runtime():  # type: ignore[no-untyped-def]
    return importlib.import_module("tap.entrypoints.tapper_runtime")


@pytest.mark.asyncio
async def test_suggestion_worker_runtime_uses_idle_wakeups(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from tap.entrypoints.tapper_suggestion_worker import IdleWakeups

    module = _runtime()
    settings = module.TapperSettings.from_mapping(valid_settings())

    class FakeHttpServices:
        prompt_suggestions = object()
        prompt_suggestion_store = object()

    class FakeApiRuntime:
        def __init__(self) -> None:
            self.http_services = FakeHttpServices()
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    fake_runtime = FakeApiRuntime()

    async def fake_create_api_runtime(_settings):  # type: ignore[no-untyped-def]
        return fake_runtime

    monkeypatch.setattr(module, "create_api_runtime", fake_create_api_runtime)

    runtime = await module.create_suggestion_worker_runtime(settings)

    assert isinstance(runtime.wakeups, IdleWakeups)
    assert runtime.worker._store is fake_runtime.http_services.prompt_suggestion_store
    assert runtime.worker._service is fake_runtime.http_services.prompt_suggestions
    assert runtime.worker._worker_id == settings.worker_id + "-suggestions"
    assert runtime.resources == (fake_runtime,)
    assert fake_runtime.closed is False


@pytest.mark.asyncio
async def test_suggestion_worker_runtime_closes_api_graph_if_composition_is_unavailable(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    module = _runtime()
    settings = module.TapperSettings.from_mapping(valid_settings())

    class FakeHttpServices:
        prompt_suggestions = None
        prompt_suggestion_store = None

    class FakeApiRuntime:
        def __init__(self) -> None:
            self.http_services = FakeHttpServices()
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    fake_runtime = FakeApiRuntime()

    async def fake_create_api_runtime(_settings):  # type: ignore[no-untyped-def]
        return fake_runtime

    monkeypatch.setattr(module, "create_api_runtime", fake_create_api_runtime)

    with pytest.raises(ValueError, match="prompt suggestion composition"):
        await module.create_suggestion_worker_runtime(settings)

    assert fake_runtime.closed is True
