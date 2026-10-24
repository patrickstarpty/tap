"""Process entrypoint for the Langfuse sync worker.

Mirrors MySQL-recorded traces and feedback into a local Langfuse instance, if
one is configured. Without `LANGFUSE_HOST` the process intentionally idles:
no MySQL store, SDK sink, or Langfuse client is constructed, so a developer
who has not opted into the optional local Langfuse dashboard (see
`.env.example`) pays no startup cost for this process beyond one log line.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from tap.entrypoints.tapper_runtime import OwnedResources, TapperSettings

if TYPE_CHECKING:
    from tap.modules.ai.application.langfuse_sync import LangfuseSyncService

logger = logging.getLogger(__name__)

SERVICE_NAME = "tap-ai-worker-langfuse-sync"
_LOOP_SLEEP_SECONDS = 5.0

SignalInstaller = Callable[[asyncio.Event], Callable[[], None] | None]


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
            errors: list[BaseException] = [error]
            for installed_signum in reversed(installed):
                try:
                    loop.remove_signal_handler(installed_signum)
                except BaseException as rollback_error:
                    errors.append(rollback_error)
            _raise_collected_errors("Tapper langfuse sync signal installation failed", errors)

    def remove_handlers() -> None:
        errors: list[BaseException] = []
        for signum in reversed(installed):
            try:
                loop.remove_signal_handler(signum)
            except BaseException as error:
                errors.append(error)
        _raise_collected_errors("Tapper langfuse sync signal removal failed", errors)

    return remove_handlers


def _raise_collected_errors(message: str, errors: list[BaseException]) -> None:
    if len(errors) == 1:
        raise errors[0]
    if errors:
        if all(isinstance(error, Exception) for error in errors):
            raise ExceptionGroup(message, cast(list[Exception], errors))
        raise BaseExceptionGroup(message, errors)


def _extend_errors(errors: list[BaseException], error: BaseException) -> None:
    if isinstance(error, BaseExceptionGroup):
        errors.extend(error.exceptions)
    else:
        errors.append(error)


def build_service(settings: TapperSettings, resources: OwnedResources) -> LangfuseSyncService:
    """Compose the MySQL store, SDK sink, and redactor behind one sync service.

    Only called when `settings.langfuse_host` is set; `TapperSettings.from_mapping`
    already guarantees the paired keys are present whenever the host is.
    """

    from tap.modules.ai.adapters.langfuse_sink import LangfuseSdkSink
    from tap.modules.ai.adapters.mysql_langfuse_sync import MysqlLangfuseSyncStore
    from tap.modules.ai.application.export_redaction import ExportRedactor, load_custom_rules
    from tap.modules.ai.application.langfuse_sync import LangfuseSyncService
    from tap.platform.db.session import create_engine_and_session_factory

    if settings.langfuse_host is None:
        raise ValueError("Langfuse sync service requires LANGFUSE_HOST")
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        raise ValueError("Langfuse sync service requires LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY")

    engine, sessions = create_engine_and_session_factory(settings.database_url)
    resources.push(engine)
    store = MysqlLangfuseSyncStore(sessions)
    sink = LangfuseSdkSink(
        host=settings.langfuse_host,
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        environment=settings.langfuse_environment,
    )
    redactor = ExportRedactor(load_custom_rules(settings.trace_redaction_rules))
    return LangfuseSyncService(
        store,
        sink,
        redactor,
        environment=settings.langfuse_environment,
        settle_seconds=settings.langfuse_sync_settle_seconds,
        clock=lambda: datetime.now(UTC),
    )


async def run_sync_loop(
    service: LangfuseSyncService,
    *,
    stop: asyncio.Event,
    sleep_seconds: float = _LOOP_SLEEP_SECONDS,
    max_iterations: int | None = None,
) -> None:
    """Run `service.run_once()` forever, logging (never raising) a failed cycle.

    A transient MySQL or Langfuse outage must not kill the process: the next
    cycle gets a fresh chance, same as every other Tapper worker's steady-state
    loop. `LangfuseSyncService` already records a per-item failure for retry;
    this only guards the outer loop against a cycle that fails before that
    (e.g. the scan call itself, or a sink that cannot connect at all).
    """

    iterations = 0
    while not stop.is_set():
        try:
            await service.run_once()
        except Exception:
            logger.exception("Langfuse sync cycle failed")
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return
        if stop.is_set():
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_seconds)
        except TimeoutError:
            pass


async def run_idle_loop(
    *,
    stop: asyncio.Event,
    sleep_seconds: float = _LOOP_SLEEP_SECONDS,
    max_iterations: int | None = None,
    announce: bool = True,
) -> None:
    """Idle without constructing any store, sink, or Langfuse SDK client."""

    if announce:
        logger.info("langfuse sync disabled")
    iterations = 0
    while not stop.is_set():
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_seconds)
        except TimeoutError:
            pass


async def run(
    *,
    settings: TapperSettings,
    signal_installer: SignalInstaller = install_signal_handlers,
    max_iterations: int | None = None,
) -> None:
    """Own one complete worker process lifecycle: idle or sync, cleanly on signal."""

    if not isinstance(settings, TapperSettings):
        raise TypeError("Tapper langfuse sync worker requires validated settings")
    if max_iterations is not None and (type(max_iterations) is not int or max_iterations < 1):
        raise ValueError("max_iterations must be positive")

    resources = OwnedResources()
    errors: list[BaseException] = []
    remove_handlers: Callable[[], None] | None = None
    try:
        stop = asyncio.Event()
        remove_handlers = signal_installer(stop)
        if settings.e2e_mode:
            logger.info(
                "langfuse sync disabled: TAP_DEMO_MODE=e2e never writes to a shared Langfuse"
            )
            await run_idle_loop(stop=stop, max_iterations=max_iterations, announce=False)
        elif settings.langfuse_host is None:
            await run_idle_loop(stop=stop, max_iterations=max_iterations)
        else:
            service = build_service(settings, resources)
            await run_sync_loop(service, stop=stop, max_iterations=max_iterations)
    except BaseException as error:
        errors.append(error)
    if remove_handlers is not None:
        try:
            remove_handlers()
        except BaseException as error:
            _extend_errors(errors, error)
    try:
        await resources.aclose()
    except BaseException as error:
        _extend_errors(errors, error)
    _raise_collected_errors("Tapper langfuse sync worker lifecycle failed", errors)


def main(environment: Mapping[str, str] | None = None) -> None:
    from tap.entrypoints.tracing_setup import start_tracing

    values = dict(os.environ) if environment is None else dict(environment)
    settings = TapperSettings.from_mapping(values)
    start_tracing(SERVICE_NAME, settings)
    asyncio.run(run(settings=settings))


def cli(environment: Mapping[str, str] | None = None) -> int:
    try:
        main(environment)
    except KeyboardInterrupt:
        return 130
    except BaseException:
        print(
            "Tapper langfuse sync worker failed; check local provider configuration.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
