import asyncio
from dataclasses import replace
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.routes.ai_agents import _public_agent
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.ai.application.assets import ApprovedAssetCatalog
from tap.modules.ai.domain.models import ModelCapability, ModelDescriptor
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.domain.conversations import TurnInput
from tests.conftest import validation_http_services
from tests.unit.workflows.test_catalog_routing import _agent


class Models:
    scope = VALIDATION_SCOPE

    async def list_models(self, _scope):
        return (
            ModelDescriptor(
                "qwen-plus",
                "Qwen Plus",
                frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED}),
            ),
        )


class Knowledge:
    scope = VALIDATION_SCOPE


class Allow:
    async def authorize(self, *_args):
        return AuthorizationDecision(True, "test")


class Coordinator:
    def __init__(self):
        self.received = []

    async def create(self, conversation_id, turn_id, idempotency_key, frozen):
        self.received.append(("create", frozen))
        return SimpleNamespace(turn_id=turn_id)

    async def append(self, conversation_id, turn_id, idempotency_key, frozen):
        self.received.append(("append", frozen))
        return SimpleNamespace(turn_id=turn_id)

    async def continue_task(self, conversation_id, turn_id, idempotency_key, frozen):
        self.received.append(("continue", frozen))
        return SimpleNamespace(turn_id=turn_id)


def _client(coordinator=None):
    conversations = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    assets = ApprovedAssetCatalog(
        agents=(
            _agent("orchestrator", "orchestrator-v1", "Tapper"),
            _agent("qae", "qae-v1", "QAE"),
            _agent("ba", "ba-v1", "BA"),
        ),
        skills=(),
    )
    assets.scope = VALIDATION_SCOPE
    services = replace(
        validation_http_services(knowledge=Knowledge()),
        conversations=conversations,
        model_catalog=Models(),
        asset_catalog=assets,
        authorization_policy=Allow(),
        workflow_admission=coordinator,
    )
    origin = "http://127.0.0.1:15175"
    return TestClient(
        create_app(services, allowed_origins=frozenset({origin})),
        headers={"Origin": origin},
        raise_server_exceptions=False,
    ), conversations


def test_new_selection_reaches_coordinator_with_root_and_all_delegates_without_legacy_queue():
    coordinator = Coordinator()
    client, conversations = _client(coordinator)
    response = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={
            "message": "Design tests",
            "modelAlias": "qwen-plus",
            "requestedAgentIds": ["qae", "ba"],
        },
        headers={"Idempotency-Key": "workflow-1"},
    )
    assert response.status_code == 202, response.text
    assert conversations.repository.values == {}
    frozen = coordinator.received[0][1]
    assert frozen.workflow_root.agent_id == "orchestrator"
    assert frozen.requested_agent_ids == ("qae", "ba")
    assert {item.agent_id for item in frozen.workflow_agents} == {"qae", "ba"}
    assert frozen.agent_revision_id is None


def test_workflow_selection_without_coordinator_fails_closed():
    client, conversations = _client()
    response = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={"message": "Design tests", "modelAlias": "qwen-plus", "requestedAgentIds": ["qae"]},
        headers={"Idempotency-Key": "workflow-2"},
    )
    assert response.status_code == 503
    assert conversations.repository.values == {}


def test_empty_requested_agent_ids_keep_legacy_chat_when_coordinator_is_absent():
    client, conversations = _client()
    response = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={"message": "Hello", "modelAlias": "qwen-plus", "requestedAgentIds": []},
        headers={"Idempotency-Key": "legacy-empty-selection"},
    )
    assert response.status_code == 202, response.text
    assert len(conversations.repository.values) == 1


def test_root_identity_is_rejected_through_both_selection_fields():
    client, _ = _client(Coordinator())
    for fields in ({"requestedAgentIds": ["orchestrator"]}, {"agentRevisionId": "orchestrator-v1"}):
        response = client.post(
            "/api/v1/projects/tapper-demo/conversations",
            json={"message": "Hello", "modelAlias": "qwen-plus", **fields},
            headers={"Idempotency-Key": str(fields)},
        )
        assert response.status_code == 422, response.text


def test_internal_root_is_not_a_public_agent_but_custom_tapper_is():
    assert not _public_agent(_agent("orchestrator", "orchestrator-v1", "Tapper"))
    assert _public_agent(_agent("custom-tapper", "custom-v1", "Tapper"))


def test_internal_root_cannot_be_disabled_through_agent_api():
    client, _ = _client()
    response = client.post("/api/v1/projects/tapper-demo/ai/agents/orchestrator/disable")
    assert response.status_code == 404, response.text


