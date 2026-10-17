"""Redis Stream adapters for Turn answer drafts and conversation wake-ups.

`RedisDraftStream` owns both streams named by the Global Constraints:
`tap:turn:{turnId}:draft` (model-answer draft chunks, written by the `model`
graph node through a batcher) and `tap:conv:{conversationId}:events`
(authoritative-event sequence hints, written by `RedisEventWakeups` after a
Conversation commit). Both are bounded (`XADD MAXLEN ~`) and TTL'd so a crashed
Turn or an abandoned conversation cannot grow Redis without limit; neither
stream carries data that MySQL does not already hold durably, so any Redis
failure degrades callers rather than failing a Turn.

Writes (`append`/`notify`) pipeline `XADD` and `EXPIRE` into one round trip
(`MULTI`/`EXEC`) so a crash between the two commands can never leave a
never-expiring stream. `read`'s `XREAD BLOCK` is the one operation that can
legitimately hold a connection open for seconds, so it takes a separate,
dedicated client (`block_redis`): sharing the general-purpose client's short
`socket_timeout` and small pool would both raise spurious timeouts on a
normal-length block window and starve other Redis traffic of connections.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from typing import Protocol

from redis.exceptions import RedisError

from tap.modules.chat.application.ports import DraftChunk, DraftStreamUnavailable, StreamEntry

_DRAFT_MAXLEN = 4096
_WAKEUP_MAXLEN = 1024
_DEFAULT_TTL = timedelta(hours=24)

# Any of these mean "Redis did not answer"; `asyncio.CancelledError` is a
# `BaseException`, not an `Exception`, so it is never caught here and always
# propagates (task cancellation must never look like a degraded Redis call).
_REDIS_FAILURES: tuple[type[BaseException], ...] = (RedisError, OSError, asyncio.TimeoutError)


class AsyncRedisPipeline(Protocol):
    """The chainable, synchronous-queuing subset of `redis.asyncio`'s Pipeline."""

    def xadd(
        self,
        name: str,
        fields: Mapping[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> "AsyncRedisPipeline": ...

    def expire(self, name: str, seconds: int) -> "AsyncRedisPipeline": ...

    async def execute(self) -> list[object]: ...

    async def __aenter__(self) -> "AsyncRedisPipeline": ...

    async def __aexit__(self, *exc_info: object) -> None: ...


class AsyncRedisStreams(Protocol):
    """The Redis Stream command subset this module depends on."""

    def pipeline(self, transaction: bool = True) -> AsyncRedisPipeline: ...

    async def xrange(
        self, name: str, min: str = "-", max: str = "+", count: int | None = None
    ) -> object: ...

    async def xread(
        self,
        streams: Mapping[str, str],
        *,
        count: int | None = None,
        block: int | None = None,
    ) -> object: ...


def _draft_key(turn_id: str) -> str:
    return f"tap:turn:{turn_id}:draft"


def _wakeup_key(conversation_id: str) -> str:
    return f"tap:conv:{conversation_id}:events"


def _decode_draft_chunk(fields: Mapping[str, object]) -> DraftChunk | None:
    attempt = fields.get("attempt")
    seq = fields.get("seq")
    text = fields.get("text")
    if not isinstance(attempt, str) or not isinstance(seq, str) or not isinstance(text, str):
        return None
    try:
        return DraftChunk(
            attempt=int(attempt), seq=int(seq), text=text, reset=fields.get("reset") == "1"
        )
    except ValueError:
        return None


def _drop_before_last_reset(chunks: list[DraftChunk]) -> list[DraftChunk]:
    """A `reset: true` chunk (Task 5's `DraftBatcher`, on a redaction-driven
    rewrite of an already-sent prefix) discards every earlier chunk for its
    attempt; `chunks` must already be sorted by `seq`."""

    kept: list[DraftChunk] = []
    for chunk in chunks:
        if chunk.reset:
            kept = []
            continue
        kept.append(chunk)
    return kept


def _decode_stream_entries(response: object) -> tuple[StreamEntry, ...]:
    if not isinstance(response, list):
        return ()
    entries: list[StreamEntry] = []
    for stream in response:
        if not isinstance(stream, (list, tuple)) or len(stream) != 2:
            continue
        key, messages = stream
        if not isinstance(key, str) or not isinstance(messages, list):
            continue
        for message in messages:
            if not isinstance(message, (list, tuple)) or len(message) != 2:
                continue
            entry_id, fields = message
            if not isinstance(entry_id, str) or not isinstance(fields, dict):
                continue
            entries.append(StreamEntry(key=key, id=entry_id, fields=fields))
    return tuple(entries)


class RedisDraftStream:
    """`DraftStream` over `redis.asyncio`; see module docstring for the keys
    and for why blocking reads use a separate client."""

    #: Margin subtracted from `socket_timeout_seconds` to get `max_block_ms`:
    #: a blocking `XREAD` that ran right up to the socket timeout would race
    #: it, so every `read()` call clamps `block_ms` to stay safely under it.
    _BLOCK_MARGIN_SECONDS = 2.0

    def __init__(
        self,
        *,
        redis: AsyncRedisStreams,
        block_redis: AsyncRedisStreams | None = None,
        maxlen: int = _DRAFT_MAXLEN,
        ttl: timedelta = _DEFAULT_TTL,
        socket_timeout_seconds: float | None = None,
    ) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be positive")
        if ttl.total_seconds() < 1:
            raise ValueError("ttl must be at least one second")
        if (
            socket_timeout_seconds is not None
            and socket_timeout_seconds <= self._BLOCK_MARGIN_SECONDS
        ):
            raise ValueError(
                f"socket_timeout_seconds must exceed the {self._BLOCK_MARGIN_SECONDS}s block margin"
            )
        self._redis = redis
        self._block_redis = block_redis if block_redis is not None else redis
        self._maxlen = maxlen
        self._ttl_seconds = int(ttl.total_seconds())
        self.max_block_ms: int | None = (
            None
            if socket_timeout_seconds is None
            else int((socket_timeout_seconds - self._BLOCK_MARGIN_SECONDS) * 1000)
        )

    async def append(
        self, turn_id: str, *, attempt: int, seq: int, text: str, reset: bool = False
    ) -> None:
        key = _draft_key(turn_id)
        fields = {"attempt": str(attempt), "seq": str(seq), "text": text}
        if reset:
            fields["reset"] = "1"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.xadd(key, fields, maxlen=self._maxlen, approximate=True)
                pipe.expire(key, self._ttl_seconds)
                await pipe.execute()
        except _REDIS_FAILURES as error:
            raise DraftStreamUnavailable(str(error)) from error

    async def replay(self, turn_id: str, *, attempt: int) -> tuple[DraftChunk, ...]:
        chunks = [chunk for chunk in await self._all_chunks(turn_id) if chunk.attempt == attempt]
        chunks.sort(key=lambda chunk: chunk.seq)
        return tuple(_drop_before_last_reset(chunks))

    async def partial_text(self, turn_id: str) -> tuple[int, str] | None:
        chunks = await self._all_chunks(turn_id)
        if not chunks:
            return None
        latest_attempt = max(chunk.attempt for chunk in chunks)
        ordered = sorted(
            (chunk for chunk in chunks if chunk.attempt == latest_attempt),
            key=lambda chunk: chunk.seq,
        )
        kept = _drop_before_last_reset(ordered)
        return latest_attempt, "".join(chunk.text for chunk in kept)

    async def read(self, keys: Mapping[str, str], *, block_ms: int) -> tuple[StreamEntry, ...]:
        # `XREAD BLOCK 0` blocks forever, so `block_ms` is always clamped to
        # at least 1ms; it is also clamped to at most `max_block_ms` so a
        # blocking read can never race this stream's socket timeout.
        if self.max_block_ms is not None:
            block_ms = min(block_ms, self.max_block_ms)
        block_ms = max(block_ms, 1)
        try:
            response = await self._block_redis.xread(dict(keys), block=block_ms)
        except _REDIS_FAILURES as error:
            raise DraftStreamUnavailable(str(error)) from error
        return _decode_stream_entries(response)

    async def _all_chunks(self, turn_id: str) -> list[DraftChunk]:
        key = _draft_key(turn_id)
        try:
            entries = await self._redis.xrange(key, "-", "+")
        except _REDIS_FAILURES as error:
            raise DraftStreamUnavailable(str(error)) from error
        if not isinstance(entries, list):
            return []
        chunks: list[DraftChunk] = []
        for entry in entries:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                continue
            _entry_id, fields = entry
            if not isinstance(fields, dict):
                continue
            chunk = _decode_draft_chunk(fields)
            if chunk is not None:
                chunks.append(chunk)
        return chunks


class RedisEventWakeups:
    """`EventWakeups` over `redis.asyncio`; see module docstring for the key.

    `sequence` is only a "something new happened" hint, not an authoritative
    cursor: it may be written before, concurrently with, or (rarely) slightly
    after the MySQL read a consumer would do in response, and more than one
    writer may race to notify the same conversation. Consumers must always
    re-read MySQL from their own last-seen sequence and must never advance
    their cursor from this value directly.
    """

    def __init__(
        self,
        *,
        redis: AsyncRedisStreams,
        maxlen: int = _WAKEUP_MAXLEN,
        ttl: timedelta = _DEFAULT_TTL,
    ) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be positive")
        if ttl.total_seconds() < 1:
            raise ValueError("ttl must be at least one second")
        self._redis = redis
        self._maxlen = maxlen
        self._ttl_seconds = int(ttl.total_seconds())

    async def notify(self, conversation_id: str, sequence: int) -> None:
        key = _wakeup_key(conversation_id)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.xadd(key, {"sequence": str(sequence)}, maxlen=self._maxlen, approximate=True)
                pipe.expire(key, self._ttl_seconds)
                await pipe.execute()
        except _REDIS_FAILURES as error:
            raise DraftStreamUnavailable(str(error)) from error
