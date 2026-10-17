"""Redis Stream adapters for Turn answer drafts and conversation wake-ups.

`RedisDraftStream` owns both streams named by the Global Constraints:
`tap:turn:{turnId}:draft` (model-answer draft chunks, written by the `model`
graph node through a batcher) and `tap:conv:{conversationId}:events`
(authoritative-event sequence hints, written by `RedisEventWakeups` after a
Conversation commit). Both are bounded (`XADD MAXLEN ~`) and TTL'd so a crashed
Turn or an abandoned conversation cannot grow Redis without limit; neither
stream carries data that MySQL does not already hold durably, so any Redis
failure degrades callers rather than failing a Turn.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Protocol

from redis.exceptions import RedisError

from tap.modules.chat.application.ports import DraftChunk, DraftStreamUnavailable, StreamEntry

_DRAFT_MAXLEN = 4096
_WAKEUP_MAXLEN = 1024
_DEFAULT_TTL = timedelta(hours=24)


class AsyncRedisStreams(Protocol):
    """The Redis Stream command subset this module depends on."""

    async def xadd(
        self,
        name: str,
        fields: Mapping[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> object: ...

    async def expire(self, name: str, seconds: int) -> object: ...

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
        return DraftChunk(attempt=int(attempt), seq=int(seq), text=text)
    except ValueError:
        return None


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
    """`DraftStream` over `redis.asyncio`; see module docstring for the keys."""

    def __init__(
        self,
        *,
        redis: AsyncRedisStreams,
        maxlen: int = _DRAFT_MAXLEN,
        ttl: timedelta = _DEFAULT_TTL,
    ) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be positive")
        if ttl.total_seconds() < 1:
            raise ValueError("ttl must be at least one second")
        self._redis = redis
        self._maxlen = maxlen
        self._ttl_seconds = int(ttl.total_seconds())

    async def append(self, turn_id: str, *, attempt: int, seq: int, text: str) -> None:
        key = _draft_key(turn_id)
        try:
            await self._redis.xadd(
                key,
                {"attempt": str(attempt), "seq": str(seq), "text": text},
                maxlen=self._maxlen,
                approximate=True,
            )
            await self._redis.expire(key, self._ttl_seconds)
        except RedisError as error:
            raise DraftStreamUnavailable(str(error)) from error

    async def replay(self, turn_id: str, *, attempt: int) -> tuple[DraftChunk, ...]:
        chunks = [chunk for chunk in await self._all_chunks(turn_id) if chunk.attempt == attempt]
        chunks.sort(key=lambda chunk: chunk.seq)
        return tuple(chunks)

    async def partial_text(self, turn_id: str) -> tuple[int, str] | None:
        chunks = await self._all_chunks(turn_id)
        if not chunks:
            return None
        latest_attempt = max(chunk.attempt for chunk in chunks)
        ordered = sorted(
            (chunk for chunk in chunks if chunk.attempt == latest_attempt),
            key=lambda chunk: chunk.seq,
        )
        return latest_attempt, "".join(chunk.text for chunk in ordered)

    async def read(self, keys: Mapping[str, str], *, block_ms: int) -> tuple[StreamEntry, ...]:
        try:
            response = await self._redis.xread(dict(keys), block=block_ms)
        except RedisError as error:
            raise DraftStreamUnavailable(str(error)) from error
        return _decode_stream_entries(response)

    async def _all_chunks(self, turn_id: str) -> list[DraftChunk]:
        key = _draft_key(turn_id)
        try:
            entries = await self._redis.xrange(key, "-", "+")
        except RedisError as error:
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
    """`EventWakeups` over `redis.asyncio`; see module docstring for the key."""

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
            await self._redis.xadd(
                key,
                {"sequence": str(sequence)},
                maxlen=self._maxlen,
                approximate=True,
            )
            await self._redis.expire(key, self._ttl_seconds)
        except RedisError as error:
            raise DraftStreamUnavailable(str(error)) from error
