from __future__ import annotations

import asyncio

import pytest

from tap.entrypoints.tapper_generation_worker import run_worker_loop


class _ScriptedWorker:
    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.calls = 0

    async def run_once(self, *, limit: int) -> int:
        self.calls += 1
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _recording_wait(timeouts: list[float], *, stop_after: int | None = None):
    calls = 0

    async def wait(stop: asyncio.Event, timeout: float) -> None:
        nonlocal calls
        calls += 1
        timeouts.append(timeout)
        if stop_after is not None and calls == stop_after:
            stop.set()

    return wait


@pytest.mark.asyncio
async def test_loop_survives_run_once_errors_with_doubling_backoff() -> None:
    worker = _ScriptedWorker(
        [RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom"), 1, 0]
    )
    stop = asyncio.Event()
    timeouts: list[float] = []
    wait = _recording_wait(timeouts, stop_after=4)

    await run_worker_loop(worker, stop, batch_size=10, poll_seconds=5.0, wait=wait)

    assert timeouts == [1.0, 2.0, 4.0, 5.0]
    assert worker.calls == 5


@pytest.mark.asyncio
async def test_backoff_resets_after_success_and_caps_at_30() -> None:
    worker = _ScriptedWorker(
        [
            RuntimeError("boom"),
            RuntimeError("boom"),
            RuntimeError("boom"),
            RuntimeError("boom"),
            RuntimeError("boom"),
            RuntimeError("boom"),
            RuntimeError("boom"),
            1,
            RuntimeError("boom"),
        ]
    )
    stop = asyncio.Event()
    timeouts: list[float] = []
    wait = _recording_wait(timeouts, stop_after=8)

    await run_worker_loop(worker, stop, batch_size=10, poll_seconds=5.0, wait=wait)

    assert timeouts == [1, 2, 4, 8, 16, 30, 30, 1]
    assert worker.calls == 9


@pytest.mark.asyncio
async def test_loop_exits_when_stop_is_set() -> None:
    worker = _ScriptedWorker([])
    stop = asyncio.Event()
    stop.set()
    timeouts: list[float] = []
    wait = _recording_wait(timeouts)

    await run_worker_loop(worker, stop, batch_size=10, poll_seconds=5.0, wait=wait)

    assert worker.calls == 0
    assert timeouts == []
