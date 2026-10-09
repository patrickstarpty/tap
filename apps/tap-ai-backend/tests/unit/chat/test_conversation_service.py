from dataclasses import replace
from datetime import UTC, datetime

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    AnswerEvidenceSnapshot,
    GraphContextStatus,
    RetrievalSummary,
    TurnInput,
    TurnInputSnapshot,
)


def _input(message: str = "What changed?") -> TurnInput:
    return TurnInput(
        message=message,
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode=VALIDATION_SCOPE.identity_mode.value,
        model_alias="qwen-plus",
        source_revision_ids=("revision-1",),
        document_revision_ids=("document-revision-1",),
        agent_revision_id="validation-knowledge-agent-v1",
        agent_revision_digest="sha256:" + "a" * 64,
        skill_revision_ids=("validation-citation-skill-v1",),
        skill_revision_digests=("sha256:" + "b" * 64,),
        retrieval_policy_digest="sha256:" + "c" * 64,
    )


def test_pre_insights_snapshot_hashes_remain_loadable_and_new_content_is_bound():
    # Digests captured from the pre-Insights persisted format at 6ef806e.
    value = TurnInput("Explain", "actor-1", "validation", "tapper-chat")
    now = datetime(2026, 9, 27, tzinfo=UTC)
    original = TurnInputSnapshot(
        "input-1",
        "project-1",
        "turn-1",
        value,
        "sha256:1cf8c481155c488bef9bc86c9c1cf7350cea1a5163b6d50d00d4a0c4fb763931",
        now,
    )
    answer = AnswerEvidenceSnapshot(
        "answer-1",
        "project-1",
        "turn-1",
        original.digest,
        AnswerEvidence(
            "Grounded", "completed", RetrievalSummary("completed"), GraphContextStatus.NOT_REQUESTED
        ),
        "sha256:398af4f45ba60c85d478ac9ae25c93518f5eadaf8177e556e9c8ca08cf6d7e7b",
        "sha256:1c6a41d85f7a007bae6cfe6092d5c97837afc4ffc0eecae47fcd9cf52d816574",
        now,
    )
    with pytest.raises(ValueError, match="digest"):
        replace(
            original,
            value=replace(value, insights_query_id="query-1", insights_report_refs=("report-1",)),
        )
    with pytest.raises(ValueError, match="digest"):
        replace(answer, value=replace(answer.value, insights_explanation={"queryId": "query-1"}))


@pytest.mark.asyncio
async def test_first_message_atomically_creates_conversation_turn_and_immutable_input_snapshot():
    repository = InMemoryConversationRepository()
    service = ConversationService(repository, scope=VALIDATION_SCOPE)
    accepted = await service.create("conversation-1", "turn-1", "request-1", _input())

    detail = await service.load("conversation-1")
    assert accepted.turn_id == "turn-1"
    assert detail.turns[0].input_snapshot.digest.startswith("sha256:")
    assert detail.events[0].payload == {
        "conversationId": "conversation-1",
        "turnId": "turn-1",
        "inputSnapshotDigest": detail.turns[0].input_snapshot.digest,
    }


@pytest.mark.asyncio
async def test_blank_first_message_never_creates_a_conversation():
    repository = InMemoryConversationRepository()
    service = ConversationService(repository, scope=VALIDATION_SCOPE)
    with pytest.raises(ValueError, match="blank"):
        await service.create("conversation-1", "turn-1", "request-1", _input(" \n"))
    assert await service.list(limit=20, cursor=None) == ((), None)


@pytest.mark.asyncio
async def test_append_is_idempotent_but_conflicting_reuse_is_rejected_and_pages_are_stable():
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    await service.create("conversation-1", "turn-1", "request-1", _input())
    original = await service.append("conversation-1", "turn-2", "request-2", _input("Next"))
    replay = await service.append("conversation-1", "discard", "request-2", _input("Next"))
    assert replay.turn_id == original.turn_id
    with pytest.raises(ValueError, match="idempotency"):
        await service.append("conversation-1", "discard", "request-2", _input("Different"))
    with pytest.raises(ValueError, match="idempotency"):
        await service.append(
            "conversation-1",
            "discard",
            "request-2",
            replace(_input("Next"), model_alias="other-model"),
        )
    page, cursor = await service.list(limit=1, cursor=None)
    assert [item.conversation_id for item in page] == ["conversation-1"]
    assert cursor is None


