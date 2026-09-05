"""Process entrypoint for the bounded Outbox relay/reconciler role."""

from __future__ import annotations

import asyncio
import math
import os
import signal
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, cast

from redis.asyncio import Redis

from tap.entrypoints.tapper_runtime import OwnedResources, TapperSettings
from tap.modules.chat.adapters.mysql import OutboxStore
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.messaging.redis_dispatch import (
    MAX_RELAY_BATCH_SIZE,
    MIN_RELAY_BATCH_SIZE,
    AsyncRedis,
    RedisDispatchPublisher,
    Relay,
    SystemClock,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

SignalInstaller = Callable[[asyncio.Event], Callable[[], None] | None]


@dataclass(frozen=True, slots=True)
class RelaySettings:
    database_url: str = field(repr=False)
    redis_url: str = field(repr=False)
    batch_size: int
    poll_seconds: float
    stream_name: str
    worker_id: str


def load_settings(environment: Mapping[str, str] | None = None) -> RelaySettings:
    values = dict(os.environ) if environment is None else dict(environment)
    tapper = TapperSettings.from_mapping(values)
    batch_size = int(values.get("TAP_RELAY_BATCH_SIZE", "100"))
    if not MIN_RELAY_BATCH_SIZE <= batch_size <= MAX_RELAY_BATCH_SIZE:
        raise ValueError("TAP_RELAY_BATCH_SIZE must be between 1 and 500")
    poll_seconds = float(values.get("TAP_RELAY_POLL_SECONDS", "1"))
    if not math.isfinite(poll_seconds) or not 0 < poll_seconds <= 60:
        raise ValueError("TAP_RELAY_POLL_SECONDS must be between 0 and 60")
    return RelaySettings(
        database_url=tapper.database_url,
        redis_url=tapper.redis_url,
        batch_size=batch_size,
        poll_seconds=poll_seconds,
        stream_name=tapper.redis_stream,
        worker_id=tapper.worker_id,
    )


def create_redis_client(redis_url: str) -> Redis:
    return cast(
        Redis,
        Redis.from_url(
            redis_url,
            decode_responses=True,
            max_connections=20,
            socket_connect_timeout=5.0,
            socket_timeout=5.0,
            socket_keepalive=True,
            health_check_interval=30,
        ),
    )


def _open_database(
    settings: RelaySettings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    return create_engine_and_session_factory(settings.database_url)


def _build_relay(
    settings: RelaySettings,
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
) -> Relay:
    return Relay(
        outbox=OutboxStore(sessions),
        publisher=RedisDispatchPublisher(
            redis=cast(AsyncRedis, redis),
            stream_name=settings.stream_name,
            dedup_ttl=timedelta(days=7),
            max_stream_length=10_000,
        ),
        clock=SystemClock(),
        worker_id=settings.worker_id,
        lease_duration=timedelta(seconds=30),
        retry_delay=timedelta(seconds=5),
    )


def install_signal_handlers(stop: asyncio.Event) -> Callable[[], None]:
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
            installed.append(signum)
        except NotImplementedError:
            continue
        except BaseException as error:
            errors: list[BaseException] = [error]
            for installed_signum in reversed(installed):
                try:
                    loop.remove_signal_handler(installed_signum)
                except BaseException as rollback_error:
                    errors.append(rollback_error)
            _raise_collected_errors("Tapper relay signal installation failed", errors)

    def remove_handlers() -> None:
        errors: list[BaseException] = []
        for signum in reversed(installed):
            try:
                loop.remove_signal_handler(signum)
            except BaseException as error:
                errors.append(error)
        _raise_collected_errors("Tapper relay signal removal failed", errors)

    return remove_handlers


async def run(
    *,
    settings: RelaySettings,
    signal_installer: SignalInstaller = install_signal_handlers,
    max_iterations: int | None = None,
) -> None:
    if not isinstance(settings, RelaySettings):
        raise TypeError("relay runtime requires validated settings")
    if max_iterations is not None and (type(max_iterations) is not int or max_iterations < 1):
        raise ValueError("max_iterations must be positive")

    resources = OwnedResources()
    errors: list[BaseException] = []
    remove_handlers: Callable[[], None] | None = None
    try:
        engine, sessions = _open_database(settings)
        resources.push(engine)
        redis = create_redis_client(settings.redis_url)
        resources.push(redis)
        relay = _build_relay(settings, sessions, redis)
        stop = asyncio.Event()
        remove_handlers = signal_installer(stop)
        iterations = 0
        while not stop.is_set():
            published = await relay.publish_pending(settings.batch_size)
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            if published < settings.batch_size:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.poll_seconds)
                except TimeoutError:
                    pass
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
    _raise_collected_errors("Tapper relay lifecycle failed", errors)


def _extend_errors(errors: list[BaseException], error: BaseException) -> None:
    if isinstance(error, BaseExceptionGroup):
        errors.extend(error.exceptions)
    else:
        errors.append(error)


def _raise_collected_errors(message: str, errors: list[BaseException]) -> None:
    if len(errors) == 1:
        raise errors[0]
    if errors:
        if all(isinstance(error, Exception) for error in errors):
            raise ExceptionGroup(message, cast(list[Exception], errors))
        raise BaseExceptionGroup(message, errors)


def main(environment: Mapping[str, str] | None = None) -> None:
    values = dict(os.environ) if environment is None else dict(environment)
    settings = load_settings(values)
    asyncio.run(run(settings=settings))


def cli(environment: Mapping[str, str] | None = None) -> int:
    try:
        main(environment)
    except KeyboardInterrupt:
        return 130
    except BaseException:
        print(
            "Tapper relay failed; check local provider configuration.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
