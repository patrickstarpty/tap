"""Process entrypoint for the bounded Outbox relay/reconciler role."""

from __future__ import annotations

import asyncio
import os
import socket
from datetime import timedelta
from typing import cast

from redis.asyncio import Redis

from tap.modules.chat.adapters.mysql import OutboxStore
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.messaging.redis_dispatch import (
    AsyncRedis,
    RedisDispatchPublisher,
    Relay,
    SystemClock,
)


async def run() -> None:
    database_url = os.getenv(
        "TAP_DATABASE_URL",
        "mysql+asyncmy://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4",
    )
    redis_url = os.getenv("TAP_REDIS_URL", "redis://127.0.0.1:6379/0")
    batch_size = int(os.getenv("TAP_RELAY_BATCH_SIZE", "100"))
    poll_seconds = float(os.getenv("TAP_RELAY_POLL_SECONDS", "1"))
    engine, sessions = create_engine_and_session_factory(database_url)
    redis = Redis.from_url(redis_url, decode_responses=True)
    relay = Relay(
        outbox=OutboxStore(sessions),
        publisher=RedisDispatchPublisher(
            redis=cast(AsyncRedis, redis),
            stream_name=os.getenv("TAP_REDIS_COMMAND_STREAM", "tap:commands"),
            dedup_ttl=timedelta(days=7),
        ),
        clock=SystemClock(),
        worker_id=os.getenv("TAP_RELAY_WORKER_ID", socket.gethostname()),
        lease_duration=timedelta(seconds=30),
        retry_delay=timedelta(seconds=5),
    )
    try:
        while True:
            published = await relay.publish_pending(batch_size)
            if published < batch_size:
                await asyncio.sleep(poll_seconds)
    finally:
        await redis.aclose()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
