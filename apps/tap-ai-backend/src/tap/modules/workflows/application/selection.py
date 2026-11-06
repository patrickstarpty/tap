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
        material = json.dumps(
            (
                scope.enterprise_id,
                scope.project_id,
                conversation_id,
                task_id,
                idempotency_key,
            ),
            separators=(",", ":"),
        )
        command_id = hashlib.sha256(material.encode()).hexdigest()
        existing = await self._store.command(scope, task_id, command_id)
        if existing is not None:
            if (
                existing.kind != "steer"
                or existing.target_node_id != target_node_id
                or existing.text != frozen.message
            ):
                raise ValueError("command id payload conflict")
            return await self._controls.submit(scope, existing)
        command = ControlCommand(
            command_id=command_id,
            task_id=task_id,
            target_node_id=target_node_id,
            kind="steer",
            expected_plan_revision=plan_revision,
            text=frozen.message,
        )
        return await self._controls.submit(scope, command)
