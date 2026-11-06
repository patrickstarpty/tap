"""Admission persists the effective frozen specialist selection."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.domain.conversations import ConversationTurn, TurnInput, TurnInputSnapshot
from tap.modules.workflows.application.admission import WorkflowAdmissionCoordinator
from tap.modules.workflows.domain.models import TaskSnapshot, WorkflowStatus
from tap.modules.workflows.ports.catalog import AgentSnapshot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_ids", "effective_ids"),
    [((), ("qae",)), (("ba",), ("ba", "qae"))],
)
async def test_legacy_revision_is_in_effective_persisted_selection(raw_ids, effective_ids):
    root = AgentSnapshot("orchestrator", "root-v1", "Tapper", "", "", (), "qwen-plus", ())
    candidates = tuple(
        AgentSnapshot(agent_id, f"{agent_id}-v1", agent_id, "", "", (), "qwen-plus", ())
        for agent_id in effective_ids
    )
    value = TurnInput(
        message="Review",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        requested_agent_ids=raw_ids,
        requested_agent_revision_id="qae-v1",
        delegate_ids=effective_ids,
        workflow_root=root,
        workflow_agents=candidates,
    )
    turn = ConversationTurn(
        "turn-selection",
        "request-selection",
        1,
        "queued",
        TurnInputSnapshot.create(
            snapshot_id="snapshot-selection",
            project_id=VALIDATION_SCOPE.project_id,
            turn_id="turn-selection",
            value=value,
            now=datetime.now(UTC),
        ),
    )

    class Store:
        request = None

        async def task_by_key(self, scope, key):
            return None

        async def create_task(self, scope, request, key):
            self.request = request
            return TaskSnapshot(
                "task-selection", scope.project_id, "chat-selection", 1, WorkflowStatus.QUEUED, 1
            )

        async def task_request(self, scope, task_id):
            return self.request

        async def activate_task(self, scope, task_id, turn_id):
            return None

    class Chat:
        async def link_task(self, conversation_id, turn_id, task_id):
            return False

    store = Store()
    coordinator = WorkflowAdmissionCoordinator(
        SimpleNamespace(scope=VALIDATION_SCOPE), Chat(), store
    )
    await coordinator._ensure_task("chat-selection", turn)
    assert store.request["requested_ids"] == list(effective_ids)
    assert store.request["requested_agent_revision_id"] == "qae-v1"
    assert store.request["execution_window_seconds"] == coordinator.max_seconds
