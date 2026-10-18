from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from tap.modules.chat.adapters.redis_drafts import RedisDraftStream, RedisEventWakeups
from tap.modules.chat.application.ports import DraftStreamUnavailable


class _FakePipeline:
    """Queues `xadd`/`expire` synchronously; `execute()` applies them to the
    same in-memory streams as the owning fake client, mirroring
    `redis.asyncio`'s MULTI/EXEC pipeline semantics closely enough for tests."""

    def __init__(self, redis: "_FakeAsyncRedis") -> None:
        self._redis = redis
        self._ops: list[tuple[str, object, object, object]] = []

    def xadd(self, name, fields, *, maxlen=None, approximate=True):
        self._ops.append(("xadd", name, dict(fields), maxlen))
        return self

    def expire(self, name, seconds):
        self._ops.append(("expire", name, seconds, None))
        return self

    async def execute(self):
        results = []
        for kind, name, value, maxlen in self._ops:
            if kind == "xadd":
                results.append(await self._redis.xadd(name, value, maxlen=maxlen))
            else:
                results.append(await self._redis.expire(name, value))
        self._ops = []
        return results

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeAsyncRedis:
    """An in-memory stand-in for the redis.asyncio client's Stream subset."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.maxlens: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.block_calls: list[int | None] = []
        self._counter = 0

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self)

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
        self.block_calls.append(block)
        result = []
        for name, start in streams.items():
            entries = self.streams.get(name, [])
            if start == "$":
                continue
            filtered = [entry for entry in entries if entry[0] > start]
            if filtered:
                result.append([name, filtered])
        return result


class _RaisingPipeline:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def xadd(self, *args, **kwargs):
        return self

    def expire(self, *args, **kwargs):
        return self

    async def execute(self):
        raise self._error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FailingRedis:
    """Raises `error` from every operation this module depends on."""

    def __init__(self, error: BaseException | None = None) -> None:
        self._error = error if error is not None else RedisError("redis unavailable")

    def pipeline(self, transaction: bool = True) -> _RaisingPipeline:
        return _RaisingPipeline(self._error)

    async def xrange(self, *args, **kwargs):
        raise self._error

    async def xread(self, *args, **kwargs):
        raise self._error


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

    chunks, last_id = await drafts.replay("turn-1", attempt=1)

    assert [(chunk.attempt, chunk.seq, chunk.text) for chunk in chunks] == [
        (1, 1, "a"),
        (1, 2, "b"),
    ]
    # The last entry id spans every attempt in the stream, not just the one
    # replayed: the "a"/attempt=1 chunk was appended last, after "x"/attempt=2.
    assert last_id == redis.streams["tap:turn:turn-1:draft"][-1][0]


@pytest.mark.asyncio
async def test_replay_drops_chunks_before_the_last_reset() -> None:
    redis = _FakeAsyncRedis()
    drafts = RedisDraftStream(redis=redis)

    await drafts.append("turn-1", attempt=1, seq=1, text="stale-prefix")
    await drafts.append("turn-1", attempt=1, seq=2, text="", reset=True)
    await drafts.append("turn-1", attempt=1, seq=3, text="fresh")

    chunks, last_id = await drafts.replay("turn-1", attempt=1)

    assert [(chunk.seq, chunk.text, chunk.reset) for chunk in chunks] == [(3, "fresh", False)]
    assert last_id == redis.streams["tap:turn:turn-1:draft"][-1][0]


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
async def test_partial_text_drops_chunks_before_the_last_reset() -> None:
    redis = _FakeAsyncRedis()
    drafts = RedisDraftStream(redis=redis)

    await drafts.append("turn-1", attempt=1, seq=1, text="13812345678")
    await drafts.append("turn-1", attempt=1, seq=2, text="", reset=True)
    await drafts.append("turn-1", attempt=1, seq=3, text="[REDACTED_PHONE]")

    result = await drafts.partial_text("turn-1")

    assert result == (1, "[REDACTED_PHONE]")


@pytest.mark.asyncio
async def test_partial_text_returns_none_when_no_drafts_exist() -> None:
    drafts = RedisDraftStream(redis=_FakeAsyncRedis())

    assert await drafts.partial_text("turn-missing") is None


@pytest.mark.asyncio
async def test_replay_returns_0_0_for_a_missing_turn() -> None:
    """I-4: the live loop's draft cursor must never start from `"$"`; an
    empty/missing stream hands back the sentinel `"0-0"` instead, so the
    first live `read()` still sees every entry ever written for this Turn."""
    drafts = RedisDraftStream(redis=_FakeAsyncRedis())

    chunks, last_id = await drafts.replay("turn-missing", attempt=1)

    assert chunks == ()
    assert last_id == "0-0"


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
async def test_read_uses_the_dedicated_block_client() -> None:
    """`read`'s `XREAD BLOCK` must go over `block_redis`, not the
    general-purpose `redis` client, so it never shares that client's short
    socket timeout or small pool (I-1/I-2)."""
    redis = _FakeAsyncRedis()
    block_redis = _FakeAsyncRedis()
    drafts = RedisDraftStream(redis=redis, block_redis=block_redis)

    await drafts.append("turn-1", attempt=1, seq=1, text="hi")
    entries = await drafts.read({"tap:turn:turn-1:draft": "0"}, block_ms=10)

    # The entry only exists on `redis` (where `append` wrote it); `read`
    # reading an empty `block_redis` instead would return nothing.
    assert entries == ()
    await block_redis.xadd("tap:turn:turn-1:draft", {"attempt": "1", "seq": "9", "text": "y"})
    entries = await drafts.read({"tap:turn:turn-1:draft": "0"}, block_ms=10)
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_read_clamps_block_ms_above_the_socket_timeout_margin() -> None:
    """M-b: `read` must clamp, not raise -- a caller asking for more than the
    client can safely block for just gets the largest safe window instead."""
    redis = _FakeAsyncRedis()
    drafts = RedisDraftStream(redis=redis, socket_timeout_seconds=5.0)
    assert drafts.max_block_ms == 3_000  # 5s - the 2s margin

    await drafts.read({"tap:turn:turn-1:draft": "0"}, block_ms=5_000)

    assert redis.block_calls == [3_000]


@pytest.mark.asyncio
async def test_read_passes_block_ms_through_below_the_margin() -> None:
    redis = _FakeAsyncRedis()
    drafts = RedisDraftStream(redis=redis, socket_timeout_seconds=25.0)
    assert drafts.max_block_ms == 23_000

    entries = await drafts.read({"tap:turn:turn-1:draft": "0"}, block_ms=15_000)

    assert entries == ()
    assert redis.block_calls == [15_000]


@pytest.mark.asyncio
async def test_read_never_clamps_when_no_socket_timeout_is_configured() -> None:
    redis = _FakeAsyncRedis()
    drafts = RedisDraftStream(redis=redis)
    assert drafts.max_block_ms is None

    await drafts.read({"tap:turn:turn-1:draft": "0"}, block_ms=60_000)

    assert redis.block_calls == [60_000]


def test_socket_timeout_must_exceed_the_block_margin() -> None:
    with pytest.raises(ValueError):
        RedisDraftStream(redis=_FakeAsyncRedis(), socket_timeout_seconds=2.0)


@pytest.mark.asyncio
async def test_read_clamps_a_zero_block_ms_to_one_millisecond() -> None:
    """M-2: `XREAD BLOCK 0` blocks forever, so `block_ms=0` (or negative)
    must never reach the client unclamped."""
    redis = _FakeAsyncRedis()
    drafts = RedisDraftStream(redis=redis, socket_timeout_seconds=5.0)

    await drafts.read({"tap:turn:turn-1:draft": "0"}, block_ms=0)

    assert redis.block_calls == [1]


@pytest.mark.asyncio
async def test_read_clamps_a_negative_block_ms_to_one_millisecond() -> None:
    redis = _FakeAsyncRedis()
    drafts = RedisDraftStream(redis=redis)

    await drafts.read({"tap:turn:turn-1:draft": "0"}, block_ms=-5)

    assert redis.block_calls == [1]


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        RedisTimeoutError("timed out"),
        RedisConnectionError("connection reset"),
        OSError("broken pipe"),
        asyncio.TimeoutError("socket timeout"),
    ],
)
async def test_non_redis_transport_failures_also_raise_draft_stream_unavailable(error) -> None:
    drafts = RedisDraftStream(redis=_FailingRedis(error))
    wakeups = RedisEventWakeups(redis=_FailingRedis(error))

    with pytest.raises(DraftStreamUnavailable):
        await drafts.append("turn-1", attempt=1, seq=1, text="x")
    with pytest.raises(DraftStreamUnavailable):
        await wakeups.notify("conv-1", 1)


@pytest.mark.asyncio
async def test_cancelled_error_propagates_instead_of_degrading() -> None:
    drafts = RedisDraftStream(redis=_FailingRedis(asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        await drafts.append("turn-1", attempt=1, seq=1, text="x")
