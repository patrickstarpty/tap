"""Bounded process loop for the provider-neutral Athena ingestion worker."""

from __future__ import annotations

import asyncio
import math
import os
import signal
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from tap.modules.knowledge.application.ingestion import IngestionWorker
from tap.platform.messaging.redis_wakeup import WakeupConsumer


class BoundedWorker(Protocol):
    async def run_once(self, limit: int): ...  # type: ignore[no-untyped-def]


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    database_url: str
    redis_url: str
    job_batch_size: int
    poll_seconds: float
    wakeup_seconds: float
    stream_name: str
    group_name: str
    worker_id: str


def load_settings(environment: Mapping[str, str] | None = None) -> WorkerSettings:
    values = os.environ if environment is None else environment
    batch_size = int(values.get("TAP_ATHENA_JOB_BATCH_SIZE", "10"))
    if not 1 <= batch_size <= 50:
        raise ValueError("TAP_ATHENA_JOB_BATCH_SIZE must be between 1 and 50")
    poll_seconds = _bounded_seconds(
        values.get("TAP_ATHENA_POLL_SECONDS", "1"), "TAP_ATHENA_POLL_SECONDS"
    )
    wakeup_seconds = _bounded_seconds(
        values.get("TAP_ATHENA_WAKEUP_SECONDS", "1"), "TAP_ATHENA_WAKEUP_SECONDS"
    )
    worker_id = values.get("TAP_ATHENA_WORKER_ID", socket.gethostname())
    if not worker_id.strip():
        raise ValueError("TAP_ATHENA_WORKER_ID must be nonblank")
    stream_name = values.get("TAP_REDIS_COMMAND_STREAM", "tap:commands")
    if not stream_name.strip():
        raise ValueError("TAP_REDIS_COMMAND_STREAM must be nonblank")
    return WorkerSettings(
        database_url=values.get(
            "TAP_DATABASE_URL",
            "mysql+asyncmy://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4",
        ),
        redis_url=values.get("TAP_REDIS_URL", "redis://127.0.0.1:6379/0"),
        job_batch_size=batch_size,
        poll_seconds=poll_seconds,
        wakeup_seconds=wakeup_seconds,
        stream_name=stream_name,
        group_name="athena-ingestion",
        worker_id=worker_id,
    )


async def run_worker_loop(
    *,
    worker: IngestionWorker | BoundedWorker,
    wakeups: WakeupConsumer,
    settings: WorkerSettings,
    stop: asyncio.Event,
    max_iterations: int | None = None,
) -> None:
    if max_iterations is not None and (type(max_iterations) is not int or max_iterations < 1):
        raise ValueError("max_iterations must be positive")
    iterations = 0
    while not stop.is_set():
        wakeup = await wakeups.wait(
            max_wait_seconds=min(settings.poll_seconds, settings.wakeup_seconds)
        )
        if stop.is_set():
            break
        await worker.run_once(limit=settings.job_batch_size)
        if wakeup is not None:
            await wakeups.ack(wakeup)
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return


def install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            # Windows and embedded event loops can still cancel the outer task.
            continue


def _bounded_seconds(value: str, name: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or not 0 < seconds <= 60:
        raise ValueError(f"{name} must be between 0 and 60")
    return seconds
