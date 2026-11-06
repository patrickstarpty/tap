"""Redis workflow wake-up hints; MySQL events and polling remain authoritative."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from time import monotonic

from redis.exceptions import RedisError

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.application.ports import DispatchMessage
from tap.modules.chat.domain.models import CommandId
from tap.platform.db.project_scope import require_project_scope
from tap.platform.messaging.redis_dispatch import (
    AsyncRedis,
    AsyncRedisStream,
    DispatchWakeup,
    RedisDispatchPublisher,
)
from tap.platform.messaging.redis_wakeup import RedisWakeupConsumer

logger = logging.getLogger(__name__)


class RedisWorkflowEvents:
    """Publish identities after commit; consumer always rescans scoped MySQL work."""

    def __init__(
        self,
        publish_redis: AsyncRedis,
        consume_redis: AsyncRedisStream,
        scope: ProjectScopeContext,
        worker_id: str,
    ) -> None:
        self._scope = require_project_scope(scope)
        self._hint_redis = consume_redis
        self._stream = f"tap:workflow:{scope.enterprise_id}:{scope.project_id}:wakeups"
        stream = self._stream
        self._publisher = RedisDispatchPublisher(
            redis=publish_redis,
            scope=scope,
            stream_name=stream,
            dedup_ttl=timedelta(days=1),
        )
        self._consumer = RedisWakeupConsumer(
            redis=consume_redis,
            scope=scope,
            stream_name=stream,
            group_name="tap-workflow-workers",
            consumer_name=worker_id,
            aggregate_type="workflow_task",
        )

    async def notify(self, scope: ProjectScopeContext, task_id: str, sequence: int) -> None:
        scope = require_project_scope(scope)
        if (scope.enterprise_id, scope.project_id) != (
            self._scope.enterprise_id,
            self._scope.project_id,
        ):
            raise ValueError("workflow wake-up scope mismatch")
        if sequence < 1:
            raise ValueError("workflow wake-up sequence must be positive")
        message = DispatchMessage(
            enterprise_id=scope.enterprise_id,
            project_id=scope.project_id,
            command_id=CommandId(f"workflow:{task_id}:{sequence}"),
            outbox_id=f"workflow:{task_id}:{sequence}",
            aggregate_type="workflow_task",
            aggregate_id=task_id,
            sequence=sequence,
        )
        try:
            await self._publisher.publish_once(message)
        except Exception:
            # A notification has no authority: a failed Redis write cannot
            # roll back the committed task or stop bounded MySQL repair polling.
            logger.warning("workflow Redis wake-up unavailable", exc_info=True)

    async def wait(self, *, max_wait_seconds: float) -> DispatchWakeup | None:
        return await self._consumer.wait(max_wait_seconds=max_wait_seconds)

    async def ack(self, wakeup: DispatchWakeup) -> None:
        await self._consumer.ack(wakeup)

    async def read_hint(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        cursor: str,
        *,
        max_wait_seconds: float,
    ) -> str:
        """Non-group XREAD; each SSE subscriber owns its own Redis ID cursor."""
        scope = require_project_scope(scope)
        if (scope.enterprise_id, scope.project_id) != (
            self._scope.enterprise_id,
            self._scope.project_id,
        ):
            raise ValueError("workflow hint scope mismatch")
        if not 0 < max_wait_seconds <= 10:
            raise ValueError("invalid workflow hint wait")
        deadline = monotonic() + max_wait_seconds
        try:
            while monotonic() < deadline:
                remaining = max(1, min(1000, int((deadline - monotonic()) * 1000)))
                response = await self._hint_redis.xread(
                    {self._stream: cursor}, count=100, block=remaining
                )
                if not response:
                    return cursor
                if not isinstance(response, (list, tuple)):
                    return cursor
                for stream in response:
                    if not isinstance(stream, (list, tuple)) or len(stream) != 2:
                        continue
                    entries = stream[1]
                    if not isinstance(entries, (list, tuple)):
                        continue
                    for entry in entries:
                        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                            continue
                        stream_id, fields = entry
                        if not isinstance(stream_id, str) or not isinstance(fields, dict):
                            continue
                        cursor = stream_id
                        raw = fields.get("payload")
                        try:
                            fact = json.loads(raw) if isinstance(raw, str) else {}
                        except ValueError:
                            continue
                        if (
                            fact.get("enterpriseId") == scope.enterprise_id
                            and fact.get("projectId") == scope.project_id
                            and fact.get("aggregateType") == "workflow_task"
                            and fact.get("aggregateId") == task_id
                        ):
                            return cursor
            return cursor
        except (RedisError, OSError, asyncio.TimeoutError):
            await asyncio.sleep(max(0.0, deadline - monotonic()))
            return cursor
