from __future__ import annotations

import asyncio
from time import monotonic
from uuid import uuid4

from tap.entrypoints import tapper_runtime
from tests.unit.entrypoints.test_tapper_runtime import valid_settings


def _settings(owned_redis_url: str) -> tapper_runtime.TapperSettings:
    """The real settings-construction path, so `create_api_runtime`'s own
    `_create_redis`/`_create_redis_stream_block_client`/`_create_draft_stream`
    factories are exercised exactly as production builds them (I-5) -- not a
    hand-rolled `RedisDraftStream(redis=Redis.from_url(...))`."""
    return tapper_runtime.TapperSettings.from_mapping(
        valid_settings()
        | {
            "TAP_REDIS_URL": owned_redis_url,
            "TAP_REDIS_STREAM_SOCKET_TIMEOUT_SECONDS": "20",
            "TAP_REDIS_STREAM_POOL_SIZE": "10",
        }
    )


def test_read_blocks_until_draft_or_wakeup_arrives(owned_redis_url: str) -> None:
    async def scenario() -> None:
        settings = _settings(owned_redis_url)
        redis = tapper_runtime._create_redis(settings)
        block_redis = tapper_runtime._create_redis_stream_block_client(settings)
        drafts = tapper_runtime._create_draft_stream(redis, block_redis, settings)
        turn_id = f"test-{uuid4().hex}"
        conversation_id = f"test-{uuid4().hex}"
        draft_key = f"tap:turn:{turn_id}:draft"
        wake_key = f"tap:conv:{conversation_id}:events"
        try:

            async def delayed_append() -> None:
                await asyncio.sleep(0.2)
                await drafts.append(turn_id, attempt=1, seq=1, text="hello")

            append_task = asyncio.create_task(delayed_append())
            started = monotonic()
            # block_ms (6s) comfortably exceeds the general-purpose client's
            # 5s socket_timeout (I-1): if `read` used that client instead of
            # the dedicated `block_redis`, this would raise `RedisTimeoutError`
            # well before the append below ever lands.
            entries = await drafts.read({draft_key: "0", wake_key: "0"}, block_ms=6_000)
            elapsed = monotonic() - started
            await append_task

            assert elapsed < 1.0
            assert any(entry.key == draft_key for entry in entries)
        finally:
            await redis.delete(draft_key, wake_key)
            await redis.aclose()
            await block_redis.aclose()

    asyncio.run(scenario())


def test_notify_is_observable_via_read(owned_redis_url: str) -> None:
    async def scenario() -> None:
        settings = _settings(owned_redis_url)
        redis = tapper_runtime._create_redis(settings)
        block_redis = tapper_runtime._create_redis_stream_block_client(settings)
        drafts = tapper_runtime._create_draft_stream(redis, block_redis, settings)
        wakeups = tapper_runtime._create_event_wakeups(redis)
        conversation_id = f"test-{uuid4().hex}"
        wake_key = f"tap:conv:{conversation_id}:events"
        try:
            await wakeups.notify(conversation_id, 3)
            entries = await drafts.read({wake_key: "0"}, block_ms=1_000)
            assert len(entries) == 1
            assert entries[0].fields["sequence"] == "3"
        finally:
            await redis.delete(wake_key)
            await redis.aclose()
            await block_redis.aclose()

    asyncio.run(scenario())


def test_append_and_notify_survive_a_block_ms_the_general_client_could_not(
    owned_redis_url: str,
) -> None:
    """I-1/I-2 regression: writes must not share the dedicated blocking
    client's pool, and a blocking read above the general client's 5s socket
    timeout must not raise `DraftStreamUnavailable`."""

    async def scenario() -> None:
        from tap.modules.chat.application.ports import DraftStreamUnavailable

        settings = _settings(owned_redis_url)
        redis = tapper_runtime._create_redis(settings)
        block_redis = tapper_runtime._create_redis_stream_block_client(settings)
        drafts = tapper_runtime._create_draft_stream(redis, block_redis, settings)
        turn_id = f"test-{uuid4().hex}"
        draft_key = f"tap:turn:{turn_id}:draft"
        try:
            await drafts.append(turn_id, attempt=1, seq=1, text="hello")
            try:
                await drafts.read({draft_key: "$"}, block_ms=6_000)
            except DraftStreamUnavailable as error:
                raise AssertionError(
                    "read over the dedicated block client raised DraftStreamUnavailable "
                    f"for a 6s block, which must stay under its own socket timeout: {error}"
                ) from error
        finally:
            await redis.delete(draft_key)
            await redis.aclose()
            await block_redis.aclose()

    asyncio.run(scenario())
