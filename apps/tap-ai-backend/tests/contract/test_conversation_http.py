import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tap.contracts.http import (
    ConversationEventItem,
    ConversationTurnInputView,
    ConversationTurnSummary,
)
from tap.interfaces.http.app import create_app
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.ai.domain.assets import AssetRevisionStatus
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
                asset_id=f"{identity}-asset",
                content_digest="sha256:" + "b" * 64,
                instruction_template="frozen skill instruction",
                instruction_template_digest=text_digest("frozen skill instruction"),
                applicable_tasks=frozenset({"knowledge.answer"}),
                status=AssetRevisionStatus.ENABLED,
            )

        async def resolve_historical_skill(self, scope, identity):
            return await self.get_skill(scope, identity)

        async def list_skills(self, _scope):
            return ()

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
        "skillNotices": [],
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


def test_append_route_opens_turn_request_span(span_recorder):
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
            return SimpleNamespace(
                document_revision_ids=(),
                source_revision_ids=(),
                resolved_resources=(),
            )

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

    created = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={"message": "Hello", "modelAlias": "qwen-plus"},
        headers={"Idempotency-Key": "first-turn"},
    )
    assert created.status_code == 202, created.text
    conversation_id = created.json()["conversationId"]
    span_recorder.clear()

    appended = client.post(
        f"/api/v1/projects/tapper-demo/conversations/{conversation_id}/turns",
        json={"message": "Follow up", "modelAlias": "qwen-plus"},
        headers={"Idempotency-Key": "second-turn"},
    )
    assert appended.status_code == 202, appended.text
    turn_id = appended.json()["turnId"]

    request_spans = [s for s in span_recorder.get_finished_spans() if s.name == "turn.request"]
    assert len(request_spans) == 1
    assert request_spans[0].attributes["tap.turn_id"] == turn_id


@pytest.mark.parametrize("status", ["STALE", "EMPTY"])
def test_conversation_turn_summary_accepts_stale_and_empty_graph_status(status: str) -> None:
    """The relation analysis subgraph's `STALE`/`EMPTY` outcomes
    must validate on the Conversation turn summary, not only the answer."""
    summary = ConversationTurnSummary.model_validate(
        {
            "turnId": "turn-1",
            "state": "completed",
            "attempt": 1,
            "inputSnapshotDigest": "sha256:" + "a" * 64,
            "graphContextStatus": status,
            "input": ConversationTurnInputView(
                message="question",
                model_alias="qwen-plus",
                source_revision_ids=[],
                document_revision_ids=[],
                resolved_resources=[],
                skill_revision_ids=[],
                skill_labels=[],
            ).model_dump(by_alias=True),
        }
    )
    assert summary.graph_context_status == status


def test_conversation_event_item_accepts_graph_context_ready() -> None:
    """The Conversation history route must not
    reject a stored `graph.context_ready` event."""
    item = ConversationEventItem.model_validate(
        {
            "eventId": "event-1",
            "sequence": 1,
            "turnId": "turn-1",
            "eventType": "graph.context_ready",
            "payload": {
                "status": "APPLIED",
                "graphVersion": "7",
                "seedCount": 2,
                "querySeedCount": 2,
                "paths": [["核保流程", "健康告知"]],
                "relationCount": 1,
            },
            "occurredAt": "2026-10-09T00:00:00Z",
        }
    )
    assert item.event_type == "graph.context_ready"


def test_conversation_event_item_accepts_task_step() -> None:
    """LangGraph PR 5 Task 9/12: the Conversation history route must not
    reject a stored `task.step` event (one per native tool call) -- caught
    missing from this closed set by Task 12's real MySQL integration test."""
    item = ConversationEventItem.model_validate(
        {
            "eventId": "event-1",
            "sequence": 1,
            "turnId": "turn-1",
            "eventType": "task.step",
            "payload": {
                "stepId": "turn-1:r1:c0",
                "tool": "knowledge.search",
                "status": "completed",
                "summary": 'knowledge.search "x" → +1 条证据（S2）',
                "durationMs": 42,
                "toolRound": 1,
            },
            "occurredAt": "2026-10-09T00:00:00Z",
        }
    )
    assert item.event_type == "task.step"