@pytest.mark.asyncio
async def test_completed_turn_cannot_be_canceled_and_retry_creates_a_new_attempt():
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    await service.create("conversation-1", "turn-1", "request-1", _input())
    await service.complete_for_test(
        "conversation-1", "turn-1", answer="Grounded", graph_status="NOT_REQUESTED"
    )
    completed = await service.cancel("conversation-1", "turn-1")
    assert completed.state == "completed"
    retry = await service.retry("conversation-1", "turn-1", "turn-2", "request-2")
    assert retry.attempt == 0
    assert retry.input_snapshot.digest != completed.input_snapshot.digest
    assert retry.input_snapshot.value == completed.input_snapshot.value


@pytest.mark.asyncio
async def test_in_memory_completion_binds_terminal_event_and_cannot_reverse_cancellation():
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    await service.create("conversation-1", "turn-1", "request-1", _input())
    evidence = AnswerEvidence(
        "Grounded",
        "completed",
        RetrievalSummary("completed"),
        GraphContextStatus.NOT_REQUESTED,
    )
    completed = await service.complete_evidence(
        "conversation-1",
        "turn-1",
        evidence,
        terminal_event=("turn.completed", {"answer": {"answer": "Grounded"}}),
    )
    detail = await service.load("conversation-1")
    assert completed.state == "completed"
    assert detail.events[-2].turn_id == "turn-1"

    await service.create("conversation-2", "turn-2", "request-2", _input())
    canceled = await service.cancel("conversation-2", "turn-2")
    before = await service.load("conversation-2")
    late = await service.complete_evidence(
        "conversation-2",
        "turn-2",
        evidence,
        terminal_event=("turn.completed", {"answer": {"answer": "late"}}),
    )
    after = await service.load("conversation-2")
    assert canceled.state == late.state == "canceled"
    assert after.events == before.events


@pytest.mark.asyncio
async def test_complete_evidence_persists_graph_context_ready_stream_event():
    """`graph.context_ready` must be a recognized
    stream event type, not just validated by the chat_stream/HTTP contracts
    in isolation. `ConversationEvent.__post_init__` must accept it, or every
    non-failed knowledge turn that streams it would crash at completion."""
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    await service.create("conversation-1", "turn-1", "request-1", _input())
    evidence = AnswerEvidence(
        "Grounded",
        "completed",
        RetrievalSummary("completed"),
        GraphContextStatus.APPLIED,
        graph_snapshot_id="7",
    )
    completed = await service.complete_evidence(
        "conversation-1",
        "turn-1",
        evidence,
        terminal_event=("turn.completed", {"answer": {"answer": "Grounded"}}),
        stream_events=(
            (
                "graph.context_ready",
                {
                    "status": "APPLIED",
                    "graphVersion": "7",
                    "seedCount": 2,
                    "paths": [["核保流程", "健康告知"]],
                    "relationCount": 1,
                },
            ),
        ),
    )
    assert completed.state == "completed"
    detail = await service.load("conversation-1")
    graph_context_ready_events = [
        event for event in detail.events if event.event_type == "graph.context_ready"
    ]
    assert len(graph_context_ready_events) == 1
    assert graph_context_ready_events[0].payload["status"] == "APPLIED"


def test_snapshot_digest_is_bound_to_project_turn_and_immutable_input():
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    turn, _ = service._turn("conversation-1", "turn-1", "request-1", _input())
    with pytest.raises(ValueError, match="digest"):
        replace(turn.input_snapshot, project_id="other-project")
    with pytest.raises(ValueError, match="digest"):
        replace(turn.input_snapshot, digest="sha256:" + "f" * 64)
