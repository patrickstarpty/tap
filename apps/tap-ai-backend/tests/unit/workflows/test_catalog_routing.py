import asyncio
import json
from dataclasses import replace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.ai.application.assets import (
    VALIDATION_OUTPUT_SCHEMA,
    ApprovedAssetCatalog,
    workflow_asset_seed,
)
from tap.modules.ai.domain.assets import AiAgentRevision, AssetRevisionStatus, text_digest
from tap.modules.ai.domain.models import schema_digest
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.domain.conversations import TurnInput
from tap.modules.workflows.adapters.asset_catalog import AssetWorkflowCatalog
from tap.modules.workflows.application.routing import RouteDecision, merge_route, requested_delegate
from tap.modules.workflows.application.selection import WorkflowSelection
from tap.modules.workflows.application.service import WorkflowService
from tap.modules.workflows.ports.catalog import AgentSnapshot


def test_selected_agent_is_delegate_not_replacement_root():
    assert requested_delegate("qae", ("qae",)) == {"root": "orchestrator", "delegate": "qae"}


def test_default_assistant_needs_no_agent_selection():
    assert requested_delegate(None, ()) == {"root": "orchestrator", "delegate": None}


@pytest.mark.parametrize("agent_id", ["orchestrator", "missing"])
def test_system_root_or_unknown_cannot_be_requested_as_delegate(agent_id):
    with pytest.raises(ValueError):
        requested_delegate(agent_id, ("orchestrator", "qae"))


def test_model_suggestion_routes_script_generation_to_coding():
    suggested = RouteDecision("coding", "generate test script", 0.95, ("code.write",))
    assert merge_route(explicit_kind=None, suggested=suggested, coding_available=True) == suggested


def test_code_explanation_stays_native_even_with_coding_suggestion():
    suggested = RouteDecision("coding", "code mentioned", 0.9, ("code.write",))
    result = merge_route(explicit_kind="native", suggested=suggested, coding_available=True)
    assert result.kind == "native"


def test_published_artifact_execution_uses_execute_route():
    result = merge_route(explicit_kind="execute", suggested=None, coding_available=True)
    assert result.kind == "execute"


def test_coding_unavailable_is_explicit():
    with pytest.raises(ValueError, match="coding capability unavailable"):
        merge_route(explicit_kind="coding", suggested=None, coding_available=False)


def test_low_confidence_escalation_requires_clarification():
    suggested = RouteDecision("coding", "uncertain", 0.3, ("code.write",))
    with pytest.raises(ValueError, match="clarification required"):
        merge_route(explicit_kind=None, suggested=suggested, coding_available=True)


def _agent(asset_id, revision_id, name, status=AssetRevisionStatus.ENABLED):
    instruction = "Use approved knowledge."
    return AiAgentRevision(
        revision_id=revision_id,
        asset_id=asset_id,
        display_name=name,
        description="Analyze quality evidence",
        scope=VALIDATION_SCOPE,
        content_digest=text_digest(revision_id),
        system_instruction_digest=text_digest(instruction),
        tool_allowlist=frozenset({"knowledge.answer"}),
        output_schema_digest=schema_digest(VALIDATION_OUTPUT_SCHEMA),
        status=status,
        system_instruction=instruction,
        output_schema_json=json.dumps(
            VALIDATION_OUTPUT_SCHEMA, sort_keys=True, separators=(",", ":")
        ),
    )


def test_seed_has_versioned_internal_root_and_four_professional_roles():
    seed = workflow_asset_seed(VALIDATION_SCOPE)
    agents = {item.asset_id: item for item in seed.agents}
    assert {"orchestrator", "ba", "sde", "qae", "sdet"} <= agents.keys()
    for asset_id in ("ba", "sde", "qae", "sdet"):
        assert agents[asset_id].description
        assert agents[asset_id].system_instruction
        assert agents[asset_id].revision_id.endswith("-v1")
    assert "统筹" in agents["orchestrator"].display_name


def test_agent_snapshot_restores_tuple_fields_from_json():
    restored = AgentSnapshot.from_json(
        {
            "agent_id": "qae",
            "revision_id": "qae-v1",
            "name": "QAE",
            "description": "Review",
            "instruction": "Review independently",
            "tool_ids": ["knowledge.search"],
            "model_alias": None,
            "skill_revision_ids": ["citation-v1"],
        }
    )
    assert restored.tool_ids == ("knowledge.search",)
    assert restored.skill_revision_ids == ("citation-v1",)


