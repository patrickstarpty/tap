"""Bounded process loop for the provider-neutral Tapper ingestion worker."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast

from tap.modules.knowledge.application.ingestion import IngestionWorker
from tap.platform.messaging.redis_wakeup import WakeupConsumer

if TYPE_CHECKING:
    from tap.entrypoints.tapper_runtime import TapperSettings


class BoundedWorker(Protocol):
    async def run_once(self, limit: int): ...  # type: ignore[no-untyped-def]


class AsyncCloseable(Protocol):
    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    database_url: str = field(repr=False)
    redis_url: str = field(repr=False)
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


RuntimeFactory = Callable[["TapperSettings"], Awaitable[WorkerRuntime]]
SignalInstaller = Callable[[asyncio.Event], Callable[[], None] | None]


def load_settings(environment: Mapping[str, str] | None = None) -> WorkerSettings:
    from tap.entrypoints.tapper_runtime import TapperSettings

    values = dict(os.environ) if environment is None else dict(environment)
    return worker_settings_from_tapper(TapperSettings.from_mapping(values))


def worker_settings_from_tapper(settings: TapperSettings) -> WorkerSettings:
    """Derive loop controls from the one fully validated process snapshot."""

    from tap.entrypoints.tapper_runtime import TapperSettings

    if not isinstance(settings, TapperSettings):
        raise TypeError("worker settings require validated Tapper settings")
    return WorkerSettings(
        database_url=settings.database_url,
        redis_url=settings.redis_url,
        job_batch_size=settings.job_batch_size,
        poll_seconds=settings.poll_seconds,
        wakeup_seconds=settings.poll_seconds,
        stream_name=settings.redis_stream,
        group_name="tapper-ingestion",
        worker_id=settings.worker_id,
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
            _raise_collected_errors("Tapper signal installation failed", errors)

    def remove_handlers() -> None:
        errors: list[BaseException] = []
        for signum in reversed(installed):
            try:
                loop.remove_signal_handler(signum)
            except BaseException as error:
                errors.append(error)
        _raise_collected_errors("Tapper signal removal failed", errors)

    return remove_handlers


async def run(
    *,
    runtime_factory: RuntimeFactory,
    settings: TapperSettings,
    signal_installer: SignalInstaller = install_signal_handlers,
    max_iterations: int | None = None,
) -> None:
    """Own one complete worker process lifecycle around an injected composition root."""

    worker_settings = worker_settings_from_tapper(settings)
    runtime = await runtime_factory(settings)
    stop = asyncio.Event()
    errors: list[BaseException] = []
    remove_handlers: Callable[[], None] | None = None
    try:
        remove_handlers = signal_installer(stop)
        await run_worker_loop(
            worker=runtime.worker,
            wakeups=runtime.wakeups,
            settings=worker_settings,
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
    _raise_collected_errors("Tapper worker lifecycle failed", errors)


def _raise_collected_errors(message: str, errors: list[BaseException]) -> None:
    if len(errors) == 1:
        raise errors[0]
    if errors:
        if all(isinstance(error, Exception) for error in errors):
            raise ExceptionGroup(message, cast(list[Exception], errors))
        raise BaseExceptionGroup(message, errors)


def main(environment: Mapping[str, str] | None = None) -> None:
    from tap.entrypoints.tapper_runtime import TapperSettings, create_worker_runtime
    from tap.operations.milvus.client import suppress_pymilvus_rpc_logging

    values = dict(os.environ) if environment is None else dict(environment)
    settings = TapperSettings.from_mapping(values)
    with suppress_pymilvus_rpc_logging():
        asyncio.run(run(runtime_factory=create_worker_runtime, settings=settings))


def cli(environment: Mapping[str, str] | None = None) -> int:
    try:
        main(environment)
    except KeyboardInterrupt:
        return 130
    except BaseException:
        print(
            "Tapper ingestion worker failed; check local provider configuration.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
