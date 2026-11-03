"""Long-running worker loop that survives iteration failures with bounded backoff."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from opentelemetry.trace import Status, StatusCode

from tap.platform.telemetry import span

logger = logging.getLogger(__name__)

_FAILURE_BASE_SECONDS = 0.5


async def run_worker_loop(
    name: str,
    iteration: Callable[[], Awaitable[bool]],
    *,
    idle_wait: Callable[[], Awaitable[None]],
    stop: asyncio.Event | None = None,
    failure_cap_seconds: float = 30.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Run ``iteration`` until ``stop`` is set; it returns whether it did work."""

    failures = 0
    while stop is None or not stop.is_set():
        try:
            worked = await iteration()
        except Exception as error:
            failures += 1
            logger.exception(
                "worker %s iteration failed (consecutive failures: %d)", name, failures
            )
            with span(
                "worker.iteration.failed",
                {"tap.worker": name, "tap.consecutive_failures": failures},
            ) as failed:
                failed.set_status(Status(StatusCode.ERROR, type(error).__name__))
                failed.record_exception(error)
            delay = min(failure_cap_seconds, _FAILURE_BASE_SECONDS * 2 ** min(failures - 1, 32))
            await _sleep_until_stopped(sleep, delay, stop)
            continue
        failures = 0
        if stop is not None and stop.is_set():
            return
        if not worked:
            await idle_wait()


async def _sleep_until_stopped(
    sleep: Callable[[float], Awaitable[None]], delay: float, stop: asyncio.Event | None
) -> None:
    if stop is None:
        await sleep(delay)
        return
    sleeping = asyncio.ensure_future(sleep(delay))
    stopping = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait((sleeping, stopping), return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (sleeping, stopping):
            task.cancel()
    if sleeping.done() and not sleeping.cancelled():
        sleeping.result()