def test_legacy_turn_material_omits_default_workflow_fields():
    value = TurnInput(
        message="Hello",
        actor_id="actor",
        identity_mode="validation",
        model_alias="qwen-plus",
    )
    material = value.material(project_id="project", turn_id="turn")
    for key in (
        "requested_agent_ids",
        "requested_agent_revision_id",
        "delegate_ids",
        "workflow_task_id",
        "workflow_root",
        "workflow_agents",
        "route_decision",
    ):
        assert key not in material


@pytest.mark.asyncio
async def test_catalog_freezes_all_candidates_and_keeps_duplicate_names_by_id():
    catalog = AssetWorkflowCatalog(
        ApprovedAssetCatalog(
            agents=(
                _agent("orchestrator", "orchestrator-v1", "Tapper"),
                _agent("qae", "qae-v1", "Quality"),
                _agent("custom", "custom-v1", "Quality"),
            ),
            skills=(),
        )
    )
    frozen = await catalog.freeze_agents(VALIDATION_SCOPE, ("qae", "custom"))
    assert tuple(item.agent_id for item in frozen.candidates) == ("custom", "qae")
    assert frozen.requested_ids == ("qae", "custom")
    assert frozen.root.agent_id == "orchestrator"
    assert frozen.root.revision_id == "orchestrator-v1"
    assert frozen.candidates[0].name == frozen.candidates[1].name


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_id", ["orchestrator", "disabled", "missing"])
async def test_catalog_rejects_root_disabled_and_unknown_selection(requested_id):
    catalog = AssetWorkflowCatalog(
        ApprovedAssetCatalog(
            agents=(
                _agent("orchestrator", "orchestrator-v1", "Tapper"),
                _agent("disabled", "disabled-v1", "QAE", AssetRevisionStatus.DISABLED),
            ),
            skills=(),
        )
    )
    with pytest.raises(ValueError, match="delegate unavailable"):
        await catalog.freeze_agents(VALIDATION_SCOPE, (requested_id,))


@pytest.mark.asyncio
async def test_catalog_rejects_other_project_agent():
    other_scope = ProjectScopeContext(
        enterprise_id="local",
        project_id="other-project",
        actor_id="other-actor",
        identity_mode=IdentityMode.VALIDATION,
    )
    other = _agent("outside", "outside-v1", "Outside")
    catalog = AssetWorkflowCatalog(
        ApprovedAssetCatalog(
            agents=(
                _agent("orchestrator", "orchestrator-v1", "Tapper"),
                replace(other, scope=other_scope),
            ),
            skills=(),
        )
    )
    with pytest.raises(ValueError, match="delegate unavailable"):
        await catalog.freeze_agents(VALIDATION_SCOPE, ("outside",))


class _TaskStore:
    def __init__(self):
        self.created = 0
        self.commands = []
        self.revision = 2
        self.request_kind = "native"

    async def snapshot(self, scope, task_id):
        assert scope == VALIDATION_SCOPE
        assert task_id == "task-1"
        return {
            "task_id": task_id,
            "project_id": scope.project_id,
            "conversation_id": "conversation-1",
            "plan_revision": self.revision,
            "status": "running",
            "request_json": {"kind": self.request_kind},
        }

    async def create_task(self, *_args):
        self.created += 1
        raise AssertionError("continuation must not create a task")

    async def submit_command(self, scope, command):
        assert scope == VALIDATION_SCOPE
        for existing in self.commands:
            if existing.command_id == command.command_id:
                if existing != command:
                    raise ValueError("command id payload conflict")
                return {"status": "accepted", "command_id": command.command_id}
        self.commands.append(command)
        return {"status": "accepted", "command_id": command.command_id}

    async def command(self, scope, task_id, command_id):
        assert scope == VALIDATION_SCOPE
        assert task_id == "task-1"
        return next((item for item in self.commands if item.command_id == command_id), None)


@pytest.mark.asyncio
async def test_scoped_progress_reads_existing_task_without_creating_work():
    conversations = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    await conversations.create(
        "conversation-1",
        "turn-1",
        "first",
        TurnInput("Start", VALIDATION_SCOPE.actor_id, "validation", "qwen-plus"),
    )
    store = _TaskStore()
    selection = WorkflowSelection(conversations, store, WorkflowService(store))
    progress = await selection.progress(VALIDATION_SCOPE, "conversation-1", "task-1")
    assert progress["status"] == "running"
    assert store.created == 0
    assert store.commands == []


