import asyncio
from dataclasses import replace
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.routes import insights_explanations
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.ports.insights import InsightsAuthorizationChanged
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    GraphContextStatus,
    RetrievalSummary,
    TurnInput,
)
from tests.conftest import validation_http_services


def test_insights_explanation_accepts_and_restores_stored_turn(monkeypatch) -> None:
    calls = []
    frozen_aliases = []
    revoked = False

    class Explanation:
        async def reauthorize_result(self, scope, query_id, refs, selection, result):
            if revoked:
                raise InsightsAuthorizationChanged("withdrawn")
            calls.append((scope, query_id, refs, selection, result))

    async def frozen_input(body, _request):
        frozen_aliases.append(body.model_alias)
        return TurnInput(
            message=body.message,
            actor_id=VALIDATION_SCOPE.actor_id,
            identity_mode=VALIDATION_SCOPE.identity_mode.value,
            model_alias=body.model_alias,
        )

    monkeypatch.setattr(insights_explanations, "_input", frozen_input)
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    services = replace(
        validation_http_services(),
        conversations=service,
        insights_explanation=Explanation(),
        model_catalog=SimpleNamespace(default_alias="qwen-max", scope=VALIDATION_SCOPE),
    )
    origin = "http://127.0.0.1:15175"
    client = TestClient(
        create_app(services, allowed_origins=frozenset({origin})),
        headers={"Origin": origin},
    )
    path = "/api/v1/projects/tapper-demo/insights/explanations"
    body = {
        "queryId": "query-a",
        "resourceRefs": ["receipt-a"],
        "question": "Explain the failed run",
    }
    posted = client.post(path, json=body, headers={"Idempotency-Key": "request-a"})
    assert posted.status_code == 202, posted.text
    assert frozen_aliases == ["qwen-max"]
    accepted = posted.json()
    assert accepted["state"] == "queued"
    assert client.post(path, json=body, headers={"Idempotency-Key": "request-a"}).json() == accepted
    assert (
        client.post(
            path,
            json={**body, "userAuthorization": "caller-token"},
            headers={"Idempotency-Key": "request-b"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, json={**body, "numerator": 3}, headers={"Idempotency-Key": "request-b"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, json={**body, "question": "changed"}, headers={"Idempotency-Key": "request-a"}
        ).status_code
        == 409
    )

    turn_path = f"{path}/{accepted['conversationId']}/turns/{accepted['turnId']}"
    queued = client.get(turn_path)
    assert queued.status_code == 202
    assert queued.json()["state"] == "queued"
    result = {
        "queryId": "query-a",
        "metricVersion": "insights-metrics-v1",
        "asOf": "2026-09-25T08:00:00Z",
        "facts": [],
        "reportCoverage": [],
        "hypotheses": [],
        "evidenceExcerpts": [],
        "missingInformation": ["Application logs were not supplied."],
        "stopReason": "completed",
    }
    asyncio.run(
        service.complete_evidence(
            accepted["conversationId"],
            accepted["turnId"],
            AnswerEvidence(
                "Insights interpretation",
                "completed",
                RetrievalSummary("completed", trace_id="query-a"),
                GraphContextStatus.NOT_REQUESTED,
                insights_explanation=result,
            ),
        )
    )
    restored = client.get(turn_path)
    assert restored.status_code == 200, restored.text
    assert restored.json()["queryId"] == "query-a"
    assert len(calls) == 1
    assert calls[0][0] == VALIDATION_SCOPE
    assert calls[0][1:3] == ("query-a", ("receipt-a",))
    revoked = True
    assert client.get(turn_path).status_code == 403


def test_insights_explanation_is_unavailable_without_server_configuration() -> None:
    origin = "http://127.0.0.1:15175"
    client = TestClient(
        create_app(validation_http_services(), allowed_origins=frozenset({origin})),
        headers={"Origin": origin},
    )
    response = client.post(
        "/api/v1/projects/tapper-demo/insights/explanations",
        json={"queryId": "query-a", "resourceRefs": ["receipt-a"], "question": "Explain"},
        headers={"Idempotency-Key": "request-a"},
    )
    assert response.status_code == 503
