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
    ConversationService,
)
from tap.modules.chat.domain.conversations import ConversationEvent, TurnInput
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