@pytest.mark.asyncio
async def test_follow_up_submits_idempotent_steer_for_same_task_and_frozen_message():
    conversations = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    await conversations.create(
        "conversation-1",
        "turn-1",
        "first",
        TurnInput("Start", VALIDATION_SCOPE.actor_id, "validation", "qwen-plus"),
    )
    store = _TaskStore()
    selection = WorkflowSelection(conversations, store, WorkflowService(store))
    frozen = TurnInput(
        "Only login",
        VALIDATION_SCOPE.actor_id,
        "validation",
        "qwen-plus",
        workflow_task_id="task-1",
    )
    first = await selection.follow_up(VALIDATION_SCOPE, "conversation-1", frozen, "request-1")
    original_command = store.commands[0]
    store.revision = 3
    second = await selection.follow_up(VALIDATION_SCOPE, "conversation-1", frozen, "request-1")
    assert first == second
    assert store.commands == [original_command]
    assert len({item.command_id for item in store.commands}) == 1
    assert all(item.task_id == "task-1" and item.kind == "steer" for item in store.commands)
    assert all(
        item.text == "Only login" and item.expected_plan_revision == 2 for item in store.commands
    )
    assert store.created == 0
    with pytest.raises(ValueError, match="command id payload conflict"):
        await selection.follow_up(
            VALIDATION_SCOPE,
            "conversation-1",
            replace(frozen, message="Other work"),
            "request-1",
        )
    with pytest.raises(ValueError, match="command id payload conflict"):
        await selection.follow_up(
            VALIDATION_SCOPE,
            "conversation-1",
            frozen,
            "request-1",
            target_node_id="other-node",
        )
    with pytest.raises(ValueError, match="conversation scope mismatch"):
        await selection.follow_up(
            VALIDATION_SCOPE,
            "conversation-1",
            replace(frozen, actor_id="other-actor"),
            "request-1",
        )


@pytest.mark.asyncio
async def test_direct_follow_up_refuses_persisted_browser_task_before_command():
    conversations = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    await conversations.create(
        "conversation-1",
        "turn-1",
        "first",
        TurnInput("Start", VALIDATION_SCOPE.actor_id, "validation", "qwen-plus"),
    )
    store = _TaskStore()
    store.request_kind = "browser"
    selection = WorkflowSelection(conversations, store, WorkflowService(store))
    frozen = TurnInput(
        "Change the browser run",
        VALIDATION_SCOPE.actor_id,
        "validation",
        "qwen-plus",
        workflow_task_id="task-1",
    )
    with pytest.raises(ValueError, match="browser task cannot be continued"):
        await selection.follow_up(VALIDATION_SCOPE, "conversation-1", frozen, "request-1")
    assert store.commands == []


@pytest.mark.asyncio
async def test_concurrent_follow_up_reloads_winning_command_after_plan_changes():
    conversations = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    await conversations.create(
        "conversation-1",
        "turn-1",
        "first",
        TurnInput("Start", VALIDATION_SCOPE.actor_id, "validation", "qwen-plus"),
    )
    store = _TaskStore()

    class PausedFirstSubmit(WorkflowService):
        def __init__(self):
            super().__init__(store)
            self.waiting = asyncio.Event()
            self.resume = asyncio.Event()
            self.calls = 0

        async def submit(self, scope, command):
            self.calls += 1
            if self.calls == 1:
                self.waiting.set()
                await self.resume.wait()
            return await super().submit(scope, command)

    controls = PausedFirstSubmit()
    selection = WorkflowSelection(conversations, store, controls)
    frozen = TurnInput(
        "Only login",
        VALIDATION_SCOPE.actor_id,
        "validation",
        "qwen-plus",
        workflow_task_id="task-1",
    )
    first = asyncio.create_task(
        selection.follow_up(VALIDATION_SCOPE, "conversation-1", frozen, "request-1")
    )
    await controls.waiting.wait()
    store.revision = 3
    second_receipt = await selection.follow_up(
        VALIDATION_SCOPE, "conversation-1", frozen, "request-1"
    )
    controls.resume.set()
    assert await first == second_receipt
    assert len(store.commands) == 1
    assert store.commands[0].expected_plan_revision == 3


@pytest.mark.asyncio
async def test_follow_up_rejects_other_conversation_before_submit():
    conversations = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    await conversations.create(
        "other-conversation",
        "turn-1",
        "first",
        TurnInput("Start", VALIDATION_SCOPE.actor_id, "validation", "qwen-plus"),
    )
    store = _TaskStore()
    selection = WorkflowSelection(conversations, store, WorkflowService(store))
    frozen = TurnInput(
        "Only login",
        VALIDATION_SCOPE.actor_id,
        "validation",
        "qwen-plus",
        workflow_task_id="task-1",
    )
    with pytest.raises(ValueError, match="task conversation mismatch"):
        await selection.follow_up(VALIDATION_SCOPE, "other-conversation", frozen, "request-1")
    assert store.commands == []
