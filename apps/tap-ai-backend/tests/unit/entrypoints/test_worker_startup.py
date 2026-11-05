from __future__ import annotations

import logging

import pytest

from tap.entrypoints import tapper_ingestion_worker
from tap.entrypoints.tapper_runtime import TapperSettings
from tests.object_settings import S3_SETTINGS


class RecordingWorker:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def run_once(self, limit: int):  # type: ignore[no-untyped-def]
        self._events.append("run_once")


class IdleWakeups:
    async def wait(self, *, max_wait_seconds: float):  # type: ignore[no-untyped-def]
        return None

    async def ack(self, wakeup) -> None:  # type: ignore[no-untyped-def]
        raise AssertionError("no wakeup was delivered")


async def _run(startup) -> list[str]:  # type: ignore[no-untyped-def]
    events: list[str] = []

    async def factory(settings):  # type: ignore[no-untyped-def]
        return tapper_ingestion_worker.WorkerRuntime(
            RecordingWorker(events), IdleWakeups(), startup=lambda: startup(events)
        )

    await tapper_ingestion_worker.run(
        runtime_factory=factory,
        settings=TapperSettings.from_mapping(S3_SETTINGS | {"TAPPER_POLL_SECONDS": "0.01"}),
        signal_installer=lambda stop: None,
        max_iterations=2,
    )
    return events


@pytest.mark.asyncio
async def test_startup_step_runs_once_before_the_worker_loop() -> None:
    async def startup(events: list[str]) -> None:
        events.append("startup")

    assert await _run(startup) == ["startup", "run_once", "run_once"]


@pytest.mark.asyncio
async def test_a_failing_startup_step_is_logged_and_the_loop_still_runs(caplog) -> None:
    async def startup(events: list[str]) -> None:
        events.append("startup")
        raise RuntimeError("database not ready")

    with caplog.at_level(logging.WARNING):
        events = await _run(startup)

    assert events == ["startup", "run_once", "run_once"]
    assert "worker startup step failed" in caplog.text
