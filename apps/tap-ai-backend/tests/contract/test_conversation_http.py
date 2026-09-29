import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.ai.domain.models import (
    ModelCapability,
    ModelDescriptor,
    ModelGatewayUnavailable,
)
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    CitationEvidence,
    GraphContextStatus,
    RetrievalSummary,
    TurnInput,
)
from tests.conftest import validation_http_services

STRUCTURED_CHAT_MODEL = ModelDescriptor(
    "qwen-plus",
    "Qwen Plus",
    frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED}),
)
CHAT_ONLY_MODEL = ModelDescriptor("qwen-flash", "Qwen Flash", frozenset({ModelCapability.CHAT}))


def _model_selection_client(models):
    class Allow:
        async def authorize(self, *_args):
            return AuthorizationDecision(True, "test")

    class Knowledge:
        scope = VALIDATION_SCOPE

        async def resolve_conversation_selection(self, _revision_ids):
            raise AssertionError("An unavailable model must fail before Knowledge selection")

    conversations = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    services = replace(
        validation_http_services(knowledge=Knowledge()),
        conversations=conversations,
        model_catalog=models,
        authorization_policy=Allow(),
    )
    origin = "http://127.0.0.1:15175"
    client = TestClient(
        create_app(services, allowed_origins=frozenset({origin})), headers={"Origin": origin}
    )
    return client, conversations


class _SelectableModels:
    scope = VALIDATION_SCOPE

    async def list_models(self, _scope):
        return [STRUCTURED_CHAT_MODEL, CHAT_ONLY_MODEL]


@pytest.mark.parametrize("model_alias", ["missing-model", "qwen-flash"])
def test_conversation_with_unknown_or_chat_only_model_is_non_retryable_client_error(model_alias):
    client, conversations = _model_selection_client(_SelectableModels())

    response = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={"message": "Hello", "modelAlias": model_alias},
        headers={"Idempotency-Key": f"unavailable-{model_alias}"},
    )

    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"].endswith("/model-not-selectable")
    assert body["retryable"] is False
    assert conversations.repository.values == {}


def test_append_turn_with_unselectable_model_is_non_retryable_client_error():
    client, conversations = _model_selection_client(_SelectableModels())
    created = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={"message": "Hello", "modelAlias": "qwen-plus"},
        headers={"Idempotency-Key": "append-model-create"},
    )
    assert created.status_code == 202, created.text
    conversation_id = created.json()["conversationId"]

    response = client.post(
        f"/api/v1/projects/tapper-demo/conversations/{conversation_id}/turns",
        json={"message": "Again", "modelAlias": "qwen-flash"},
        headers={"Idempotency-Key": "append-model-turn"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["type"].endswith("/model-not-selectable")
    assert response.json()["retryable"] is False


def test_conversation_model_catalog_outage_stays_retryable_503():
    class Outage:
        scope = VALIDATION_SCOPE

        async def list_models(self, _scope):
            raise ModelGatewayUnavailable()

    client, conversations = _model_selection_client(Outage())

    response = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={"message": "Hello", "modelAlias": "qwen-plus"},
        headers={"Idempotency-Key": "catalog-outage"},
    )

    assert response.status_code == 503, response.text
    assert response.json()["type"].endswith("/model-unavailable")
    assert response.json()["retryable"] is True
    assert conversations.repository.values == {}


def test_conversation_turn_routes_document_422_and_503_problems():
    paths = TestClient(create_app(validation_mode=True)).app.openapi()["paths"]
    for path in (
        "/api/v1/projects/{project_id}/conversations",
        "/api/v1/projects/{project_id}/conversations/{conversation_id}/turns",
    ):
        responses = paths[path]["post"]["responses"]
        assert {"422", "503"} <= set(responses), path


def test_conversation_routes_are_registered_and_blank_first_message_is_rejected():
    client = TestClient(create_app(validation_mode=True))
    paths = client.app.openapi()["paths"]
    assert "/api/v1/projects/{project_id}/conversations" in paths
    assert "/api/v1/projects/{project_id}/conversations/{conversation_id}/events" in paths
    assert (
        "/api/v1/projects/{project_id}/conversations/{conversation_id}/turns/{turn_id}/citations/{citation_id}"
        in paths
    )
    schema = client.app.openapi()["components"]["schemas"]["ConversationCreateRequest"]
    assert schema["properties"]["message"]["minLength"] == 1
    stream = paths["/api/v1/projects/{project_id}/conversations/{conversation_id}/stream"]["get"]
    assert "text/event-stream" in stream["responses"]["200"]["content"]


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        ("test-plan.generation.waiting", {"jobId": "job-1", "reason": "review"}),
        (
            "test-plan.generation.result_ready",
            {
                "jobId": "job-1",
                "testPlanId": "plan-1",
                "revisionId": "revision-1",
                "deepLink": "/test-management/plan-1/revisions/revision-1",
            },
        ),
        ("test-plan.generation.failed", {"jobId": "job-1", "failureCode": "failed"}),
        ("test-plan.generation.canceled", {"jobId": "job-1", "reason": "canceled"}),
    ],
)
def test_conversation_events_http_reads_persisted_test_plan_lifecycle(
    event_type: str, payload: dict[str, str]
) -> None:
    conversations = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    asyncio.run(
        conversations.create(
            "conversation-1",
            "turn-1",
            "request-1",
            TurnInput(
                message="Design tests",
                actor_id=VALIDATION_SCOPE.actor_id,
                identity_mode=VALIDATION_SCOPE.identity_mode.value,
                model_alias="qwen-plus",
            ),
        )
    )
    asyncio.run(conversations.emit("conversation-1", "turn-1", event_type, payload))
    client = TestClient(
        create_app(replace(validation_http_services(), conversations=conversations)),
        raise_server_exceptions=False,
    )

    detail = client.get("/api/v1/projects/tapper-demo/conversations/conversation-1")
    response = client.get("/api/v1/projects/tapper-demo/conversations/conversation-1/events")

    assert detail.status_code == 200, detail.text
    assert response.status_code == 200, response.text
    assert response.json()["items"][-1]["eventType"] == event_type
    assert response.json()["items"][-1]["payload"] == payload


