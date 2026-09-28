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
