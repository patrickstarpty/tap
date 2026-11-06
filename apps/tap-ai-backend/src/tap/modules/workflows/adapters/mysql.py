"""Atomic MySQL workflow snapshots, events and task budget accounting."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext, require_identifier
from tap.modules.workflows.adapters.schema import (
    workflow_dependency,
    workflow_event,
    workflow_node,
    workflow_plan_revision,
    workflow_task,
)
from tap.modules.workflows.domain.dependencies import validate_dependency_dag
from tap.modules.workflows.domain.models import TaskSnapshot, WorkflowEvent, WorkflowStatus
from tap.modules.workflows.domain.transitions import transition
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values


def _canonical(value: dict[str, object]) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _digest(value: dict[str, object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


def _budget(value: object, *, field: str) -> dict[str, int]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or type(amount) is not int or amount < 0
        for key, amount in value.items()
    ):
        raise ValueError(f"{field} must contain non-negative integer counters")
    return value


def _event(row: object) -> WorkflowEvent:
    fields = row._mapping  # type: ignore[attr-defined]
    occurred_at = fields["occurred_at"]
    return WorkflowEvent(
        event_id=fields["event_id"],
        task_id=fields["task_id"],
        sequence=fields["sequence"],
        node_id=fields["node_id"],
        attempt=fields["attempt"],
        kind=fields["kind"],
        payload=fields["payload"],
        occurred_at=occurred_at.replace(tzinfo=UTC),
    )


def _task(row: object) -> TaskSnapshot:
    fields = row._mapping  # type: ignore[attr-defined]
    return TaskSnapshot(
        task_id=fields["task_id"],
        project_id=fields["project_id"],
        conversation_id=fields["conversation_id"],
        plan_revision=fields["plan_revision"],
        status=WorkflowStatus(fields["status"]),
        last_sequence=fields["next_sequence"] - 1,
    )


class MysqlWorkflowStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_task(
        self, scope: ProjectScopeContext, request: dict[str, object], idempotency_key: str
    ) -> TaskSnapshot:
        scope = require_project_scope(scope)
        require_identifier("idempotency_key", idempotency_key)
        goal = request.get("goal") if isinstance(request, dict) else None
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal is required")
        conversation_id = request.get("conversation_id")
        if conversation_id is not None:
            if not isinstance(conversation_id, str):
                raise ValueError("conversation_id must be a string")
            require_identifier("conversation_id", conversation_id)
        limit = _budget(request.get("budget_limit", {}), field="budget_limit")
        digest = _digest(request)
        task_id = uuid4().hex
        revision_id = uuid4().hex
        event_id = uuid4().hex
        now = datetime.now(UTC).replace(tzinfo=None)
        try:
            async with self._sessions.begin() as session:
                await session.execute(
                    workflow_task.insert().values(
                        task_id=task_id,
                        idempotency_key=idempotency_key,
                        request_digest=digest,
                        request_json=request,
                        goal=goal,
                        conversation_id=conversation_id,
                        status="queued",
                        plan_revision=1,
                        next_sequence=2,
                        budget_limit=limit,
                        budget_used={},
                        created_at=now,
                        updated_at=now,
                        **scope_values(scope),
                    )
                )
                await session.execute(
                    workflow_plan_revision.insert().values(
                        revision_id=revision_id,
                        task_id=task_id,
                        revision_number=1,
                        goal=goal,
                        dependencies=[],
                        input_snapshot=request.get("input_snapshot", {}),
                        created_at=now,
                        **scope_values(scope),
                    )
                )
                payload: dict[str, object] = {"status": "queued"}
                await session.execute(
                    workflow_event.insert().values(
                        event_id=event_id,
                        task_id=task_id,
                        sequence=1,
                        node_id=None,
                        attempt=0,
                        kind="task.created",
                        payload=payload,
                        payload_digest=_digest(payload),
                        occurred_at=now,
                        **scope_values(scope),
                    )
                )
        except IntegrityError:
            async with self._sessions() as session:
                prior = (
                    await session.execute(
                        select(workflow_task).where(
                            *scope_predicates(workflow_task, scope),
                            workflow_task.c.idempotency_key == idempotency_key,
                        )
                    )
                ).first()
            if prior is None:
                raise
            if prior._mapping["request_digest"] != digest:
                raise ValueError("idempotency key payload conflict") from None
            return _task(prior)
        return TaskSnapshot(task_id, scope.project_id, conversation_id, 1, WorkflowStatus.QUEUED, 1)

    async def snapshot(self, scope: ProjectScopeContext, task_id: str) -> dict[str, object]:
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            # Pin all three reads to one MVCC view even if the engine defaults
            # to READ COMMITTED. This must precede the first SQL statement.
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            row = (
                await session.execute(
                    select(workflow_task).where(
                        *scope_predicates(workflow_task, scope), workflow_task.c.task_id == task_id
                    )
                )
            ).first()
            if row is None:
                raise LookupError("workflow task not found")
            task = _task(row)
            nodes = (
                (
                    await session.execute(
                        select(workflow_node)
                        .where(
                            *scope_predicates(workflow_node, scope),
                            workflow_node.c.task_id == task_id,
                        )
                        .order_by(workflow_node.c.created_at, workflow_node.c.node_id)
                    )
                )
                .mappings()
                .all()
            )
            events = (
                await session.execute(
                    select(workflow_event)
                    .where(
                        *scope_predicates(workflow_event, scope),
                        workflow_event.c.task_id == task_id,
                        workflow_event.c.sequence <= task.last_sequence,
                    )
                    .order_by(workflow_event.c.sequence)
                )
            ).all()
            return {
                "task_id": task.task_id,
                "project_id": task.project_id,
                "conversation_id": task.conversation_id,
                "plan_revision": task.plan_revision,
                "status": task.status.value,
                "last_sequence": task.last_sequence,
                "budget_limit": row._mapping["budget_limit"],
                "budget_used": row._mapping["budget_used"],
                "nodes": tuple(dict(node) for node in nodes),
                "events": tuple(_event(event) for event in events),
            }

    async def append_event(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        lease_token: str | None,
        event_id: str,
        kind: str,
        payload: dict[str, object],
    ) -> WorkflowEvent:
        scope = require_project_scope(scope)
        require_identifier("event_id", event_id)
        if not isinstance(payload, dict) or not isinstance(kind, str) or not kind:
            raise ValueError("event kind and payload are required")
        digest = _digest(payload)
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(workflow_task)
                    .where(
                        *scope_predicates(workflow_task, scope), workflow_task.c.task_id == task_id
                    )
                    .with_for_update()
                )
            ).first()
            if row is None:
                raise LookupError("workflow task not found")
            task = row._mapping
            lease_until = task["lease_until"]
            if (
                not lease_token
                or task["lease_token"] != lease_token
                or not task["lease_owner"]
                or lease_until is None
                or lease_until <= datetime.now(UTC).replace(tzinfo=None)
            ):
                raise ValueError("stale workflow lease token")
            previous = (
                await session.execute(
                    select(workflow_event).where(workflow_event.c.event_id == event_id)
                )
            ).first()
            if previous is not None:
                existing = previous._mapping
                if (
                    existing["task_id"] != task_id
                    or existing["project_id"] != scope.project_id
                    or existing["kind"] != kind
                    or existing["payload_digest"] != digest
                ):
                    raise ValueError("event id payload conflict")
                return _event(previous)
            node_id = payload.get("node_id")
            if node_id is not None:
                if not isinstance(node_id, str):
                    raise ValueError("node_id must be a string")
                node = (
                    await session.execute(
                        select(workflow_node.c.node_id).where(
                            *scope_predicates(workflow_node, scope),
                            workflow_node.c.task_id == task_id,
                            workflow_node.c.node_id == node_id,
                        )
                    )
                ).first()
                if node is None:
                    raise ValueError("event node belongs to another task")
            attempt = payload.get("attempt", 0)
            if type(attempt) is not int or attempt < 0:
                raise ValueError("attempt must be non-negative")
            changes: dict[str, object] = {
                "next_sequence": task["next_sequence"] + 1,
                "updated_at": datetime.now(UTC).replace(tzinfo=None),
            }
            if kind == "task.status":
                signal = payload.get("signal")
                if not isinstance(signal, str):
                    raise ValueError("status signal required")
                if signal == "reconciled" and task["recovering_from"] != "running":
                    raise ValueError("recovery requires a confirmed safe outcome")
                if signal == "lease_lost":
                    changes["recovering_from"] = task["status"]
                elif signal == "reconciled":
                    changes["recovering_from"] = None
                changes["status"] = transition(task["status"], signal)
            if kind == "task.budget":
                delta = _budget(payload, field="budget delta")
                used = dict(task["budget_used"])
                limit = task["budget_limit"]
                for key, amount in delta.items():
                    total = used.get(key, 0) + amount
                    if key in limit and total > limit[key]:
                        raise ValueError("workflow task budget exceeded")
                    used[key] = total
                changes["budget_used"] = used
            now = datetime.now(UTC).replace(tzinfo=None)
            await session.execute(
                update(workflow_task).where(workflow_task.c.task_id == task_id).values(**changes)
            )
            await session.execute(
                workflow_event.insert().values(
                    event_id=event_id,
                    task_id=task_id,
                    sequence=task["next_sequence"],
                    node_id=node_id,
                    attempt=attempt,
                    kind=kind,
                    payload=payload,
                    payload_digest=digest,
                    occurred_at=now,
                    **scope_values(scope),
                )
            )
            return WorkflowEvent(
                event_id,
                task_id,
                task["next_sequence"],
                node_id,
                attempt,
                kind,
                payload,
                now.replace(tzinfo=UTC),
            )

    async def events_after(
        self, scope: ProjectScopeContext, task_id: str, sequence: int, limit: int
    ) -> tuple[WorkflowEvent, ...]:
        scope = require_project_scope(scope)
        if sequence < 0 or limit < 1 or limit > 1000:
            raise ValueError("invalid event cursor or page size")
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(workflow_event)
                    .where(
                        *scope_predicates(workflow_event, scope),
                        workflow_event.c.task_id == task_id,
                        workflow_event.c.sequence > sequence,
                    )
                    .order_by(workflow_event.c.sequence)
                    .limit(limit)
                )
            ).all()
            return tuple(_event(row) for row in rows)

    async def add_dependency(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        lease_token: str,
        from_node_id: str,
        to_node_id: str,
        plan_revision: int,
    ) -> None:
        """Append a same-task DAG edge and its event under the task row lock."""
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(workflow_task)
                    .where(
                        *scope_predicates(workflow_task, scope),
                        workflow_task.c.task_id == task_id,
                    )
                    .with_for_update()
                )
            ).first()
            if row is None:
                raise LookupError("workflow task not found")
            task = row._mapping
            if (
                not lease_token
                or task["lease_token"] != lease_token
                or not task["lease_owner"]
                or task["lease_until"] is None
                or task["lease_until"] <= datetime.now(UTC).replace(tzinfo=None)
            ):
                raise ValueError("stale workflow lease token")
            if plan_revision != task["plan_revision"]:
                raise ValueError("stale workflow plan revision")
            nodes = (
                (
                    await session.execute(
                        select(workflow_node.c.node_id).where(
                            *scope_predicates(workflow_node, scope),
                            workflow_node.c.task_id == task_id,
                            workflow_node.c.node_id.in_((from_node_id, to_node_id)),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if set(nodes) != {from_node_id, to_node_id}:
                raise ValueError("dependency nodes must belong to the same task")
            edge_rows = (
                await session.execute(
                    select(
                        workflow_dependency.c.from_node_id,
                        workflow_dependency.c.to_node_id,
                    ).where(
                        *scope_predicates(workflow_dependency, scope),
                        workflow_dependency.c.task_id == task_id,
                        workflow_dependency.c.plan_revision == plan_revision,
                    )
                )
            ).all()
            edges = [(row[0], row[1]) for row in edge_rows]
            if (from_node_id, to_node_id) in edges:
                return
            validate_dependency_dag(edges, from_node_id, to_node_id)
            now = datetime.now(UTC).replace(tzinfo=None)
            event_payload: dict[str, object] = {
                "from_node_id": from_node_id,
                "to_node_id": to_node_id,
                "plan_revision": plan_revision,
            }
            await session.execute(
                workflow_dependency.insert().values(
                    dependency_id=uuid4().hex,
                    task_id=task_id,
                    from_node_id=from_node_id,
                    to_node_id=to_node_id,
                    plan_revision=plan_revision,
                    **scope_values(scope),
                )
            )
            await session.execute(
                workflow_event.insert().values(
                    event_id=uuid4().hex,
                    task_id=task_id,
                    sequence=task["next_sequence"],
                    node_id=None,
                    attempt=0,
                    kind="plan.dependency_added",
                    payload=event_payload,
                    payload_digest=_digest(event_payload),
                    occurred_at=now,
                    **scope_values(scope),
                )
            )
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task_id)
                .values(next_sequence=task["next_sequence"] + 1, updated_at=now)
            )
