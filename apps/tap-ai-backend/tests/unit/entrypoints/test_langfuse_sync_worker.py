"""The Langfuse sync worker: idle (no store/sink built) without LANGFUSE_HOST,
otherwise `run_once` on a timer that survives a failing cycle."""

from __future__ import annotations

import asyncio
import logging

import pytest

from tap.entrypoints import tapper_langfuse_sync_worker as worker
from tap.entrypoints.tapper_runtime import TapperSettings
from tests.object_settings import S3_SETTINGS


@pytest.mark.asyncio
async def test_worker_idles_without_langfuse_host(caplog, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = TapperSettings.from_mapping(S3_SETTINGS)
    assert settings.langfuse_host is None

    def _fail_if_called(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("build_service must not run when LANGFUSE_HOST is unset")

    monkeypatch.setattr(worker, "build_service", _fail_if_called)

    with caplog.at_level(logging.INFO, logger=worker.logger.name):
        await worker.run(settings=settings, signal_installer=lambda _stop: None, max_iterations=1)

    assert any("langfuse sync disabled" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_worker_loop_survives_run_once_exception() -> None:
    calls = 0

    class FakeService:
        async def run_once(self):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("boom")

    stop = asyncio.Event()
    await worker.run_sync_loop(FakeService(), stop=stop, sleep_seconds=0.01, max_iterations=2)

    assert calls == 2


@pytest.mark.asyncio
async def test_worker_sync_loop_stops_on_stop_event() -> None:
    calls = 0

    class FakeService:
        async def run_once(self):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1

    stop = asyncio.Event()
    stop.set()
    await worker.run_sync_loop(FakeService(), stop=stop, sleep_seconds=0.01)

    assert calls == 0


def test_worker_service_name_is_stable() -> None:
    assert worker.SERVICE_NAME == "tap-ai-worker-langfuse-sync"
