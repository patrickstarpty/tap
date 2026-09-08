from dataclasses import replace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.domain.conversations import TurnInput


def _input(message: str = "What changed?") -> TurnInput:
    return TurnInput(
        message=message,
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode=VALIDATION_SCOPE.identity_mode.value,
        model_alias="tapper-chat",
        source_revision_ids=("revision-1",),
        document_revision_ids=("document-revision-1",),
        agent_revision_id="validation-knowledge-agent-v1",
        agent_revision_digest="sha256:" + "a" * 64,
        skill_revision_ids=("validation-citation-skill-v1",),
        skill_revision_digests=("sha256:" + "b" * 64,),
        retrieval_policy_digest="sha256:" + "c" * 64,
    )


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
    assert retry.attempt == 2
    assert retry.input_snapshot.digest != completed.input_snapshot.digest
    assert retry.input_snapshot.value == completed.input_snapshot.value


def test_snapshot_digest_is_bound_to_project_turn_and_immutable_input():
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    turn, _ = service._turn("conversation-1", "turn-1", "request-1", _input())
    with pytest.raises(ValueError, match="digest"):
        replace(turn.input_snapshot, project_id="other-project")
    with pytest.raises(ValueError, match="digest"):
        replace(turn.input_snapshot, digest="sha256:" + "f" * 64)