def test_conversation_accepts_a_model_only_turn_without_knowledge_revisions():
    class Allow:
        async def authorize(self, *_args):
            return AuthorizationDecision(True, "test")

    class Models:
        scope = VALIDATION_SCOPE

        async def list_models(self, _scope):
            return [STRUCTURED_CHAT_MODEL]

    class Knowledge:
        scope = VALIDATION_SCOPE

        async def resolve_conversation_selection(self, _revision_ids):
            raise AssertionError("Model-only chat must not enter Knowledge selection")

    conversations = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    services = replace(
        validation_http_services(knowledge=Knowledge()),
        conversations=conversations,
        model_catalog=Models(),
        authorization_policy=Allow(),
    )
    origin = "http://127.0.0.1:15175"
    client = TestClient(
        create_app(services, allowed_origins=frozenset({origin})), headers={"Origin": origin}
    )

    response = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={"message": "Hello", "modelAlias": "qwen-plus"},
        headers={"Idempotency-Key": "model-only-chat"},
    )

    assert response.status_code == 202, response.text
    frozen = next(iter(conversations.repository.values.values())).turns[0].input_snapshot.value
    assert frozen.resolved_resources == ()
    assert frozen.source_revision_ids == ()
    assert frozen.document_revision_ids == ()


def test_idempotent_http_replay_uses_historical_snapshot_before_current_asset_resolution():
    class Allow:
        async def authorize(self, *_args):
            return AuthorizationDecision(True, "test")

    class Models:
        scope = VALIDATION_SCOPE

        async def list_models(self, _scope):
            return [STRUCTURED_CHAT_MODEL]

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
                source_name="Product handbook",
                filename="handbook.md",
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
        "modelAlias": "qwen-plus",
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


