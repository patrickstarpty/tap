"""The generation worker persists incremental answer text and resets it on reclaim."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_knowledge_answer_streaming import knowledge_models

from tap.contracts.chat_stream import ChatEventEnvelope
from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.application.conversations import (
    ConversationConflict,
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    GraphContextStatus,
    RetrievalSummary,
    TurnInput,
)


def _input() -> TurnInput:
    return TurnInput(
        message="How are claims resolved?",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        agent_revision_id="validation-knowledge-agent-v1",
        agent_revision_digest="sha256:" + "a" * 64,
        skill_revision_ids=("validation-citation-skill-v1",),
        skill_revision_digests=("sha256:" + "b" * 64,),
        retrieval_policy_digest="sha256:" + "c" * 64,
    )


def _response(text: str) -> SimpleNamespace:
    payload = {
        "traceId": "trace-1",
        "queryPlanId": "plan-1",
        "contextSnapshotId": "context-1",
        "corpusVersion": "v1",
        "retrievalProfileId": "quick",
        "degradedMode": False,
        "answer": text,
        "abstained": False,
        "abstentionReason": None,
        "claims": [],
        "citations": [],
    }
    return SimpleNamespace(
        answer=text,
        citations=[],
        abstained=False,
        trace_id="trace-1",
        model_dump=lambda **_: payload,
    )


class _StreamingKnowledge:
    """Answers through the deterministic gateway's real structured stream."""

    def __init__(self) -> None:
        self.final_answers: list[str] = []

    async def answer_conversation(self, request, frozen_input, *, on_delta=None):
        from test_knowledge_api import _claim_resolution_evidence

        models, _gateway = knowledge_models()
        result = await models.answer(
            request.query, (_claim_resolution_evidence(),), "quick-hybrid-v1", on_delta=on_delta
        )
        self.final_answers.append(result.text)
        return _response(result.text)


async def _accepted() -> ConversationService:
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    await service.create("conversation-1", "turn-1", "request-1", _input())
    return service


def _envelope(event) -> dict[str, object]:
    return {
        "eventId": event.event_id,
        "sequence": event.sequence,
        "chatId": "conversation-1",
        "turnId": event.turn_id or "turn-1",
        "occurredAt": event.occurred_at.isoformat(),
        "schemaVersion": 1,
        "event": {"type": event.event_type, "payload": dict(event.payload)},
    }


@pytest.mark.asyncio
async def test_worker_persists_incremental_deltas_that_concatenate_to_the_answer():
    service = await _accepted()
    knowledge = _StreamingKnowledge()
    worker = GenerationWorker(service, knowledge, checkpointer=InMemorySaver(), delta_max_chars=8)

    assert await worker.run_once(limit=1) == 1

    conversation = await service.load("conversation-1")
    assert conversation.turns[0].state == "completed"
    events = conversation.events
    for event in events:
        ChatEventEnvelope.model_validate(_envelope(event))
    deltas = [event.payload["text"] for event in events if event.event_type == "answer.delta"]
    assert len(deltas) > 1
    assert "".join(deltas) == knowledge.final_answers[0]
    assert "".join(deltas) == conversation.turns[0].answer_snapshot.value.answer
    types = [event.event_type for event in events]
    assert types.index("turn.started") < types.index("answer.delta")
    assert max(i for i, kind in enumerate(types) if kind == "answer.delta") < types.index(
        "turn.completed"
    )
    assert "answer.reset" not in types
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))


