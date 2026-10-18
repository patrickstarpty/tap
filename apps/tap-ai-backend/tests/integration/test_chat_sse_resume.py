import asyncio
import contextlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.contracts.http import DocumentAnchor, RetrievalAnswerResponse, RetrievalCitation
from tap.entrypoints import tapper_runtime
from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.interfaces.http.routes.conversations import stream as stream_route
from tap.interfaces.http.sse import encode_sse
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.application.ports import DraftStreamUnavailable
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    ConversationEvent,
    GraphContextStatus,
    RetrievalSummary,
    TurnInput,
)
from tests.conftest import validation_http_services
from tests.unit.entrypoints.test_tapper_runtime import valid_settings


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
                "event": {
                    "type": "stage.completed",
                    "payload": {"stage": "model", "durationMs": 1},
                },
            },
        ],
        last_event_id="4",
    )
    assert "id: 4" not in rendered
    assert "id: 5\n" in rendered
    assert "event: stage.completed\n" in rendered
    assert '"eventId":"event-5"' in rendered
    assert '"chatId":"chat-1"' in rendered


def test_unknown_event_type_is_rejected_and_invalid_resume_never_replays():
    with pytest.raises(ValueError, match="unknown conversation event"):
        ConversationEvent("event-1", 1, "provider.secret", {}, datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="Last-Event-ID"):
        encode_sse([], last_event_id="-1")


def test_http_sse_reconnect_returns_complete_envelope_for_events_added_while_disconnected():
    # PR 3 Task 6: the stream now closes only once every Turn is terminal
    # (no more fixed idle timeout), so each Turn touched by a synchronous
    # `TestClient.get()` here must already be terminal before the request.
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    value = TurnInput(
        message="question",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
    )
    asyncio.run(service.create("chat-1", "turn-1", "request-1", value))
    asyncio.run(
        service.complete_evidence(
            "chat-1",
            "turn-1",
            AnswerEvidence(
                "first",
                "completed",
                RetrievalSummary("completed"),
                GraphContextStatus.NOT_REQUESTED,
            ),
            terminal_event=("turn.completed", {"state": "completed"}),
        )
    )
    services = replace(validation_http_services(), conversations=service)
    client = TestClient(create_app(services))
    first = client.get("/api/v1/projects/tapper-demo/conversations/chat-1/stream")
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("text/event-stream")
    assert "id: 1\n" in first.text
    last_seen = max(int(line[4:]) for line in first.text.splitlines() if line.startswith("id: "))

    asyncio.run(service.append("chat-1", "turn-2", "request-2", value))
    asyncio.run(
        service.complete_evidence(
            "chat-1",
            "turn-2",
            AnswerEvidence(
                "later",
                "completed",
                RetrievalSummary("completed"),
                GraphContextStatus.NOT_REQUESTED,
            ),
            terminal_event=("turn.completed", {"state": "completed"}),
        )
    )
    resumed = client.get(
        "/api/v1/projects/tapper-demo/conversations/chat-1/stream",
        headers={"Last-Event-ID": str(last_seen)},
    )
    assert f"id: {last_seen}\n" not in resumed.text
    assert "event: turn.completed" in resumed.text
    completed = next(
        json.loads(line[6:])
        for line in resumed.text.splitlines()
        if line.startswith("data: ") and '"turn.completed"' in line
    )
    assert completed["turnId"] == "turn-2"
    assert completed["event"] == {
        "type": "turn.completed",
        "payload": {"answer": None, "state": "completed"},
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
        model_alias="qwen-plus",
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
    # PR 3 Task 6: the stream only closes once the Turn is terminal.
    asyncio.run(
        service.complete_evidence(
            "chat-plan",
            "turn-plan",
            AnswerEvidence(
                "", "completed", RetrievalSummary("completed"), GraphContextStatus.NOT_REQUESTED
            ),
            terminal_event=("turn.completed", {"state": "completed"}),
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
                model_alias="qwen-plus",
            ),
        )
    )
    asyncio.run(
        service.emit(
            "chat-published",
            "turn-published",
            "stage.completed",
            {"stage": "model", "durationMs": 1},
        )
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


def _validation_input() -> TurnInput:
    return TurnInput(
        message="question",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
    )


async def _read_frames(body_iterator, *, count: int, timeout: float = 5.0) -> list[str]:
    frames: list[str] = []
    for _ in range(count):
        frames.append(await asyncio.wait_for(body_iterator.__anext__(), timeout=timeout))
    return frames


def _redis_settings(owned_redis_url: str) -> tapper_runtime.TapperSettings:
    """The real settings-construction path, so the draft stream is built the
    same way `create_api_runtime` builds it in production (consistent with
    `tests/integration/test_redis_drafts_integration.py`)."""
    return tapper_runtime.TapperSettings.from_mapping(
        valid_settings()
        | {
            "TAP_REDIS_URL": owned_redis_url,
            "TAP_REDIS_STREAM_SOCKET_TIMEOUT_SECONDS": "20",
            "TAP_REDIS_STREAM_POOL_SIZE": "10",
        }
    )


def test_draft_frames_arrive_before_terminal_event(owned_redis_url, owned_project_mysql) -> None:
    """PR 3 Task 6: a Turn still running must stream `answer.draft` frames
    (replayed from Redis) before any terminal MySQL event exists at all."""

    async def scenario() -> None:
        settings = _redis_settings(owned_redis_url)
        redis = tapper_runtime._create_redis(settings)
        block_redis = tapper_runtime._create_redis_stream_block_client(settings)
        drafts = tapper_runtime._create_draft_stream(redis, block_redis, settings)
        engine = create_async_engine(
            owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy")
        )
        turn_id = f"turn-{uuid4().hex}"
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
            service = ConversationService(repository, scope=VALIDATION_SCOPE)
            conversation_id = f"chat-{uuid4().hex}"
            await service.create(conversation_id, turn_id, "request-1", _validation_input())
            # Claim the Turn (as the generation worker would) so it is
            # `running` at attempt 1, matching the attempt the drafts below
            # are written under -- `replay()` only returns the current attempt.
            claimed = (await repository.claim_queued(limit=1))[0][1]
            assert claimed.attempt == 1
            await drafts.append(turn_id, attempt=1, seq=0, text="Evi")
            await drafts.append(turn_id, attempt=1, seq=1, text="dence")

            request = SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(
                        http_services=HttpServices(conversations=service, draft_stream=drafts)
                    )
                )
            )
            response = await stream_route(
                conversation_id, request, last_event_id=None, service=service
            )
            try:
                # `conversation.turn.requested` + `turn.started` catch-up,
                # then the two replayed draft chunks.
                frames = await _read_frames(response.body_iterator, count=4, timeout=5.0)
            finally:
                await response.body_iterator.aclose()

            joined = "".join(frames)
            assert joined.index("event: turn.started") < joined.index("event: answer.draft")
            assert "Evi" in joined
            assert "dence" in joined
            assert "turn.completed" not in joined
            assert "id: " not in joined.split("event: answer.draft", 1)[1].split("\n\n")[0]
        finally:
            await redis.delete(f"tap:turn:{turn_id}:draft")
            await redis.aclose()
            await block_redis.aclose()
            await engine.dispose()

    asyncio.run(scenario())


