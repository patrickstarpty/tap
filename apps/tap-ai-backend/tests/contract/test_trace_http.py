"""Contract tests for the turn trace and model-call detail HTTP routes."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from tap.contracts.http import (
    ModelCallDetail,
    ModelCallView,
    TraceSpanView,
    TurnTrace,
    TurnTraceSummary,
)
from tap.interfaces.http.app import create_app
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.ai.domain.models import ModelCapability, ModelDescriptor
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tests.conftest import validation_http_services

STRUCTURED_CHAT_MODEL = ModelDescriptor(
    "qwen-plus",
    "Qwen Plus",
    frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED}),
)

_TRACE = TurnTrace(
    trace_id="a" * 32,
    summary=TurnTraceSummary(
        total_duration_ms=350,
        input_tokens=20,
        output_tokens=10,
        cost_usd=Decimal("0.0012"),
        cost_incomplete=True,
        requested_models=["qwen-plus"],
        upstream_models=["qwen-plus-2025"],
        attempt_count=2,
    ),
    spans=[
        TraceSpanView(
            span_id="1" * 16,
            parent_span_id=None,
            name="turn.request",
            status="ok",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            duration_ms=100,
            attributes={"tap.attempt": 1},
            attempt=1,
        )
    ],
    model_calls=[
        ModelCallView(
            call_id="call-0",
            span_id="1" * 16,
            operation="chat",
            model_name="qwen-plus",
            upstream_model="qwen-plus-2025",
            provider="litellm",
            input_tokens=20,
            output_tokens=10,
            cost_usd=Decimal("0.0012"),
            latency_ms=100,
            attempts=1,
            status="ok",
            error_code=None,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ],
)

_CALL_DETAIL = ModelCallDetail(
    call_id="call-0",
    span_id="1" * 16,
    operation="chat",
    model_name="qwen-plus",
    upstream_model="qwen-plus-2025",
    provider="litellm",
    input_tokens=20,
    output_tokens=10,
    cost_usd=Decimal("0.0012"),
    latency_ms=100,
    attempts=1,
    status="ok",
    error_code=None,
    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    request="{}",
    response="ok",
    reasoning=None,
)


class _Traces:
    async def turn_trace(self, scope, conversation_id, turn_id):
        del scope
        if (conversation_id, turn_id) != ("conv-1", "turn-1"):
            return None
        return _TRACE

    async def model_call(self, scope, call_id):
        del scope
        if call_id != "call-0":
            return None
        return _CALL_DETAIL


def _client():
    services = validation_http_services(traces=_Traces())
    return TestClient(create_app(services))


def test_trace_route_returns_turn_trace():
    client = _client()

    response = client.get("/api/v1/projects/tapper-demo/conversations/conv-1/turns/turn-1/trace")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["traceId"] == "a" * 32
    assert body["summary"]["costIncomplete"] is True
    assert body["summary"]["attemptCount"] == 2
    assert "modelCalls" in body
    assert body["modelCalls"][0]["upstreamModel"] == "qwen-plus-2025"


def test_turn_without_traceparent_returns_404():
    client = _client()

    response = client.get("/api/v1/projects/tapper-demo/conversations/conv-1/turns/missing/trace")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_model_call_detail_includes_content():
    client = _client()

    response = client.get("/api/v1/projects/tapper-demo/model-calls/call-0")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request"] == "{}"
    assert body["response"] == "ok"
    assert body["reasoning"] is None


def test_model_call_from_other_project_is_not_found():
    client = _client()

    response = client.get("/api/v1/projects/tapper-demo/model-calls/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_trace_routes_require_knowledge_answer_authorization():
    client = _client()

    response = client.get("/api/v1/projects/other-project/conversations/conv-1/turns/turn-1/trace")

    assert response.status_code == 403


def test_turn_summary_exposes_trace_id():
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
        validation_http_services(knowledge=Knowledge(), traces=_Traces()),
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
        headers={"Idempotency-Key": "trace-summary-turn"},
    )
    assert created.status_code == 202, created.text
    conversation_id = created.json()["conversationId"]

    detail = client.get(f"/api/v1/projects/tapper-demo/conversations/{conversation_id}")
    turn = detail.json()["turns"][0]
    assert "traceId" in turn


def test_openapi_declares_trace_routes():
    schema = create_app(validation_mode=True).openapi()

    paths = schema["paths"]
    assert (
        "/api/v1/projects/{project_id}/conversations/{conversation_id}/turns/{turn_id}/trace"
        in paths
    )
    assert "/api/v1/projects/{project_id}/model-calls/{call_id}" in paths
