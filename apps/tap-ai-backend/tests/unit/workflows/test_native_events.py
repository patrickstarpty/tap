"""Workflow wake-ups are scoped hints after durable MySQL commits."""

from __future__ import annotations

import json

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.adapters.events import RedisWorkflowEvents


class FakeRedis:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    async def eval(self, script, number_of_keys, *values):
        del script, number_of_keys
        self.payloads.append(str(values[-1]))
        return 1


@pytest.mark.asyncio
async def test_workflow_wakeup_carries_only_scoped_task_identity_and_sequence() -> None:
    redis = FakeRedis()
    events = RedisWorkflowEvents(redis, redis, VALIDATION_SCOPE, "test-worker")
    await events.notify(VALIDATION_SCOPE, "task-1", 7)
    payload = json.loads(redis.payloads[0])
    assert payload["aggregateType"] == "workflow_task"
    assert payload["aggregateId"] == "task-1"
    assert payload["sequence"] == 7
    assert "answer" not in redis.payloads[0]
    other = ProjectScopeContext(
        enterprise_id=VALIDATION_SCOPE.enterprise_id,
        project_id="other-project",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode=VALIDATION_SCOPE.identity_mode,
    )
    with pytest.raises(ValueError, match="scope mismatch"):
        await events.notify(other, "task-1", 8)


@pytest.mark.asyncio
async def test_redis_notification_failure_does_not_raise_after_db_commit() -> None:
    class BrokenRedis:
        async def eval(self, *_args):
            raise ConnectionError("Redis unavailable")

    redis = BrokenRedis()
    events = RedisWorkflowEvents(redis, redis, VALIDATION_SCOPE, "test-worker")
    await events.notify(VALIDATION_SCOPE, "task-committed", 1)
