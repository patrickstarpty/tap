"""Versioned durable AI graph-run state and fencing rules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import uuid4


class ExecutionMode(StrEnum):
    INLINE = "inline"
    DURABLE = "durable"


class ReasoningMode(StrEnum):
    DIRECT = "direct"
    WORKFLOW = "workflow"
    AGENTIC = "agentic"


class GraphRunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class GraphLeaseLost(RuntimeError):
    pass


class GraphCheckpointUnavailable(RuntimeError):
    """Durable checkpoint storage failed before its transaction committed."""


@dataclass(frozen=True, slots=True)
class GraphRunBudget:
    max_model_calls: int
    max_seconds: int
    max_cost_micros: int

    def __post_init__(self) -> None:
        if (
            type(self.max_model_calls) is not int
            or type(self.max_seconds) is not int
            or type(self.max_cost_micros) is not int
            or self.max_model_calls < 0
            or self.max_seconds < 1
            or self.max_cost_micros < 0
        ):
            raise ValueError("graph run budget must be bounded and nonnegative")


@dataclass(frozen=True, slots=True)
class GraphRun:
    run_id: str
    project_id: str
    graph_version: str
    state_schema_version: int
    execution_mode: ExecutionMode
    reasoning_mode: ReasoningMode
    status: GraphRunStatus
    budget: GraphRunBudget
    created_at: datetime
    updated_at: datetime
    current_checkpoint_id: str | None = None
    waiting_reason: str | None = None
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_until: datetime | None = None
    attempt_count: int = 0

    def __post_init__(self) -> None:
        for name, value in (
            ("run_id", self.run_id),
            ("project_id", self.project_id),
            ("graph_version", self.graph_version),
        ):
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{name} must be a nonblank stable value")
        if type(self.state_schema_version) is not int or self.state_schema_version < 1:
            raise ValueError("state schema version must be positive")
        if self.attempt_count < 0:
            raise ValueError("graph run attempt count cannot be negative")
        lease_values = (self.lease_owner, self.lease_token, self.lease_until)
        if any(value is None for value in lease_values) != all(
            value is None for value in lease_values
        ):
            raise ValueError("graph run lease fields must change together")

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        project_id: str,
        graph_version: str,
        state_schema_version: int,
        execution_mode: ExecutionMode,
        reasoning_mode: ReasoningMode,
        budget: GraphRunBudget,
        now: datetime,
    ) -> GraphRun:
        return cls(
            run_id=run_id,
            project_id=project_id,
            graph_version=graph_version,
            state_schema_version=state_schema_version,
            execution_mode=execution_mode,
            reasoning_mode=reasoning_mode,
            status=GraphRunStatus.QUEUED,
            budget=budget,
            created_at=now,
            updated_at=now,
        )

    def claim(self, worker_id: str, *, now: datetime, lease_duration: timedelta) -> GraphRun:
        if not worker_id.strip() or not timedelta(0) < lease_duration <= timedelta(minutes=15):
            raise ValueError("graph run lease request is invalid")
        if self.status is GraphRunStatus.RUNNING and self.lease_until and self.lease_until >= now:
            raise GraphLeaseLost("graph run still has an active fencing token")
        if self.status in {
            GraphRunStatus.SUCCEEDED,
            GraphRunStatus.FAILED,
            GraphRunStatus.CANCELLED,
        }:
            raise GraphLeaseLost("terminal graph run cannot be claimed")
        return replace(
            self,
            status=GraphRunStatus.RUNNING,
            waiting_reason=None,
            lease_owner=worker_id,
            lease_token=uuid4().hex,
            lease_until=now + lease_duration,
            attempt_count=self.attempt_count + 1,
            updated_at=now,
        )

    def checkpoint(self, lease_token: str, *, checkpoint_id: str, now: datetime) -> GraphRun:
        self._require_lease(lease_token, now)
        if not checkpoint_id.strip():
            raise ValueError("checkpoint identity must be nonblank")
        return replace(self, current_checkpoint_id=checkpoint_id, updated_at=now)

    def renew(
        self,
        lease_token: str,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> GraphRun:
        self._require_lease(lease_token, now)
        if not timedelta(0) < lease_duration <= timedelta(minutes=15):
            raise ValueError("graph run lease renewal is invalid")
        return replace(self, lease_until=now + lease_duration, updated_at=now)

    def wait(self, lease_token: str, *, reason: str, now: datetime) -> GraphRun:
        self._require_lease(lease_token, now)
        if not reason.strip():
            raise ValueError("waiting reason must be nonblank")
        return replace(
            self,
            status=GraphRunStatus.WAITING,
            waiting_reason=reason,
            lease_owner=None,
            lease_token=None,
            lease_until=None,
            updated_at=now,
        )

    def cancel(self, *, now: datetime) -> GraphRun:
        if self.status is GraphRunStatus.CANCELLED:
            return self
        if self.status in {GraphRunStatus.SUCCEEDED, GraphRunStatus.FAILED}:
            raise GraphLeaseLost("terminal graph run cannot be cancelled")
        return self._terminal(GraphRunStatus.CANCELLED, now=now)

    def succeed(self, lease_token: str, *, now: datetime) -> GraphRun:
        self._require_lease(lease_token, now)
        return self._terminal(GraphRunStatus.SUCCEEDED, now=now)

    def fail(self, lease_token: str, *, now: datetime) -> GraphRun:
        self._require_lease(lease_token, now)
        return self._terminal(GraphRunStatus.FAILED, now=now)

    def _terminal(self, status: GraphRunStatus, *, now: datetime) -> GraphRun:
        return replace(
            self,
            status=status,
            waiting_reason=None,
            lease_owner=None,
            lease_token=None,
            lease_until=None,
            updated_at=now,
        )

    def _require_lease(self, lease_token: str, now: datetime) -> None:
        if (
            self.status is not GraphRunStatus.RUNNING
            or not lease_token
            or lease_token != self.lease_token
            or self.lease_until is None
            or self.lease_until < now
        ):
            raise GraphLeaseLost("graph run fencing token is stale")
