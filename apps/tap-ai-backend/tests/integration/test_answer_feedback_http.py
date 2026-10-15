import asyncio
from dataclasses import replace

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.application.feedback import FeedbackService, InMemoryFeedbackRepository
from tap.modules.chat.domain.conversations import TurnInput
from tests.conftest import validation_http_services

BASE = "/api/v1/projects/tapper-demo/conversations"
OTHER_SCOPE = replace(VALIDATION_SCOPE, actor_id="other-actor")
ORIGIN = "http://127.0.0.1:15175"
ORIGIN_HEADERS = {"Origin": ORIGIN}


def _input():
    return TurnInput(
        message="question",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
    )


def _client(*, completed: bool = True, owned_by_other: bool = False):
    repository = InMemoryConversationRepository()
    conversations = ConversationService(repository, scope=VALIDATION_SCOPE)
    asyncio.run(conversations.create("conversation-1", "turn-1", "request-1", _input()))
    if completed:
        asyncio.run(
            conversations.complete_for_test(
                "conversation-1", "turn-1", answer="Grounded", graph_status="NOT_REQUESTED"
            )
        )
    if owned_by_other:
        # Same Project scope, different owning actor: the demo app has a single
        # caller identity, so "someone else's Conversation" is simulated by
        # directly rewriting the stored owner rather than a second scope.
        repository.values["conversation-1"] = replace(
            repository.values["conversation-1"], actor_id=OTHER_SCOPE.actor_id
        )
    feedback = FeedbackService(InMemoryFeedbackRepository(), conversations, scope=VALIDATION_SCOPE)
    services = replace(validation_http_services(), conversations=conversations, feedback=feedback)
    app = create_app(services, allowed_origins=frozenset({ORIGIN}))
    return TestClient(app)


def test_put_feedback_is_idempotent_and_reflected_on_conversation_get():
    client = _client()
    first = client.put(
        f"{BASE}/conversation-1/turns/turn-1/feedback",
        json={"rating": "up"},
        headers=ORIGIN_HEADERS,
    )
    assert first.status_code == 200, first.text
    assert first.json() == {"rating": "up", "comment": None}

    second = client.put(
        f"{BASE}/conversation-1/turns/turn-1/feedback",
        json={"rating": "up"},
        headers=ORIGIN_HEADERS,
    )
    assert second.status_code == 200
    assert second.json() == {"rating": "up", "comment": None}

    detail = client.get(f"{BASE}/conversation-1")
    assert detail.status_code == 200
    turn = detail.json()["turns"][0]
    assert turn["myFeedback"] == {"rating": "up", "comment": None}


def test_delete_feedback_clears_my_feedback_on_get():
    client = _client()
    client.put(
        f"{BASE}/conversation-1/turns/turn-1/feedback",
        json={"rating": "down"},
        headers=ORIGIN_HEADERS,
    )

    deleted = client.delete(f"{BASE}/conversation-1/turns/turn-1/feedback", headers=ORIGIN_HEADERS)
    assert deleted.status_code == 204

    detail = client.get(f"{BASE}/conversation-1")
    turn = detail.json()["turns"][0]
    assert turn["myFeedback"] is None


def test_feedback_on_incomplete_turn_is_409():
    client = _client(completed=False)
    response = client.put(
        f"{BASE}/conversation-1/turns/turn-1/feedback",
        json={"rating": "up"},
        headers=ORIGIN_HEADERS,
    )
    assert response.status_code == 409


def test_feedback_on_other_actors_conversation_is_404():
    client = _client(owned_by_other=True)
    response = client.put(
        f"{BASE}/conversation-1/turns/turn-1/feedback",
        json={"rating": "up"},
        headers=ORIGIN_HEADERS,
    )
    assert response.status_code == 404
    deleted = client.delete(f"{BASE}/conversation-1/turns/turn-1/feedback", headers=ORIGIN_HEADERS)
    assert deleted.status_code == 404


def test_feedback_comment_over_bound_is_422():
    client = _client()
    response = client.put(
        f"{BASE}/conversation-1/turns/turn-1/feedback",
        json={"rating": "down", "comment": "x" * 501},
        headers=ORIGIN_HEADERS,
    )
    assert response.status_code == 422
