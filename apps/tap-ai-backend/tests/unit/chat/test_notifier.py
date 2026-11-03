"""Conversation event notifiers wake readers and fall back safely."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.application.notifier import (
    InMemoryConversationEventNotifier,
    conversation_channel,
)
from tap.platform.messaging.redis_chat_notifier import RedisConversationEventNotifier


def test_channel_is_project_and_chat_scoped():
    assert (
        conversation_channel(VALIDATION_SCOPE, "chat-1")
        == f"tap:chat:{VALIDATION_SCOPE.project_id}:chat-1"
    )


@pytest.mark.asyncio
async def test_in_memory_wait_returns_true_after_publish():
    notifier = InMemoryConversationEventNotifier()
    waiter = asyncio.create_task(notifier.wait(VALIDATION_SCOPE, "chat-1", 5))
    await asyncio.sleep(0)

    started = time.monotonic()
    await notifier.publish(VALIDATION_SCOPE, "chat-1")

    assert await waiter is True
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_in_memory_wait_times_out_and_ignores_other_chats():
    notifier = InMemoryConversationEventNotifier()
    waiter = asyncio.create_task(notifier.wait(VALIDATION_SCOPE, "chat-1", 0.05))
    await asyncio.sleep(0)
    await notifier.publish(VALIDATION_SCOPE, "chat-2")

    assert await waiter is False
    assert notifier._waiters == {}


@pytest.mark.asyncio
async def test_in_memory_publish_from_another_event_loop_wakes_the_reader():
    notifier = InMemoryConversationEventNotifier()
    waiter = asyncio.create_task(notifier.wait(VALIDATION_SCOPE, "chat-1", 5))
    await asyncio.sleep(0)

    thread = threading.Thread(
        target=lambda: asyncio.run(notifier.publish(VALIDATION_SCOPE, "chat-1"))
    )
    thread.start()
    thread.join()

    assert await asyncio.wait_for(waiter, 1) is True


class _PubSub:
    def __init__(self, messages, *, fail: Exception | None = None) -> None:
        self.messages = list(messages)
        self.fail = fail
        self.subscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel):
        if self.fail is not None:
            raise self.fail
        self.subscribed.append(channel)

    async def get_message(self, *, ignore_subscribe_messages, timeout):
        assert ignore_subscribe_messages is True
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(timeout)
        return None

    async def aclose(self):
        self.closed = True


class _Redis:
    def __init__(self, pubsub: _PubSub) -> None:
        self._pubsub = pubsub
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel, message):
        self.published.append((channel, message))

    def pubsub(self, **_kwargs):
        return self._pubsub


@pytest.mark.asyncio
async def test_redis_publish_uses_the_conversation_channel():
    redis = _Redis(_PubSub([]))
    await RedisConversationEventNotifier(redis).publish(VALIDATION_SCOPE, "chat-1")
    assert redis.published == [(conversation_channel(VALIDATION_SCOPE, "chat-1"), "1")]


@pytest.mark.asyncio
async def test_redis_wait_returns_true_on_message_and_closes_the_subscription():
    pubsub = _PubSub([None, {"type": "message", "data": "1"}])
    notifier = RedisConversationEventNotifier(_Redis(pubsub))

    assert await notifier.wait(VALIDATION_SCOPE, "chat-1", 1) is True
    assert pubsub.subscribed == [conversation_channel(VALIDATION_SCOPE, "chat-1")]
    assert pubsub.closed is True


@pytest.mark.asyncio
async def test_redis_wait_times_out_to_false():
    pubsub = _PubSub([])
    assert (
        await RedisConversationEventNotifier(_Redis(pubsub)).wait(VALIDATION_SCOPE, "chat-1", 0.05)
        is False
    )
    assert pubsub.closed is True


@pytest.mark.asyncio
async def test_redis_wait_failure_returns_false_for_database_fallback():
    pubsub = _PubSub([], fail=ConnectionError("redis down"))
    assert (
        await RedisConversationEventNotifier(_Redis(pubsub)).wait(VALIDATION_SCOPE, "chat-1", 1)
        is False
    )
    assert pubsub.closed is True


class _RecordingNotifier:
    def __init__(self, *, fail: bool = False) -> None:
        self.published: list[tuple[str, str]] = []
        self.fail = fail

    async def publish(self, scope, chat_id):
        self.published.append((scope.project_id, chat_id))
        if self.fail:
            raise ConnectionError("redis down")

    async def wait(self, scope, chat_id, timeout):
        return False


def _turn_input():
    from tap.modules.chat.domain.conversations import TurnInput

    return TurnInput(
        message="question",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
    )


@pytest.mark.asyncio
async def test_in_memory_repository_publishes_after_every_event_write():
    from tap.modules.chat.application.conversations import (
        ConversationService,
        InMemoryConversationRepository,
    )
    from tap.modules.chat.domain.conversations import (
        AnswerEvidence,
        GraphContextStatus,
        RetrievalSummary,
    )

    notifier = _RecordingNotifier()
    service = ConversationService(InMemoryConversationRepository(notifier), scope=VALIDATION_SCOPE)
    await service.create("chat-1", "turn-1", "request-1", _turn_input())
    await service.append("chat-1", "turn-2", "request-2", _turn_input())
    claimed = await service.repository.claim_queued(limit=1)
    await service.emit(
        "chat-1", "turn-1", "answer.delta", {"text": "a"}, lease_token=claimed[0][1].lease_token
    )
    await service.complete_evidence(
        "chat-1",
        "turn-1",
        AnswerEvidence(
            "a", "completed", RetrievalSummary("completed"), GraphContextStatus.NOT_REQUESTED
        ),
    )

    assert notifier.published == [(VALIDATION_SCOPE.project_id, "chat-1")] * 5
    assert [event.sequence for event in await service.events_after("chat-1", 3)] == [4, 5]
    assert await service.has_active_turns("chat-1") is True
    await service.cancel("chat-1", "turn-2")
    assert await service.has_active_turns("chat-1") is False


@pytest.mark.asyncio
async def test_publish_failure_only_logs_a_warning(caplog):
    from tap.modules.chat.application.conversations import (
        ConversationService,
        InMemoryConversationRepository,
    )

    notifier = _RecordingNotifier(fail=True)
    service = ConversationService(InMemoryConversationRepository(notifier), scope=VALIDATION_SCOPE)

    with caplog.at_level("WARNING"):
        turn = await service.create("chat-1", "turn-1", "request-1", _turn_input())

    assert turn.turn_id == "turn-1"
    assert notifier.published == [(VALIDATION_SCOPE.project_id, "chat-1")]
    assert "conversation event notification failed: ConnectionError" in caplog.text
