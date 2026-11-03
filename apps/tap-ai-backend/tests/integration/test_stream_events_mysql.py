"""MySQL stream events honor the generation lease and reset reclaimed answers."""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
from tap.modules.chat.application.conversations import (
    ConversationConflict,
    ConversationNotFound,
    ConversationService,
)
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    ConversationEvent,
    GraphContextStatus,
    RetrievalSummary,
    TurnInput,
)
from tests.owned_mysql import owned_project_database_url

_OWNED = (
    "outbox",
    "turn_artifact_link",
    "turn_answer_evidence_snapshot",
    "turn_input_snapshot",
    "turn_snapshot",
    "chat_event",
    "chat_turn",
    "conversation",
)


def _input() -> TurnInput:
    return TurnInput(
        message="Stream this",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        agent_revision_id="validation-knowledge-agent-v1",
        agent_revision_digest="sha256:" + "a" * 64,
        skill_revision_ids=("validation-citation-skill-v1",),
        skill_revision_digests=("sha256:" + "b" * 64,),
        retrieval_policy_digest="sha256:" + "c" * 64,
    )


def _delta(value: str) -> ConversationEvent:
    return ConversationEvent(
        "event-" + value, 1, "answer.delta", {"text": value}, datetime.now(UTC)
    )


def test_append_stream_event_requires_the_current_lease_and_reclaim_resets(owned_project_mysql):
    async def scenario():
        url = owned_project_database_url(owned_project_mysql).replace(
            "mysql+pymysql", "mysql+asyncmy"
        )
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in _OWNED:
                await connection.execute(text(f"DELETE FROM {table}"))
            await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        try:
            repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
            service = ConversationService(repository, scope=VALIDATION_SCOPE)
            await service.create("chat-1", "turn-1", "request-1", _input())
            first = (await repository.claim_queued(limit=1))[0][1]
            assert first.attempt == 1

            with pytest.raises(ConversationConflict, match="lease"):
                await repository.append_stream_event(
                    "chat-1", "turn-1", _delta("wrong"), lease_token="not-the-lease"
                )
            with pytest.raises(ConversationConflict, match="lease"):
                await repository.append_stream_event("chat-1", "turn-1", _delta("missing"))
            appended = await repository.append_stream_event(
                "chat-1", "turn-1", _delta("first"), lease_token=first.lease_token
            )
            assert appended.sequence == 3

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE chat_turn SET processing_lease_expires_at=UTC_TIMESTAMP(6) "
                        "- INTERVAL 1 SECOND WHERE turn_id='turn-1'"
                    )
                )
            second = (await repository.claim_queued(limit=1))[0][1]
            assert second.attempt == 2

            with pytest.raises(ConversationConflict, match="lease"):
                await repository.append_stream_event(
                    "chat-1", "turn-1", _delta("stale"), lease_token=first.lease_token
                )
            await repository.append_stream_event(
                "chat-1", "turn-1", _delta("second"), lease_token=second.lease_token
            )

            events = (await service.load("chat-1")).events
            assert [(item.sequence, item.event_type, dict(item.payload)) for item in events] == [
                (1, "conversation.turn.requested", dict(events[0].payload)),
                (2, "turn.started", {"state": "running"}),
                (3, "answer.delta", {"text": "first"}),
                (4, "turn.started", {"state": "running"}),
                (5, "answer.reset", {"attempt": 2}),
                (6, "answer.delta", {"text": "second"}),
            ]
            async with engine.connect() as connection:
                last_sequence = await connection.scalar(
                    text("SELECT last_sequence FROM chat_turn WHERE turn_id='turn-1'")
                )
            assert last_sequence == 6
        finally:
            await engine.dispose()

    asyncio.run(scenario())


class _RecordingNotifier:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, scope, chat_id):
        assert scope.project_id == VALIDATION_SCOPE.project_id
        self.published.append(chat_id)
        raise ConnectionError("publish failures never fail the commit")

    async def wait(self, scope, chat_id, timeout):
        return False


