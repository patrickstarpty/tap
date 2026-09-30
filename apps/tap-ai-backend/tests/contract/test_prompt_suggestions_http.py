from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.chat.application.suggestion_ports import CurrentSource
from tap.modules.chat.application.suggestions import SuggestionView
from tap.modules.chat.domain.suggestions import SuggestionKey
from tests.conftest import validation_http_services

ORIGIN = "http://127.0.0.1:15175"
BASE = "/api/v1/projects/tapper-demo/prompt-suggestions"


class _Allow:
    async def authorize(self, *_args):
        return AuthorizationDecision(True, "test")


class _FakePromptSuggestionService:
    def __init__(self, views: tuple[SuggestionView, ...] = ()) -> None:
        self._views = views
        self.received_keys: list[SuggestionKey] = []

    async def list(self, key: SuggestionKey) -> tuple[SuggestionView, ...]:
        self.received_keys.append(key)
        return self._views


def _client(service: _FakePromptSuggestionService) -> TestClient:
    services = replace(
        validation_http_services(),
        prompt_suggestions=service,
        authorization_policy=_Allow(),
    )
    return TestClient(
        create_app(services, allowed_origins=frozenset({ORIGIN})),
        headers={"Origin": ORIGIN},
        raise_server_exceptions=False,
    )


def test_prompt_suggestions_are_listed_for_locale():
    service = _FakePromptSuggestionService(
        (
            SuggestionView(
                suggestion_id="s1",
                question="What changed in the refund policy?",
                sources=(
                    CurrentSource(
                        source_id="src_" + "a" * 32, name="Refund Policy", version="r1:0"
                    ),
                ),
            ),
        )
    )
    client = _client(service)

    response = client.get(BASE, params={"locale": "zh"})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [
            {
                "id": "s1",
                "question": "What changed in the refund policy?",
                "sources": [{"sourceId": "src_" + "a" * 32, "name": "Refund Policy"}],
            }
        ]
    }
    assert service.received_keys == [SuggestionKey(actor_id=VALIDATION_SCOPE.actor_id, locale="zh")]


def test_prompt_suggestions_reject_unknown_locale():
    client = _client(_FakePromptSuggestionService())

    response = client.get(BASE, params={"locale": "fr"})

    assert response.status_code == 422, response.text
    assert response.json()["type"].endswith("/request-validation")


def test_prompt_suggestions_return_empty_list():
    client = _client(_FakePromptSuggestionService(()))

    response = client.get(BASE, params={"locale": "en"})

    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}


def test_prompt_suggestions_operation_is_published():
    client = _client(_FakePromptSuggestionService())

    operation = client.app.openapi()["paths"]["/api/v1/projects/{project_id}/prompt-suggestions"][
        "get"
    ]

    assert operation["operationId"] == "prompt_suggestion_list"