def test_reconnect_replays_only_current_attempt(owned_redis_url, owned_project_mysql) -> None:
    """PR 3 Task 6: a reconnect must replay drafts for the Turn's *current*
    attempt only -- not a stale earlier attempt's drafts left in Redis after
    a lease-expiry retry."""

    async def scenario() -> None:
        settings = _redis_settings(owned_redis_url)
        redis = tapper_runtime._create_redis(settings)
        block_redis = tapper_runtime._create_redis_stream_block_client(settings)
        drafts = tapper_runtime._create_draft_stream(redis, block_redis, settings)
        engine = create_async_engine(
            owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy")
        )
        turn_id = f"turn-{uuid4().hex}"
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
            service = ConversationService(repository, scope=VALIDATION_SCOPE)
            conversation_id = f"chat-{uuid4().hex}"
            await service.create(conversation_id, turn_id, "request-1", _validation_input())

            first_claim = (await repository.claim_queued(limit=1))[0][1]
            assert first_claim.attempt == 1
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE chat_turn SET processing_lease_expires_at=UTC_TIMESTAMP(6) "
                        "- INTERVAL 1 SECOND WHERE turn_id=:turn_id"
                    ),
                    {"turn_id": turn_id},
                )
            second_claim = (await repository.claim_queued(limit=1))[0][1]
            assert second_claim.attempt == 2

            await drafts.append(turn_id, attempt=1, seq=0, text="stale-attempt-one")
            await drafts.append(turn_id, attempt=2, seq=0, text="current-attempt-two")

            # Skip past both claims' MySQL catch-up (each `claim_queued` call
            # commits its own `turn.started`) so the next frame read is the
            # draft replay itself.
            caught_up = await service.load(conversation_id)
            last_seq = max(event.sequence for event in caught_up.events)

            request = SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(
                        http_services=HttpServices(conversations=service, draft_stream=drafts)
                    )
                )
            )
            response = await stream_route(
                conversation_id, request, last_event_id=str(last_seq), service=service
            )
            try:
                frames = await _read_frames(response.body_iterator, count=1, timeout=5.0)
            finally:
                await response.body_iterator.aclose()

            joined = "".join(frames)
            assert "current-attempt-two" in joined
            assert "stale-attempt-one" not in joined
        finally:
            await redis.delete(f"tap:turn:{turn_id}:draft")
            await redis.aclose()
            await block_redis.aclose()
            await engine.dispose()

    asyncio.run(scenario())