def test_conversation_detail_exposes_only_authorized_immutable_input_view():
    class Allow:
        async def authorize(self, *_args):
            return AuthorizationDecision(True, "test")

    class Models:
        scope = VALIDATION_SCOPE

        async def list_models(self, _scope):
            return [STRUCTURED_CHAT_MODEL]

    class Knowledge:
        scope = VALIDATION_SCOPE

        async def resolve_conversation_selection(self, revision_ids):
            assert revision_ids == ("source-revision-1", "document-revision-1")
            values = tuple(
                SimpleNamespace(
                    source_id="src_" + str(index) * 32,
                    document_id=f"document-{index}",
                    revision_id=revision,
                    source_content_hash="sha256:" + str(index) * 64,
                    source_name=f"Source {index}",
                    filename=f"document-{index}.md",
                )
                for index, revision in enumerate(revision_ids, 1)
            )
            policy = SimpleNamespace(
                acl_digest="sha256:" + "d" * 64,
                decision_id="decision-1",
                policy_version="policy-1",
                active_corpus_version="tapper-demo-v1",
            )
            return values, policy

    conversations = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    services = replace(
        validation_http_services(knowledge=Knowledge()),
        conversations=conversations,
        model_catalog=Models(),
        authorization_policy=Allow(),
    )
    origin = "http://127.0.0.1:15175"
    client = TestClient(
        create_app(services, allowed_origins=frozenset({origin})), headers={"Origin": origin}
    )
    body = {
        "message": "  retain the exact user message  ",
        "modelAlias": "qwen-plus",
        "sourceRevisionIds": ["source-revision-1"],
        "documentRevisionIds": ["document-revision-1"],
    }
    accepted = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json=body,
        headers={"Idempotency-Key": "immutable-view"},
    )
    assert accepted.status_code == 202, accepted.text

    detail = client.get(
        f"/api/v1/projects/tapper-demo/conversations/{accepted.json()['conversationId']}"
    )
    assert detail.status_code == 200
    assert detail.json()["turns"][0]["input"] == {
        "message": "  retain the exact user message  ",
        "modelAlias": "qwen-plus",
        "sourceRevisionIds": ["source-revision-1"],
        "documentRevisionIds": ["document-revision-1"],
        "resolvedResources": [
            {
                "sourceId": "src_" + "1" * 32,
                "documentId": "document-1",
                "sourceRevisionId": "source-revision-1",
                "documentRevisionId": "source-revision-1",
                "label": "Source 1",
            },
            {
                "sourceId": "src_" + "2" * 32,
                "documentId": "document-2",
                "sourceRevisionId": None,
                "documentRevisionId": "document-revision-1",
                "label": "Source 2",
            },
        ],
        "agentRevisionId": None,
        "agentLabel": None,
        "skillRevisionIds": [],
        "skillLabels": [],
        "insightsQueryId": None,
    }
    serialized = json.dumps(detail.json())
    for forbidden in ("acl", "instruction", "credential", "provider", "system"):
        assert forbidden not in serialized.lower()

    wrong_project = client.get(
        f"/api/v1/projects/other-project/conversations/{accepted.json()['conversationId']}"
    )
    assert wrong_project.status_code == 403


def test_conversation_citation_requires_the_turn_immutable_evidence_link():
    class Allow:
        async def authorize(self, *_args):
            return AuthorizationDecision(True, "test")

    class Knowledge:
        scope = VALIDATION_SCOPE

        async def historical_citation(self, citation_id):
            assert citation_id == "citation-1"
            from tap.contracts.http import CitationPreview, DocumentAnchor, StructuralAnchor

            return CitationPreview(
                citation_id=citation_id,
                document_id="document-1",
                revision_id="revision-1",
                filename="deleted-source.md",
                source_content_hash="sha256:" + "c" * 64,
                chunk_content_hash="sha256:" + "d" * 64,
                anchor=StructuralAnchor(
                    root=DocumentAnchor(
                        type="document", heading_path=["History"], start_offset=0, end_offset=5
                    )
                ),
                quote="proof",
            )

    conversations = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    services = replace(
        validation_http_services(knowledge=Knowledge()),
        conversations=conversations,
        authorization_policy=Allow(),
    )
    client = TestClient(create_app(services))
    value = __import__("tap.modules.chat.domain.conversations", fromlist=["TurnInput"]).TurnInput(
        message="question",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode=VALIDATION_SCOPE.identity_mode.value,
        model_alias="qwen-plus",
    )
    import asyncio

    asyncio.run(conversations.create("conversation-1", "turn-1", "request-1", value))
    asyncio.run(
        conversations.complete_evidence(
            "conversation-1",
            "turn-1",
            AnswerEvidence(
                "answer",
                "completed",
                RetrievalSummary("completed", trace_id="trace-1"),
                GraphContextStatus.NOT_REQUESTED,
                citations=(CitationEvidence("citation-1", "sha256:" + "e" * 64),),
            ),
        )
    )
    base = "/api/v1/projects/tapper-demo/conversations/conversation-1/turns/turn-1/citations"
    response = client.get(f"{base}/citation-1")
    assert response.status_code == 200, response.text
    assert response.json()["quote"] == "proof"
    assert client.get(f"{base}/citation-not-linked").status_code == 404
    assert (
        client.get(
            "/api/v1/projects/tapper-demo/conversations/conversation-1/turns/missing/citations/citation-1"
        ).status_code
        == 404
    )
