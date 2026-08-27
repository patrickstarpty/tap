"""Bounded process loop for the provider-neutral Athena ingestion worker."""

from __future__ import annotations

import asyncio
import importlib
import math
import os
import signal
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from tap.modules.knowledge.application.ingestion import IngestionWorker
from tap.platform.messaging.redis_wakeup import WakeupConsumer


class BoundedWorker(Protocol):
    async def run_once(self, limit: int): ...  # type: ignore[no-untyped-def]


class AsyncCloseable(Protocol):
    async def aclose(self) -> None: ...


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


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    worker: BoundedWorker
    wakeups: WakeupConsumer
    resources: tuple[AsyncCloseable, ...] = ()


RuntimeFactory = Callable[[WorkerSettings], Awaitable[WorkerRuntime]]
SignalInstaller = Callable[[asyncio.Event], Callable[[], None] | None]


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
        wakeup_failed = False
        try:
            wakeup = await wakeups.wait(
                max_wait_seconds=min(settings.poll_seconds, settings.wakeup_seconds)
            )
        except Exception:
            wakeup = None
            wakeup_failed = True
        if stop.is_set():
            break
        await worker.run_once(limit=settings.job_batch_size)
        if wakeup is not None:
            await wakeups.ack(wakeup)
        if wakeup_failed:
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.poll_seconds)
            except TimeoutError:
                pass
        if stop.is_set():
            break
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return


def install_signal_handlers(stop: asyncio.Event) -> Callable[[], None]:
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
            installed.append(signum)
        except NotImplementedError:
            # Windows and embedded event loops can still cancel the outer task.
            continue
        except BaseException as error:
            errors = [error]
            for installed_signum in reversed(installed):
                try:
                    loop.remove_signal_handler(installed_signum)
                except BaseException as rollback_error:
                    errors.append(rollback_error)
            _raise_collected_errors("Athena signal installation failed", errors)

    def remove_handlers() -> None:
        for signum in installed:
            loop.remove_signal_handler(signum)

    return remove_handlers


async def run(
    *,
    runtime_factory: RuntimeFactory,
    environment: Mapping[str, str] | None = None,
    signal_installer: SignalInstaller = install_signal_handlers,
    max_iterations: int | None = None,
) -> None:
    """Own one complete worker process lifecycle around an injected composition root."""

    settings = load_settings(environment)
    runtime = await runtime_factory(settings)
    stop = asyncio.Event()
    errors: list[BaseException] = []
    remove_handlers: Callable[[], None] | None = None
    try:
        remove_handlers = signal_installer(stop)
        await run_worker_loop(
            worker=runtime.worker,
            wakeups=runtime.wakeups,
            settings=settings,
            stop=stop,
            max_iterations=max_iterations,
        )
    except BaseException as error:
        errors.append(error)
    if remove_handlers is not None:
        try:
            remove_handlers()
        except BaseException as error:
            errors.append(error)
    for resource in reversed(runtime.resources):
        try:
            await resource.aclose()
        except BaseException as error:
            errors.append(error)
    _raise_collected_errors("Athena worker lifecycle failed", errors)


def _raise_collected_errors(message: str, errors: list[BaseException]) -> None:
    if len(errors) == 1:
        raise errors[0]
    if errors:
        if all(isinstance(error, Exception) for error in errors):
            raise ExceptionGroup(message, cast(list[Exception], errors))
        raise BaseExceptionGroup(message, errors)


def main(environment: Mapping[str, str] | None = None) -> None:
    values = os.environ if environment is None else environment
    factory_path = values.get("TAP_ATHENA_RUNTIME_FACTORY")
    if factory_path is None or not factory_path.strip():
        raise RuntimeError(
            "TAP_ATHENA_RUNTIME_FACTORY must name a Task 5 composition factory as module:attribute"
        )
    module_name, separator, attribute_name = factory_path.partition(":")
    if not separator or not module_name or not attribute_name:
        raise RuntimeError("TAP_ATHENA_RUNTIME_FACTORY must use module:attribute syntax")
    factory = getattr(importlib.import_module(module_name), attribute_name, None)
    if not callable(factory):
        raise RuntimeError("TAP_ATHENA_RUNTIME_FACTORY must resolve to a callable")
    asyncio.run(run(runtime_factory=cast(RuntimeFactory, factory), environment=values))


def _bounded_seconds(value: str, name: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or not 0 < seconds <= 60:
        raise ValueError(f"{name} must be between 0 and 60")
    return seconds


if __name__ == "__main__":
    main()
