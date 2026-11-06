"""Read existing task progress and submit scoped follow-up requirements."""

from __future__ import annotations

import hashlib
import json

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.chat.domain.conversations import TurnInput
from tap.modules.workflows.application.service import WorkflowService
from tap.modules.workflows.domain.commands import ControlCommand
from tap.modules.workflows.ports.store import WorkflowStore


class WorkflowSelection:
    def __init__(
        self,
        conversations: ConversationService,
        store: WorkflowStore,
        controls: WorkflowService,
    ) -> None:
        self._conversations = conversations
        self._store = store
        self._controls = controls

    async def progress(
        self, scope: ProjectScopeContext, conversation_id: str, task_id: str
    ) -> dict[str, object]:
        if scope != self._conversations.scope:
            raise ValueError("conversation scope mismatch")
        await self._conversations.load(conversation_id)
        snapshot = await self._store.snapshot(scope, task_id)
        if (
            snapshot.get("project_id") != scope.project_id
            or snapshot.get("conversation_id") != conversation_id
        ):
            raise ValueError("task conversation mismatch")
        return snapshot

    async def follow_up(
        self,
        scope: ProjectScopeContext,
        conversation_id: str,
        frozen: TurnInput,
        idempotency_key: str,
        *,
        target_node_id: str | None = None,
    ) -> dict[str, object]:
        task_id = frozen.workflow_task_id
        if task_id is None or not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("existing task and bounded idempotency key are required")
        if frozen.actor_id != scope.actor_id or frozen.identity_mode != scope.identity_mode.value:
            raise ValueError("conversation scope mismatch")
        snapshot = await self.progress(scope, conversation_id, task_id)
        plan_revision = snapshot["plan_revision"]
        if type(plan_revision) is not int:
            raise ValueError("task plan revision unavailable")
        command_id = self.command_id(scope, conversation_id, task_id, idempotency_key)
        existing = await self._store.command(scope, task_id, command_id)
        if existing is not None:
            self._validate_replay(existing, frozen.message, target_node_id)
            return await self._controls.submit(scope, existing)
        command = ControlCommand(
            command_id=command_id,
            task_id=task_id,
            target_node_id=target_node_id,
            kind="steer",
            expected_plan_revision=plan_revision,
            text=frozen.message,
        )
        try:
            return await self._controls.submit(scope, command)
        except ValueError as error:
            if str(error) != "command id payload conflict":
                raise
            # Another request may have submitted this ID after our lookup, using
            # the plan revision it observed. Keep the winner's complete command.
            winner = await self._store.command(scope, task_id, command_id)
            if winner is None:
                raise
            self._validate_replay(winner, frozen.message, target_node_id)
            return await self._controls.submit(scope, winner)

    @staticmethod
    def command_id(
        scope: ProjectScopeContext, conversation_id: str, task_id: str, idempotency_key: str
    ) -> str:
        material = json.dumps(
            (scope.enterprise_id, scope.project_id, conversation_id, task_id, idempotency_key),
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode()).hexdigest()

    @staticmethod
    def _validate_replay(command: ControlCommand, message: str, target_node_id: str | None) -> None:
        if (
            command.kind != "steer"
            or command.target_node_id != target_node_id
            or command.text != message
        ):
            raise ValueError("command id payload conflict")