def test_workflow_task_continuation_uses_coordinator_command_without_legacy_append():
    coordinator = Coordinator()
    client, conversations = _client(coordinator)
    asyncio.run(
        conversations.create(
            "conversation-1",
            "turn-1",
            "initial",
            TurnInput(
                message="Start",
                actor_id=VALIDATION_SCOPE.actor_id,
                identity_mode=VALIDATION_SCOPE.identity_mode.value,
                model_alias="qwen-plus",
            ),
        )
    )
    response = client.post(
        "/api/v1/projects/tapper-demo/conversations/conversation-1/turns",
        json={"message": "Only login", "modelAlias": "qwen-plus", "workflowTaskId": "task-1"},
        headers={"Idempotency-Key": "continuation-1"},
    )
    assert response.status_code == 202, response.text
    assert len(conversations.repository.values["conversation-1"].turns) == 1
    assert coordinator.received[0][0] == "continue"
    assert coordinator.received[0][1].workflow_task_id == "task-1"


def test_no_agent_selection_keeps_tapper_and_candidate_catalog():
    coordinator = Coordinator()
    client, conversations = _client(coordinator)
    response = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={"message": "Explain this", "modelAlias": "qwen-plus"},
        headers={"Idempotency-Key": "default-workflow"},
    )
    assert response.status_code == 202, response.text
    frozen = coordinator.received[0][1]
    assert frozen.workflow_root.agent_id == "orchestrator"
    assert frozen.requested_agent_ids == ()
    assert {item.agent_id for item in frozen.workflow_agents} == {"qae", "ba"}


def test_replay_returns_original_frozen_selection_after_latest_catalog_changes():
    coordinator = Coordinator()
    client, conversations = _client(coordinator)
    body = {"message": "Design tests", "modelAlias": "qwen-plus", "requestedAgentIds": ["qae"]}
    headers = {"Idempotency-Key": "stable-workflow"}
    first = client.post("/api/v1/projects/tapper-demo/conversations", json=body, headers=headers)
    assert first.status_code == 202, first.text
    original = coordinator.received[0][1]
    asyncio.run(
        conversations.create(
            first.json()["conversationId"],
            first.json()["turnId"],
            "stable-workflow",
            original,
        )
    )
    assets = client.app.state.http_services.asset_catalog
    assets._agents = tuple(item for item in assets._agents if item.asset_id != "qae")
    replay = client.post("/api/v1/projects/tapper-demo/conversations", json=body, headers=headers)
    assert replay.status_code == 202, replay.text
    assert len(coordinator.received) == 1
    stored = asyncio.run(conversations.replay(first.json()["conversationId"], "stable-workflow"))
    assert stored.input_snapshot.value.workflow_agents == original.workflow_agents


def test_legacy_revision_maps_to_exact_delegate_and_conflicting_stable_selection_rejects():
    coordinator = Coordinator()
    client, conversations = _client(coordinator)
    assets = client.app.state.http_services.asset_catalog
    old = _agent("qae", "qae-v0", "QAE older")
    assets._agents = (*assets._agents, old)
    latest = assets._agents[:-1]

    async def list_latest(_scope):
        return latest

    assets.list_agents = list_latest
    compatible = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={
            "message": "Review",
            "modelAlias": "qwen-plus",
            "requestedAgentIds": ["ba"],
            "agentRevisionId": "qae-v0",
        },
        headers={"Idempotency-Key": "exact-legacy"},
    )
    assert compatible.status_code == 202, compatible.text
    frozen = coordinator.received[-1][1]
    assert frozen.requested_agent_ids == ("ba",)
    assert frozen.delegate_ids == ("ba", "qae")
    assert frozen.requested_agent_revision_id == "qae-v0"
    assert (
        next(item for item in frozen.workflow_agents if item.agent_id == "qae").revision_id
        == "qae-v0"
    )
    asyncio.run(
        conversations.create(
            compatible.json()["conversationId"],
            compatible.json()["turnId"],
            "exact-legacy",
            frozen,
        )
    )
    replay = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={
            "message": "Review",
            "modelAlias": "qwen-plus",
            "requestedAgentIds": ["ba"],
            "agentRevisionId": "qae-v0",
        },
        headers={"Idempotency-Key": "exact-legacy"},
    )
    assert replay.status_code == 202, replay.text

    conflict = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={
            "message": "Review",
            "modelAlias": "qwen-plus",
            "requestedAgentIds": ["qae"],
            "agentRevisionId": "qae-v0",
        },
        headers={"Idempotency-Key": "conflicting-legacy"},
    )
    assert conflict.status_code == 422, conflict.text