@pytest.mark.asyncio
async def test_delta_persistence_failure_disables_streaming_but_completes_the_answer(
    monkeypatch, caplog
):
    service = await _accepted()
    knowledge = _StreamingKnowledge()
    original_emit = service.emit
    attempts: list[str] = []

    async def flaky_emit(conversation_id, turn_id, event_type, payload, **kwargs):
        if event_type == "answer.delta":
            attempts.append(payload["text"])
            raise TimeoutError("database timeout")
        return await original_emit(conversation_id, turn_id, event_type, payload, **kwargs)

    monkeypatch.setattr(service, "emit", flaky_emit)
    worker = GenerationWorker(service, knowledge, checkpointer=InMemorySaver(), delta_max_chars=8)

    with caplog.at_level("WARNING"):
        assert await worker.run_once(limit=1) == 1

    conversation = await service.load("conversation-1")
    assert conversation.turns[0].state == "completed"
    assert conversation.turns[0].answer_snapshot.value.answer == knowledge.final_answers[0]
    assert len(attempts) == 1
    assert "answer delta persistence failed" in caplog.text
    assert "TimeoutError" in caplog.text
    assert not [event for event in conversation.events if event.event_type == "answer.delta"]


@pytest.mark.asyncio
async def test_default_batching_coalesces_a_short_answer():
    service = await _accepted()
    knowledge = _StreamingKnowledge()

    await GenerationWorker(service, knowledge, checkpointer=InMemorySaver()).run_once(limit=1)

    events = (await service.load("conversation-1")).events
    deltas = [event.payload["text"] for event in events if event.event_type == "answer.delta"]
    assert deltas and "".join(deltas) == knowledge.final_answers[0]


@pytest.mark.asyncio
async def test_reclaimed_turn_resets_provisional_text_before_new_deltas():
    service = await _accepted()
    repository = service.repository
    abandoned = (await repository.claim_queued(limit=1))[0][1]
    await service.emit(
        "conversation-1",
        "turn-1",
        "answer.delta",
        {"text": "abandoned"},
        lease_token=abandoned.lease_token,
    )
    repository.lease_expires_at["turn-1"] = datetime.now(UTC) - timedelta(seconds=1)
    knowledge = _StreamingKnowledge()

    worker = GenerationWorker(service, knowledge, checkpointer=InMemorySaver(), delta_max_chars=8)
    assert await worker.run_once(limit=1) == 1

    events = (await service.load("conversation-1")).events
    types = [event.event_type for event in events]
    reset_index = types.index("answer.reset")
    assert events[reset_index].payload == {"attempt": 2}
    assert types[reset_index - 1] == "turn.started"
    assert types.count("turn.started") == 2
    after_reset = [
        event.payload["text"]
        for event in events[reset_index:]
        if event.event_type == "answer.delta"
    ]
    assert "".join(after_reset) == knowledge.final_answers[0]
    for event in events:
        ChatEventEnvelope.model_validate(_envelope(event))


@pytest.mark.asyncio
async def test_stale_lease_cannot_append_stream_events_in_memory():
    service = await _accepted()
    repository = service.repository
    first = (await repository.claim_queued(limit=1))[0][1]
    repository.lease_expires_at["turn-1"] = datetime.now(UTC) - timedelta(seconds=1)
    second = (await repository.claim_queued(limit=1))[0][1]
    assert second.attempt == 2 and second.lease_token != first.lease_token

    with pytest.raises(ConversationConflict, match="lease"):
        await service.emit(
            "conversation-1", "turn-1", "answer.delta", {"text": "x"}, lease_token=first.lease_token
        )
    event = await service.emit(
        "conversation-1", "turn-1", "answer.delta", {"text": "y"}, lease_token=second.lease_token
    )
    assert event.turn_id == "turn-1"


def test_answer_reset_contract_requires_a_positive_attempt():
    valid = {
        "eventId": "event-1",
        "sequence": 1,
        "chatId": "chat-1",
        "turnId": "turn-1",
        "occurredAt": "2026-11-04T00:00:00Z",
        "schemaVersion": 1,
        "event": {"type": "answer.reset", "payload": {"attempt": 2}},
    }
    assert ChatEventEnvelope.model_validate(valid).event.payload.attempt == 2
    for payload in ({"attempt": 0}, {"attempt": "2"}, {}, {"attempt": 2, "text": ""}):
        with pytest.raises(ValueError):
            ChatEventEnvelope.model_validate(
                {**valid, "event": {"type": "answer.reset", "payload": payload}}
            )


