import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.sse import encode_sse
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.domain.conversations import ConversationEvent, TurnInput
from tests.conftest import validation_http_services


def test_sse_ids_are_sequences_and_resume_starts_after_last_event_id():
    rendered = encode_sse(
        [
            {
                "eventId": "event-4",
                "sequence": 4,
                "chatId": "chat-1",
                "turnId": "turn-1",
                "occurredAt": "2026-09-09T00:00:00Z",
                "schemaVersion": 1,
                "event": {"type": "turn.started", "payload": {"state": "running"}},
            },
            {
                "eventId": "event-5",
                "sequence": 5,
                "chatId": "chat-1",
                "turnId": "turn-1",
                "occurredAt": "2026-09-09T00:00:01Z",
                "schemaVersion": 1,
                "event": {"type": "answer.delta", "payload": {"text": "a"}},
            },
        ],
        last_event_id="4",
    )
    assert "id: 4" not in rendered
    assert "id: 5\n" in rendered
    assert "event: answer.delta\n" in rendered
    assert '"eventId":"event-5"' in rendered
    assert '"chatId":"chat-1"' in rendered


def test_unknown_event_type_is_rejected_and_invalid_resume_never_replays():
    with pytest.raises(ValueError, match="unknown conversation event"):
        ConversationEvent("event-1", 1, "provider.secret", {}, datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="Last-Event-ID"):
        encode_sse([], last_event_id="-1")


def test_http_sse_reconnect_returns_complete_envelope_for_events_added_while_disconnected():
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    value = TurnInput(
        message="question",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="tapper-chat",
    )
    asyncio.run(service.create("chat-1", "turn-1", "request-1", value))
    services = replace(validation_http_services(), conversations=service)
    client = TestClient(create_app(services))
    first = client.get("/api/v1/projects/tapper-demo/conversations/chat-1/stream")
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("text/event-stream")
    assert "id: 1\n" in first.text

    asyncio.run(service.emit("chat-1", "turn-1", "answer.delta", {"text": "later"}))
    resumed = client.get(
        "/api/v1/projects/tapper-demo/conversations/chat-1/stream",
        headers={"Last-Event-ID": "1"},
    )
    assert "id: 1\n" not in resumed.text
    assert "id: 2\n" in resumed.text
    data = json.loads(
        next(line[6:] for line in resumed.text.splitlines() if line.startswith("data: "))
    )
    assert data == {
        "eventId": data["eventId"],
        "sequence": 2,
        "chatId": "chat-1",
        "turnId": "turn-1",
        "occurredAt": data["occurredAt"],
        "schemaVersion": 1,
        "event": {"type": "answer.delta", "payload": {"text": "later"}},
    }
    invalid = client.get(
        "/api/v1/projects/tapper-demo/conversations/chat-1/stream",
        headers={"Last-Event-ID": "-1"},
    )
    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith("application/problem+json")
