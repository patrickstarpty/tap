"""Worker main loop survives iteration failures with bounded backoff."""

import asyncio
import time
from collections.abc import Awaitable, Callable

import pytest
from opentelemetry.trace import StatusCode

from tap.platform.worker_loop import run_worker_loop


def _scripted(
    steps: list[Callable[[], bool]],
) -> Callable[[], Awaitable[bool]]:
    remaining = list(steps)

    async def iteration() -> bool:
        return remaining.pop(0)()

    return iteration


def _raise() -> bool:
    raise RuntimeError("database unavailable")


async def _no_idle() -> None:
    raise AssertionError("idle_wait must not run after work")


@pytest.mark.asyncio
async def test_failures_back_off_then_loop_continues(span_recorder) -> None:
    stop = asyncio.Event()
    sleeps: list[float] = []
    ran: list[str] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    def work() -> bool:
        ran.append("work")
        return True

    def finish() -> bool:
        ran.append("finish")
        stop.set()
        return True

    await run_worker_loop(
        "ingestion",
        _scripted([_raise, _raise, work, finish]),
        idle_wait=_no_idle,
        stop=stop,
        sleep=sleep,
    )

    assert sleeps == [0.5, 1.0]
    assert ran == ["work", "finish"]
    failed = [s for s in span_recorder.get_finished_spans() if s.name == "worker.iteration.failed"]
    assert [s.attributes["tap.consecutive_failures"] for s in failed] == [1, 2]
    assert all(s.attributes["tap.worker"] == "ingestion" for s in failed)
    assert all(s.status.status_code is StatusCode.ERROR for s in failed)


@pytest.mark.asyncio
async def test_failure_backoff_resets_after_success_and_is_capped() -> None:
    stop = asyncio.Event()
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    def finish() -> bool:
        stop.set()
        return True

    steps = [_raise] * 8 + [lambda: True, _raise, finish]
    await run_worker_loop(
        "generation",
        _scripted(steps),
        idle_wait=_no_idle,
        stop=stop,
        failure_cap_seconds=30.0,
        sleep=sleep,
    )

    assert sleeps == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 0.5]


@pytest.mark.asyncio
async def test_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    stop = asyncio.Event()

    async def sleep(delay: float) -> None:
        stop.set()

    await run_worker_loop("graph", _scripted([_raise]), idle_wait=_no_idle, stop=stop, sleep=sleep)

    assert any(
        record.exc_info is not None and "graph" in record.getMessage() for record in caplog.records
    )


@pytest.mark.asyncio
async def test_cancelled_error_propagates() -> None:
    def cancel() -> bool:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_worker_loop("graph", _scripted([cancel]), idle_wait=_no_idle)


@pytest.mark.asyncio
async def test_idle_wait_runs_only_when_no_work_was_done() -> None:
    stop = asyncio.Event()
    events: list[str] = []

    def idle() -> bool:
        events.append("iteration:idle")
        return False

    def busy() -> bool:
        events.append("iteration:busy")
        return True

    def finish() -> bool:
        events.append("iteration:finish")
        stop.set()
        return False

    async def idle_wait() -> None:
        events.append("idle_wait")

    await run_worker_loop(
        "suggestion", _scripted([idle, busy, finish]), idle_wait=idle_wait, stop=stop
    )

    assert events == ["iteration:idle", "idle_wait", "iteration:busy", "iteration:finish"]


@pytest.mark.asyncio
async def test_preset_stop_returns_without_iterating() -> None:
    stop = asyncio.Event()
    stop.set()

    await run_worker_loop("graph", _scripted([]), idle_wait=_no_idle, stop=stop)


@pytest.mark.asyncio
async def test_stop_interrupts_failure_backoff() -> None:
    stop = asyncio.Event()

    async def iteration() -> bool:
        asyncio.get_running_loop().call_later(0.01, stop.set)
        raise RuntimeError("boom")

    started = time.monotonic()
    await run_worker_loop("graph", iteration, idle_wait=_no_idle, stop=stop)

    assert time.monotonic() - started < 0.3
