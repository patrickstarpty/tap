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


def test_mysql_checkpoint_serializer_allows_domain_draft_but_blocks_unknown_constructor() -> None:
    checkpointer = MysqlGraphCheckpointer(None, scope=VALIDATION_SCOPE)  # type: ignore[arg-type]

    blocked = checkpointer.serde.loads_typed(
        checkpointer.serde.dumps_typed(
            GraphRunBudget(max_model_calls=1, max_seconds=1, max_cost_micros=1)
        )
    )
    assert blocked == {"max_model_calls": 1, "max_seconds": 1, "max_cost_micros": 1}
    assert not isinstance(blocked, GraphRunBudget)
