"""Atomic MySQL workflow snapshots, events and task budget accounting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import Result, Row, RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext, require_identifier
from tap.modules.ai.adapters.mysql_checkpointer import graph_checkpoint, graph_run
from tap.modules.workflows.adapters.schema import (
    workflow_artifact,
    workflow_attempt,
    workflow_command,
    workflow_dependency,
    workflow_event,
    workflow_node,
    workflow_plan_revision,
    workflow_task,
)
from tap.modules.workflows.domain.commands import (
    ControlCommand,
    WorkflowDispatchBlocked,
    WorkflowLimitExceeded,
    command_disposition,
)
from tap.modules.workflows.domain.dependencies import validate_dependency_dag
from tap.modules.workflows.domain.models import TaskSnapshot, WorkflowEvent, WorkflowStatus
from tap.modules.workflows.domain.transitions import transition
from tap.modules.workflows.ports.runtime import RecoveryEpisode, RuntimeInspection
from tap.platform.db.lock_contention import claim_or_nothing, claim_transaction
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values


def _native_request(request: Mapping[str, object]) -> bool:
    return request.get("origin", "native") == "native"


def _native_sql():
    origin = workflow_task.c.request_json["origin"].as_string()
    return or_(origin.is_(None), origin == "native")


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _digest(value: Mapping[str, object]) -> str:
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
        self._scan_cursors: dict[tuple[str, str], str] = {}

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
                        admission_ready="origin_turn_id" not in request,
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

    async def task_by_key(
        self, scope: ProjectScopeContext, idempotency_key: str
    ) -> TaskSnapshot | None:
        """Locate only the task bound to this scoped durable admission identity."""
        scope = require_project_scope(scope)
        require_identifier("idempotency_key", idempotency_key)
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(workflow_task).where(
                        *scope_predicates(workflow_task, scope),
                        workflow_task.c.idempotency_key == idempotency_key,
                    )
                )
            ).first()
        return None if row is None else _task(row)

    async def snapshot(self, scope: ProjectScopeContext, task_id: str) -> dict[str, Any]:
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
            attempts = (
                (
                    await session.execute(
                        select(workflow_attempt)
                        .where(
                            *scope_predicates(workflow_attempt, scope),
                            workflow_attempt.c.task_id == task_id,
                        )
                        .order_by(workflow_attempt.c.node_id, workflow_attempt.c.attempt)
                    )
                )
                .mappings()
                .all()
            )
            edges = (
                (
                    await session.execute(
                        select(workflow_dependency)
                        .where(
                            *scope_predicates(workflow_dependency, scope),
                            workflow_dependency.c.task_id == task_id,
                            workflow_dependency.c.plan_revision == task.plan_revision,
                        )
                        .order_by(
                            workflow_dependency.c.from_node_id, workflow_dependency.c.to_node_id
                        )
                    )
                )
                .mappings()
                .all()
            )
            commands = (
                (
                    await session.execute(
                        select(workflow_command)
                        .where(
                            *scope_predicates(workflow_command, scope),
                            workflow_command.c.task_id == task_id,
                        )
                        .order_by(workflow_command.c.created_at, workflow_command.c.command_id)
                    )
                )
                .mappings()
                .all()
            )
            artifacts = (
                (
                    await session.execute(
                        select(workflow_artifact)
                        .where(
                            *scope_predicates(workflow_artifact, scope),
                            workflow_artifact.c.task_id == task_id,
                        )
                        .order_by(workflow_artifact.c.created_at, workflow_artifact.c.artifact_id)
                    )
                )
                .mappings()
                .all()
            )
            return {
                "request_json": row._mapping["request_json"],
                "created_at": row._mapping["created_at"],
                "actor_id": row._mapping["actor_id"],
                "identity_mode": row._mapping["identity_mode"],
                "attempts": tuple(dict(item) for item in attempts),
                "edges": tuple(dict(item) for item in edges),
                "commands": tuple(dict(item) for item in commands),
                "artifacts": tuple(dict(item) for item in artifacts),
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

    async def list_tasks(
        self,
        scope: ProjectScopeContext,
        limit: int,
        cursor: str | None,
        agent_id: str | None = None,
    ) -> tuple[tuple[dict[str, Any], ...], str | None]:
        scope = require_project_scope(scope)
        if not 1 <= limit <= 50:
            raise ValueError("invalid workflow page size")
        async with self._sessions() as session:
            previous = None
            if cursor is not None:
                previous = (
                    await session.execute(
                        select(workflow_task.c.created_at, workflow_task.c.task_id).where(
                            *scope_predicates(workflow_task, scope),
                            workflow_task.c.task_id == cursor,
                        )
                    )
                ).first()
                if previous is None:
                    raise ValueError("invalid workflow list cursor")
            statement = select(workflow_task).where(*scope_predicates(workflow_task, scope))
            if agent_id is not None:
                statement = statement.where(
                    workflow_task.c.task_id.in_(
                        select(workflow_node.c.task_id).where(
                            *scope_predicates(workflow_node, scope),
                            workflow_node.c.role == agent_id,
                            workflow_node.c.kind == "agent",
                        )
                    )
                )
            if previous is not None:
                statement = statement.where(
                    or_(
                        workflow_task.c.created_at < previous.created_at,
                        and_(
                            workflow_task.c.created_at == previous.created_at,
                            workflow_task.c.task_id < previous.task_id,
                        ),
                    )
                )
            rows = (
                (
                    await session.execute(
                        statement.order_by(
                            workflow_task.c.created_at.desc(), workflow_task.c.task_id.desc()
                        ).limit(limit + 1)
                    )
                )
                .mappings()
                .all()
            )
            page = rows[:limit]
            task_ids = tuple(row["task_id"] for row in page)
            node_rows = (
                (
                    await session.execute(
                        select(
                            workflow_node.c.task_id,
                            workflow_node.c.node_id,
                            workflow_node.c.role,
                            workflow_node.c.kind,
                            workflow_node.c.agent_revision_id,
                            workflow_node.c.attempt,
                            workflow_node.c.status,
                            workflow_node.c.created_at,
                        ).where(
                            *scope_predicates(workflow_node, scope),
                            workflow_node.c.task_id.in_(task_ids),
                        )
                    )
                )
                .mappings()
                .all()
                if task_ids
                else ()
            )
            latest = (
                select(
                    workflow_event.c.task_id,
                    func.max(workflow_event.c.sequence).label("sequence"),
                )
                .where(
                    *scope_predicates(workflow_event, scope),
                    workflow_event.c.task_id.in_(task_ids),
                )
                .group_by(workflow_event.c.task_id)
                .subquery()
            )
            event_rows = (
                (
                    await session.execute(
                        select(
                            workflow_event.c.task_id,
                            workflow_event.c.kind,
                            workflow_event.c.occurred_at,
                        )
                        .join(
                            latest,
                            and_(
                                workflow_event.c.task_id == latest.c.task_id,
                                workflow_event.c.sequence == latest.c.sequence,
                            ),
                        )
                        .where(*scope_predicates(workflow_event, scope))
                    )
                )
                .mappings()
                .all()
                if task_ids
                else ()
            )
        latest_by_task = {row["task_id"]: dict(row) for row in event_rows}
        by_task: dict[str, list[dict[str, Any]]] = {task_id: [] for task_id in task_ids}
        for node in node_rows:
            by_task[node["task_id"]].append(dict(node))
        has_more = len(rows) > limit
        return (
            tuple(
                {
                    **dict(row),
                    "nodes": tuple(by_task[row["task_id"]]),
                    "recent_event": latest_by_task.get(row["task_id"]),
                }
                for row in page
            ),
            page[-1]["task_id"] if has_more else None,
        )

    async def node_logs(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        node_id: str,
        attempt: int,
        sequence: int,
        limit: int,
    ) -> tuple[tuple[WorkflowEvent, ...], int | None]:
        scope = require_project_scope(scope)
        if attempt < 1 or sequence < 0 or not 1 <= limit <= 100:
            raise ValueError("invalid workflow log page")
        async with self._sessions() as session:
            exists = (
                await session.execute(
                    select(workflow_attempt.c.attempt_id).where(
                        *scope_predicates(workflow_attempt, scope),
                        workflow_attempt.c.task_id == task_id,
                        workflow_attempt.c.node_id == node_id,
                        workflow_attempt.c.attempt == attempt,
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                raise LookupError("workflow node attempt not found")
            rows = (
                await session.execute(
                    select(workflow_event)
                    .where(
                        *scope_predicates(workflow_event, scope),
                        workflow_event.c.task_id == task_id,
                        workflow_event.c.node_id == node_id,
                        workflow_event.c.attempt == attempt,
                        workflow_event.c.sequence > sequence,
                    )
                    .order_by(workflow_event.c.sequence)
                    .limit(limit + 1)
                )
            ).all()
        has_more = len(rows) > limit
        page = tuple(_event(row) for row in rows[:limit])
        return page, page[-1].sequence if has_more and page else None

    async def task_request(self, scope: ProjectScopeContext, task_id: str) -> dict[str, Any]:
        """Private frozen admission/runtime input, never an HTTP projection."""
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            request = (
                await session.execute(
                    select(workflow_task.c.request_json).where(
                        *scope_predicates(workflow_task, scope),
                        workflow_task.c.task_id == task_id,
                    )
                )
            ).scalar_one_or_none()
        if request is None:
            raise LookupError("workflow task not found")
        return dict(request)

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

    async def submit_command(
        self, scope: ProjectScopeContext, command: ControlCommand
    ) -> dict[str, object]:
        """Serialize acceptance by task; this receipt never claims execution."""
        scope = require_project_scope(scope)
        if type(command) is not ControlCommand:
            raise TypeError("control command required")
        request = {
            "task_id": command.task_id,
            "target_node_id": command.target_node_id,
            "kind": command.kind,
            "expected_plan_revision": command.expected_plan_revision,
            "text": command.text,
        }
        digest = _digest(request)
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(workflow_task)
                    .where(
                        *scope_predicates(workflow_task, scope),
                        workflow_task.c.task_id == command.task_id,
                    )
                    .with_for_update()
                )
            ).first()
            if row is None:
                raise LookupError("workflow task not found")
            task = row._mapping
            if not _native_request(task["request_json"]):
                raise ValueError("observed workflow task has no native controls")
            previous = (
                await session.execute(
                    select(workflow_command).where(
                        workflow_command.c.command_id == command.command_id
                    )
                )
            ).first()
            if previous is not None:
                existing = previous._mapping
                if (
                    existing["project_id"] != scope.project_id
                    or existing["task_id"] != command.task_id
                    or existing["payload_digest"] != digest
                ):
                    raise ValueError("command id payload conflict")
                return dict(existing["receipt"])
            if command.target_node_id is not None:
                node = (
                    await session.execute(
                        select(workflow_node.c.node_id).where(
                            *scope_predicates(workflow_node, scope),
                            workflow_node.c.task_id == command.task_id,
                            workflow_node.c.node_id == command.target_node_id,
                        )
                    )
                ).first()
                if node is None:
                    raise LookupError("workflow node not found")
            disposition = command_disposition(
                command.expected_plan_revision, task["plan_revision"], False
            )
            if task["status"] in ("succeeded", "failed", "canceled", "skipped"):
                disposition = "rejected"
                reason = "terminal task requires a new task"
            elif disposition == "stale":
                reason = "plan revision changed"
            elif disposition == "rejected":
                reason = "future plan revision"
            else:
                disposition = "accepted"
                reason = None
            receipt: dict[str, object] = {
                "status": disposition,
                "reason": reason,
                "applied_plan_revision": None,
            }
            now = datetime.now(UTC).replace(tzinfo=None)
            sequence = task["next_sequence"]
            await session.execute(
                workflow_command.insert().values(
                    command_id=command.command_id,
                    task_id=command.task_id,
                    target_node_id=command.target_node_id,
                    kind=command.kind,
                    payload_digest=digest,
                    payload={"text": command.text, "accepted_sequence": sequence},
                    expected_plan_revision=command.expected_plan_revision,
                    receipt=receipt,
                    created_at=now,
                    **scope_values(scope),
                )
            )
            event_payload = {"command_id": command.command_id, "status": disposition}
            await session.execute(
                workflow_event.insert().values(
                    event_id=uuid4().hex,
                    task_id=command.task_id,
                    sequence=sequence,
                    node_id=command.target_node_id,
                    attempt=0,
                    kind="command.received",
                    payload=event_payload,
                    payload_digest=_digest(event_payload),
                    occurred_at=now,
                    **scope_values(scope),
                )
            )
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == command.task_id)
                .values(next_sequence=sequence + 1, updated_at=now)
            )
            return receipt

    async def command(
        self, scope: ProjectScopeContext, task_id: str, command_id: str
    ) -> ControlCommand | None:
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(workflow_command).where(
                            *scope_predicates(workflow_command, scope),
                            workflow_command.c.task_id == task_id,
                            workflow_command.c.command_id == command_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return ControlCommand(
            row["command_id"],
            row["task_id"],
            row["target_node_id"],
            row["kind"],
            row["expected_plan_revision"],
            row["payload"]["text"],
        )

    async def pending_commands(
        self, scope: ProjectScopeContext, task_id: str
    ) -> tuple[ControlCommand, ...]:
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            task = (
                await session.execute(
                    select(workflow_task.c.task_id).where(
                        *scope_predicates(workflow_task, scope), workflow_task.c.task_id == task_id
                    )
                )
            ).first()
            if task is None:
                raise LookupError("workflow task not found")
            rows = (
                (
                    await session.execute(
                        select(workflow_command).where(
                            *scope_predicates(workflow_command, scope),
                            workflow_command.c.task_id == task_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
        ordered = sorted(
            (row for row in rows if row["receipt"]["status"] in ("accepted", "pending")),
            key=lambda row: row["payload"]["accepted_sequence"],
        )
        return tuple(
            ControlCommand(
                row["command_id"],
                row["task_id"],
                row["target_node_id"],
                row["kind"],
                row["expected_plan_revision"],
                row["payload"]["text"],
            )
            for row in ordered
        )

    async def expire_idle_task(self, scope: ProjectScopeContext, task_id: str) -> bool:
        """End an unowned task whose creation-time absolute deadline elapsed."""
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        select(workflow_task)
                        .where(
                            *scope_predicates(workflow_task, scope),
                            workflow_task.c.task_id == task_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError("workflow task not found")
            if not _native_request(row["request_json"]):
                return False
            deadline = row["request_json"].get("deadline_at")
            if not isinstance(deadline, str):
                return False
            now = datetime.now(UTC).replace(tzinfo=None)
            if (
                datetime.fromisoformat(deadline).replace(tzinfo=None) > now
                or row["status"] not in ("queued", "waiting_dependency", "awaiting_input", "paused")
                or (row["lease_until"] is not None and row["lease_until"] > now)
            ):
                return False
            task = dict(row)
            pending = (
                (
                    await session.execute(
                        select(workflow_command).where(
                            *scope_predicates(workflow_command, scope),
                            workflow_command.c.task_id == task_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
            for command in sorted(pending, key=lambda item: item["payload"]["accepted_sequence"]):
                if command["receipt"]["status"] not in ("accepted", "pending"):
                    continue
                receipt = {
                    "status": "rejected",
                    "reason": "workflow deadline exceeded",
                    "applied_plan_revision": None,
                }
                await session.execute(
                    update(workflow_command)
                    .where(
                        workflow_command.c.command_id == command["command_id"],
                    )
                    .values(receipt=receipt)
                )
                payload = {"command_id": command["command_id"], "receipt": receipt}
                await session.execute(
                    workflow_event.insert().values(
                        event_id=uuid4().hex,
                        task_id=task_id,
                        sequence=task["next_sequence"],
                        node_id=command["target_node_id"],
                        attempt=0,
                        kind="command.receipted",
                        payload=payload,
                        payload_digest=_digest(payload),
                        occurred_at=now,
                        **scope_values(scope),
                    )
                )
                task["next_sequence"] += 1
            payload = {"reason": "deadline exceeded"}
            await session.execute(
                update(workflow_task)
                .where(
                    workflow_task.c.task_id == task_id,
                )
                .values(
                    status="failed",
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                    next_sequence=task["next_sequence"] + 1,
                    updated_at=now,
                )
            )
            await session.execute(
                workflow_event.insert().values(
                    event_id=uuid4().hex,
                    task_id=task_id,
                    sequence=task["next_sequence"],
                    node_id=None,
                    attempt=0,
                    kind="task.failed",
                    payload=payload,
                    payload_digest=_digest(payload),
                    occurred_at=now,
                    **scope_values(scope),
                )
            )
            return True

    async def claim_task(self, scope: ProjectScopeContext, task_id: str, owner: str) -> str | None:
        scope = require_project_scope(scope)
        require_identifier("owner", owner)
        require_identifier("task_id", task_id)

        async def attempt() -> str | None:
            async with claim_transaction(self._sessions) as session:
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
                if not _native_request(task["request_json"]):
                    return None
                now = datetime.now(UTC).replace(tzinfo=None)
                if (
                    task["status"] not in ("queued", "waiting_dependency")
                    or not task["admission_ready"]
                    or (
                        task["lease_until"] is not None
                        and task["lease_until"] > now
                        and task["lease_token"]
                        and task["lease_owner"]
                    )
                ):
                    return None
                if task["status"] == "waiting_dependency":
                    nodes = (
                        await session.execute(
                            select(workflow_node.c.node_id, workflow_node.c.status).where(
                                *scope_predicates(workflow_node, scope),
                                workflow_node.c.task_id == task_id,
                            )
                        )
                    ).all()
                    edges = (
                        await session.execute(
                            select(
                                workflow_dependency.c.from_node_id, workflow_dependency.c.to_node_id
                            ).where(
                                *scope_predicates(workflow_dependency, scope),
                                workflow_dependency.c.task_id == task_id,
                                workflow_dependency.c.plan_revision == task["plan_revision"],
                            )
                        )
                    ).all()
                    statuses = {node_id: status for node_id, status in nodes}
                    predecessors: dict[str, set[str]] = {}
                    for from_id, to_id in edges:
                        predecessors.setdefault(to_id, set()).add(from_id)
                    if not any(
                        status == "queued"
                        and all(
                            statuses.get(parent) == "succeeded"
                            for parent in predecessors.get(node_id, ())
                        )
                        for node_id, status in nodes
                    ):
                        return None
                token = uuid4().hex
                await session.execute(
                    update(workflow_task)
                    .where(workflow_task.c.task_id == task_id)
                    .values(
                        status="running",
                        lease_owner=owner,
                        lease_token=token,
                        lease_until=now + timedelta(minutes=5),
                        updated_at=now,
                        next_sequence=task["next_sequence"] + 1,
                    )
                )
                payload: dict[str, object] = {"owner": owner, "execution_token": token}
                await session.execute(
                    workflow_event.insert().values(
                        event_id=uuid4().hex,
                        task_id=task_id,
                        sequence=task["next_sequence"],
                        node_id=None,
                        attempt=0,
                        kind="task.claimed",
                        payload=payload,
                        payload_digest=_digest(payload),
                        occurred_at=now,
                        **scope_values(scope),
                    )
                )
                return token

        return await claim_or_nothing(attempt, kind="workflow", nothing=None)

    @staticmethod
    def _action_event_id(action_id: str, phase: str) -> str:
        return hashlib.sha256(f"{phase}:{action_id}".encode()).hexdigest()

    async def reserve_action(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        lease_token: str,
        action_id: str,
        action_kind: str,
        *,
        node_id: str | None = None,
        attempt: int = 0,
    ) -> str:
        """Reserve one external dispatch and its shared budget before calling it."""
        scope = require_project_scope(scope)
        require_identifier("action_id", action_id)
        if action_kind not in ("model", "tool"):
            raise ValueError("unsupported action kind")
        event_id = self._action_event_id(action_id, "reserved")
        async with self._sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        select(workflow_task)
                        .where(
                            *scope_predicates(workflow_task, scope),
                            workflow_task.c.task_id == task_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError("workflow task not found")
            now = datetime.now(UTC).replace(tzinfo=None)
            if (
                row["status"] != "running"
                or row["lease_owner"] is None
                or row["lease_token"] != lease_token
                or row["lease_until"] is None
                or row["lease_until"] <= now
            ):
                raise ValueError("stale workflow lease token")
            if node_id is not None:
                node = (
                    (
                        await session.execute(
                            select(workflow_node).where(
                                *scope_predicates(workflow_node, scope),
                                workflow_node.c.task_id == task_id,
                                workflow_node.c.node_id == node_id,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if node is None or node["status"] != "running" or node["attempt"] != attempt:
                    raise WorkflowDispatchBlocked("workflow node is not running")
            blockers = (
                await session.execute(
                    select(
                        workflow_command.c.kind,
                        workflow_command.c.target_node_id,
                        workflow_command.c.receipt,
                    ).where(
                        *scope_predicates(workflow_command, scope),
                        workflow_command.c.task_id == task_id,
                        workflow_command.c.kind.in_(("pause", "cancel")),
                    )
                )
            ).all()
            if any(
                item.receipt["status"] in ("accepted", "pending")
                and item.target_node_id in (None, node_id)
                for item in blockers
            ):
                raise WorkflowDispatchBlocked("workflow control blocks new dispatch")
            previous = (
                (
                    await session.execute(
                        select(workflow_event).where(workflow_event.c.event_id == event_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if previous is not None:
                if (
                    previous["task_id"] != task_id
                    or previous["project_id"] != scope.project_id
                    or previous["payload"].get("action_id") != action_id
                    or previous["payload"].get("action_kind") != action_kind
                ):
                    raise ValueError("action identity conflict")
                return "existing"
            deadline = row["request_json"].get("deadline_at")
            if deadline and datetime.fromisoformat(deadline).replace(tzinfo=None) <= now:
                raise WorkflowLimitExceeded("workflow deadline exceeded")
            used = dict(row["budget_used"])
            cost_cap = row["budget_limit"].get("cost_micros")
            if cost_cap is not None and used.get("cost_micros", 0) >= cost_cap:
                raise WorkflowLimitExceeded("workflow known cost budget reached")
            if action_kind == "model":
                total = used.get("model_calls", 0) + 1
                cap = row["budget_limit"].get("model_calls")
                if cap is not None and total > cap:
                    raise WorkflowLimitExceeded("workflow model call budget exceeded")
                used["model_calls"] = total
            payload: dict[str, object] = {
                "action_id": action_id,
                "action_kind": action_kind,
                "execution_token": lease_token,
                "node_id": node_id,
                "attempt": attempt,
            }
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task_id)
                .values(budget_used=used, next_sequence=row["next_sequence"] + 1, updated_at=now)
            )
            await session.execute(
                workflow_event.insert().values(
                    event_id=event_id,
                    task_id=task_id,
                    sequence=row["next_sequence"],
                    node_id=node_id,
                    attempt=attempt,
                    kind="action.reserved",
                    payload=payload,
                    payload_digest=_digest(payload),
                    occurred_at=now,
                    **scope_values(scope),
                )
            )
        return "reserved"

    async def settle_action(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        lease_token: str,
        action_id: str,
        result: dict[str, object],
        *,
        node_id: str | None = None,
        attempt: int = 0,
    ) -> dict[str, object]:
        scope = require_project_scope(scope)
        event_id = self._action_event_id(action_id, "settled")
        async with self._sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        select(workflow_task)
                        .where(
                            *scope_predicates(workflow_task, scope),
                            workflow_task.c.task_id == task_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError("workflow task not found")
            now = datetime.now(UTC).replace(tzinfo=None)
            if (
                row["status"] != "running"
                or row["lease_owner"] is None
                or row["lease_token"] != lease_token
                or row["lease_until"] is None
                or row["lease_until"] <= now
            ):
                raise ValueError("stale workflow lease token")
            previous = (
                (
                    await session.execute(
                        select(workflow_event).where(workflow_event.c.event_id == event_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if previous is not None:
                return dict(previous["payload"]["result"])
            reserved = (
                (
                    await session.execute(
                        select(workflow_event).where(
                            workflow_event.c.event_id
                            == self._action_event_id(action_id, "reserved")
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if reserved is None or reserved["payload"].get("execution_token") != lease_token:
                raise ValueError("action was not reserved by this execution")
            payload: dict[str, object] = {
                "action_id": action_id,
                "execution_token": lease_token,
                "result": result,
            }
            used = dict(row["budget_used"])
            if "skipped" not in result:
                raw_cost = result.get("cost_usd")
                try:
                    cost = Decimal(str(raw_cost)) if raw_cost is not None else None
                except InvalidOperation:
                    cost = None
                if cost is not None and cost.is_finite() and cost >= 0:
                    micros = int((cost * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
                    used["cost_micros"] = used.get("cost_micros", 0) + micros
                else:
                    used["unknown_cost_actions"] = used.get("unknown_cost_actions", 0) + 1
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task_id)
                .values(
                    budget_used=used,
                    next_sequence=row["next_sequence"] + 1,
                    updated_at=now,
                )
            )
            await session.execute(
                workflow_event.insert().values(
                    event_id=event_id,
                    task_id=task_id,
                    sequence=row["next_sequence"],
                    node_id=node_id,
                    attempt=attempt,
                    kind="action.settled",
                    payload=payload,
                    payload_digest=_digest(payload),
                    occurred_at=now,
                    **scope_values(scope),
                )
            )
        return result

    async def action_result(
        self, scope: ProjectScopeContext, action_id: str
    ) -> dict[str, object] | None:
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(workflow_event).where(
                            *scope_predicates(workflow_event, require_project_scope(scope)),
                            workflow_event.c.event_id
                            == self._action_event_id(action_id, "settled"),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else dict(row["payload"]["result"])

    async def pending_admissions(
        self, scope: ProjectScopeContext, limit: int
    ) -> tuple[tuple[str, str, str], ...]:
        scope = require_project_scope(scope)
        if limit < 1:
            raise ValueError("admission scan limit must be positive")
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(workflow_task)
                        .where(
                            *scope_predicates(workflow_task, scope),
                            workflow_task.c.admission_ready.is_(False),
                            workflow_task.c.status == "queued",
                        )
                        .order_by(workflow_task.c.created_at)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(
            (row["task_id"], row["conversation_id"], row["request_json"]["origin_turn_id"])
            for row in rows
            if row["conversation_id"] and row["request_json"].get("origin_turn_id")
        )

    async def activate_task(
        self, scope: ProjectScopeContext, task_id: str, origin_turn_id: str
    ) -> None:
        """Trusted composition calls this only after Chat confirms the exact link."""
        scope = require_project_scope(scope)
        require_identifier("origin_turn_id", origin_turn_id)
        async with self._sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        select(workflow_task)
                        .where(
                            *scope_predicates(workflow_task, scope),
                            workflow_task.c.task_id == task_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                row is None
                or not _native_request(row["request_json"])
                or row["request_json"].get("origin_turn_id") != origin_turn_id
            ):
                raise ValueError("workflow origin mismatch")
            if row["status"] != "queued":
                return
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task_id)
                .values(admission_ready=True, updated_at=datetime.now(UTC).replace(tzinfo=None))
            )

    async def ack_command(
        self,
        scope: ProjectScopeContext,
        command_id: str,
        lease_token: str,
        receipt: dict[str, object],
    ) -> dict[str, object]:
        """Apply and receipt one command atomically under a live worker fence."""
        scope = require_project_scope(scope)
        require_identifier("command_id", command_id)
        if not isinstance(receipt, dict) or receipt.get("status") not in ("applied", "rejected"):
            raise ValueError("runtime receipt must be applied or rejected")
        async with self._sessions.begin() as session:
            command_row = (
                await session.execute(
                    select(workflow_command.c.task_id).where(
                        *scope_predicates(workflow_command, scope),
                        workflow_command.c.command_id == command_id,
                    )
                )
            ).first()
            if command_row is None:
                raise LookupError("workflow command not found")
            task_id = command_row.task_id
            task_row = (
                await session.execute(
                    select(workflow_task)
                    .where(
                        *scope_predicates(workflow_task, scope), workflow_task.c.task_id == task_id
                    )
                    .with_for_update()
                )
            ).first()
            if task_row is None:
                raise LookupError("workflow task not found")
            task = task_row._mapping
            self._require_lease(task, lease_token)
            current_command = (
                await session.execute(
                    select(workflow_command)
                    .where(
                        *scope_predicates(workflow_command, scope),
                        workflow_command.c.command_id == command_id,
                        workflow_command.c.task_id == task_id,
                    )
                    .with_for_update()
                )
            ).first()
            if current_command is None:
                raise LookupError("workflow command not found")
            command = current_command._mapping
            if command["receipt"]["status"] not in ("accepted", "pending"):
                return dict(command["receipt"])
            if command["payload"]["accepted_sequence"] != self._first_pending_sequence(
                await session.execute(
                    select(workflow_command.c.payload, workflow_command.c.receipt)
                    .where(
                        *scope_predicates(workflow_command, scope),
                        workflow_command.c.task_id == task_id,
                    )
                    .with_for_update()
                )
            ):
                raise ValueError("earlier control command is pending")
            disposition = command_disposition(
                command["expected_plan_revision"], task["plan_revision"], False
            )
            if task["status"] in ("succeeded", "failed", "canceled", "skipped"):
                result: dict[str, object] = {
                    "status": "rejected",
                    "reason": "terminal task requires a new task",
                    "applied_plan_revision": None,
                }
            elif disposition != "pending":
                result = {
                    "status": disposition,
                    "reason": "plan revision changed"
                    if disposition == "stale"
                    else "future plan revision",
                    "applied_plan_revision": None,
                }
            elif receipt["status"] == "rejected":
                result = {
                    "status": "rejected",
                    "reason": receipt.get("reason") or "runtime rejected",
                    "applied_plan_revision": None,
                }
            else:
                result = await self._apply_command(session, scope, task, command, receipt)
            now = datetime.now(UTC).replace(tzinfo=None)
            await session.execute(
                update(workflow_command)
                .where(workflow_command.c.command_id == command_id)
                .values(receipt=result)
            )
            event_payload: dict[str, object] = {"command_id": command_id, "receipt": result}
            await session.execute(
                workflow_event.insert().values(
                    event_id=uuid4().hex,
                    task_id=task_id,
                    sequence=task["next_sequence"],
                    node_id=command["target_node_id"],
                    attempt=0,
                    kind="command.receipted",
                    payload=event_payload,
                    payload_digest=_digest(event_payload),
                    occurred_at=now,
                    **scope_values(scope),
                )
            )
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task_id)
                .values(
                    next_sequence=task["next_sequence"] + 1,
                    updated_at=now,
                )
            )
            return result

    @staticmethod
    def _first_pending_sequence(rows: Result[Any]) -> int:
        return min(
            row.payload["accepted_sequence"]
            for row in rows
            if row.receipt["status"] in ("accepted", "pending")
        )

    @staticmethod
    def _require_lease(task: RowMapping, token: str) -> None:
        fields = task
        if (
            not token
            or fields["lease_token"] != token
            or not fields["lease_owner"]
            or fields["lease_until"] is None
            or fields["lease_until"] <= datetime.now(UTC).replace(tzinfo=None)
        ):
            raise ValueError("stale workflow lease token")

    async def _apply_command(
        self,
        session: AsyncSession,
        scope: ProjectScopeContext,
        task: RowMapping,
        command: RowMapping,
        receipt: dict[str, object],
    ) -> dict[str, object]:
        fields = task
        item = command
        task_id = fields["task_id"]
        kind = item["kind"]
        node_id = item["target_node_id"]
        now = datetime.now(UTC).replace(tzinfo=None)
        revision: int | None = None
        if kind == "steer":
            prior = (
                (
                    await session.execute(
                        select(workflow_plan_revision).where(
                            *scope_predicates(workflow_plan_revision, scope),
                            workflow_plan_revision.c.task_id == task_id,
                            workflow_plan_revision.c.revision_number == fields["plan_revision"],
                        )
                    )
                )
                .mappings()
                .one()
            )
            old_edges = (
                await session.execute(
                    select(
                        workflow_dependency.c.from_node_id, workflow_dependency.c.to_node_id
                    ).where(
                        *scope_predicates(workflow_dependency, scope),
                        workflow_dependency.c.task_id == task_id,
                        workflow_dependency.c.plan_revision == fields["plan_revision"],
                    )
                )
            ).all()
            nodes = (
                (
                    await session.execute(
                        select(workflow_node).where(
                            *scope_predicates(workflow_node, scope),
                            workflow_node.c.task_id == task_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
            by_id = {node["node_id"]: node for node in nodes}
            revision = fields["plan_revision"] + 1
            rework_id: str | None = None
            target = by_id.get(node_id) if node_id is not None else None
            if target is not None and target["status"] in (
                "succeeded",
                "failed",
                "canceled",
                "skipped",
            ):
                rework_id = uuid4().hex
            new_edges: set[tuple[str, str]] = set()
            for from_id, to_id in old_edges:
                if rework_id is not None and to_id == node_id:
                    new_edges.add((from_id, rework_id))
                    continue
                if (
                    rework_id is not None
                    and from_id == node_id
                    and by_id[to_id]["status"] in ("queued", "waiting_dependency")
                ):
                    from_id = rework_id
                new_edges.add((from_id, to_id))
            ordered_edges = sorted(new_edges)
            await session.execute(
                workflow_plan_revision.insert().values(
                    revision_id=uuid4().hex,
                    task_id=task_id,
                    revision_number=revision,
                    goal=prior["goal"],
                    dependencies=[
                        {"from_node_id": from_id, "to_node_id": to_id}
                        for from_id, to_id in ordered_edges
                    ],
                    input_snapshot={
                        "source_revision": fields["plan_revision"],
                        "source_input": prior["input_snapshot"],
                        "steer_instruction": item["payload"]["text"],
                    },
                    created_at=now,
                    **scope_values(scope),
                )
            )
            if rework_id is not None and target is not None:
                await session.execute(
                    workflow_node.insert().values(
                        node_id=rework_id,
                        task_id=task_id,
                        plan_revision=revision,
                        parent_node_id=node_id,
                        agent_revision_id=target["agent_revision_id"],
                        role=target["role"],
                        kind=target["kind"],
                        runtime=target["runtime"],
                        status="queued",
                        attempt=1,
                        created_at=now,
                        **scope_values(scope),
                    )
                )
                await session.execute(
                    workflow_attempt.insert().values(
                        attempt_id=uuid4().hex,
                        task_id=task_id,
                        node_id=rework_id,
                        attempt=1,
                        status="queued",
                        created_at=now,
                        **scope_values(scope),
                    )
                )
            for from_id, to_id in ordered_edges:
                await session.execute(
                    workflow_dependency.insert().values(
                        dependency_id=uuid4().hex,
                        task_id=task_id,
                        from_node_id=from_id,
                        to_node_id=to_id,
                        plan_revision=revision,
                        **scope_values(scope),
                    )
                )
            changes: dict[str, object] = {"plan_revision": revision}
            if fields["status"] == "awaiting_input":
                await session.execute(
                    update(workflow_node)
                    .where(
                        *scope_predicates(workflow_node, scope),
                        workflow_node.c.task_id == task_id,
                        workflow_node.c.status == "awaiting_input",
                    )
                    .values(status="queued")
                )
                await session.execute(
                    update(workflow_attempt)
                    .where(
                        *scope_predicates(workflow_attempt, scope),
                        workflow_attempt.c.task_id == task_id,
                        workflow_attempt.c.status == "awaiting_input",
                    )
                    .values(status="queued")
                )
                changes["status"] = "queued"
            await session.execute(
                update(workflow_task).where(workflow_task.c.task_id == task_id).values(**changes)
            )
        elif kind == "pause":
            if node_id is None:
                if fields["status"] not in (
                    "running",
                    "queued",
                    "waiting_dependency",
                    "awaiting_input",
                ):
                    raise ValueError("task cannot be paused")
                if (
                    receipt.get("stopped") is True
                    and receipt.get("checkpoint_compatible") is not True
                ):
                    raise ValueError("pause requires a compatible checkpoint")
                changes = {"status": "paused" if receipt.get("stopped") is True else "pausing"}
                if receipt.get("stopped") is True:
                    changes.update(lease_owner=None, lease_token=None, lease_until=None)
                    await session.execute(
                        update(workflow_node)
                        .where(
                            *scope_predicates(workflow_node, scope),
                            workflow_node.c.task_id == task_id,
                            workflow_node.c.status == "running",
                        )
                        .values(status="paused")
                    )
                    await session.execute(
                        update(workflow_attempt)
                        .where(
                            *scope_predicates(workflow_attempt, scope),
                            workflow_attempt.c.task_id == task_id,
                            workflow_attempt.c.status == "running",
                        )
                        .values(status="paused")
                    )
                await session.execute(
                    update(workflow_task)
                    .where(workflow_task.c.task_id == task_id)
                    .values(**changes)
                )
            else:
                node = await self._command_node(session, scope, task_id, node_id)
                if node["status"] not in ("running", "queued"):
                    raise ValueError("node cannot be paused")
                if (
                    receipt.get("stopped") is True
                    and receipt.get("checkpoint_compatible") is not True
                ):
                    raise ValueError("pause requires a compatible checkpoint")
                await session.execute(
                    update(workflow_node)
                    .where(workflow_node.c.node_id == node_id)
                    .values(status="paused" if receipt.get("stopped") is True else "pausing")
                )
        elif kind == "resume":
            if node_id is None:
                if fields["status"] != "paused":
                    raise ValueError("task is not paused")
                await session.execute(
                    update(workflow_node)
                    .where(
                        *scope_predicates(workflow_node, scope),
                        workflow_node.c.task_id == task_id,
                        workflow_node.c.status == "paused",
                    )
                    .values(status="queued")
                )
                await session.execute(
                    update(workflow_attempt)
                    .where(
                        *scope_predicates(workflow_attempt, scope),
                        workflow_attempt.c.task_id == task_id,
                        workflow_attempt.c.status == "paused",
                    )
                    .values(status="queued")
                )
                await session.execute(
                    update(workflow_task)
                    .where(workflow_task.c.task_id == task_id)
                    .values(status="queued", lease_owner=None, lease_token=None, lease_until=None)
                )
            else:
                node = await self._command_node(session, scope, task_id, node_id)
                if node["status"] != "paused":
                    raise ValueError("node is not paused")
                await session.execute(
                    update(workflow_node)
                    .where(workflow_node.c.node_id == node_id)
                    .values(status="queued")
                )
        elif kind == "cancel":
            if fields["status"] not in (
                "queued",
                "running",
                "waiting_dependency",
                "awaiting_input",
                "paused",
            ):
                raise ValueError("task cannot be canceled from this state")
            status = "canceled" if receipt.get("stopped") is True else "canceling"
            changes = {"status": status}
            if status == "canceled":
                changes.update(lease_owner=None, lease_token=None, lease_until=None)
            await session.execute(
                update(workflow_task).where(workflow_task.c.task_id == task_id).values(**changes)
            )
        elif kind == "retry":
            node = await self._command_node(session, scope, task_id, node_id)
            if fields["status"] in ("succeeded", "failed", "canceled", "skipped"):
                raise ValueError("terminal task requires a new task")
            if node["status"] not in ("failed", "canceled", "skipped"):
                raise ValueError("retry requires a terminal node")
            if (
                receipt.get("side_effects") != "clear"
                or receipt.get("checkpoint_compatible") is not True
            ):
                raise ValueError("retry requires confirmed side effects and checkpoint")
            if any(
                fields["budget_used"].get(key, 0) >= amount
                for key, amount in fields["budget_limit"].items()
            ):
                raise ValueError("workflow task budget exhausted")
            prior = (
                (
                    await session.execute(
                        select(workflow_attempt).where(
                            *scope_predicates(workflow_attempt, scope),
                            workflow_attempt.c.task_id == task_id,
                            workflow_attempt.c.node_id == node_id,
                            workflow_attempt.c.attempt == node["attempt"],
                        )
                    )
                )
                .mappings()
                .one()
            )
            attempt = node["attempt"] + 1
            if attempt > int(fields["request_json"].get("max_attempts", 3)):
                raise ValueError("workflow node attempt budget exceeded")
            await session.execute(
                workflow_attempt.insert().values(
                    attempt_id=uuid4().hex,
                    task_id=task_id,
                    node_id=node_id,
                    attempt=attempt,
                    status="queued",
                    checkpoint_ref=None,
                    graph_run_id=hashlib.sha256(
                        f"{task_id}:{node_id}:retry:{attempt}".encode()
                    ).hexdigest(),
                    created_at=now,
                    **scope_values(scope),
                )
            )
            await session.execute(
                update(workflow_node)
                .where(workflow_node.c.node_id == node_id)
                .values(attempt=attempt, status="queued")
            )
        else:
            raise ValueError("unsupported workflow command")
        return {"status": "applied", "reason": None, "applied_plan_revision": revision}

    @staticmethod
    async def _command_node(
        session: AsyncSession, scope: ProjectScopeContext, task_id: str, node_id: str
    ) -> RowMapping:
        return (
            (
                await session.execute(
                    select(workflow_node).where(
                        *scope_predicates(workflow_node, scope),
                        workflow_node.c.task_id == task_id,
                        workflow_node.c.node_id == node_id,
                    )
                )
            )
            .mappings()
            .one()
        )

    async def mark_dispatch_blocked(
        self, scope: ProjectScopeContext, task_id: str, lease_token: str
    ) -> None:
        """Quarantine an exited graph whose stop/checkpoint proof is incomplete."""
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        select(workflow_task)
                        .where(
                            *scope_predicates(workflow_task, scope),
                            workflow_task.c.task_id == task_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError("workflow task not found")
            self._require_lease(row, lease_token)
            if row["status"] != "running":
                raise ValueError("workflow task is not running")
            now = datetime.now(UTC).replace(tzinfo=None)
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task_id)
                .values(
                    status="recovering",
                    recovering_from="running",
                    lease_owner=None,
                    lease_until=None,
                    next_sequence=row["next_sequence"] + 1,
                    updated_at=now,
                )
            )
            payload = {"reason": "checkpoint or external effect not proven"}
            await session.execute(
                workflow_event.insert().values(
                    event_id=uuid4().hex,
                    task_id=task_id,
                    sequence=row["next_sequence"],
                    node_id=None,
                    attempt=0,
                    kind="task.dispatch_blocked",
                    payload=payload,
                    payload_digest=_digest(payload),
                    occurred_at=now,
                    **scope_values(scope),
                )
            )

    async def mark_lease_lost(
        self, scope: ProjectScopeContext, task_id: str
    ) -> RecoveryEpisode | str:
        scope = require_project_scope(scope)
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
            if not _native_request(task["request_json"]):
                return task["status"]
            if task["status"] == "recovering":
                token = task["lease_token"]
                return RecoveryEpisode(task_id, token) if token else "recovering"
            if task["status"] not in ("running", "pausing", "canceling"):
                return task["status"]
            now = datetime.now(UTC).replace(tzinfo=None)
            if task["lease_until"] is not None and task["lease_until"] > now:
                return task["status"]
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task_id)
                .values(
                    status="recovering",
                    recovering_from=task["status"],
                    lease_owner=None,
                    lease_until=None,
                    next_sequence=task["next_sequence"] + 1,
                    updated_at=now,
                )
            )
            payload: dict[str, object] = {"from": task["status"]}
            await session.execute(
                workflow_event.insert().values(
                    event_id=uuid4().hex,
                    task_id=task_id,
                    sequence=task["next_sequence"],
                    node_id=None,
                    attempt=0,
                    kind="task.lease_lost",
                    payload=payload,
                    payload_digest=_digest(payload),
                    occurred_at=now,
                    **scope_values(scope),
                )
            )
            token = task["lease_token"]
            return RecoveryEpisode(task_id, token) if token else "recovering"

    async def resolve_recovery(
        self, scope: ProjectScopeContext, task_id: str, inspection: RuntimeInspection
    ) -> str:
        scope = require_project_scope(scope)
        if type(inspection) is not RuntimeInspection:
            raise TypeError("runtime inspection required")
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
            if task["status"] != "recovering":
                return task["status"]
            if not inspection.execution_token or inspection.execution_token != task["lease_token"]:
                raise ValueError("stale recovery episode observation")
            if inspection.outcome != "stopped":
                return "recovering"
            origin = task["recovering_from"]
            if origin == "canceling":
                result = "canceled"
            elif origin == "pausing" and inspection.checkpoint_compatible:
                result = "paused"
            elif (
                origin == "running"
                and inspection.checkpoint_compatible
                and inspection.side_effects == "clear"
            ):
                result = "queued"
            else:
                return "recovering"
            now = datetime.now(UTC).replace(tzinfo=None)
            if result == "queued":
                active = (
                    (
                        await session.execute(
                            select(workflow_node).where(
                                *scope_predicates(workflow_node, scope),
                                workflow_node.c.task_id == task_id,
                                workflow_node.c.status == "running",
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                deadline = task["request_json"].get("deadline_at")
                exhausted = any(
                    int(node["attempt"]) >= int(task["request_json"].get("max_attempts", 3))
                    for node in active
                )
                expired = (
                    isinstance(deadline, str)
                    and datetime.fromisoformat(deadline).replace(tzinfo=None) <= now
                )
                if exhausted or expired:
                    result = "failed"
                    await session.execute(
                        update(workflow_node)
                        .where(
                            *scope_predicates(workflow_node, scope),
                            workflow_node.c.task_id == task_id,
                            workflow_node.c.status == "running",
                        )
                        .values(status="failed")
                    )
                    await session.execute(
                        update(workflow_attempt)
                        .where(
                            *scope_predicates(workflow_attempt, scope),
                            workflow_attempt.c.task_id == task_id,
                            workflow_attempt.c.status == "running",
                        )
                        .values(
                            status="failed",
                            error_snapshot={
                                "reason": "deadline exceeded"
                                if expired
                                else "attempt limit reached",
                            },
                        )
                    )
            if result == "queued":
                rows = (
                    (
                        await session.execute(
                            select(workflow_node).where(
                                *scope_predicates(workflow_node, scope),
                                workflow_node.c.task_id == task_id,
                                workflow_node.c.status == "running",
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                for node in rows:
                    previous = (
                        (
                            await session.execute(
                                select(workflow_attempt).where(
                                    *scope_predicates(workflow_attempt, scope),
                                    workflow_attempt.c.task_id == task_id,
                                    workflow_attempt.c.node_id == node["node_id"],
                                    workflow_attempt.c.attempt == node["attempt"],
                                )
                            )
                        )
                        .mappings()
                        .one()
                    )
                    await session.execute(
                        update(workflow_attempt)
                        .where(workflow_attempt.c.attempt_id == previous["attempt_id"])
                        .values(status="interrupted", error_snapshot={"reason": "lease_lost"})
                    )
                    attempt = node["attempt"] + 1
                    await session.execute(
                        workflow_attempt.insert().values(
                            attempt_id=uuid4().hex,
                            task_id=task_id,
                            node_id=node["node_id"],
                            attempt=attempt,
                            status="queued",
                            checkpoint_ref=previous["checkpoint_ref"],
                            graph_run_id=previous["graph_run_id"],
                            created_at=now,
                            **scope_values(scope),
                        )
                    )
                    await session.execute(
                        update(workflow_node)
                        .where(workflow_node.c.node_id == node["node_id"])
                        .values(status="queued", attempt=attempt)
                    )
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task_id)
                .values(
                    status=result,
                    recovering_from=None,
                    next_sequence=task["next_sequence"] + 1,
                    updated_at=now,
                )
            )
            payload: dict[str, object] = (
                {"reason": "deadline exceeded" if expired else "attempt limit reached"}
                if result == "failed"
                else {"status": result, "runtime_outcome": inspection.outcome}
            )
            await session.execute(
                workflow_event.insert().values(
                    event_id=uuid4().hex,
                    task_id=task_id,
                    sequence=task["next_sequence"],
                    node_id=None,
                    attempt=0,
                    kind="task.failed" if result == "failed" else "task.reconciled",
                    payload=payload,
                    payload_digest=_digest(payload),
                    occurred_at=now,
                    **scope_values(scope),
                )
            )
            return result

    async def ready_nodes(self, scope: ProjectScopeContext, task_id: str) -> tuple[str, ...]:
        """Only queued nodes whose current-plan predecessors succeeded are ready."""
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            task = (
                await session.execute(
                    select(workflow_task.c.plan_revision, workflow_task.c.status).where(
                        *scope_predicates(workflow_task, scope),
                        workflow_task.c.task_id == task_id,
                    )
                )
            ).first()
            if task is None:
                raise LookupError("workflow task not found")
            if task.status != "running":
                return ()
            nodes = (
                await session.execute(
                    select(workflow_node.c.node_id, workflow_node.c.status).where(
                        *scope_predicates(workflow_node, scope),
                        workflow_node.c.task_id == task_id,
                    )
                )
            ).all()
            edges = (
                await session.execute(
                    select(
                        workflow_dependency.c.from_node_id, workflow_dependency.c.to_node_id
                    ).where(
                        *scope_predicates(workflow_dependency, scope),
                        workflow_dependency.c.task_id == task_id,
                        workflow_dependency.c.plan_revision == task[0],
                    )
                )
            ).all()
        statuses: dict[str, str] = {str(node_id): str(status) for node_id, status in nodes}
        predecessors: dict[str, set[str]] = {}
        for from_id, to_id in edges:
            predecessors.setdefault(to_id, set()).add(from_id)
        return tuple(
            sorted(
                node_id
                for node_id, status in nodes
                if status == "queued"
                and all(
                    statuses.get(predecessor) == "succeeded"
                    for predecessor in predecessors.get(node_id, ())
                )
            )
        )

    async def claim_control(
        self, scope: ProjectScopeContext, task_id: str, owner: str
    ) -> str | None:
        """Give a control worker a fence for paused or otherwise idle tasks."""
        scope = require_project_scope(scope)
        require_identifier("task_id", task_id)
        require_identifier("owner", owner)

        async def attempt() -> str | None:
            async with claim_transaction(self._sessions) as session:
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
                if not _native_request(task["request_json"]):
                    return None
                if task["status"] not in (
                    "queued",
                    "paused",
                    "waiting_dependency",
                    "awaiting_input",
                ):
                    return None
                now = datetime.now(UTC).replace(tzinfo=None)
                if task["lease_until"] is not None and task["lease_until"] > now:
                    return None
                pending = (
                    await session.execute(
                        select(workflow_command.c.command_id, workflow_command.c.receipt).where(
                            *scope_predicates(workflow_command, scope),
                            workflow_command.c.task_id == task_id,
                        )
                    )
                ).all()
                if not any(row.receipt["status"] in ("accepted", "pending") for row in pending):
                    return None
                token = uuid4().hex
                await session.execute(
                    update(workflow_task)
                    .where(workflow_task.c.task_id == task_id)
                    .values(
                        lease_owner=owner,
                        lease_token=token,
                        lease_until=now + timedelta(minutes=5),
                        updated_at=now,
                    )
                )
                return token

        return await claim_or_nothing(attempt, kind="workflow-control", nothing=None)

    async def node_running(self, scope: ProjectScopeContext, task_id: str, node_id: str) -> bool:
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            status = await session.scalar(
                select(workflow_node.c.status).where(
                    *scope_predicates(workflow_node, scope),
                    workflow_node.c.task_id == task_id,
                    workflow_node.c.node_id == node_id,
                )
            )
        return status == "running"

    async def graph_lineage(
        self, scope: ProjectScopeContext, task_id: str, node_id: str, attempt: int
    ) -> tuple[str, int]:
        """Return the durable graph identity and first replayable attempt.

        Lease-loss retries retain a lineage; explicit node retries start a new one.
        """
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        workflow_attempt.c.attempt,
                        workflow_attempt.c.graph_run_id,
                    )
                    .where(
                        *scope_predicates(workflow_attempt, scope),
                        workflow_attempt.c.task_id == task_id,
                        workflow_attempt.c.node_id == node_id,
                    )
                    .order_by(workflow_attempt.c.attempt)
                )
            ).all()
        current = next((row for row in rows if row.attempt == attempt), None)
        if current is None:
            raise LookupError("workflow node attempt not found")
        base_id = hashlib.sha256(f"{task_id}:{node_id}".encode()).hexdigest()
        run_id = current.graph_run_id or base_id
        floor = min(row.attempt for row in rows if (row.graph_run_id or base_id) == run_id)
        return run_id, floor

    async def active_lease(
        self, scope: ProjectScopeContext, task_id: str, lease_token: str
    ) -> bool:
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(
                        workflow_task.c.status,
                        workflow_task.c.lease_token,
                        workflow_task.c.lease_until,
                    ).where(
                        *scope_predicates(workflow_task, scope), workflow_task.c.task_id == task_id
                    )
                )
            ).one_or_none()
        return bool(
            row is not None
            and row.status == "running"
            and row.lease_token == lease_token
            and row.lease_until is not None
            and row.lease_until > datetime.now(UTC).replace(tzinfo=None)
        )

    async def retry_evidence(self, scope: ProjectScopeContext, task_id: str, node_id: str) -> bool:
        """A failed node may retry only with a compatible graph and settled actions."""
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            node = (
                (
                    await session.execute(
                        select(workflow_node).where(
                            *scope_predicates(workflow_node, scope),
                            workflow_node.c.task_id == task_id,
                            workflow_node.c.node_id == node_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if node is None or node["status"] not in ("failed", "canceled", "skipped"):
                return False
            current_attempt = (
                await session.execute(
                    select(workflow_attempt.c.graph_run_id).where(
                        *scope_predicates(workflow_attempt, scope),
                        workflow_attempt.c.task_id == task_id,
                        workflow_attempt.c.node_id == node_id,
                        workflow_attempt.c.attempt == node["attempt"],
                    )
                )
            ).scalar_one()
            run_id = current_attempt or hashlib.sha256(f"{task_id}:{node_id}".encode()).hexdigest()
            graph = (
                (
                    await session.execute(
                        select(graph_run).where(
                            *scope_predicates(graph_run, scope),
                            graph_run.c.run_id == run_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                graph is None
                or graph["graph_version"] != "agent-workflow-v1"
                or graph["state_schema_version"] != 1
                or graph["current_checkpoint_id"] is None
            ):
                return False
            checkpoint = await session.scalar(
                select(graph_checkpoint.c.checkpoint_id).where(
                    *scope_predicates(graph_checkpoint, scope),
                    graph_checkpoint.c.run_id == run_id,
                    graph_checkpoint.c.checkpoint_ns == "",
                    graph_checkpoint.c.checkpoint_id == graph["current_checkpoint_id"],
                )
            )
            if checkpoint is None:
                return False
            events = (
                (
                    await session.execute(
                        select(workflow_event).where(
                            *scope_predicates(workflow_event, scope),
                            workflow_event.c.task_id == task_id,
                            workflow_event.c.node_id == node_id,
                            workflow_event.c.kind.in_(("action.reserved", "action.settled")),
                        )
                    )
                )
                .mappings()
                .all()
            )
            reserved = {
                event["payload"]["action_id"]
                for event in events
                if event["kind"] == "action.reserved"
            }
            settled = {
                event["payload"]["action_id"]
                for event in events
                if event["kind"] == "action.settled"
            }
            return not (reserved - settled)

    async def episode_facts(
        self, scope: ProjectScopeContext, episode: RecoveryEpisode
    ) -> dict[str, object]:
        """Read exact old-token process, checkpoint and action facts for inspection."""
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            task = (
                (
                    await session.execute(
                        select(workflow_task).where(
                            *scope_predicates(workflow_task, scope),
                            workflow_task.c.task_id == episode.task_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                task is None
                or task["status"] != "recovering"
                or task["lease_token"] != episode.execution_token
            ):
                return {"owner": None, "checkpoint_compatible": False, "unsettled": True}
            claims = (
                (
                    await session.execute(
                        select(workflow_event).where(
                            *scope_predicates(workflow_event, scope),
                            workflow_event.c.task_id == episode.task_id,
                            workflow_event.c.kind == "task.claimed",
                            workflow_event.c.payload["execution_token"].as_string()
                            == episode.execution_token,
                        )
                    )
                )
                .mappings()
                .all()
            )
            owner = claims[-1]["payload"].get("owner") if len(claims) == 1 else None
            actions = (
                (
                    await session.execute(
                        select(workflow_event).where(
                            *scope_predicates(workflow_event, scope),
                            workflow_event.c.task_id == episode.task_id,
                            workflow_event.c.payload["execution_token"].as_string()
                            == episode.execution_token,
                            workflow_event.c.kind.in_(("action.reserved", "action.settled")),
                        )
                    )
                )
                .mappings()
                .all()
            )
            reserved = {
                event["payload"]["action_id"]
                for event in actions
                if event["kind"] == "action.reserved"
            }
            settled = {
                event["payload"]["action_id"]
                for event in actions
                if event["kind"] == "action.settled"
            }
            nodes = (
                (
                    await session.execute(
                        select(workflow_node).where(
                            *scope_predicates(workflow_node, scope),
                            workflow_node.c.task_id == episode.task_id,
                            workflow_node.c.status == "running",
                        )
                    )
                )
                .mappings()
                .all()
            )
            if not nodes and len(claims) == 1:
                # The old epoch can have committed node.finished immediately
                # before dying, leaving only its scheduler/finalization handoff.
                finished = (
                    (
                        await session.execute(
                            select(workflow_event).where(
                                *scope_predicates(workflow_event, scope),
                                workflow_event.c.task_id == episode.task_id,
                                workflow_event.c.kind == "node.finished",
                                workflow_event.c.sequence > claims[0]["sequence"],
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                finished_attempts = {(event["node_id"], event["attempt"]) for event in finished}
                if finished_attempts:
                    nodes = [
                        node
                        for node in (
                            (
                                await session.execute(
                                    select(workflow_node).where(
                                        *scope_predicates(workflow_node, scope),
                                        workflow_node.c.task_id == episode.task_id,
                                        workflow_node.c.status == "succeeded",
                                    )
                                )
                            )
                            .mappings()
                            .all()
                        )
                        if (node["node_id"], node["attempt"]) in finished_attempts
                    ]
            compatible = bool(nodes)
            for node in nodes:
                current_attempt = (
                    await session.execute(
                        select(workflow_attempt.c.graph_run_id).where(
                            *scope_predicates(workflow_attempt, scope),
                            workflow_attempt.c.task_id == episode.task_id,
                            workflow_attempt.c.node_id == node["node_id"],
                            workflow_attempt.c.attempt == node["attempt"],
                        )
                    )
                ).scalar_one()
                run_id = (
                    current_attempt
                    or hashlib.sha256(f"{episode.task_id}:{node['node_id']}".encode()).hexdigest()
                )
                graph = (
                    (
                        await session.execute(
                            select(graph_run).where(
                                *scope_predicates(graph_run, scope),
                                graph_run.c.run_id == run_id,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    graph is None
                    or graph["graph_version"] != "agent-workflow-v1"
                    or graph["state_schema_version"] != 1
                    or graph["current_checkpoint_id"] is None
                ):
                    compatible = False
                    break
                checkpoint = await session.scalar(
                    select(graph_checkpoint.c.checkpoint_id).where(
                        *scope_predicates(graph_checkpoint, scope),
                        graph_checkpoint.c.run_id == run_id,
                        graph_checkpoint.c.checkpoint_ns == "",
                        graph_checkpoint.c.checkpoint_id == graph["current_checkpoint_id"],
                    )
                )
                if checkpoint is None:
                    compatible = False
                    break
            return {
                "owner": owner,
                "checkpoint_compatible": compatible,
                "unsettled": bool(reserved - settled),
            }

    async def stop_evidence(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        lease_token: str,
        target_node_id: str | None,
    ) -> bool:
        """No in-flight action, and each active target has a checkpoint after settlement."""
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        select(workflow_task)
                        .where(
                            *scope_predicates(workflow_task, scope),
                            workflow_task.c.task_id == task_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError("workflow task not found")
            self._require_lease(row, lease_token)
            nodes = (
                (
                    await session.execute(
                        select(workflow_node).where(
                            *scope_predicates(workflow_node, scope),
                            workflow_node.c.task_id == task_id,
                            workflow_node.c.status == "running",
                        )
                    )
                )
                .mappings()
                .all()
            )
            if target_node_id is not None:
                nodes = [node for node in nodes if node["node_id"] == target_node_id]
            events = (
                (
                    await session.execute(
                        select(workflow_event).where(
                            *scope_predicates(workflow_event, scope),
                            workflow_event.c.task_id == task_id,
                            workflow_event.c.payload["execution_token"].as_string() == lease_token,
                            workflow_event.c.kind.in_(("action.reserved", "action.settled")),
                        )
                    )
                )
                .mappings()
                .all()
            )
            target_ids = {node["node_id"] for node in nodes}
            if target_node_id is not None:
                target_ids.add(target_node_id)
            reserved = {
                event["payload"]["action_id"]
                for event in events
                if event["kind"] == "action.reserved"
                and (target_node_id is None or event["node_id"] in target_ids)
            }
            settled = {
                event["payload"]["action_id"]
                for event in events
                if event["kind"] == "action.settled"
                and (target_node_id is None or event["node_id"] in target_ids)
            }
            if reserved - settled:
                return False
            for node in nodes:
                current_attempt = (
                    await session.execute(
                        select(workflow_attempt.c.graph_run_id).where(
                            *scope_predicates(workflow_attempt, scope),
                            workflow_attempt.c.task_id == task_id,
                            workflow_attempt.c.node_id == node["node_id"],
                            workflow_attempt.c.attempt == node["attempt"],
                        )
                    )
                ).scalar_one()
                run_id = (
                    current_attempt
                    or hashlib.sha256(f"{task_id}:{node['node_id']}".encode()).hexdigest()
                )
                graph = (
                    (
                        await session.execute(
                            select(graph_run).where(
                                *scope_predicates(graph_run, scope),
                                graph_run.c.run_id == run_id,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    graph is None
                    or graph["graph_version"] != "agent-workflow-v1"
                    or graph["state_schema_version"] != 1
                    or graph["current_checkpoint_id"] is None
                ):
                    return False
                checkpoint = (
                    await session.execute(
                        select(graph_checkpoint.c.created_at).where(
                            *scope_predicates(graph_checkpoint, scope),
                            graph_checkpoint.c.run_id == run_id,
                            # LangGraph normalizes the top-level graph namespace to empty.
                            # The run_id already binds this checkpoint to the node.
                            graph_checkpoint.c.checkpoint_ns == "",
                            graph_checkpoint.c.checkpoint_id == graph["current_checkpoint_id"],
                        )
                    )
                ).scalar_one_or_none()
                if checkpoint is None:
                    return False
                last_settlement = max(
                    (
                        event["occurred_at"]
                        for event in events
                        if event["kind"] == "action.settled" and event["node_id"] == node["node_id"]
                    ),
                    default=None,
                )
                if last_settlement is not None and checkpoint < last_settlement:
                    return False
            return True

    async def applied_steers(
        self, scope: ProjectScopeContext, task_id: str, node_id: str
    ) -> tuple[str, ...]:
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(workflow_command).where(
                            *scope_predicates(workflow_command, scope),
                            workflow_command.c.task_id == task_id,
                            workflow_command.c.kind == "steer",
                        )
                    )
                )
                .mappings()
                .all()
            )
        ordered = sorted(
            (
                row
                for row in rows
                if row["receipt"]["status"] == "applied"
                and row["target_node_id"] in (None, node_id)
            ),
            key=lambda row: row["payload"]["accepted_sequence"],
        )
        return tuple(str(row["payload"]["text"]) for row in ordered)

    async def scan_work(
        self, scope: ProjectScopeContext, limit: int
    ) -> tuple[tuple[str, str], ...]:
        """Scan eligible work fairly; claims still recheck state under the task lock."""
        scope = require_project_scope(scope)
        if limit < 1:
            raise ValueError("workflow scan limit must be positive")
        now = datetime.now(UTC).replace(tzinfo=None)
        key = (scope.enterprise_id, scope.project_id)
        cursor = self._scan_cursors.get(key)
        eligible = select(workflow_task.c.task_id, workflow_task.c.status).where(
            *scope_predicates(workflow_task, scope),
            _native_sql(),
            workflow_task.c.status.in_(
                (
                    "queued",
                    "waiting_dependency",
                    "paused",
                    "awaiting_input",
                    "running",
                    "pausing",
                    "canceling",
                    "recovering",
                )
            ),
            (workflow_task.c.lease_until.is_(None) | (workflow_task.c.lease_until <= now)),
        )
        async with self._sessions() as session:
            rows: list[Row[Any]] = []
            if cursor is not None:
                rows = list(
                    (
                        await session.execute(
                            eligible.where(workflow_task.c.task_id > cursor)
                            .order_by(workflow_task.c.task_id)
                            .limit(limit)
                        )
                    ).all()
                )
            if len(rows) < limit:
                wrap = eligible
                if cursor is not None:
                    wrap = wrap.where(workflow_task.c.task_id <= cursor)
                rows.extend(
                    (
                        await session.execute(
                            wrap.order_by(workflow_task.c.task_id).limit(limit - len(rows))
                        )
                    ).all()
                )
        if rows:
            self._scan_cursors[key] = str(rows[-1].task_id)
        return tuple((str(row.task_id), str(row.status)) for row in rows)

    async def release_control(
        self, scope: ProjectScopeContext, task_id: str, lease_token: str
    ) -> None:
        """Release an idle control claim without changing task status."""
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        select(workflow_task)
                        .where(
                            *scope_predicates(workflow_task, scope),
                            workflow_task.c.task_id == task_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["lease_token"] != lease_token:
                return
            await session.execute(
                update(workflow_task)
                .where(
                    workflow_task.c.task_id == task_id,
                )
                .values(lease_owner=None, lease_token=None, lease_until=None)
            )

    async def rerun_task(
        self, scope: ProjectScopeContext, task_id: str, idempotency_key: str
    ) -> TaskSnapshot:
        """Create a fresh task with explicit provenance from a terminal task."""
        scope = require_project_scope(scope)
        require_identifier("task_id", task_id)
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(workflow_task.c.status, workflow_task.c.request_json).where(
                        *scope_predicates(workflow_task, scope),
                        workflow_task.c.task_id == task_id,
                    )
                )
            ).first()
        if row is None:
            raise LookupError("workflow task not found")
        if not _native_request(row.request_json):
            raise ValueError("observed workflow task cannot be rerun")
        if row.status not in ("succeeded", "failed", "canceled", "skipped"):
            raise ValueError("rerun requires a terminal task")
        request = dict(row.request_json)
        request["rerun_from_task_id"] = task_id
        return await self.create_task(scope, request, idempotency_key)
