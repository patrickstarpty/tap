"""Process entrypoint for the bounded Outbox relay/reconciler role."""

from __future__ import annotations

import asyncio
import math
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import cast

from redis.asyncio import Redis

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


@dataclass(frozen=True, slots=True)
class RelaySettings:
    database_url: str
    redis_url: str
    batch_size: int
    poll_seconds: float
    stream_name: str
    worker_id: str


def load_settings(environment: Mapping[str, str] | None = None) -> RelaySettings:
    values = os.environ if environment is None else environment
    batch_size = int(values.get("TAP_RELAY_BATCH_SIZE", "100"))
    if not MIN_RELAY_BATCH_SIZE <= batch_size <= MAX_RELAY_BATCH_SIZE:
        raise ValueError("TAP_RELAY_BATCH_SIZE must be between 1 and 500")
    poll_seconds = float(values.get("TAP_RELAY_POLL_SECONDS", "1"))
    if not math.isfinite(poll_seconds) or not 0 < poll_seconds <= 60:
        raise ValueError("TAP_RELAY_POLL_SECONDS must be between 0 and 60")
    return RelaySettings(
        database_url=values.get(
            "TAP_DATABASE_URL",
            "mysql+asyncmy://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4",
        ),
        redis_url=values.get("TAP_REDIS_URL", "redis://127.0.0.1:6379/0"),
        batch_size=batch_size,
        poll_seconds=poll_seconds,
        stream_name=values.get("TAP_REDIS_COMMAND_STREAM", "tap:commands"),
        worker_id=values.get("TAP_RELAY_WORKER_ID", socket.gethostname()),
    )


def create_redis_client(redis_url: str) -> Redis:
    return Redis.from_url(
        redis_url,
        decode_responses=True,
        max_connections=20,
        socket_connect_timeout=5.0,
        socket_timeout=5.0,
        socket_keepalive=True,
        health_check_interval=30,
    )


async def run() -> None:
    settings = load_settings()
    engine, sessions = create_engine_and_session_factory(settings.database_url)
    redis = create_redis_client(settings.redis_url)
    relay = Relay(
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
    try:
        while True:
            published = await relay.publish_pending(settings.batch_size)
            if published < settings.batch_size:
                await asyncio.sleep(settings.poll_seconds)
    finally:
        await redis.aclose()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