def _library_skill(skill_id, revision_number, **overrides):
    from tap.modules.ai.domain.assets import SkillRevision, text_digest

    instruction = overrides.pop("instruction", f"{skill_id} r{revision_number} instruction")
    values = dict(
        revision_id=f"{skill_id}-r{revision_number}",
        asset_id=skill_id,
        display_name=skill_id.replace("-", " ").capitalize(),
        scope=VALIDATION_SCOPE,
        content_digest=text_digest(f"{skill_id}-r{revision_number}-content"),
        instruction_template_digest=text_digest(instruction),
        applicable_tasks=frozenset({"knowledge.answer"}),
        instruction_template=instruction,
        description=f"{skill_id} description",
    )
    values.update(overrides)
    return SkillRevision(**values)


def _library_catalog():
    """An asset catalog whose `list_skills` behaves like the MySQL catalog:
    each Skill's latest revision, whatever its status. The Agent pins three
    Skills (one with a newer revision, one disabled, one not in the library),
    excludes one, and still carries a legacy bound revision of a Skill it no
    longer pins."""

    from tap.modules.ai.application.assets import VALIDATION_OUTPUT_SCHEMA, ApprovedAssetCatalog
    from tap.modules.ai.domain.assets import AiAgentRevision, text_digest
    from tap.modules.ai.domain.models import schema_digest

    class LibraryCatalog(ApprovedAssetCatalog):
        scope = VALIDATION_SCOPE

        def __init__(self, *, agents, skills):
            super().__init__(agents=agents, skills=skills)
            self.revisions = list(skills)

        def publish(self, skill):
            self.revisions.append(skill)
            self._skills = tuple(self.revisions)

        async def list_skills(self, scope):
            latest = {}
            for item in self.revisions:
                latest[item.asset_id] = item
            return tuple(latest.values())

    schema = VALIDATION_OUTPUT_SCHEMA
    agent = AiAgentRevision(
        revision_id="agent-1",
        asset_id="agent-1-asset",
        display_name="Demo agent",
        scope=VALIDATION_SCOPE,
        content_digest=text_digest("agent-1-content"),
        system_instruction_digest=text_digest("agent system instruction"),
        tool_allowlist=frozenset({"knowledge.answer"}),
        output_schema_digest=schema_digest(schema),
        system_instruction="agent system instruction",
        output_schema_json=json.dumps(schema, sort_keys=True, separators=(",", ":")),
        skill_revision_ids=("skill-legacy-bound-r1",),
        pinned_skill_ids=("skill-pinned", "skill-pinned-disabled", "skill-missing"),
        excluded_skill_ids=("skill-excluded",),
    )
    skills = (
        _library_skill("skill-pinned", 1),
        _library_skill("skill-pinned", 2),
        _library_skill("skill-pinned-disabled", 1, status=AssetRevisionStatus.DISABLED),
        _library_skill("skill-manual", 1),
        _library_skill("skill-excluded", 1),
        _library_skill("skill-legacy-bound", 1),
        _library_skill("skill-catalog", 1),
        _library_skill("skill-wrong-task", 1, applicable_tasks=frozenset({"test-plan.generate"})),
    )
    return LibraryCatalog(agents=(agent,), skills=skills)


def _library_client(catalog):
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
            return (), SimpleNamespace(
                acl_digest="sha256:" + "0" * 64,
                decision_id="decision-1",
                policy_version="policy-1",
                active_corpus_version="tapper-demo-v1",
            )

    conversations = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    services = replace(
        validation_http_services(knowledge=Knowledge()),
        conversations=conversations,
        model_catalog=Models(),
        asset_catalog=catalog,
        authorization_policy=Allow(),
    )
    origin = "http://127.0.0.1:15175"
    client = TestClient(
        create_app(services, allowed_origins=frozenset({origin})), headers={"Origin": origin}
    )
    return client, conversations


def _only_turn(conversations):
    (value,) = conversations.repository.values.values()
    (turn,) = value.turns
    return turn


