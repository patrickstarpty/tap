"""Fenced, task-local native node transitions; every change is one transaction."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.adapters.mysql import MysqlWorkflowStore, _digest
from tap.modules.workflows.adapters.schema import (
    workflow_attempt,
    workflow_command,
    workflow_dependency,
    workflow_event,
    workflow_node,
    workflow_task,
)
from tap.modules.workflows.domain.delegation import DelegationRequest, child_result_summary
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values


def _identity(*parts: str) -> str:
    return hashlib.sha256(":\x00".join(parts).encode()).hexdigest()


class NativeStateStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def _locked(
        self, session: AsyncSession, scope: ProjectScopeContext, task_id: str, token: str
    ):
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
        MysqlWorkflowStore._require_lease(row, token)
        if row["status"] != "running":
            raise ValueError("workflow task is not running")
        return row

    @staticmethod
    async def _event(
        session: AsyncSession,
        scope: ProjectScopeContext,
        task: dict,
        kind: str,
        payload: dict[str, object],
        node_id: str | None = None,
        attempt: int = 0,
    ) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        await session.execute(
            workflow_event.insert().values(
                event_id=uuid4().hex,
                task_id=task["task_id"],
                sequence=task["next_sequence"],
                node_id=node_id,
                attempt=attempt,
                kind=kind,
                payload=payload,
                payload_digest=_digest(payload),
                occurred_at=now,
                **scope_values(scope),
            )
        )
        await session.execute(
            update(workflow_task)
            .where(workflow_task.c.task_id == task["task_id"])
            .values(next_sequence=task["next_sequence"] + 1, updated_at=now)
        )

    async def ensure_root(self, scope: ProjectScopeContext, task_id: str, token: str) -> str:
        scope = require_project_scope(scope)
        node_id = _identity(task_id, "root")
        async with self._sessions.begin() as session:
            task = await self._locked(session, scope, task_id, token)
            existing = (
                await session.execute(
                    select(workflow_node.c.node_id).where(
                        *scope_predicates(workflow_node, scope),
                        workflow_node.c.node_id == node_id,
                        workflow_node.c.task_id == task_id,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                return node_id
            now = datetime.now(UTC).replace(tzinfo=None)
            root = task["request_json"].get("root") or {}
            await session.execute(
                workflow_node.insert().values(
                    node_id=node_id,
                    task_id=task_id,
                    plan_revision=task["plan_revision"],
                    parent_node_id=None,
                    agent_revision_id=root.get("revision_id"),
                    role="root",
                    kind="agent",
                    runtime="native",
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
                    node_id=node_id,
                    attempt=1,
                    status="queued",
                    created_at=now,
                    **scope_values(scope),
                )
            )
            await self._event(
                session,
                scope,
                task,
                "node.created",
                {"node_id": node_id, "role": "root"},
                node_id,
                1,
            )
        return node_id

    async def delegate(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        token: str,
        parent_id: str,
        request: DelegationRequest,
        call_id: str,
    ) -> str:
        scope = require_project_scope(scope)
        node_id = _identity(task_id, parent_id, call_id)
        async with self._sessions.begin() as session:
            task = await self._locked(session, scope, task_id, token)
            parent = (
                (
                    await session.execute(
                        select(workflow_node).where(
                            *scope_predicates(workflow_node, scope),
                            workflow_node.c.task_id == task_id,
                            workflow_node.c.node_id == parent_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if parent is None:
                raise ValueError("delegation parent belongs to another task")
            if parent["status"] != "running":
                raise ValueError("delegation parent is not running")
            if parent["role"] != "root":
                raise ValueError("only the root may delegate")
            allowed_refs = {
                str(item["source_id"])
                for item in task["request_json"].get("resolved_resources", ())
                if isinstance(item, dict) and item.get("source_id")
            }
            if request.input_refs and not set(request.input_refs).issubset(allowed_refs):
                raise ValueError("delegation input refs are outside authorized resources")
            candidates = task["request_json"].get("candidates") or []
            candidate = next(
                (item for item in candidates if item.get("agent_id") == request.agent_id), None
            )
            if candidate is None:
                raise ValueError("delegation agent was not frozen in admission")
            existing = (
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
            payload: dict[str, object] = {
                "node_id": node_id,
                "parent_node_id": parent_id,
                "call_id": call_id,
                "agent_id": request.agent_id,
                "objective": request.objective,
                "input_refs": list(request.input_refs),
                "expected_output": request.expected_output,
                "depends_on": list(request.depends_on),
            }
            if existing is not None:
                prior = (
                    await session.execute(
                        select(workflow_event.c.payload).where(
                            *scope_predicates(workflow_event, scope),
                            workflow_event.c.task_id == task_id,
                            workflow_event.c.node_id == node_id,
                            workflow_event.c.kind == "node.delegated",
                        )
                    )
                ).scalar_one_or_none()
                if (
                    existing["agent_revision_id"] != candidate["revision_id"]
                    or existing["parent_node_id"] != parent_id
                    or prior != payload
                ):
                    raise ValueError("delegation call id conflict")
                return node_id
            dependencies = tuple(request.depends_on)
            if dependencies:
                rows = (
                    (
                        await session.execute(
                            select(workflow_node.c.node_id).where(
                                *scope_predicates(workflow_node, scope),
                                workflow_node.c.task_id == task_id,
                                workflow_node.c.node_id.in_(dependencies),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if set(rows) != set(dependencies):
                    raise ValueError("delegation dependencies must belong to the task")
            now = datetime.now(UTC).replace(tzinfo=None)
            await session.execute(
                workflow_node.insert().values(
                    node_id=node_id,
                    task_id=task_id,
                    plan_revision=task["plan_revision"],
                    parent_node_id=parent_id,
                    agent_revision_id=candidate["revision_id"],
                    role=request.agent_id,
                    kind="agent",
                    runtime="native",
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
                    node_id=node_id,
                    attempt=1,
                    status="queued",
                    created_at=now,
                    **scope_values(scope),
                )
            )
            for predecessor in dependencies:
                await session.execute(
                    workflow_dependency.insert().values(
                        dependency_id=uuid4().hex,
                        task_id=task_id,
                        from_node_id=predecessor,
                        to_node_id=node_id,
                        plan_revision=task["plan_revision"],
                        **scope_values(scope),
                    )
                )
            await self._event(
                session,
                scope,
                task,
                "node.delegated",
                payload,
                node_id,
                1,
            )
        return node_id

    async def release_task(
        self, scope: ProjectScopeContext, task_id: str, token: str, status: str
    ) -> None:
        if status not in ("waiting_dependency", "awaiting_input", "queued"):
            raise ValueError("invalid release status")
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            task = await self._locked(session, scope, task_id, token)
            now = datetime.now(UTC).replace(tzinfo=None)
            await session.execute(
                update(workflow_task)
                .where(
                    workflow_task.c.task_id == task_id,
                )
                .values(
                    status=status,
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                    updated_at=now,
                )
            )
            await self._event(session, scope, task, "task.yielded", {"status": status})

    async def start_node(
        self, scope: ProjectScopeContext, task_id: str, token: str, node_id: str
    ) -> int:
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            task = await self._locked(session, scope, task_id, token)
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
            if node is None or node["status"] not in ("queued", "running"):
                raise ValueError("node is not ready")
            if node["attempt"] > int(task["request_json"].get("max_attempts", 3)):
                raise ValueError("workflow node attempt budget exceeded")
            if node["status"] == "running":
                return int(node["attempt"])
            await session.execute(
                update(workflow_node)
                .where(workflow_node.c.node_id == node_id)
                .values(status="running")
            )
            await session.execute(
                update(workflow_attempt)
                .where(
                    workflow_attempt.c.task_id == task_id,
                    workflow_attempt.c.node_id == node_id,
                    workflow_attempt.c.attempt == node["attempt"],
                )
                .values(status="running")
            )
            await self._event(
                session,
                scope,
                task,
                "node.started",
                {"node_id": node_id, "attempt": node["attempt"]},
                node_id,
                node["attempt"],
            )
            return int(node["attempt"])

    async def wait_node(
        self, scope: ProjectScopeContext, task_id: str, token: str, node_id: str
    ) -> None:
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            task = await self._locked(session, scope, task_id, token)
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
            if node is None or node["status"] != "running":
                raise ValueError("node is not running")
            await session.execute(
                update(workflow_node)
                .where(workflow_node.c.node_id == node_id)
                .values(status="waiting_dependency")
            )
            await session.execute(
                update(workflow_attempt)
                .where(
                    workflow_attempt.c.task_id == task_id,
                    workflow_attempt.c.node_id == node_id,
                    workflow_attempt.c.attempt == node["attempt"],
                )
                .values(status="waiting_dependency")
            )
            await self._event(
                session,
                scope,
                task,
                "node.waiting",
                {"node_id": node_id, "reason": "children"},
                node_id,
                node["attempt"],
            )

    async def await_input(
        self, scope: ProjectScopeContext, task_id: str, token: str, node_id: str, question: str
    ) -> None:
        """Publish a replayable clarification only after the graph checkpoint committed."""
        if not question.strip():
            raise ValueError("clarification question is empty")
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            task = await self._locked(session, scope, task_id, token)
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
                .one()
            )
            if node["status"] != "running":
                raise ValueError("clarification node is not running")
            now = datetime.now(UTC).replace(tzinfo=None)
            await session.execute(
                update(workflow_node)
                .where(
                    workflow_node.c.node_id == node_id,
                )
                .values(status="awaiting_input")
            )
            await session.execute(
                update(workflow_attempt)
                .where(
                    workflow_attempt.c.task_id == task_id,
                    workflow_attempt.c.node_id == node_id,
                    workflow_attempt.c.attempt == node["attempt"],
                )
                .values(status="awaiting_input")
            )
            await session.execute(
                update(workflow_task)
                .where(
                    workflow_task.c.task_id == task_id,
                )
                .values(
                    status="awaiting_input",
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                    updated_at=now,
                )
            )
            await self._event(
                session,
                scope,
                task,
                "task.awaiting_input",
                {
                    "node_id": node_id,
                    "question": question,
                },
                node_id,
                node["attempt"],
            )

    async def fail_node(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        token: str,
        node_id: str,
        reason: str,
    ) -> None:
        """Persist a graph validation failure without terminating sibling branches."""
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            task = await self._locked(session, scope, task_id, token)
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
            if node is None or node["status"] != "running":
                raise ValueError("node is not running")
            await session.execute(
                update(workflow_node)
                .where(
                    workflow_node.c.node_id == node_id,
                )
                .values(status="failed")
            )
            await session.execute(
                update(workflow_attempt)
                .where(
                    *scope_predicates(workflow_attempt, scope),
                    workflow_attempt.c.task_id == task_id,
                    workflow_attempt.c.node_id == node_id,
                    workflow_attempt.c.attempt == node["attempt"],
                )
                .values(status="failed", error_snapshot={"reason": reason})
            )
            await self._event(
                session,
                scope,
                task,
                "node.failed",
                {"node_id": node_id, "reason": reason},
                node_id,
                node["attempt"],
            )

    async def finish_node(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        token: str,
        node_id: str,
        summary: str,
        outcome: str | None = None,
    ) -> None:
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            task = await self._locked(session, scope, task_id, token)
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
            if node is None or node["status"] != "running":
                raise ValueError("node is not running")
            await session.execute(
                update(workflow_node)
                .where(workflow_node.c.node_id == node_id)
                .values(status="succeeded")
            )
            await session.execute(
                update(workflow_attempt)
                .where(
                    workflow_attempt.c.task_id == task_id,
                    workflow_attempt.c.node_id == node_id,
                    workflow_attempt.c.attempt == node["attempt"],
                )
                .values(status="succeeded")
            )
            await self._event(
                session,
                scope,
                task,
                "node.finished",
                {
                    "node_id": node_id,
                    "summary": child_result_summary(node_id, summary),
                    "private_result": summary,
                    **({"outcome": outcome} if outcome is not None else {}),
                },
                node_id,
                node["attempt"],
            )

    async def unblock_parents(
        self, scope: ProjectScopeContext, task_id: str, token: str
    ) -> tuple[str, ...]:
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            task = await self._locked(session, scope, task_id, token)
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
            children: dict[str, list[str]] = {}
            for node in nodes:
                if node["parent_node_id"]:
                    children.setdefault(node["parent_node_id"], []).append(node["status"])
            unblocked = tuple(
                sorted(
                    node["node_id"]
                    for node in nodes
                    if node["status"] == "waiting_dependency"
                    and children.get(node["node_id"])
                    and all(status == "succeeded" for status in children[node["node_id"]])
                )
            )
            for node_id in unblocked:
                node = next(item for item in nodes if item["node_id"] == node_id)
                await session.execute(
                    update(workflow_node)
                    .where(workflow_node.c.node_id == node_id)
                    .values(status="queued")
                )
                await session.execute(
                    update(workflow_attempt)
                    .where(
                        workflow_attempt.c.task_id == task_id,
                        workflow_attempt.c.node_id == node_id,
                        workflow_attempt.c.attempt == node["attempt"],
                    )
                    .values(status="queued")
                )
            if unblocked:
                await self._event(
                    session, scope, task, "node.unblocked", {"node_ids": list(unblocked)}
                )
            return unblocked

    async def fail_task(
        self, scope: ProjectScopeContext, task_id: str, token: str, reason: str
    ) -> None:
        """Terminalize an expected hard limit after all started branches return."""
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            task = dict(await self._locked(session, scope, task_id, token))
            now = datetime.now(UTC).replace(tzinfo=None)
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
            for command in sorted(pending, key=lambda row: row["payload"]["accepted_sequence"]):
                if command["receipt"]["status"] not in ("accepted", "pending"):
                    continue
                receipt = {"status": "rejected", "reason": reason, "applied_plan_revision": None}
                await session.execute(
                    update(workflow_command)
                    .where(
                        workflow_command.c.command_id == command["command_id"],
                    )
                    .values(receipt=receipt)
                )
                await self._event(
                    session,
                    scope,
                    task,
                    "command.receipted",
                    {
                        "command_id": command["command_id"],
                        "receipt": receipt,
                    },
                    command["target_node_id"],
                )
                task["next_sequence"] += 1
            await session.execute(
                update(workflow_node)
                .where(
                    *scope_predicates(workflow_node, scope),
                    workflow_node.c.task_id == task_id,
                    workflow_node.c.status != "succeeded",
                )
                .values(status="failed")
            )
            await session.execute(
                update(workflow_attempt)
                .where(
                    *scope_predicates(workflow_attempt, scope),
                    workflow_attempt.c.task_id == task_id,
                    workflow_attempt.c.status != "succeeded",
                )
                .values(status="failed", error_snapshot={"reason": reason})
            )
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
                    updated_at=now,
                )
            )
            await self._event(session, scope, task, "task.failed", {"reason": reason})

    async def finish_task(
        self, scope: ProjectScopeContext, task_id: str, token: str, answer: str, outcome: str
    ) -> None:
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            task = dict(await self._locked(session, scope, task_id, token))
            remaining = (
                await session.execute(
                    select(workflow_node.c.node_id).where(
                        *scope_predicates(workflow_node, scope),
                        workflow_node.c.task_id == task_id,
                        workflow_node.c.status != "succeeded",
                    )
                )
            ).all()
            if remaining:
                raise ValueError("workflow task has unfinished nodes")
            # A follow-up accepted just before finalization must receive an
            # authoritative terminal rejection. Its command receipt and event
            # sequence commit under this same task row lock as completion.
            pending = (
                (
                    await session.execute(
                        select(workflow_command)
                        .where(
                            *scope_predicates(workflow_command, scope),
                            workflow_command.c.task_id == task_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .all()
            )
            for command in sorted(pending, key=lambda row: row["payload"]["accepted_sequence"]):
                if command["receipt"]["status"] not in ("accepted", "pending"):
                    continue
                receipt: dict[str, object] = {
                    "status": "rejected",
                    "reason": "terminal task requires a new task",
                    "applied_plan_revision": None,
                }
                await session.execute(
                    update(workflow_command)
                    .where(
                        workflow_command.c.command_id == command["command_id"],
                    )
                    .values(receipt=receipt)
                )
                await self._event(
                    session,
                    scope,
                    task,
                    "command.receipted",
                    {
                        "command_id": command["command_id"],
                        "receipt": receipt,
                    },
                    command["target_node_id"],
                )
                task["next_sequence"] += 1
            now = datetime.now(UTC).replace(tzinfo=None)
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task_id)
                .values(
                    status="succeeded",
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                    updated_at=now,
                )
            )
            await self._event(
                session,
                scope,
                task,
                "task.completed",
                {
                    "answer": answer,
                    "outcome": outcome,
                },
            )

    async def renew_task(self, scope: ProjectScopeContext, task_id: str, token: str) -> None:
        scope = require_project_scope(scope)
        async with self._sessions.begin() as session:
            await self._locked(session, scope, task_id, token)
            now = datetime.now(UTC).replace(tzinfo=None)
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task_id)
                .values(
                    lease_until=now + timedelta(minutes=5),
                    updated_at=now,
                )
            )
