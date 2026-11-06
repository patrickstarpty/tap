"""MySQL projection for source-owned observed Agent invocations."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.adapters.mysql import MysqlWorkflowStore, _digest
from tap.modules.workflows.adapters.schema import (
    workflow_attempt,
    workflow_event,
    workflow_node,
    workflow_plan_revision,
    workflow_task,
)
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values


def _identity(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


class MysqlAgentObservations:
    """Idempotent projection; native workers never own these task leases."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        store: MysqlWorkflowStore,
    ) -> None:
        self._sessions = sessions
        self._store = store
        self._repair_cursors: dict[tuple[str, str, str], str] = {}

    @staticmethod
    def task_id_for(scope: ProjectScopeContext, source_kind: str, invocation_key: str) -> str:
        key = "observed:" + _identity(
            scope.enterprise_id, scope.project_id, source_kind, invocation_key
        )
        return uuid5(NAMESPACE_URL, key).hex

    async def snapshot(self, scope: ProjectScopeContext, task_id: str):
        return await self._store.snapshot(scope, task_id)

    async def pending_source(
        self, scope: ProjectScopeContext, source_kind: str, limit: int
    ) -> tuple[tuple[str, str | None, str], ...]:
        if not 1 <= limit <= 100:
            raise ValueError("observation repair limit is invalid")
        kind = workflow_task.c.request_json["source_kind"].as_string()
        key = (scope.enterprise_id, scope.project_id, source_kind)
        cursor = self._repair_cursors.get(key)
        eligible = select(
            workflow_task.c.task_id,
            workflow_task.c.conversation_id,
            workflow_task.c.request_json,
        ).where(
            *scope_predicates(workflow_task, scope),
            kind == source_kind,
            workflow_task.c.status.in_(("queued", "running")),
        )
        async with self._sessions() as session:
            rows = []
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
                wrapped = eligible
                if cursor is not None:
                    wrapped = wrapped.where(workflow_task.c.task_id <= cursor)
                rows.extend(
                    (
                        await session.execute(
                            wrapped.order_by(workflow_task.c.task_id).limit(limit - len(rows))
                        )
                    ).all()
                )
        if rows:
            self._repair_cursors[key] = str(rows[-1].task_id)
        return tuple(
            (row.task_id, row.conversation_id, str(row.request_json["source_key"])) for row in rows
        )

    async def start(
        self,
        scope: ProjectScopeContext,
        *,
        source_kind: str,
        source_key: str,
        invocation_key: str,
        agent_key: str,
        agent_revision_id: str | None,
        display_name: str,
        conversation_id: str | None = None,
        parent_node_id: str | None = None,
        source_attempt: int = 1,
        source_claim_key: str | None = None,
    ) -> str:
        scope = require_project_scope(scope)
        is_default = source_kind == "chat" and agent_key == "orchestrator"
        request: dict[str, object] = {
            "origin": "observed",
            "goal": f"{source_kind} Agent invocation",
            "source_kind": source_kind,
            "source_key": source_key,
            "invocation_key": invocation_key,
            "agent_key": agent_key,
            "agent_revision_id": agent_revision_id,
            "display_name": display_name,
            "conversation_id": conversation_id,
        }
        if is_default:
            request["root"] = {"agent_id": "orchestrator", "name": "Tapper"}
        else:
            request["candidates"] = [{"agent_id": agent_key, "name": display_name}]
        key = "observed:" + _identity(
            scope.enterprise_id, scope.project_id, source_kind, invocation_key
        )
        task_id = uuid5(NAMESPACE_URL, key).hex
        node_id = uuid5(NAMESPACE_URL, f"{task_id}:{invocation_key}").hex
        digest = _digest(request)
        now = datetime.now(UTC).replace(tzinfo=None)
        role = "root" if is_default else agent_key
        try:
            async with self._sessions.begin() as session:
                existing = (
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
                if existing is not None:
                    if existing["request_digest"] != digest:
                        raise ValueError("observed invocation identity changed")
                    if existing["status"] not in ("succeeded", "failed", "canceled"):
                        current_node = (
                            (
                                await session.execute(
                                    select(workflow_node)
                                    .where(
                                        *scope_predicates(workflow_node, scope),
                                        workflow_node.c.task_id == task_id,
                                        workflow_node.c.parent_node_id.is_(None),
                                    )
                                    .with_for_update()
                                )
                            )
                            .mappings()
                            .one_or_none()
                        )
                        if current_node is not None:
                            known_claim = None
                            if source_claim_key is not None:
                                known_claim = (
                                    await session.execute(
                                        select(workflow_attempt.c.attempt_id).where(
                                            *scope_predicates(workflow_attempt, scope),
                                            workflow_attempt.c.task_id == task_id,
                                            workflow_attempt.c.node_id == current_node["node_id"],
                                            workflow_attempt.c.graph_run_id == source_claim_key,
                                        )
                                    )
                                ).scalar_one_or_none()
                            if known_claim is None and (
                                source_attempt > int(current_node["attempt"])
                                or source_claim_key is not None
                                and source_kind == "suggestions"
                                or source_claim_key is not None
                                and source_attempt == int(current_node["attempt"])
                            ):
                                next_attempt = max(source_attempt, int(current_node["attempt"]) + 1)
                                await session.execute(
                                    update(workflow_attempt)
                                    .where(
                                        workflow_attempt.c.task_id == task_id,
                                        workflow_attempt.c.node_id == current_node["node_id"],
                                        workflow_attempt.c.attempt == current_node["attempt"],
                                        workflow_attempt.c.status == "running",
                                    )
                                    .values(status="unknown")
                                )
                                await session.execute(
                                    workflow_attempt.insert().values(
                                        attempt_id=uuid4().hex,
                                        task_id=task_id,
                                        node_id=current_node["node_id"],
                                        attempt=next_attempt,
                                        status="running",
                                        graph_run_id=source_claim_key,
                                        created_at=now,
                                        **scope_values(scope),
                                    )
                                )
                                await session.execute(
                                    update(workflow_node)
                                    .where(
                                        workflow_node.c.node_id == current_node["node_id"],
                                    )
                                    .values(attempt=next_attempt, status="running")
                                )
                                await self._event(
                                    session,
                                    scope,
                                    task_id,
                                    current_node["node_id"],
                                    next_attempt,
                                    int(existing["next_sequence"]),
                                    "node.started",
                                    {
                                        "node_id": current_node["node_id"],
                                        "attempt": next_attempt,
                                    },
                                    now,
                                )
                                await session.execute(
                                    update(workflow_task)
                                    .where(
                                        workflow_task.c.task_id == task_id,
                                    )
                                    .values(
                                        next_sequence=int(existing["next_sequence"]) + 1,
                                        updated_at=now,
                                    )
                                )
                    return task_id
                await session.execute(
                    workflow_task.insert().values(
                        task_id=task_id,
                        idempotency_key=key,
                        request_digest=digest,
                        request_json=request,
                        goal=request["goal"],
                        conversation_id=conversation_id,
                        status="running",
                        plan_revision=1,
                        next_sequence=4,
                        budget_limit={},
                        budget_used={},
                        admission_ready=False,
                        created_at=now,
                        updated_at=now,
                        **scope_values(scope),
                    )
                )
                await session.execute(
                    workflow_plan_revision.insert().values(
                        revision_id=uuid4().hex,
                        task_id=task_id,
                        revision_number=1,
                        goal=request["goal"],
                        dependencies=[],
                        input_snapshot={},
                        created_at=now,
                        **scope_values(scope),
                    )
                )
                await session.execute(
                    workflow_node.insert().values(
                        node_id=node_id,
                        task_id=task_id,
                        plan_revision=1,
                        parent_node_id=parent_node_id,
                        agent_revision_id=agent_revision_id,
                        role=role,
                        kind="agent",
                        runtime=source_kind,
                        status="running",
                        attempt=source_attempt,
                        created_at=now,
                        **scope_values(scope),
                    )
                )
                await session.execute(
                    workflow_attempt.insert().values(
                        attempt_id=uuid4().hex,
                        task_id=task_id,
                        node_id=node_id,
                        attempt=source_attempt,
                        status="running",
                        graph_run_id=source_claim_key,
                        created_at=now,
                        **scope_values(scope),
                    )
                )
                await self._event(
                    session, scope, task_id, None, 0, 1, "task.created", {"status": "queued"}, now
                )
                await self._event(
                    session,
                    scope,
                    task_id,
                    node_id,
                    source_attempt,
                    2,
                    "node.started",
                    {"node_id": node_id, "attempt": source_attempt},
                    now,
                )
                await self._event(
                    session, scope, task_id, None, 0, 3, "task.status", {"status": "running"}, now
                )
        except IntegrityError:
            # A concurrent actual caller may have committed the same stable
            # identity. An unrelated FK/constraint failure remains an error.
            async with self._sessions() as session:
                existing = (
                    await session.execute(
                        select(workflow_task.c.request_digest).where(
                            *scope_predicates(workflow_task, scope),
                            workflow_task.c.task_id == task_id,
                        )
                    )
                ).scalar_one_or_none()
            if existing is None:
                raise
            if existing != digest:
                raise ValueError("observed invocation identity changed") from None
        return task_id

    @asynccontextmanager
    async def record_agent_invocation(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        agent_key: str,
        parent_node_id: str | None,
        invocation_key: str,
        *,
        agent_revision_id: str | None = None,
        source_kind: str = "legacy",
        source_attempt: int = 1,
    ) -> AsyncIterator[str]:
        """Record actual entry once. Context exit is not source settlement."""
        node_id = uuid5(NAMESPACE_URL, f"{task_id}:{invocation_key}").hex
        now = datetime.now(UTC).replace(tzinfo=None)
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
            if row is None or row["request_json"].get("origin") != "observed":
                raise ValueError("observed task required")
            if (
                parent_node_id is None
                and row["request_json"].get("invocation_key") != invocation_key
            ):
                raise ValueError("observed invocation identity changed")
            existing = (
                await session.execute(
                    select(workflow_node.c.node_id).where(
                        *scope_predicates(workflow_node, scope),
                        workflow_node.c.task_id == task_id,
                        workflow_node.c.node_id == node_id,
                    )
                )
            ).first()
            if existing is None and row["status"] in ("queued", "running"):
                role = (
                    "root" if agent_key == "orchestrator" and source_kind == "chat" else agent_key
                )
                await session.execute(
                    workflow_node.insert().values(
                        node_id=node_id,
                        task_id=task_id,
                        plan_revision=1,
                        parent_node_id=parent_node_id,
                        agent_revision_id=agent_revision_id,
                        role=role,
                        kind="agent",
                        runtime=source_kind,
                        status="running",
                        attempt=source_attempt,
                        created_at=now,
                        **scope_values(scope),
                    )
                )
                await session.execute(
                    workflow_attempt.insert().values(
                        attempt_id=uuid4().hex,
                        task_id=task_id,
                        node_id=node_id,
                        attempt=source_attempt,
                        status="running",
                        created_at=now,
                        **scope_values(scope),
                    )
                )
                await self._event(
                    session,
                    scope,
                    task_id,
                    node_id,
                    source_attempt,
                    int(row["next_sequence"]),
                    "node.started",
                    {"node_id": node_id, "attempt": source_attempt},
                    now,
                )
                if row["status"] == "queued":
                    await self._event(
                        session,
                        scope,
                        task_id,
                        None,
                        0,
                        int(row["next_sequence"]) + 1,
                        "task.status",
                        {"status": "running"},
                        now,
                    )
                await session.execute(
                    update(workflow_task)
                    .where(workflow_task.c.task_id == task_id)
                    .values(
                        status="running",
                        next_sequence=int(row["next_sequence"])
                        + (2 if row["status"] == "queued" else 1),
                        updated_at=now,
                    )
                )
        yield node_id

    async def finish_node(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        node_id: str,
        *,
        succeeded: bool,
    ) -> bool:
        """Settle a known child operation without settling the parent Turn."""
        now = datetime.now(UTC).replace(tzinfo=None)
        async with self._sessions.begin() as session:
            task = (
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
            if task is None or task["request_json"].get("origin") != "observed":
                raise ValueError("observed task required")
            node = (
                (
                    await session.execute(
                        select(workflow_node)
                        .where(
                            *scope_predicates(workflow_node, scope),
                            workflow_node.c.task_id == task_id,
                            workflow_node.c.node_id == node_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if node is None or node["status"] != "running":
                return False
            status = "succeeded" if succeeded else "failed"
            await session.execute(
                update(workflow_node)
                .where(
                    workflow_node.c.node_id == node_id,
                )
                .values(status=status)
            )
            await session.execute(
                update(workflow_attempt)
                .where(
                    workflow_attempt.c.task_id == task_id,
                    workflow_attempt.c.node_id == node_id,
                    workflow_attempt.c.attempt == node["attempt"],
                )
                .values(status=status)
            )
            await self._event(
                session,
                scope,
                task_id,
                node_id,
                int(node["attempt"]),
                int(task["next_sequence"]),
                "node.finished" if succeeded else "node.failed",
                {"node_id": node_id},
                now,
            )
            await session.execute(
                update(workflow_task)
                .where(
                    workflow_task.c.task_id == task_id,
                )
                .values(next_sequence=int(task["next_sequence"]) + 1, updated_at=now)
            )
            return True

    async def finish(self, scope: ProjectScopeContext, task_id: str, source_state: str) -> bool:
        """Called only after accepted source CAS or durable source-fact repair."""
        async with self._sessions.begin() as session:
            return await self.finish_in_transaction(session, scope, task_id, source_state)

    async def finish_in_transaction(
        self,
        session: AsyncSession,
        scope: ProjectScopeContext,
        task_id: str,
        source_state: str,
        *,
        missing_ok: bool = False,
        winning_claim_key: str | None = None,
    ) -> bool:
        """Project after the source's accepted CAS in its own AsyncSession."""
        status = {
            "completed": "succeeded",
            "abstained": "succeeded",
            "DRAFT_READY": "succeeded",
            "failed": "failed",
            "FAILED": "failed",
            "canceled": "canceled",
            "CANCELED": "canceled",
        }.get(source_state)
        if status is None:
            raise ValueError("source state is not terminal")
        scope = require_project_scope(scope)
        now = datetime.now(UTC).replace(tzinfo=None)
        row = (
            (
                await session.execute(
                    select(workflow_task)
                    .where(
                        *scope_predicates(workflow_task, scope), workflow_task.c.task_id == task_id
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None and missing_ok:
            return False
        if row is None or row["request_json"].get("origin") != "observed":
            raise ValueError("observed task required")
        if row["status"] in ("succeeded", "failed", "canceled"):
            return False
        nodes = (
            (
                await session.execute(
                    select(workflow_node).where(
                        *scope_predicates(workflow_node, scope), workflow_node.c.task_id == task_id
                    )
                )
            )
            .mappings()
            .all()
        )
        if not nodes:
            return False
        node = next((item for item in nodes if item["parent_node_id"] is None), nodes[0])
        winning_attempt: int | None = int(node["attempt"])
        if winning_claim_key is not None:
            claim_attempt = (
                await session.execute(
                    select(workflow_attempt.c.attempt).where(
                        *scope_predicates(workflow_attempt, scope),
                        workflow_attempt.c.task_id == task_id,
                        workflow_attempt.c.node_id == node["node_id"],
                        workflow_attempt.c.graph_run_id == winning_claim_key,
                    )
                )
            ).scalar_one_or_none()
            winning_attempt = int(claim_attempt) if claim_attempt is not None else None
            if winning_attempt is not None:
                await session.execute(
                    update(workflow_attempt)
                    .where(
                        workflow_attempt.c.task_id == task_id,
                        workflow_attempt.c.node_id == node["node_id"],
                        workflow_attempt.c.attempt != winning_attempt,
                        workflow_attempt.c.status == "running",
                    )
                    .values(status="unknown")
                )
        node_kind = {
            "succeeded": "node.finished",
            "failed": "node.failed",
            "canceled": "node.canceled",
        }[status]
        task_kind = {
            "succeeded": "task.completed",
            "failed": "task.failed",
            "canceled": "task.canceled",
        }[status]
        next_sequence = int(row["next_sequence"])
        for child in sorted(nodes, key=lambda item: item["node_id"]):
            if child["parent_node_id"] is None or child["status"] in (
                "succeeded",
                "failed",
                "canceled",
                "skipped",
                "unknown",
            ):
                continue
            # The source has ended, but no accepted result exists for this
            # operation. Preserve its current attempt as unknown rather than
            # presenting a terminal task with an apparently running child.
            await session.execute(
                update(workflow_node)
                .where(workflow_node.c.node_id == child["node_id"])
                .values(status="unknown")
            )
            await session.execute(
                update(workflow_attempt)
                .where(
                    workflow_attempt.c.task_id == task_id,
                    workflow_attempt.c.node_id == child["node_id"],
                    workflow_attempt.c.attempt == child["attempt"],
                    workflow_attempt.c.status.in_(("queued", "running")),
                )
                .values(status="unknown")
            )
            await self._event(
                session,
                scope,
                task_id,
                child["node_id"],
                int(child["attempt"]),
                next_sequence,
                "node.outcome_unknown",
                {"node_id": child["node_id"]},
                now,
            )
            next_sequence += 1
        if winning_attempt is None:
            # A source retry can complete without dispatching the Agent at all.
            # Its accepted CAS still closes the logical task, while every prior
            # actual invocation keeps its own evidence and cannot be called a
            # success of this no-model lease.
            await session.execute(
                update(workflow_attempt)
                .where(
                    workflow_attempt.c.task_id == task_id,
                    workflow_attempt.c.node_id == node["node_id"],
                    workflow_attempt.c.status.in_(("queued", "running")),
                )
                .values(status="unknown")
            )
            if node["status"] in ("queued", "running"):
                await session.execute(
                    update(workflow_node)
                    .where(workflow_node.c.node_id == node["node_id"])
                    .values(status="unknown")
                )
                await self._event(
                    session,
                    scope,
                    task_id,
                    node["node_id"],
                    int(node["attempt"]),
                    next_sequence,
                    "node.outcome_unknown",
                    {"node_id": node["node_id"]},
                    now,
                )
                next_sequence += 1
            await self._event(
                session,
                scope,
                task_id,
                None,
                0,
                next_sequence,
                task_kind,
                {"status": status, "outcome": source_state},
                now,
            )
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task_id)
                .values(status=status, next_sequence=next_sequence + 1, updated_at=now)
            )
            return True
        await session.execute(
            update(workflow_node)
            .where(workflow_node.c.node_id == node["node_id"])
            .values(status=status, attempt=winning_attempt)
        )
        await session.execute(
            update(workflow_attempt)
            .where(
                workflow_attempt.c.task_id == task_id,
                workflow_attempt.c.node_id == node["node_id"],
                workflow_attempt.c.attempt == winning_attempt,
            )
            .values(status=status)
        )
        await self._event(
            session,
            scope,
            task_id,
            node["node_id"],
            winning_attempt,
            next_sequence,
            node_kind,
            {"node_id": node["node_id"]},
            now,
        )
        await self._event(
            session,
            scope,
            task_id,
            None,
            0,
            next_sequence + 1,
            task_kind,
            {"status": status, "outcome": source_state},
            now,
        )
        await session.execute(
            update(workflow_task)
            .where(workflow_task.c.task_id == task_id)
            .values(status=status, next_sequence=next_sequence + 2, updated_at=now)
        )
        return True

    async def _event(
        self,
        session: AsyncSession,
        scope: ProjectScopeContext,
        task_id: str,
        node_id: str | None,
        attempt: int,
        sequence: int,
        kind: str,
        payload: dict[str, object],
        now: datetime,
    ) -> None:
        await session.execute(
            workflow_event.insert().values(
                event_id=uuid4().hex,
                task_id=task_id,
                sequence=sequence,
                node_id=node_id,
                attempt=attempt,
                kind=kind,
                payload=payload,
                payload_digest=_digest(payload),
                occurred_at=now,
                **scope_values(scope),
            )
        )
