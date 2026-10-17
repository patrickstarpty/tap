from __future__ import annotations

import asyncio
import os
from time import monotonic
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from tap.modules.chat.adapters.redis_drafts import RedisDraftStream, RedisEventWakeups


@pytest.mark.skipif(
    os.getenv("TAP_RUN_REDIS_INTEGRATION") != "1",
    reason="requires the explicit real Redis integration gate",
)
def test_read_blocks_until_draft_or_wakeup_arrives() -> None:
    async def scenario() -> None:
        redis = Redis.from_url(
            os.getenv("TAP_REDIS_URL", "redis://127.0.0.1:16379/0"),
            decode_responses=True,
        )
        turn_id = f"test-{uuid4().hex}"
        conversation_id = f"test-{uuid4().hex}"
        draft_key = f"tap:turn:{turn_id}:draft"
        wake_key = f"tap:conv:{conversation_id}:events"
        drafts = RedisDraftStream(redis=redis)
        try:

            async def delayed_append() -> None:
                await asyncio.sleep(0.2)
                await drafts.append(turn_id, attempt=1, seq=1, text="hello")

            append_task = asyncio.create_task(delayed_append())
            started = monotonic()
            entries = await drafts.read({draft_key: "0", wake_key: "0"}, block_ms=5_000)
            elapsed = monotonic() - started
            await append_task

            assert elapsed < 1.0
            assert any(entry.key == draft_key for entry in entries)
        finally:
            await redis.delete(draft_key, wake_key)
            await redis.aclose()

    asyncio.run(scenario())


@pytest.mark.skipif(
    os.getenv("TAP_RUN_REDIS_INTEGRATION") != "1",
    reason="requires the explicit real Redis integration gate",
)
def test_notify_is_observable_via_read() -> None:
    async def scenario() -> None:
        redis = Redis.from_url(
            os.getenv("TAP_REDIS_URL", "redis://127.0.0.1:16379/0"),
            decode_responses=True,
        )
        conversation_id = f"test-{uuid4().hex}"
        wake_key = f"tap:conv:{conversation_id}:events"
        wakeups = RedisEventWakeups(redis=redis)
        drafts = RedisDraftStream(redis=redis)
        try:
            await wakeups.notify(conversation_id, 3)
            entries = await drafts.read({wake_key: "0"}, block_ms=1_000)
            assert len(entries) == 1
            assert entries[0].fields["sequence"] == "3"
        finally:
            await redis.delete(wake_key)
            await redis.aclose()

    asyncio.run(scenario())
