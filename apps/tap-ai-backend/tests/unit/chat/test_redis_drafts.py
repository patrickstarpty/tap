from __future__ import annotations

from datetime import timedelta

import pytest
from redis.exceptions import RedisError

from tap.modules.chat.adapters.redis_drafts import RedisDraftStream, RedisEventWakeups
from tap.modules.chat.application.ports import DraftStreamUnavailable


class _FakeAsyncRedis:
    """An in-memory stand-in for the redis.asyncio client's Stream subset."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.maxlens: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self._counter = 0

    async def xadd(self, name, fields, *, maxlen=None, approximate=True):
        self._counter += 1
        entry_id = f"{self._counter:020d}-0"
        self.streams.setdefault(name, []).append((entry_id, dict(fields)))
        if maxlen is not None:
            self.maxlens[name] = maxlen
            entries = self.streams[name]
            if len(entries) > maxlen:
                del entries[: len(entries) - maxlen]
        return entry_id

    async def expire(self, name, seconds):
        self.ttls[name] = seconds
        return True

    async def xrange(self, name, min="-", max="+", count=None):
        return list(self.streams.get(name, []))

    async def xread(self, streams, *, count=None, block=None):
        result = []
        for name, start in streams.items():
            entries = self.streams.get(name, [])
            if start == "$":
                continue
            filtered = [entry for entry in entries if entry[0] > start]
            if filtered:
                result.append([name, filtered])
        return result


class _FailingRedis:
    async def xadd(self, *args, **kwargs):
        raise RedisError("redis unavailable")

    async def expire(self, *args, **kwargs):
        raise RedisError("redis unavailable")

    async def xrange(self, *args, **kwargs):
        raise RedisError("redis unavailable")

    async def xread(self, *args, **kwargs):
        raise RedisError("redis unavailable")


@pytest.mark.asyncio
async def test_append_sets_maxlen_and_ttl() -> None:
    redis = _FakeAsyncRedis()
    drafts = RedisDraftStream(redis=redis, maxlen=4096, ttl=timedelta(seconds=86_400))

    await drafts.append("turn-1", attempt=1, seq=1, text="hello")

    key = "tap:turn:turn-1:draft"
    assert redis.maxlens[key] == 4096
    assert redis.ttls[key] == 86_400
    [(entry_id, fields)] = redis.streams[key]
    assert fields == {"attempt": "1", "seq": "1", "text": "hello"}
    assert entry_id


@pytest.mark.asyncio
async def test_replay_filters_attempt_and_orders_by_seq() -> None:
    redis = _FakeAsyncRedis()
    drafts = RedisDraftStream(redis=redis)

    await drafts.append("turn-1", attempt=1, seq=2, text="b")
    await drafts.append("turn-1", attempt=2, seq=1, text="x")
    await drafts.append("turn-1", attempt=1, seq=1, text="a")

    chunks = await drafts.replay("turn-1", attempt=1)

    assert [(chunk.attempt, chunk.seq, chunk.text) for chunk in chunks] == [
        (1, 1, "a"),
        (1, 2, "b"),
    ]


@pytest.mark.asyncio
async def test_partial_text_uses_latest_attempt() -> None:
    redis = _FakeAsyncRedis()
    drafts = RedisDraftStream(redis=redis)

    await drafts.append("turn-1", attempt=1, seq=1, text="old-attempt")
    await drafts.append("turn-1", attempt=2, seq=1, text="new-")
    await drafts.append("turn-1", attempt=2, seq=2, text="draft")

    result = await drafts.partial_text("turn-1")

    assert result == (2, "new-draft")


@pytest.mark.asyncio
async def test_partial_text_returns_none_when_no_drafts_exist() -> None:
    drafts = RedisDraftStream(redis=_FakeAsyncRedis())

    assert await drafts.partial_text("turn-missing") is None


@pytest.mark.asyncio
async def test_read_returns_entries_from_both_keys() -> None:
    redis = _FakeAsyncRedis()
    drafts = RedisDraftStream(redis=redis)
    wakeups = RedisEventWakeups(redis=redis)

    await drafts.append("turn-1", attempt=1, seq=1, text="hi")
    await wakeups.notify("conv-1", 7)

    entries = await drafts.read(
        {"tap:turn:turn-1:draft": "0", "tap:conv:conv-1:events": "0"}, block_ms=10
    )

    keys = {entry.key for entry in entries}
    assert keys == {"tap:turn:turn-1:draft", "tap:conv:conv-1:events"}


@pytest.mark.asyncio
async def test_redis_errors_raise_draft_stream_unavailable() -> None:
    drafts = RedisDraftStream(redis=_FailingRedis())
    wakeups = RedisEventWakeups(redis=_FailingRedis())

    with pytest.raises(DraftStreamUnavailable):
        await drafts.append("turn-1", attempt=1, seq=1, text="x")
    with pytest.raises(DraftStreamUnavailable):
        await drafts.replay("turn-1", attempt=1)
    with pytest.raises(DraftStreamUnavailable):
        await drafts.partial_text("turn-1")
    with pytest.raises(DraftStreamUnavailable):
        await drafts.read({"tap:turn:turn-1:draft": "0"}, block_ms=10)
    with pytest.raises(DraftStreamUnavailable):
        await wakeups.notify("conv-1", 1)
