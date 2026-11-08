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


@pytest.mark.asyncio
async def test_new_child_after_snapshot_waits_for_matching_metadata_view() -> None:
    from tap.modules.workflows.application.queries import WorkflowQueries

    now = datetime(2026, 11, 6, tzinfo=UTC)
    delegated = WorkflowEvent(
        "child-event",
        "task-1",
        2,
        "child",
        1,
        "node.delegated",
        {"node_id": "child", "parent_node_id": "root", "agent_id": "qae"},
        now,
    )

    class Store:
        def __init__(self):
            self.reads = 0

        async def snapshot(self, _scope, _task_id):
            self.reads += 1
            return {
                "project_id": "tapper-demo",
                "last_sequence": 1 if self.reads == 1 else 2,
                "request_json": {"origin": "native"},
                "nodes": ()
                if self.reads == 1
                else (
                    {
                        "node_id": "child",
                        "parent_node_id": "root",
                        "runtime": "observed",
                    },
                ),
            }

        async def events_after(self, _scope, _task_id, sequence, _limit):
            return (delegated,) if sequence < 2 else ()

    queries = WorkflowQueries(Store())
    assert await queries.events(None, "task-1", 1) == ()
    replay = await queries.events(None, "task-1", 1)
    assert len(replay) == 1
    assert (replay[0]["sequence"], replay[0]["parentNodeId"], replay[0]["sourceRuntime"]) == (
        2,
        "root",
        "observed",
    )
    assert await queries.events(None, "task-1", 2) == ()


def test_browser_action_details_are_redacted_but_keep_failure_location():
    event = WorkflowEvent(
        "action-1",
        "task-1",
        9,
        "node-1",
        1,
        "browser.action_finished",
        {
            "action_id": "inspect-1",
            "operation": "click",
            "duration_ms": 21,
            "outcome": "failed",
            "url": "https://example.com/quote",
            "selector": "text=Age",
            "error": "strict mode violation: 64 elements; secret",
            "step_id": "choose-cover",
            "step_keyword": "When",
            "step_title": "Choose cover",
            "input": "secret",
            "execution_token": "private",
        },
        datetime(2026, 11, 6, tzinfo=UTC),
    )
    public = project_event(event, redact=lambda s: s.replace("secret", "[redacted]"))
    assert public["kind"] == "browser.action_finished"
    assert public["payload"]["operation"] == "click"
    assert public["payload"]["duration_ms"] == 21
    assert public["payload"]["selector"] == "text=Age"
    assert "64 elements" in public["payload"]["error"]
    assert public["payload"]["step_title"] == "Choose cover"
    assert "secret" not in str(public) and "private" not in str(public)
    assert "error" not in project_event(event)["payload"]


def test_task_failure_exposes_a_redacted_reason_for_diagnosis():
    event = WorkflowEvent(
        "failed",
        "task-1",
        10,
        None,
        None,
        "task.failed",
        {"reason": "browser timeout secret"},
        datetime(2026, 11, 6, tzinfo=UTC),
    )
    public = project_event(event, redact=lambda s: s.replace("secret", "[redacted]"))
    assert public["payload"]["reason"] == "browser timeout [redacted]"
