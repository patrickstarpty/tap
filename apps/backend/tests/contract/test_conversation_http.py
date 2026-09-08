import json
from dataclasses import replace
from types import SimpleNamespace

from apps.backend.tests.conftest import validation_http_services
from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)


def test_conversation_routes_are_registered_and_blank_first_message_is_rejected():
    client = TestClient(create_app(validation_mode=True))
    paths = client.app.openapi()["paths"]
    assert "/api/v1/projects/{project_id}/conversations" in paths
    assert "/api/v1/projects/{project_id}/conversations/{conversation_id}/events" in paths
    schema = client.app.openapi()["components"]["schemas"]["ConversationCreateRequest"]
    assert schema["properties"]["message"]["minLength"] == 1
    stream = paths["/api/v1/projects/{project_id}/conversations/{conversation_id}/stream"]["get"]
    assert "text/event-stream" in stream["responses"]["200"]["content"]


def test_idempotent_http_replay_uses_historical_snapshot_before_current_asset_resolution():
    class Allow:
        async def authorize(self, *_args):
            return AuthorizationDecision(True, "test")

    class Models:
        scope = VALIDATION_SCOPE

        async def list_models(self, _scope):
            return [SimpleNamespace(alias="tapper-chat")]

    class Assets:
        scope = VALIDATION_SCOPE

        async def get_agent(self, _scope, identity):
            raise AssertionError("Conversation acceptance must use governed agent resolution")

        async def resolve_agent(self, _scope, identity, *, tools, output_schema_digest):
            from tap.modules.ai.application.assets import VALIDATION_OUTPUT_SCHEMA
            from tap.modules.ai.domain.models import schema_digest, text_digest

            schema = VALIDATION_OUTPUT_SCHEMA
            assert tools == frozenset({"knowledge.answer"})
            assert output_schema_digest == schema_digest(schema)
            return SimpleNamespace(
                revision_id=identity,
                content_digest="sha256:" + "a" * 64,
                system_instruction="frozen agent instruction",
                system_instruction_digest=text_digest("frozen agent instruction"),
                tool_allowlist=frozenset({"knowledge.answer"}),
                output_schema_json=json.dumps(schema, sort_keys=True, separators=(",", ":")),
                output_schema_digest=schema_digest(schema),
            )

        async def get_skill(self, _scope, identity):
            from tap.modules.ai.domain.models import text_digest

            return SimpleNamespace(
                revision_id=identity,
                content_digest="sha256:" + "b" * 64,
                instruction_template="frozen skill instruction",
                instruction_template_digest=text_digest("frozen skill instruction"),
                applicable_tasks=frozenset({"knowledge.answer"}),
            )

    class Knowledge:
        scope = VALIDATION_SCOPE

        async def resolve_conversation_selection(self, revision_ids):
            assert revision_ids == ("revision-1",)
            row = SimpleNamespace(
                source_id="src_" + "1" * 32,
                document_id="document-1",
                revision_id="revision-1",
                source_content_hash="sha256:" + "c" * 64,
            )
            policy = SimpleNamespace(
                acl_digest="sha256:" + "d" * 64,
                decision_id="decision-1",
                policy_version="policy-1",
                active_corpus_version="tapper-demo-v1",
            )
            return (row,), policy

    conversations = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    services = replace(
        validation_http_services(knowledge=Knowledge()),
        conversations=conversations,
        model_catalog=Models(),
        asset_catalog=Assets(),
        authorization_policy=Allow(),
    )
    origin = "http://127.0.0.1:15175"
    app = create_app(services, allowed_origins=frozenset({origin}))
    client = TestClient(app, headers={"Origin": origin})
    body = {
        "message": "question",
        "modelAlias": "tapper-chat",
        "sourceRevisionIds": ["revision-1"],
        "agentRevisionId": "agent-1",
        "skillRevisionIds": ["skill-1"],
    }
    headers = {"Idempotency-Key": "request-1"}
    first = client.post("/api/v1/projects/tapper-demo/conversations", json=body, headers=headers)
    assert first.status_code == 202, first.text
    frozen = next(iter(conversations.repository.values.values())).turns[0].input_snapshot.value
    assert frozen.agent_system_instruction == "frozen agent instruction"
    assert frozen.agent_tool_allowlist == ("knowledge.answer",)
    from tap.modules.ai.application.assets import VALIDATION_OUTPUT_SCHEMA

    assert json.loads(frozen.agent_output_schema_json) == VALIDATION_OUTPUT_SCHEMA
    assert frozen.skill_instruction_templates == ("frozen skill instruction",)

    app.state.http_services = replace(
        services, knowledge=None, model_catalog=None, asset_catalog=None
    )
    replay = client.post("/api/v1/projects/tapper-demo/conversations", json=body, headers=headers)
    assert replay.status_code == 202
    assert replay.json() == first.json()
    conflict = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={**body, "modelAlias": "different"},
        headers=headers,
    )
    assert conflict.status_code == 409
