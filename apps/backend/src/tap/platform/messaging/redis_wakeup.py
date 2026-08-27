"""Optional, domain-neutral Redis Stream wake-ups over durable database work."""

from __future__ import annotations

import asyncio
import json
import math
from time import monotonic
from typing import Protocol, cast

from redis.exceptions import RedisError, ResponseError

from tap.platform.messaging.redis_dispatch import AsyncRedisStream, DispatchWakeup


class WakeupConsumer(Protocol):
    async def wait(self, *, max_wait_seconds: float) -> DispatchWakeup | None: ...

    async def ack(self, wakeup: DispatchWakeup) -> None: ...


class RedisWakeupConsumer:
    """Filters one aggregate type for one group while preserving other groups."""

    def __init__(
        self,
        *,
        redis: AsyncRedisStream,
        stream_name: str,
        group_name: str,
        consumer_name: str,
        aggregate_type: str,
    ) -> None:
        for name, value in {
            "stream_name": stream_name,
            "group_name": group_name,
            "consumer_name": consumer_name,
            "aggregate_type": aggregate_type,
        }.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonblank")
        self._redis = redis
        self._stream_name = stream_name
        self._group_name = group_name
        self._consumer_name = consumer_name
        self._aggregate_type = aggregate_type
        self._group_ready = False

    async def wait(self, *, max_wait_seconds: float) -> DispatchWakeup | None:
        if (
            not isinstance(max_wait_seconds, (int, float))
            or isinstance(max_wait_seconds, bool)
            or not math.isfinite(max_wait_seconds)
            or not 0 < max_wait_seconds <= 60
        ):
            raise ValueError("max_wait_seconds must be between 0 and 60")
        started = monotonic()
        try:
            await self._ensure_group()
            while True:
                remaining = max_wait_seconds - (monotonic() - started)
                if remaining <= 0:
                    return None
                response = await self._redis.xreadgroup(
                    self._group_name,
                    self._consumer_name,
                    {self._stream_name: ">"},
                    count=1,
                    block=max(1, int(remaining * 1000)),
                )
                item = _one_stream_item(response)
                if item is None:
                    return None
                stream_id, fields = item
                wakeup = _decode_wakeup(
                    stream_id,
                    fields,
                    aggregate_type=self._aggregate_type,
                )
                if wakeup is not None:
                    return wakeup
                await self._redis.xack(self._stream_name, self._group_name, stream_id)
        except asyncio.CancelledError:
            raise
        except ResponseError as error:
            if "NOGROUP" in str(error):
                self._group_ready = False
            await asyncio.sleep(max(0.0, max_wait_seconds - (monotonic() - started)))
            return None
        except RedisError:
            await asyncio.sleep(max(0.0, max_wait_seconds - (monotonic() - started)))
            return None

    async def ack(self, wakeup: DispatchWakeup) -> None:
        try:
            await self._redis.xack(self._stream_name, self._group_name, wakeup.stream_id)
        except asyncio.CancelledError:
            raise
        except RedisError:
            # MySQL already owns the work. A missed ACK can only create a duplicate hint.
            return

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self._redis.xgroup_create(
                self._stream_name,
                self._group_name,
                "0",
                mkstream=True,
            )
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise
        self._group_ready = True


def _one_stream_item(response: object) -> tuple[str, dict[str, object]] | None:
    if not isinstance(response, list) or not response:
        return None
    stream = response[0]
    if not isinstance(stream, (list, tuple)) or len(stream) != 2:
        return None
    messages = stream[1]
    if not isinstance(messages, list) or not messages:
        return None
    message = messages[0]
    if not isinstance(message, (list, tuple)) or len(message) != 2:
        return None
    stream_id, fields = message
    if not isinstance(stream_id, str) or not isinstance(fields, dict):
        return None
    return stream_id, cast(dict[str, object], fields)


def _decode_wakeup(
    stream_id: str,
    fields: dict[str, object],
    *,
    aggregate_type: str,
) -> DispatchWakeup | None:
    payload = fields.get("payload")
    if not isinstance(payload, str):
        return None
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or value.get("aggregateType") != aggregate_type:
        return None
    aggregate_id = value.get("aggregateId")
    if not isinstance(aggregate_id, str) or not aggregate_id:
        return None
    return DispatchWakeup(stream_id=stream_id, aggregate_id=aggregate_id)