def test_committed_events_publish_and_incremental_reads_follow_the_ledger(owned_project_mysql):
    async def scenario():
        url = owned_project_database_url(owned_project_mysql).replace(
            "mysql+pymysql", "mysql+asyncmy"
        )
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in _OWNED:
                await connection.execute(text(f"DELETE FROM {table}"))
            await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        try:
            notifier = _RecordingNotifier()
            repository = MysqlConversationRepository(
                sessions, scope=VALIDATION_SCOPE, notifier=notifier
            )
            service = ConversationService(repository, scope=VALIDATION_SCOPE)
            await service.create("chat-1", "turn-1", "request-1", _input())
            assert await service.has_active_turns("chat-1") is True
            claimed = (await repository.claim_queued(limit=1))[0][1]
            await repository.append_stream_event(
                "chat-1", "turn-1", _delta("a"), lease_token=claimed.lease_token
            )
            await service.complete_evidence(
                "chat-1",
                "turn-1",
                AnswerEvidence(
                    "a",
                    "completed",
                    RetrievalSummary("completed"),
                    GraphContextStatus.NOT_REQUESTED,
                ),
                lease_token=claimed.lease_token,
                terminal_event=("turn.completed", {"state": "completed"}),
            )
            assert notifier.published == ["chat-1"] * 4

            assert await service.has_active_turns("chat-1") is False
            after = await service.events_after("chat-1", 2)
            assert [(item.sequence, item.event_type) for item in after] == [
                (3, "answer.delta"),
                (4, "turn.completed"),
                (5, "conversation.turn.completed"),
            ]
            assert all(item.turn_id == "turn-1" for item in after)
            assert await service.events_after("chat-1", 5) == ()

            await service.delete("chat-1", actor_id=VALIDATION_SCOPE.actor_id)
            with pytest.raises(ConversationNotFound):
                await service.events_after("chat-1", 0)
            with pytest.raises(ConversationNotFound):
                await service.has_active_turns("chat-1")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_unfinished_turns_withdraw_provisional_answer_text(owned_project_mysql):
    async def scenario():
        url = owned_project_database_url(owned_project_mysql).replace(
            "mysql+pymysql", "mysql+asyncmy"
        )
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in _OWNED:
                await connection.execute(text(f"DELETE FROM {table}"))
            await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        try:
            repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
            service = ConversationService(repository, scope=VALIDATION_SCOPE)

            async def stream(turn_id: str, *texts: str) -> str:
                claimed = (await repository.claim_queued(limit=1))[0][1]
                assert claimed.turn_id == turn_id
                for value in texts:
                    await repository.append_stream_event(
                        "chat-1",
                        turn_id,
                        ConversationEvent(
                            f"{turn_id}-{value}",
                            1,
                            "answer.delta",
                            {"text": value},
                            datetime.now(UTC),
                        ),
                        lease_token=claimed.lease_token,
                    )
                return claimed.lease_token

            def evidence(outcome: str, answer: str = "") -> AnswerEvidence:
                return AnswerEvidence(
                    answer, outcome, RetrievalSummary(outcome), GraphContextStatus.NOT_REQUESTED
                )

            await service.create("chat-1", "turn-1", "request-1", _input())
            lease = await stream("turn-1", "kept")
            await service.complete_evidence(
                "chat-1",
                "turn-1",
                evidence("completed", "kept"),
                lease_token=lease,
                terminal_event=("turn.completed", {"state": "completed"}),
            )
            await service.append("chat-1", "turn-2", "request-2", _input())
            lease = await stream("turn-2", "denied ", "text")
            await service.complete_evidence(
                "chat-1",
                "turn-2",
                evidence("failed"),
                lease_token=lease,
                terminal_event=("turn.failed", {"state": "failed"}),
            )
            await service.append("chat-1", "turn-3", "request-3", _input())
            lease = await stream("turn-3", "abstained")
            await service.complete_evidence(
                "chat-1",
                "turn-3",
                evidence("abstained"),
                lease_token=lease,
                terminal_event=("turn.abstained", {"state": "abstained"}),
            )
            await service.append("chat-1", "turn-4", "request-4", _input())
            await stream("turn-4", "canceled")
            await service.cancel("chat-1", "turn-4")
            await service.append("chat-1", "turn-5", "request-5", _input())
            await service.cancel("chat-1", "turn-5")

            async with engine.connect() as connection:
                stored = (
                    await connection.execute(
                        text(
                            "SELECT turn_id,event_type,payload FROM chat_event "
                            "ORDER BY stream_sequence"
                        )
                    )
                ).all()
            stored_types = [(row[0], row[1]) for row in stored]
            assert stored_types.count(("turn-2", "answer.delta")) == 2
            for turn_id, terminal in (
                ("turn-2", "turn.failed"),
                ("turn-3", "turn.abstained"),
                ("turn-4", "conversation.turn.completed"),
            ):
                index = stored_types.index((turn_id, "answer.reset"))
                assert stored_types[index + 1] == (turn_id, terminal)
            assert ("turn-1", "answer.reset") not in stored_types
            assert ("turn-5", "answer.reset") not in stored_types

            loaded = (await service.load("chat-1")).events
            after = await service.events_after("chat-1", 0)
            for events in (loaded, after):
                deltas = [
                    (item.turn_id, item.payload["text"])
                    for item in events
                    if item.event_type == "answer.delta"
                ]
                assert deltas == [("turn-1", "kept")]
                assert [item.sequence for item in events] == sorted(
                    item.sequence for item in events
                )
                resets = [
                    (item.turn_id, dict(item.payload))
                    for item in events
                    if item.event_type == "answer.reset"
                ]
                assert resets == [
                    ("turn-2", {"attempt": 1}),
                    ("turn-3", {"attempt": 1}),
                    ("turn-4", {"attempt": 1}),
                ]
            assert len(loaded) == len(stored) - 4
        finally:
            await engine.dispose()

    asyncio.run(scenario())