def test_turn_freezes_catalog_without_loaded_and_excluded_skills():
    client, conversations = _library_client(_library_catalog())

    response = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={
            "message": "question",
            "modelAlias": "qwen-plus",
            "agentRevisionId": "agent-1",
            "skillRevisionIds": ["skill-manual-r1"],
        },
        headers={"Idempotency-Key": "freeze-catalog"},
    )
    assert response.status_code == 202, response.text

    frozen = _only_turn(conversations).input_snapshot.value
    # Manual first, then pinned; the legacy bound revision is not loaded.
    assert frozen.skill_revision_ids == ("skill-manual-r1", "skill-pinned-r2")
    assert frozen.requested_skill_revision_ids == ("skill-manual-r1",)
    assert frozen.skill_notices == (
        "skill_disabled:skill-pinned-disabled-r1",
        "skill_unavailable:skill-missing",
    )
    # Neither loaded (manual/pinned), excluded, disabled nor task-inapplicable.
    assert [(entry.skill_id, entry.revision_id) for entry in frozen.skill_catalog] == [
        ("skill-catalog", "skill-catalog-r1"),
        ("skill-legacy-bound", "skill-legacy-bound-r1"),
    ]
    assert frozen.skill_catalog[0].description == "skill-catalog description"

    detail = client.get(
        f"/api/v1/projects/tapper-demo/conversations/{response.json()['conversationId']}"
    )
    view = detail.json()["turns"][0]["input"]
    assert view["agentRevisionId"] == "agent-1"
    assert view["skillRevisionIds"] == ["skill-manual-r1", "skill-pinned-r2"]
    assert view["skillNotices"] == [
        "skill_disabled:skill-pinned-disabled-r1",
        "skill_unavailable:skill-missing",
    ]


def test_pinned_skill_uses_latest_enabled_revision():
    client, conversations = _library_client(_library_catalog())

    response = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={"message": "question", "modelAlias": "qwen-plus", "agentRevisionId": "agent-1"},
        headers={"Idempotency-Key": "pinned-latest"},
    )
    assert response.status_code == 202, response.text

    frozen = _only_turn(conversations).input_snapshot.value
    assert frozen.skill_revision_ids == ("skill-pinned-r2",)
    assert frozen.skill_instruction_templates == ("skill-pinned r2 instruction",)
    assert frozen.requested_skill_revision_ids == ()


def test_replay_after_library_change_is_not_a_conflict():
    catalog = _library_catalog()
    client, conversations = _library_client(catalog)
    body = {
        "message": "question",
        "modelAlias": "qwen-plus",
        "agentRevisionId": "agent-1",
        "skillRevisionIds": ["skill-manual-r1"],
    }
    headers = {"Idempotency-Key": "replay-library-change"}

    first = client.post("/api/v1/projects/tapper-demo/conversations", json=body, headers=headers)
    assert first.status_code == 202, first.text
    frozen = _only_turn(conversations).input_snapshot

    # The pinned Skill gets a newer revision and a new Skill joins the catalog.
    catalog.publish(_library_skill("skill-pinned", 3))
    catalog.publish(_library_skill("skill-new", 1))

    replay = client.post("/api/v1/projects/tapper-demo/conversations", json=body, headers=headers)
    assert replay.status_code == 202, replay.text
    assert replay.json() == first.json()
    assert _only_turn(conversations).input_snapshot == frozen


def test_replay_with_different_manual_skills_conflicts():
    client, _conversations = _library_client(_library_catalog())
    headers = {"Idempotency-Key": "replay-skill-mismatch"}
    base_body = {"message": "question", "modelAlias": "qwen-plus", "agentRevisionId": "agent-1"}

    first = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={**base_body, "skillRevisionIds": []},
        headers=headers,
    )
    assert first.status_code == 202, first.text

    # Manually requesting the pinned Skill's latest revision applies the same
    # Skill, but is still a different request.
    same_applied_different_request = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={**base_body, "skillRevisionIds": ["skill-pinned-r2"]},
        headers=headers,
    )
    assert same_applied_different_request.status_code == 409, same_applied_different_request.text
    assert same_applied_different_request.json()["type"].endswith("/idempotency-conflict")

    genuinely_different = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={**base_body, "skillRevisionIds": ["skill-manual-r1"]},
        headers=headers,
    )
    assert genuinely_different.status_code == 409, genuinely_different.text

    exact_replay = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={**base_body, "skillRevisionIds": []},
        headers=headers,
    )
    assert exact_replay.status_code == 202, exact_replay.text
    assert exact_replay.json() == first.json()


