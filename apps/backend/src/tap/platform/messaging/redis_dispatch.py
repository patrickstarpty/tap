"""Bounded Outbox relay and atomic Redis dispatch adapter."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Protocol

from tap.modules.chat.application.ports import (
    Clock,
    DispatchMessage,
    MessagePublisher,
    OutboxRepository,
)

_PUBLISH_ONCE_SCRIPT = """
if redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[1]) then
  redis.call('XADD', KEYS[2], '*', 'payload', ARGV[2])
  return 1
end
return 0
""".strip()


class AsyncRedis(Protocol):
    async def eval(self, script: str, number_of_keys: int, *values: object) -> int: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)


class RedisDispatchPublisher:
    def __init__(
        self,
        *,
        redis: AsyncRedis,
        stream_name: str,
        dedup_ttl: timedelta,
    ) -> None:
        if dedup_ttl.total_seconds() < 1:
            raise ValueError("dedup_ttl must be at least one second")
        self._redis = redis
        self._stream_name = stream_name
        self._dedup_ttl_seconds = int(dedup_ttl.total_seconds())

    async def publish_once(self, message: DispatchMessage) -> bool:
        payload = json.dumps(
            {
                "aggregateId": message.aggregate_id,
                "aggregateType": message.aggregate_type,
                "commandId": str(message.command_id),
                "outboxId": message.outbox_id,
                "sequence": message.sequence,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        result = await self._redis.eval(
            _PUBLISH_ONCE_SCRIPT,
            2,
            f"tap:dispatch-dedup:{message.command_id}",
            self._stream_name,
            self._dedup_ttl_seconds,
            payload,
        )
        return result == 1


class Relay:
    def __init__(
        self,
        *,
        outbox: OutboxRepository,
        publisher: MessagePublisher,
        clock: Clock,
        worker_id: str,
        lease_duration: timedelta,
        retry_delay: timedelta,
    ) -> None:
        if lease_duration.total_seconds() <= 0:
            raise ValueError("lease_duration must be positive")
        if retry_delay.total_seconds() < 0:
            raise ValueError("retry_delay cannot be negative")
        self._outbox = outbox
        self._publisher = publisher
        self._clock = clock
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._retry_delay = retry_delay

    async def publish_pending(self, batch_size: int) -> int:
        if batch_size <= 0:
            return 0
        now = self._clock.now()
        await self._outbox.reconcile_expired(now, batch_size)
        claimed = await self._outbox.claim_pending(
            worker_id=self._worker_id,
            now=now,
            lease_duration=self._lease_duration,
            limit=batch_size,
        )
        published = 0
        for item in claimed:
            try:
                await self._publisher.publish_once(item.message)
            except Exception as error:
                await self._outbox.mark_failed(
                    outbox_id=item.message.outbox_id,
                    worker_id=self._worker_id,
                    next_attempt_at=self._clock.now() + self._retry_delay,
                    error=str(error),
                )
                continue
            await self._outbox.mark_published(
                outbox_id=item.message.outbox_id,
                worker_id=self._worker_id,
                published_at=self._clock.now(),
            )
            published += 1
        return published
