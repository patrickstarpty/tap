import asyncio
from dataclasses import replace

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.domain.conversations import TurnInput
from tests.conftest import validation_http_services

ORIGIN = "http://127.0.0.1:15175"
BASE = "/api/v1/projects/tapper-demo/conversations"


class _Allow:
    async def authorize(self, *_args):
        return AuthorizationDecision(True, "test")


def _client(*titles: str) -> tuple[TestClient, ConversationService]:
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    for index, title in enumerate(titles):
        asyncio.run(
            service.create(
                f"conversation-{index}",
                f"turn-{index}",
                f"request-{index}",
                TurnInput(
                    message=title,
                    actor_id=VALIDATION_SCOPE.actor_id,
                    identity_mode=VALIDATION_SCOPE.identity_mode.value,
                    model_alias="tapper-chat",
                ),
            )
        )
    services = replace(
        validation_http_services(), conversations=service, authorization_policy=_Allow()
    )
    client = TestClient(
        create_app(services, allowed_origins=frozenset({ORIGIN})),
        headers={"Origin": ORIGIN},
        raise_server_exceptions=False,
    )
    return client, service


def _other_owner(service: ConversationService, conversation_id: str) -> None:
    stored = service.repository.values[conversation_id]
    service.repository.values[conversation_id] = replace(stored, actor_id="someone-else")


def test_history_control_routes_are_published_with_problem_responses():
    client, _ = _client()
    paths = client.app.openapi()["paths"]
    item = paths["/api/v1/projects/{project_id}/conversations/{conversation_id}"]
    assert item["patch"]["operationId"] == "conversation_rename"
    assert item["delete"]["operationId"] == "conversation_delete"
    assert {"200", "403", "404", "422"} <= set(item["patch"]["responses"])
    assert {"204", "403", "404"} <= set(item["delete"]["responses"])
    schema = client.app.openapi()["components"]["schemas"]["ConversationRenameRequest"]
    assert schema["required"] == ["title"]
    assert schema["properties"]["title"]["maxLength"] == 120
    listing = paths["/api/v1/projects/{project_id}/conversations"]["get"]
    query = next(item for item in listing["parameters"] if item["name"] == "q")
    assert query["required"] is False


def test_rename_returns_summary_and_rejects_blank_other_actor_and_unknown():
    client, service = _client("Original")

    response = client.patch(f"{BASE}/conversation-0", json={"title": "  Renamed  "})

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"conversationId", "title", "createdAt", "updatedAt"}
    assert body["conversationId"] == "conversation-0"
    assert body["title"] == "Renamed"
    assert body["updatedAt"] >= body["createdAt"]
    assert client.get(f"{BASE}").json()["items"][0]["title"] == "Renamed"

    blank = client.patch(f"{BASE}/conversation-0", json={"title": "   "})
    assert blank.status_code == 422
    assert blank.json()["type"].endswith("/request-validation")
    too_long = client.patch(f"{BASE}/conversation-0", json={"title": "x" * 121})
    assert too_long.status_code == 422
    missing = client.patch(f"{BASE}/missing", json={"title": "Title"})
    assert missing.status_code == 404
    assert missing.json()["type"].endswith("/conversation-not-found")

    _other_owner(service, "conversation-0")
    denied = client.patch(f"{BASE}/conversation-0", json={"title": "Stolen"})
    assert denied.status_code == 403
    assert denied.json()["type"].endswith("/authorization-denied")
    assert service.repository.values["conversation-0"].title == "Renamed"


def test_delete_is_idempotent_creator_only_and_hides_every_read_path():
    client, service = _client("Delete me", "Keep me")

    _other_owner(service, "conversation-1")
    denied = client.delete(f"{BASE}/conversation-1")
    assert denied.status_code == 403
    assert denied.json()["type"].endswith("/authorization-denied")

    deleted = client.delete(f"{BASE}/conversation-0")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.delete(f"{BASE}/conversation-0").status_code == 204
    assert client.delete(f"{BASE}/missing").status_code == 404
    assert service.repository.values["conversation-0"].turns[0].state == "canceled"

    listed = client.get(BASE).json()["items"]
    assert [item["conversationId"] for item in listed] == ["conversation-1"]
    for path in (
        f"{BASE}/conversation-0",
        f"{BASE}/conversation-0/events",
        f"{BASE}/conversation-0/stream",
        f"{BASE}/conversation-0/turns/turn-0/citations/citation-1",
    ):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json()["type"].endswith("/conversation-not-found")
    cancel = client.post(f"{BASE}/conversation-0/turns/turn-0/cancel")
    assert cancel.status_code == 404
    rename = client.patch(f"{BASE}/conversation-0", json={"title": "Back"})
    assert rename.status_code == 404
    append = client.post(
        f"{BASE}/conversation-0/turns",
        json={"message": "Again", "modelAlias": "tapper-chat"},
        headers={"Idempotency-Key": "after-delete"},
    )
    assert append.status_code == 404


def test_list_search_filters_titles_and_composes_with_cursor():
    client, _ = _client("Plan alpha", "Other", "plan beta", "100% plan", "Unrelated")

    first = client.get(BASE, params={"q": "  PLAN ", "limit": 2})
    assert first.status_code == 200, first.text
    page = first.json()
    assert [item["title"] for item in page["items"]] == ["100% plan", "plan beta"]
    second = client.get(BASE, params={"q": "plan", "limit": 2, "cursor": page["nextCursor"]})
    assert [item["title"] for item in second.json()["items"]] == ["Plan alpha"]
    assert second.json()["nextCursor"] is None

    literal = client.get(BASE, params={"q": "%"}).json()["items"]
    assert [item["title"] for item in literal] == ["100% plan"]
    assert len(client.get(BASE, params={"q": "   "}).json()["items"]) == 5
    assert client.get(BASE, params={"q": "x" * 121}).status_code == 422
