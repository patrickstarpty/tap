from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.mysql_checkpointer import MysqlGraphCheckpointer
from tap.modules.ai.domain.graph_runs import (
    ExecutionMode,
    GraphLeaseLost,
    GraphRun,
    GraphRunBudget,
    GraphRunStatus,
    ReasoningMode,
)

NOW = datetime(2026, 9, 24, 10, 0)


def _run() -> GraphRun:
    return GraphRun.create(
        run_id="run-001",
        project_id="tapper-demo",
        graph_version="test-design-generation-v1",
        state_schema_version=1,
        execution_mode=ExecutionMode.DURABLE,
        reasoning_mode=ReasoningMode.WORKFLOW,
        budget=GraphRunBudget(max_model_calls=2, max_seconds=120, max_cost_micros=50_000),
        now=NOW,
    )


def test_reclaimed_run_rejects_the_previous_fencing_token() -> None:
    first = _run().claim("worker-a", now=NOW, lease_duration=timedelta(seconds=30))
    expired = replace(first, lease_until=NOW - timedelta(seconds=1))
    second = expired.claim(
        "worker-b", now=NOW + timedelta(seconds=31), lease_duration=timedelta(seconds=30)
    )

    with pytest.raises(GraphLeaseLost, match="fencing token"):
        second.checkpoint(first.lease_token or "", checkpoint_id="checkpoint-old", now=NOW)

    saved = second.checkpoint(second.lease_token or "", checkpoint_id="checkpoint-new", now=NOW)
    assert saved.current_checkpoint_id == "checkpoint-new"
    assert saved.attempt_count == 2


def test_waiting_run_releases_worker_without_becoming_terminal() -> None:
    claimed = _run().claim("worker-a", now=NOW, lease_duration=timedelta(seconds=30))

    waiting = claimed.wait(
        claimed.lease_token or "", reason="human-review", now=NOW + timedelta(seconds=2)
    )

    assert waiting.status is GraphRunStatus.WAITING
    assert waiting.waiting_reason == "human-review"
    assert waiting.lease_owner is None
    assert waiting.lease_token is None
    assert waiting.lease_until is None


def test_running_lease_renews_without_changing_fencing_token_or_attempt() -> None:
    claimed = _run().claim("worker-a", now=NOW, lease_duration=timedelta(seconds=30))

    renewed = claimed.renew(
        claimed.lease_token or "",
        now=NOW + timedelta(seconds=10),
        lease_duration=timedelta(seconds=45),
    )

    assert renewed.lease_token == claimed.lease_token
    assert renewed.lease_until == NOW + timedelta(seconds=55)
    assert renewed.attempt_count == 1


def test_cancelled_run_releases_lease_and_fences_inflight_worker() -> None:
    claimed = _run().claim("worker-a", now=NOW, lease_duration=timedelta(seconds=30))

    cancelled = claimed.cancel(now=NOW + timedelta(seconds=5))

    assert cancelled.status is GraphRunStatus.CANCELLED
    assert cancelled.lease_owner is None
    assert cancelled.lease_token is None
    assert cancelled.lease_until is None
    with pytest.raises(GraphLeaseLost, match="fencing token"):
        cancelled.checkpoint(
            claimed.lease_token or "",
            checkpoint_id="checkpoint-after-cancel",
            now=NOW + timedelta(seconds=6),
        )


@pytest.mark.parametrize(
    ("transition", "expected_status"),
    [("succeed", GraphRunStatus.SUCCEEDED), ("fail", GraphRunStatus.FAILED)],
)
def test_terminal_transition_requires_current_fencing_token(
    transition: str, expected_status: GraphRunStatus
) -> None:
    claimed = _run().claim("worker-a", now=NOW, lease_duration=timedelta(seconds=30))

    with pytest.raises(GraphLeaseLost, match="fencing token"):
        getattr(claimed, transition)("stale-token", now=NOW + timedelta(seconds=1))

    terminal = getattr(claimed, transition)(
        claimed.lease_token or "", now=NOW + timedelta(seconds=1)
    )
    assert terminal.status is expected_status
    assert terminal.lease_owner is None
    assert terminal.lease_token is None
    assert terminal.lease_until is None


def test_mysql_checkpoint_serializer_allows_domain_draft_but_blocks_unknown_constructor() -> None:
    checkpointer = MysqlGraphCheckpointer(None, scope=VALIDATION_SCOPE)  # type: ignore[arg-type]

    blocked = checkpointer.serde.loads_typed(
        checkpointer.serde.dumps_typed(
            GraphRunBudget(max_model_calls=1, max_seconds=1, max_cost_micros=1)
        )
    )
    assert blocked == {"max_model_calls": 1, "max_seconds": 1, "max_cost_micros": 1}
    assert not isinstance(blocked, GraphRunBudget)
