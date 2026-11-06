"""Public workflow stream contract: cursor continuity and private event safety."""

from datetime import UTC, datetime

import pytest

from tap.modules.workflows.application.queries import project_event, replay_from
from tap.modules.workflows.domain.models import WorkflowEvent


def test_snapshot_cursor_and_client_cursor_do_not_skip_events() -> None:
    assert replay_from(snapshot_sequence=10, client_sequence=7) == 7
    assert replay_from(snapshot_sequence=10, client_sequence=None) == 10
    with pytest.raises(ValueError, match="future"):
        replay_from(snapshot_sequence=10, client_sequence=11)


def test_private_action_keeps_sequence_without_exposing_journal() -> None:
    event = WorkflowEvent(
        "evt-1",
        "task-1",
        7,
        "node-1",
        2,
        "action.settled",
        {"execution_token": "secret", "result": {"output": "private prompt answer"}},
        datetime(2026, 11, 6, tzinfo=UTC),
    )
    public = project_event(event)
    assert public["eventId"] == "evt-1"
    assert public["sequence"] == 7
    assert public["nodeId"] == "node-1"
    assert public["attempt"] == 2
    assert public["payload"] == {}
    assert "secret" not in str(public)
    assert "private prompt answer" not in str(public)


@pytest.mark.asyncio
async def test_two_sse_subscribers_read_same_hint_without_worker_group() -> None:
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.workflows.adapters.events import RedisWorkflowEvents

    class Redis:
        def __init__(self):
            self.calls = []

        async def xread(self, streams, *, count, block):
            self.calls.append((streams, count, block))
            return [
                [
                    next(iter(streams)),
                    [
                        [
                            "1-0",
                            {
                                "payload": (
                                    '{"enterpriseId":"local","projectId":"tapper-demo",'
                                    '"aggregateType":"workflow_task","aggregateId":"task-1","sequence":8}'
                                )
                            },
                        ]
                    ],
                ]
            ]

    redis = Redis()
    hints = RedisWorkflowEvents(redis, redis, VALIDATION_SCOPE, "worker-test")
    first = await hints.read_hint(VALIDATION_SCOPE, "task-1", "$", max_wait_seconds=1)
    second = await hints.read_hint(VALIDATION_SCOPE, "task-1", "$", max_wait_seconds=1)
    assert (first, second) == ("1-0", "1-0")
    assert len(redis.calls) == 2


def test_node_failure_is_a_safe_lifecycle_signal() -> None:
    event = WorkflowEvent(
        "failure-1",
        "task-1",
        8,
        "node-1",
        2,
        "node.failed",
        {"node_id": "node-1", "error": "provider secret", "reason": "secret"},
        datetime(2026, 11, 6, tzinfo=UTC),
    )
    value = project_event(event)
    assert value["kind"] == "node.failed"
    assert value["status"] == "failed"
    assert "secret" not in str(value)
