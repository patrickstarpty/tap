import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tap.contracts.http import DocumentAnchor, RetrievalAnswerResponse, RetrievalCitation
from tap.interfaces.http.app import create_app
from tap.interfaces.http.sse import encode_sse
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    ConversationEvent,
    GraphContextStatus,
    RetrievalSummary,
    TurnInput,
)
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


def test_http_sse_reconnect_replays_generation_waiting_and_result_link() -> None:
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    value = TurnInput(
        message="design tests",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="tapper-chat",
    )
    asyncio.run(service.create("chat-plan", "turn-plan", "request-plan", value))
    asyncio.run(
        service.emit(
            "chat-plan",
            "turn-plan",
            "test-plan.generation.waiting",
            {"jobId": "job-1", "reason": "human-confirmation"},
        )
    )
    asyncio.run(
        service.emit(
            "chat-plan",
            "turn-plan",
            "test-plan.generation.result_ready",
            {
                "jobId": "job-1",
                "testPlanId": "plan-1",
                "revisionId": "revision-1",
                "deepLink": "/test-management/plan-1/revisions/revision-1",
            },
        )
    )
    client = TestClient(create_app(replace(validation_http_services(), conversations=service)))

    resumed = client.get(
        "/api/v1/projects/tapper-demo/conversations/chat-plan/stream",
        headers={"Last-Event-ID": "1"},
    )

    assert resumed.status_code == 200
    assert "event: test-plan.generation.waiting" in resumed.text
    assert "event: test-plan.generation.result_ready" in resumed.text
    assert "/test-management/plan-1/revisions/revision-1" in resumed.text


def test_http_sse_replays_published_terminal_answer_and_closes() -> None:
    item_id = "i" * 256
    publication_id = "p" * 256
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    asyncio.run(
        service.create(
            "chat-published",
            "turn-published",
            "request-published",
            TurnInput(
                message="Question kept out of public event payloads",
                actor_id=VALIDATION_SCOPE.actor_id,
                identity_mode="validation",
                model_alias="tapper-chat",
            ),
        )
    )
    asyncio.run(
        service.emit("chat-published", "turn-published", "answer.delta", {"text": "Evidence"})
    )
    answer = RetrievalAnswerResponse.model_validate(
        {
            "traceId": "trace-1",
            "queryPlanId": "plan-1",
            "contextSnapshotId": "context-1",
            "corpusVersion": "corpus-1",
            "retrievalProfileId": "profile-1",
            "degradedMode": False,
            "answer": "Evidence",
            "abstained": False,
            "claims": [
                {
                    "claimId": "claim-1",
                    "text": "Evidence",
                    "answerStart": 0,
                    "answerEnd": 8,
                    "citationIds": ["citation-1"],
                }
            ],
            "citations": [
                {
                    "citationId": "citation-1",
                    "evidenceLabel": "1",
                    "chunkId": "chunk-1",
                    "logicalChunkId": "logical-1",
                    "source": {
                        "sourceId": "source-1",
                        "sourceType": "document",
                        "revisionKind": "blob_version",
                        "revision": "revision-1",
                        "sourceContentHash": "sha256:" + "a" * 64,
                        "anchor": {
                            "type": "document",
                            "inventoryItemId": item_id,
                            "startOffset": 0,
                            "endOffset": 8,
                        },
                    },
                    "chunkContentHash": "sha256:" + "b" * 64,
                    "contentRole": "source",
                    "publicationId": publication_id,
                    "approvalDigest": "sha256:" + "c" * 64,
                    "approvedItemId": item_id,
                }
            ],
        }
    )
    citation_payload = answer.citations[0].model_dump(mode="json", by_alias=True)
    for field in ("publicationId", "approvedItemId"):
        with pytest.raises(ValidationError):
            RetrievalCitation.model_validate({**citation_payload, field: "x" * 257})
    with pytest.raises(ValidationError):
        DocumentAnchor.model_validate({"type": "document", "inventoryItemId": "x" * 257})
    asyncio.run(
        service.complete_evidence(
            "chat-published",
            "turn-published",
            AnswerEvidence(
                "Evidence",
                "completed",
                RetrievalSummary("completed"),
                GraphContextStatus.NOT_REQUESTED,
            ),
            terminal_event=(
                "turn.completed",
                {"answer": answer.model_dump(mode="json", by_alias=True)},
            ),
        )
    )
    client = TestClient(
        create_app(replace(validation_http_services(), conversations=service)),
        raise_server_exceptions=False,
    )

    resumed = client.get(
        "/api/v1/projects/tapper-demo/conversations/chat-published/stream",
        headers={"Last-Event-ID": "2"},
    )

    assert resumed.status_code == 200
    assert [line for line in resumed.text.splitlines() if line.startswith("id: ")] == [
        "id: 3",
        "id: 4",
    ]
    assert [line for line in resumed.text.splitlines() if line.startswith("event: ")] == [
        "event: turn.completed",
        "event: conversation.turn.completed",
    ]
    terminal = json.loads(
        next(line[6:] for line in resumed.text.splitlines() if line.startswith("data: "))
    )
    citation = terminal["event"]["payload"]["answer"]["citations"][0]
    assert citation["source"]["anchor"]["inventoryItemId"] == item_id
    assert citation["publicationId"] == publication_id
    assert citation["approvalDigest"] == "sha256:" + "c" * 64
    assert citation["approvedItemId"] == item_id
    assert "Question kept out" not in resumed.text
    assert '"answerPlan"' not in resumed.text