class _FailingDraftStream:
    """A `DraftStream` double that always raises `DraftStreamUnavailable`,
    simulating Redis being down for the whole life of the connection."""

    async def append(self, *args, **kwargs) -> None:
        raise DraftStreamUnavailable("redis down")

    async def replay(self, *args, **kwargs):
        raise DraftStreamUnavailable("redis down")

    async def read(self, *args, **kwargs):
        raise DraftStreamUnavailable("redis down")

    async def partial_text(self, *args, **kwargs):
        raise DraftStreamUnavailable("redis down")


def test_stream_degrades_to_mysql_polling_when_redis_fails() -> None:
    """PR 3 Task 6: when the draft stream is unavailable, the SSE loop must
    degrade to polling MySQL roughly every second, deliver the terminal
    event once it commits, and never surface the Redis failure as an error."""

    async def scenario() -> None:
        service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
        await service.create("chat-degrade", "turn-degrade", "request-1", _validation_input())

        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    http_services=HttpServices(
                        conversations=service, draft_stream=_FailingDraftStream()
                    )
                )
            )
        )
        response = await stream_route("chat-degrade", request, last_event_id=None, service=service)

        async def complete_soon() -> None:
            await asyncio.sleep(0.3)
            await service.complete_evidence(
                "chat-degrade",
                "turn-degrade",
                AnswerEvidence(
                    "done",
                    "completed",
                    RetrievalSummary("completed"),
                    GraphContextStatus.NOT_REQUESTED,
                ),
                terminal_event=("turn.completed", {"state": "completed"}),
            )

        completer = asyncio.create_task(complete_soon())
        started = monotonic()
        frames: list[str] = []
        try:
            async for chunk in response.body_iterator:
                frames.append(chunk)
                if "turn.completed" in chunk:
                    break
        finally:
            await completer
        elapsed = monotonic() - started

        assert any("turn.completed" in frame for frame in frames)
        # The degraded poll interval is ~1s; this must not take anywhere
        # near the 15s Redis block window the healthy path would use.
        assert elapsed < 2.5

    asyncio.run(scenario())


class _CountingConversationService:
    """Wraps a real `ConversationService`, counting `load` (MySQL read)
    calls so a test can assert they do not grow while a Redis block is
    still pending."""

    def __init__(self, inner: ConversationService) -> None:
        self._inner = inner
        self.load_calls = 0

    async def load(self, conversation_id: str):
        self.load_calls += 1
        return await self._inner.load(conversation_id)


class _SlowBlockingDraftStream:
    """A `DraftStream` double whose `read` blocks far longer than the test
    needs to wait, simulating an in-progress Redis `XREAD BLOCK`."""

    async def replay(self, *args, **kwargs):
        return ()

    async def read(self, *args, **kwargs):
        await asyncio.sleep(5.0)
        return ()


def test_stream_does_not_poll_when_idle() -> None:
    """PR 3 Task 6: while a blocking Redis read is in flight, the SSE loop
    must not re-read MySQL again -- it only re-reads once the read() call
    returns (or times out), never on a busy-poll cadence."""

    async def scenario() -> None:
        inner = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
        await inner.create("chat-idle", "turn-idle", "request-1", _validation_input())
        counting = _CountingConversationService(inner)

        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    http_services=HttpServices(
                        conversations=inner, draft_stream=_SlowBlockingDraftStream()
                    )
                )
            )
        )
        response = await stream_route("chat-idle", request, last_event_id=None, service=counting)

        task = asyncio.ensure_future(_drain(response.body_iterator))
        await asyncio.sleep(0.5)
        assert counting.load_calls == 2  # the route's own pre-check, plus one loop iteration
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert counting.load_calls == 2  # unchanged while still blocked on the slow read()

    asyncio.run(scenario())


async def _drain(body_iterator) -> None:
    async for _ in body_iterator:
        pass
