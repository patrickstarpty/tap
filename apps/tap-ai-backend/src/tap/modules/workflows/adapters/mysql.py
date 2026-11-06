"""Atomic MySQL workflow snapshots, events and task budget accounting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import Result, RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext, require_identifier
from tap.modules.workflows.adapters.schema import (
    workflow_attempt,
    workflow_command,
    workflow_dependency,
    workflow_event,
    workflow_node,
    workflow_plan_revision,
    workflow_task,
)
from tap.modules.workflows.domain.commands import ControlCommand, command_disposition
from tap.modules.workflows.domain.dependencies import validate_dependency_dag
from tap.modules.workflows.domain.models import TaskSnapshot, WorkflowEvent, WorkflowStatus
from tap.modules.workflows.domain.transitions import transition
from tap.modules.workflows.ports.runtime import RecoveryEpisode, RuntimeInspection
from tap.platform.db.lock_contention import claim_or_nothing, claim_transaction
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values


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
                    raise ValueError("command node belongs to another task")
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
                now = datetime.now(UTC).replace(tzinfo=None)
                if task["status"] != "queued" or (
                    task["lease_until"] is not None
                    and task["lease_until"] > now
                    and task["lease_token"]
                    and task["lease_owner"]
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
                payload: dict[str, object] = {"owner": owner}
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
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task_id)
                .values(plan_revision=revision)
            )
        elif kind == "pause":
            if node_id is None:
                if fields["status"] != "running":
                    raise ValueError("task is not running")
                if (
                    receipt.get("stopped") is True
                    and receipt.get("checkpoint_compatible") is not True
                ):
                    raise ValueError("pause requires a compatible checkpoint")
                changes: dict[str, object] = {
                    "status": "paused" if receipt.get("stopped") is True else "pausing"
                }
                if receipt.get("stopped") is True:
                    changes.update(lease_owner=None, lease_token=None, lease_until=None)
                await session.execute(
                    update(workflow_task)
                    .where(workflow_task.c.task_id == task_id)
                    .values(**changes)
                )
            else:
                node = await self._command_node(session, scope, task_id, node_id)
                if node["status"] != "running":
                    raise ValueError("node is not running")
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
            await session.execute(
                workflow_attempt.insert().values(
                    attempt_id=uuid4().hex,
                    task_id=task_id,
                    node_id=node_id,
                    attempt=attempt,
                    status="queued",
                    checkpoint_ref=prior["checkpoint_ref"],
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
            payload: dict[str, object] = {"status": result, "runtime_outcome": inspection.outcome}
            await session.execute(
                workflow_event.insert().values(
                    event_id=uuid4().hex,
                    task_id=task_id,
                    sequence=task["next_sequence"],
                    node_id=None,
                    attempt=0,
                    kind="task.reconciled",
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
        if row.status not in ("succeeded", "failed", "canceled", "skipped"):
            raise ValueError("rerun requires a terminal task")
        request = dict(row.request_json)
        request["rerun_from_task_id"] = task_id
        return await self.create_task(scope, request, idempotency_key)
