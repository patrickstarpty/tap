"""Coordinate durable Chat Turn creation, task creation, link, and repair."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import cast

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.chat.application.workflow_bridge import WorkflowChatPort
from tap.modules.chat.domain.conversations import ConversationTurn, TurnInput
from tap.modules.workflows.application.selection import WorkflowSelection
from tap.modules.workflows.application.service import WorkflowService
from tap.modules.workflows.domain.commands import ControlCommand
from tap.modules.workflows.domain.models import TaskSnapshot
from tap.modules.workflows.ports.runtime import WorkflowWakeups
from tap.modules.workflows.ports.store import WorkflowStore


class WorkflowAdmissionCoordinator:
    def __init__(
        self,
        conversations: ConversationService,
        chat: WorkflowChatPort,
        store: WorkflowStore,
        *,
        max_model_calls: int = 40,
        max_seconds: int = 900,
        max_attempts: int = 3,
        max_parallel_agents: int = 3,
        max_cost_micros: int = 0,
        wakeups: WorkflowWakeups | None = None,
    ) -> None:
        if min(max_model_calls, max_seconds, max_attempts, max_parallel_agents) < 1:
            raise ValueError("workflow limits must be positive")
        if max_cost_micros < 0:
            raise ValueError("workflow cost limit must be non-negative")
        self._conversations = conversations
        self._chat = chat
        self._store = store
        self._controls = WorkflowService(store)
        self._selection = WorkflowSelection(conversations, store, self._controls)
        self.max_model_calls = max_model_calls
        self.max_seconds = max_seconds
        self.max_attempts = max_attempts
        self.max_parallel_agents = max_parallel_agents
        self.max_cost_micros = max_cost_micros
        self.enabled = True
        self._wakeups = wakeups

    async def _notify(self, task_id: str) -> None:
        if self._wakeups is not None:
            snapshot = await self._store.snapshot(self._conversations.scope, task_id)
            await self._wakeups.notify(
                self._conversations.scope, task_id, int(str(snapshot["last_sequence"]))
            )

    def _key(self, turn_id: str) -> str:
        scope = self._conversations.scope
        raw = json.dumps((scope.enterprise_id, scope.project_id, turn_id), separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    async def _cancel_command(self, task_id: str, turn_id: str) -> None:
        scope = self._conversations.scope
        command_id = hashlib.sha256(
            f"cancel:{scope.project_id}:{task_id}:{turn_id}".encode()
        ).hexdigest()
        old = await self._store.command(scope, task_id, command_id)
        if old is not None:
            await self._controls.submit(scope, old)
            await self._notify(task_id)
            return
        task = await self._store.snapshot(scope, task_id)
        await self._controls.submit(
            scope,
            ControlCommand(command_id, task_id, None, "cancel", int(task["plan_revision"]), None),
        )

    async def _ensure_task(self, conversation_id: str, turn: ConversationTurn) -> None:
        scope = self._conversations.scope
        frozen = turn.input_snapshot.value
        if frozen.workflow_root is None:
            raise ValueError("workflow root is required")
        request: dict[str, object] = {
            "goal": frozen.message,
            "conversation_id": conversation_id,
            "origin_turn_id": turn.turn_id,
            "input_digest": turn.input_snapshot.digest,
            "model_alias": frozen.model_alias,
            "root": asdict(frozen.workflow_root),
            "candidates": [asdict(item) for item in frozen.workflow_agents],
            "requested_ids": list(frozen.requested_agent_ids),
            "requested_agent_revision_id": frozen.requested_agent_revision_id,
            "resolved_resources": [asdict(item) for item in frozen.resolved_resources],
            "skill_revision_ids": list(frozen.skill_revision_ids),
            "skill_instructions": list(frozen.skill_instruction_templates),
            "skill_instruction_digests": list(frozen.skill_instruction_template_digests),
            "skill_catalog": [asdict(item) for item in frozen.skill_catalog],
        }
        request.update(self._limits(turn.input_snapshot.created_at))
        key = self._key(turn.turn_id)
        task = await self._store.task_by_key(scope, key)
        if task is None:
            try:
                task = await self._store.create_task(scope, request, key)
            except ValueError as error:
                if str(error) != "idempotency key payload conflict":
                    raise
                task = await self._store.task_by_key(scope, key)
                if task is None:
                    raise
        original = await self._store.task_request(scope, task.task_id)
        if (
            original.get("origin_turn_id") != turn.turn_id
            or original.get("conversation_id") != conversation_id
            or original.get("input_digest") != turn.input_snapshot.digest
        ):
            raise ValueError("existing workflow task does not match frozen origin Turn")
        cancel_pending = await self._chat.link_task(conversation_id, turn.turn_id, task.task_id)
        if cancel_pending:
            await self._cancel_command(task.task_id, turn.turn_id)
        else:
            await self._store.activate_task(scope, task.task_id, turn.turn_id)
        await self._notify(task.task_id)

    def _limits(self, created_at: datetime) -> dict[str, object]:
        return {
            "budget_limit": {
                "model_calls": self.max_model_calls,
                **({"cost_micros": self.max_cost_micros} if self.max_cost_micros else {}),
            },
            "deadline_at": (
                created_at.astimezone(UTC) + timedelta(seconds=self.max_seconds)
            ).isoformat(),
            "max_attempts": self.max_attempts,
            "max_parallel_agents": self.max_parallel_agents,
        }

    def _standalone_key(self, scope: ProjectScopeContext, idempotency_key: str) -> str:
        return hashlib.sha256(
            json.dumps(
                (scope.enterprise_id, scope.project_id, "standalone", idempotency_key),
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    async def existing_standalone(
        self, scope: ProjectScopeContext, public_request: dict[str, object], idempotency_key: str
    ) -> TaskSnapshot | None:
        """Replay an admitted request before mutable catalogs are consulted."""
        if scope != self._conversations.scope:
            raise ValueError("standalone workflow scope mismatch")
        existing = await self._store.task_by_key(
            scope, self._standalone_key(scope, idempotency_key)
        )
        if existing is None:
            return None
        prior = await self._store.task_request(scope, existing.task_id)
        if prior.get("public_request") != public_request:
            raise ValueError("idempotency key payload conflict")
        return existing

    async def create_standalone(
        self,
        scope: ProjectScopeContext,
        frozen: dict[str, object],
        idempotency_key: str,
    ) -> TaskSnapshot:
        """Admit already-authorized, server-frozen HTTP input without a Chat Turn."""
        if scope != self._conversations.scope or frozen.get("origin") != "native":
            raise ValueError("standalone workflow scope or origin mismatch")
        existing = await self.existing_standalone(
            scope, cast(dict[str, object], frozen["public_request"]), idempotency_key
        )
        if existing is not None:
            return existing
        request = {**frozen, **self._limits(datetime.now(UTC))}
        try:
            return await self._store.create_task(
                scope, request, self._standalone_key(scope, idempotency_key)
            )
        except ValueError as error:
            if str(error) != "idempotency key payload conflict":
                raise
            # A concurrent winner may have frozen a different deadline/catalog.
            # Compare only the caller-owned input; keep the winner's full digest strict.
            existing = await self.existing_standalone(
                scope, cast(dict[str, object], frozen["public_request"]), idempotency_key
            )
            if existing is None:
                raise
            return existing

    async def create(
        self, conversation_id: str, turn_id: str, idempotency_key: str, frozen: TurnInput
    ) -> ConversationTurn:
        turn = await self._conversations.create(conversation_id, turn_id, idempotency_key, frozen)
        await self._ensure_task(conversation_id, turn)
        return turn

    async def append(
        self, conversation_id: str, turn_id: str, idempotency_key: str, frozen: TurnInput
    ) -> ConversationTurn:
        turn = await self._conversations.append(conversation_id, turn_id, idempotency_key, frozen)
        await self._ensure_task(conversation_id, turn)
        return turn

    async def continue_task(
        self, conversation_id: str, turn_id: str, idempotency_key: str, frozen: TurnInput
    ) -> ConversationTurn:
        if frozen.workflow_task_id is None:
            raise ValueError("workflowTaskId is required")
        await self._selection.progress(
            self._conversations.scope, conversation_id, frozen.workflow_task_id
        )
        original = await self._store.task_request(
            self._conversations.scope, frozen.workflow_task_id
        )
        selected = tuple(original.get("requested_ids") or ())
        if (frozen.requested_agent_ids and set(frozen.requested_agent_ids) != set(selected)) or (
            frozen.requested_agent_revision_id is not None
            and frozen.requested_agent_revision_id != original.get("requested_agent_revision_id")
        ):
            raise ValueError("已有任务不能在追问中更改委派选择，可向已有节点补充要求")
        turn = await self._conversations.append(conversation_id, turn_id, idempotency_key, frozen)
        cancel_pending = await self._chat.link_task(
            conversation_id, turn.turn_id, frozen.workflow_task_id
        )
        if cancel_pending:
            await self._cancel_command(frozen.workflow_task_id, turn.turn_id)
        else:
            await self._selection.follow_up(
                self._conversations.scope, conversation_id, frozen, idempotency_key
            )
        await self._notify(frozen.workflow_task_id)
        return turn

    async def handles_turn(self, conversation_id: str, turn_id: str) -> bool:
        return await self._chat.is_workflow_turn(conversation_id, turn_id)

    async def request_cancel(self, conversation_id: str, turn_id: str) -> ConversationTurn:
        task_id = await self._chat.request_cancel(conversation_id, turn_id)
        if task_id is not None:
            await self._cancel_command(task_id, turn_id)
            await self._notify(task_id)
        conversation = await self._conversations.load(conversation_id)
        return next(turn for turn in conversation.turns if turn.turn_id == turn_id)

    async def repair(self, *, limit: int = 50) -> None:
        scope = self._conversations.scope
        for conversation_id, turn_id, task_id, canceled in await self._chat.repair_candidates(
            limit
        ):
            if task_id is None:
                conversation = await self._conversations.load(conversation_id)
                turn = next(turn for turn in conversation.turns if turn.turn_id == turn_id)
                if turn.input_snapshot.value.workflow_task_id is not None:
                    await self._chat.link_task(
                        conversation_id, turn_id, turn.input_snapshot.value.workflow_task_id
                    )
                    if canceled:
                        await self._cancel_command(
                            turn.input_snapshot.value.workflow_task_id, turn_id
                        )
                    else:
                        await self._selection.follow_up(
                            scope,
                            conversation_id,
                            turn.input_snapshot.value,
                            turn.client_request_id,
                        )
                    await self._notify(turn.input_snapshot.value.workflow_task_id)
                else:
                    await self._ensure_task(conversation_id, turn)
            elif canceled:
                await self._cancel_command(task_id, turn_id)
                await self._notify(task_id)
            else:
                conversation = await self._conversations.load(conversation_id)
                turn = next(turn for turn in conversation.turns if turn.turn_id == turn_id)
                frozen = turn.input_snapshot.value
                if frozen.workflow_task_id == task_id:
                    command_id = WorkflowSelection.command_id(
                        scope, conversation_id, task_id, turn.client_request_id
                    )
                    if await self._store.command(scope, task_id, command_id) is None:
                        await self._selection.follow_up(
                            scope, conversation_id, frozen, turn.client_request_id
                        )
                        await self._notify(task_id)
        # A crash after the Chat link but before task activation leaves no unlinked Turn.
        for task_id, conversation_id, origin_turn_id in await self._store.pending_admissions(
            scope, limit
        ):
            canceled = await self._chat.link_task(conversation_id, origin_turn_id, task_id)
            if canceled:
                await self._cancel_command(task_id, origin_turn_id)
            else:
                await self._store.activate_task(scope, task_id, origin_turn_id)
