from dataclasses import replace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.application.feedback import (
    FeedbackNotAllowed,
    FeedbackService,
    InMemoryFeedbackRepository,
)
from tap.modules.chat.domain.conversations import TurnInput

OTHER_SCOPE = replace(VALIDATION_SCOPE, actor_id="other-actor")


def _input(message: str = "What changed?") -> TurnInput:
    return TurnInput(
        message=message,
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode=VALIDATION_SCOPE.identity_mode.value,
        model_alias="qwen-plus",
    )


async def _completed_conversation(repository, *, scope=VALIDATION_SCOPE):
    conversations = ConversationService(repository, scope=scope)
    await conversations.create("conversation-1", "turn-1", "request-1", _input())
    await conversations.complete_for_test(
        "conversation-1", "turn-1", answer="Grounded answer", graph_status="NOT_REQUESTED"
    )
    return conversations


@pytest.mark.asyncio
async def test_submit_requires_completed_owned_turn():
    repository = InMemoryConversationRepository()
    conversations = ConversationService(repository, scope=VALIDATION_SCOPE)
    await conversations.create("conversation-1", "turn-1", "request-1", _input())

    service = FeedbackService(InMemoryFeedbackRepository(), conversations, scope=VALIDATION_SCOPE)

    with pytest.raises(FeedbackNotAllowed):
        await service.submit("conversation-1", "turn-1", "up", None)

    await conversations.complete_for_test(
        "conversation-1", "turn-1", answer="partial", graph_status="NOT_REQUESTED"
    )

    other_service = FeedbackService(InMemoryFeedbackRepository(), conversations, scope=OTHER_SCOPE)
    with pytest.raises(AuthorizationDenied):
        await other_service.submit("conversation-1", "turn-1", "up", None)


@pytest.mark.asyncio
async def test_comment_only_with_down_and_bounded():
    repository = InMemoryConversationRepository()
    conversations = await _completed_conversation(repository)
    service = FeedbackService(InMemoryFeedbackRepository(), conversations, scope=VALIDATION_SCOPE)

    up = await service.submit("conversation-1", "turn-1", "up", "ignored comment")
    assert up.rating == "up"
    assert up.comment is None

    with pytest.raises(ValueError, match="500"):
        await service.submit("conversation-1", "turn-1", "down", "x" * 501)


@pytest.mark.asyncio
async def test_switch_and_retract():
    repository = InMemoryConversationRepository()
    conversations = await _completed_conversation(repository)
    service = FeedbackService(InMemoryFeedbackRepository(), conversations, scope=VALIDATION_SCOPE)

    up = await service.submit("conversation-1", "turn-1", "up", None)
    down = await service.submit("conversation-1", "turn-1", "down", "needs work")
    assert down.feedback_id == up.feedback_id
    assert down.created_at == up.created_at
    assert down.updated_at > up.updated_at
    assert down.rating == "down"
    assert down.comment == "needs work"

    await service.retract("conversation-1", "turn-1")
    stored = await service.repository.get("turn-1", VALIDATION_SCOPE.actor_id)
    assert stored is not None
    assert stored.rating is None
    assert stored.comment is None
    assert stored.updated_at > down.updated_at


@pytest.mark.asyncio
async def test_mine_returns_only_active_feedback():
    repository = InMemoryConversationRepository()
    conversations = await _completed_conversation(repository)
    service = FeedbackService(InMemoryFeedbackRepository(), conversations, scope=VALIDATION_SCOPE)

    await service.submit("conversation-1", "turn-1", "up", None)
    mine = await service.mine(["turn-1"])
    assert mine["turn-1"].rating == "up"

    await service.retract("conversation-1", "turn-1")
    mine_after_retract = await service.mine(["turn-1"])
    assert "turn-1" not in mine_after_retract
