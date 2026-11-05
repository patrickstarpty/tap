import asyncio
from unittest.mock import AsyncMock

import pytest

from tap.entrypoints.tapper_ingestion_worker import WorkerSettings, run_worker_loop


@pytest.mark.asyncio
async def test_worker_recovers_persisted_chunk_edits_after_ingestion():
    order = []
    worker = AsyncMock()
    worker.run_once.side_effect = lambda **kwargs: order.append("ingest")
    pending = AsyncMock(side_effect=lambda **kwargs: order.append("chunks"))
    wakeups = AsyncMock()
    wakeups.wait.return_value = None
    settings = WorkerSettings("mysql", "redis", 2, 0.01, 0.01, "stream", "group", "worker")
    await run_worker_loop(
        worker=worker,
        wakeups=wakeups,
        settings=settings,
        stop=asyncio.Event(),
        max_iterations=1,
        pending_work=pending,
    )
    assert order == ["ingest", "chunks"]
    pending.assert_awaited_once_with(limit=2)


class DrainableWorker:
    """A bounded worker whose pass blocks (like a long model call) until it is
    told to stop claiming, or forever when `honours_drain` is false."""

    def __init__(self, *, honours_drain: bool) -> None:
        self.started = asyncio.Event()
        self.drained = asyncio.Event()
        self.cancelled = False
        self._honours_drain = honours_drain

    def stop_claiming(self) -> None:
        if self._honours_drain:
            self.drained.set()

    async def run_once(self, limit: int) -> None:
        del limit
        self.started.set()
        try:
            await self.drained.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def _stop_after_start(worker: DrainableWorker, stop: asyncio.Event) -> None:
    await worker.started.wait()
    stop.set()


@pytest.mark.asyncio
async def test_stop_drains_the_running_pass_then_exits():
    worker = DrainableWorker(honours_drain=True)
    wakeups = AsyncMock()
    wakeups.wait.return_value = None
    pending = AsyncMock()
    stop = asyncio.Event()
    settings = WorkerSettings(
        "mysql", "redis", 2, 0.01, 0.01, "stream", "group", "worker", shutdown_grace_seconds=5
    )

    await asyncio.wait_for(
        asyncio.gather(
            run_worker_loop(
                worker=worker, wakeups=wakeups, settings=settings, stop=stop, pending_work=pending
            ),
            _stop_after_start(worker, stop),
        ),
        timeout=2,
    )

    assert worker.drained.is_set() and not worker.cancelled
    pending.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_cancels_a_pass_that_outlives_the_grace_period():
    worker = DrainableWorker(honours_drain=False)
    wakeups = AsyncMock()
    wakeups.wait.return_value = None
    stop = asyncio.Event()
    settings = WorkerSettings(
        "mysql", "redis", 2, 0.01, 0.01, "stream", "group", "worker", shutdown_grace_seconds=0.05
    )

    await asyncio.wait_for(
        asyncio.gather(
            run_worker_loop(worker=worker, wakeups=wakeups, settings=settings, stop=stop),
            _stop_after_start(worker, stop),
        ),
        timeout=2,
    )

    assert worker.cancelled