async def _streamed(service: ConversationService) -> str:
    claimed = (await service.repository.claim_queued(limit=1))[0][1]
    for text in ("provisional ", "answer"):
        await service.emit(
            "conversation-1",
            "turn-1",
            "answer.delta",
            {"text": text},
            lease_token=claimed.lease_token,
        )
    return claimed.lease_token


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "terminal_type"), [("failed", "turn.failed"), ("abstained", "turn.abstained")]
)
async def test_unfinished_turn_withdraws_provisional_answer_text(outcome, terminal_type):
    service = await _accepted()
    lease_token = await _streamed(service)

    await service.complete_evidence(
        "conversation-1",
        "turn-1",
        AnswerEvidence("", outcome, RetrievalSummary(outcome), GraphContextStatus.NOT_REQUESTED),
        lease_token=lease_token,
        terminal_event=(terminal_type, {"state": outcome}),
    )

    stored = service.repository.values["conversation-1"].events
    stored_types = [event.event_type for event in stored]
    assert stored_types.count("answer.delta") == 2
    reset = stored_types.index("answer.reset")
    assert stored[reset].payload == {"attempt": 1}
    assert stored_types[reset + 1] == terminal_type
    assert [event.sequence for event in stored] == list(range(1, len(stored) + 1))

    for events in (
        (await service.load("conversation-1")).events,
        await service.events_after("conversation-1", 0),
    ):
        types = [event.event_type for event in events]
        assert "answer.delta" not in types
        assert types[-3:] == ["answer.reset", terminal_type, "conversation.turn.completed"]
        ChatEventEnvelope.model_validate(_envelope(events[-3]))


@pytest.mark.asyncio
async def test_canceled_turn_withdraws_provisional_answer_text():
    service = await _accepted()
    await _streamed(service)

    await service.cancel("conversation-1", "turn-1")

    stored_types = [
        event.event_type for event in service.repository.values["conversation-1"].events
    ]
    assert stored_types[-2:] == ["answer.reset", "conversation.turn.completed"]
    types = [event.event_type for event in (await service.load("conversation-1")).events]
    assert "answer.delta" not in types
    assert types[-2:] == ["answer.reset", "conversation.turn.completed"]


@pytest.mark.asyncio
async def test_unfinished_turn_without_provisional_text_writes_no_reset():
    service = await _accepted()

    await service.cancel("conversation-1", "turn-1")

    types = [event.event_type for event in (await service.load("conversation-1")).events]
    assert types == ["conversation.turn.requested", "conversation.turn.completed"]


@pytest.mark.asyncio
async def test_completed_turn_keeps_its_answer_text_beside_a_withdrawn_turn():
    service = await _accepted()
    lease_token = await _streamed(service)
    await service.complete_evidence(
        "conversation-1",
        "turn-1",
        AnswerEvidence(
            "provisional answer",
            "completed",
            RetrievalSummary("completed"),
            GraphContextStatus.NOT_REQUESTED,
        ),
        lease_token=lease_token,
        terminal_event=("turn.completed", {"state": "completed"}),
    )
    await service.append("conversation-1", "turn-2", "request-2", _input())
    claimed = (await service.repository.claim_queued(limit=1))[0][1]
    assert claimed.turn_id == "turn-2"
    await service.emit(
        "conversation-1",
        "turn-2",
        "answer.delta",
        {"text": "withdrawn"},
        lease_token=claimed.lease_token,
    )
    await service.cancel("conversation-1", "turn-2")

    events = (await service.load("conversation-1")).events
    deltas = [
        (event.turn_id, event.payload["text"])
        for event in events
        if event.event_type == "answer.delta"
    ]
    assert deltas == [("turn-1", "provisional "), ("turn-1", "answer")]
    assert [event.event_type for event in events if event.turn_id == "turn-1"].count(
        "answer.reset"
    ) == 0
    sequences = [event.sequence for event in service.repository.values["conversation-1"].events]
    assert sequences == list(range(1, len(sequences) + 1))