def test_skill_budget_exceeded_over_96kb_returns_skill_problem():
    from tap.modules.ai.application.assets import ApprovedAssetCatalog
    from tap.modules.ai.domain.assets import SkillRevision, text_digest

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
            raise AssertionError("A rejected skill budget must fail before Knowledge selection")

    oversized_instruction = "x" * (96 * 1024 + 1)
    oversized_skill = SkillRevision(
        revision_id="skill-oversized",
        asset_id="skill-oversized-asset",
        display_name="Oversized skill",
        scope=VALIDATION_SCOPE,
        content_digest=text_digest("skill-oversized-content"),
        instruction_template_digest=text_digest(oversized_instruction),
        applicable_tasks=frozenset({"knowledge.answer"}),
        instruction_template=oversized_instruction,
    )
    asset_catalog = ApprovedAssetCatalog(agents=(), skills=(oversized_skill,))
    asset_catalog.scope = VALIDATION_SCOPE

    conversations = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    services = replace(
        validation_http_services(knowledge=Knowledge()),
        conversations=conversations,
        model_catalog=Models(),
        asset_catalog=asset_catalog,
        authorization_policy=Allow(),
    )
    origin = "http://127.0.0.1:15175"
    client = TestClient(
        create_app(services, allowed_origins=frozenset({origin})), headers={"Origin": origin}
    )

    response = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={
            "message": "question",
            "modelAlias": "qwen-plus",
            "skillRevisionIds": ["skill-oversized"],
        },
        headers={"Idempotency-Key": "skill-budget-exceeded"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "skill_budget_exceeded"
    assert conversations.repository.values == {}


@pytest.mark.parametrize(
    "insights_report_refs",
    [["report-1", "report-1"], [" "], [""]],
)
def test_create_turn_rejects_invalid_insights_report_refs_as_a_422(insights_report_refs):
    """Fix round 1, I-1: a duplicate or blank `insightsReportRefs` item must
    fail the HTTP contract itself (422) -- not reach `TurnInput.__post_init__`,
    whose `ValueError` the catch-all handler would otherwise surface as a
    misleading 503. This never touches knowledge/model services, so request
    parsing alone must reject it."""
    client, _conversations = _model_selection_client(_SelectableModels())

    response = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={
            "message": "Hello",
            "modelAlias": "qwen-plus",
            "insightsReportRefs": insights_report_refs,
        },
        headers={"Idempotency-Key": "insights-refs-invalid"},
    )

    assert response.status_code == 422, response.text


def test_create_turn_freezes_insights_report_refs_without_query():
    """Task 8, user decision B: a plain conversation Turn may freeze
    `insightsReportRefs` alone (no historical `insights_query_id` -- that
    has no HTTP entry point). When the Insights runtime is configured, the
    frozen Turn input carries exactly the requested refs; when it is not
    configured, the request fails with the same `KnowledgeRuntimeUnavailable`
    503 an unconfigured knowledge runtime already uses."""

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

    unconfigured = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={
            "message": "Hello",
            "modelAlias": "qwen-plus",
            "insightsReportRefs": ["report-1"],
        },
        headers={"Idempotency-Key": "insights-unconfigured"},
    )

    assert unconfigured.status_code == 503, unconfigured.text
    assert conversations.repository.values == {}

    client.app.state.http_services = replace(services, insights_explanation=object())

    configured = client.post(
        "/api/v1/projects/tapper-demo/conversations",
        json={
            "message": "Hello",
            "modelAlias": "qwen-plus",
            "insightsReportRefs": ["report-1"],
        },
        headers={"Idempotency-Key": "insights-configured"},
    )

    assert configured.status_code == 202, configured.text
    frozen = next(iter(conversations.repository.values.values())).turns[0].input_snapshot.value
    assert frozen.insights_report_refs == ("report-1",)
    assert frozen.insights_query_id is None
