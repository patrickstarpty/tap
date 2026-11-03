"""Conversation SSE is driven by event notifications, not fixed-interval polling."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import pytest

from tap.interfaces.http.app import create_app
from tap.interfaces.http.routes import conversations as conversation_routes
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    GraphContextStatus,
    RetrievalSummary,
    TurnInput,
)
from tests.conftest import validation_http_services

_PATH = "/api/v1/projects/tapper-demo/conversations/chat-1/stream"


def _input() -> TurnInput:
    return TurnInput(
        message="question",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
    )


class _Stream:
    """Drives the ASGI app directly so body chunks are observed as they are sent."""

    def __init__(self, app, path: str, headers: dict[str, str] | None = None) -> None:
        self.chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.status: int | None = None
        self._disconnect = asyncio.Event()
        self._requested = False
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [
                (b"host", b"testserver"),
                *((key.lower().encode(), value.encode()) for key, value in (headers or {}).items()),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
        self.task = asyncio.create_task(app(scope, self._receive, self._send))

    async def _receive(self):
        if not self._requested:
            self._requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
        elif message["type"] == "http.response.body":
            if message.get("body"):
                await self.chunks.put(message["body"])
            if not message.get("more_body", False):
                await self.chunks.put(None)

    async def next_chunk(self, timeout: float = 2.0) -> bytes | None:
        return await asyncio.wait_for(self.chunks.get(), timeout)

    async def rest(self, timeout: float = 2.0) -> bytes:
        body = b""
        while (chunk := await self.next_chunk(timeout)) is not None:
            body += chunk
        return body

    async def close(self) -> None:
        self._disconnect.set()
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


async def _service() -> ConversationService:
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    await service.create("chat-1", "turn-1", "request-1", _input())
    return service


def _app(service: ConversationService):
    return create_app(replace(validation_http_services(), conversations=service))


async def _complete(service: ConversationService) -> None:
    await service.complete_evidence(
        "chat-1",
        "turn-1",
        AnswerEvidence(
            "done", "completed", RetrievalSummary("completed"), GraphContextStatus.NOT_REQUESTED
        ),
        terminal_event=("turn.failed", {"problem": _problem()}),
    )


def _problem() -> dict[str, object]:
    from tap.contracts.problems import build_problem

    return build_problem("answer-unavailable", correlation_id="turn-1").model_dump(
        mode="json", by_alias=True
    )


@pytest.mark.asyncio
async def test_active_turn_keeps_the_stream_open_and_notifications_deliver_promptly(
    monkeypatch,
):
    monkeypatch.setattr(conversation_routes, "_STREAM_WAIT_SECONDS", 5.0)
    service = await _service()
    stream = _Stream(_app(service), _PATH)
    try:
        first = await stream.next_chunk()
        assert first is not None and b"id: 1\n" in first
        assert stream.status == 200

        await asyncio.sleep(0.3)
        assert stream.chunks.empty() and not stream.task.done()

        started = time.monotonic()
        await service.emit("chat-1", "turn-1", "answer.delta", {"text": "partial"})
        delta = await stream.next_chunk()
        assert time.monotonic() - started < 0.5
        assert delta is not None and b"id: 2\nevent: answer.delta\n" in delta

        started = time.monotonic()
        await _complete(service)
        tail = await stream.rest()
        assert time.monotonic() - started < 0.5
        assert b"event: turn.failed\n" in tail
        assert b"event: conversation.turn.completed\n" in tail
        await asyncio.wait_for(stream.task, 1)
    finally:
        await stream.close()


@pytest.mark.asyncio
async def test_wait_timeout_sends_keepalive_and_the_stream_is_bounded(monkeypatch):
    monkeypatch.setattr(conversation_routes, "_STREAM_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(conversation_routes, "_STREAM_MAX_SECONDS", 0.3)
    service = await _service()
    stream = _Stream(_app(service), _PATH)
    try:
        body = await stream.rest()
        assert b"id: 1\n" in body
        assert body.count(b": keepalive\n\n") >= 2
        await asyncio.wait_for(stream.task, 1)
    finally:
        await stream.close()


@pytest.mark.asyncio
async def test_last_event_id_resumes_with_only_new_events_and_closes_when_caught_up(
    monkeypatch,
):
    monkeypatch.setattr(conversation_routes, "_STREAM_WAIT_SECONDS", 5.0)
    service = await _service()
    await service.emit("chat-1", "turn-1", "answer.delta", {"text": "seen"})
    await service.emit("chat-1", "turn-1", "answer.delta", {"text": "new"})
    await _complete(service)

    stream = _Stream(_app(service), _PATH, {"Last-Event-ID": "2"})
    try:
        body = await stream.rest(timeout=1.0)
        assert b"id: 1\n" not in body and b"id: 2\n" not in body
        assert b"id: 3\n" in body and b'"text":"new"' in body
        assert b"event: conversation.turn.completed\n" in body
        assert b"keepalive" not in body
    finally:
        await stream.close()


@pytest.mark.asyncio
async def test_deleting_the_conversation_ends_an_open_stream(monkeypatch):
    monkeypatch.setattr(conversation_routes, "_STREAM_WAIT_SECONDS", 5.0)
    service = await _service()
    stream = _Stream(_app(service), _PATH)
    try:
        assert await stream.next_chunk() is not None
        await service.delete("chat-1", actor_id=VALIDATION_SCOPE.actor_id)
        await stream.rest(timeout=1.0)
        await asyncio.wait_for(stream.task, 1)
    finally:
        await stream.close()


class _FailingPubSub:
    async def subscribe(self, _channel):
        raise ConnectionError("redis down")

    async def aclose(self):
        return None


class _DownRedis:
    async def publish(self, _channel, _message):
        raise ConnectionError("redis down")

    def pubsub(self, **_kwargs):
        return _FailingPubSub()


@pytest.mark.asyncio
async def test_redis_outage_does_not_spin_database_reads(monkeypatch):
    from tap.platform.messaging import redis_chat_notifier
    from tap.platform.messaging.redis_chat_notifier import RedisConversationEventNotifier

    monkeypatch.setattr(conversation_routes, "_STREAM_WAIT_SECONDS", 15.0)
    monkeypatch.setattr(redis_chat_notifier, "_FAILURE_BACKOFF_SECONDS", 0.1)
    notifier = RedisConversationEventNotifier(_DownRedis())
    service = ConversationService(InMemoryConversationRepository(notifier), scope=VALIDATION_SCOPE)
    await service.create("chat-1", "turn-1", "request-1", _input())
    reads = 0
    original = service.events_after

    async def counting_events_after(conversation_id, sequence):
        nonlocal reads
        reads += 1
        return await original(conversation_id, sequence)

    monkeypatch.setattr(service, "events_after", counting_events_after)
    stream = _Stream(_app(service), _PATH)
    try:
        assert await stream.next_chunk() is not None
        await asyncio.sleep(0.5)
        # 0.5s at a 0.1s backoff allows ~6 reads; a busy loop would perform thousands.
        assert 1 <= reads <= 8
        assert not stream.task.done()

        await service.emit("chat-1", "turn-1", "answer.delta", {"text": "late"})
        delivered = b""
        while b"answer.delta" not in delivered:
            chunk = await stream.next_chunk(timeout=1.0)
            assert chunk is not None
            delivered += chunk
    finally:
        await stream.close()
