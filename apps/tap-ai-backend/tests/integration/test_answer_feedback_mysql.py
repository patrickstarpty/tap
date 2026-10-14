"""MysqlFeedbackRepository integration tests: one active row per (turn, actor), batch read."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
from tap.modules.chat.adapters.mysql_feedback import MysqlFeedbackRepository
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.chat.domain.conversations import TurnInput
from tap.modules.chat.domain.feedback import AnswerFeedback
from tests.owned_mysql import owned_project_database_url


def _input():
    return TurnInput(
        message="question",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
    )


def _feedback(turn_id: str, *, rating="up", comment=None, feedback_id=None, now=None):
    now = now or datetime.now(timezone.utc)
    return AnswerFeedback(
        feedback_id=feedback_id or uuid4().hex,
        project_id=VALIDATION_SCOPE.project_id,
        conversation_id="conversation-1",
        turn_id=turn_id,
        actor_id=VALIDATION_SCOPE.actor_id,
        trace_id=None,
        rating=rating,
        comment=comment,
        created_at=now,
        updated_at=now,
    )


def test_upsert_keeps_one_row_per_turn_actor_and_for_turns_batches(owned_project_mysql):
    async def scenario():
        url = owned_project_database_url(owned_project_mysql).replace(
            "mysql+pymysql", "mysql+asyncmy"
        )
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in ("answer_feedback", "chat_turn", "conversation"):
                await connection.execute(text(f"DELETE FROM {table}"))
            await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))

        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE), scope=VALIDATION_SCOPE
        )
        await conversations.create("conversation-1", "turn-1", "request-1", _input())
        await conversations.append("conversation-1", "turn-2", "request-2", _input())

        repository = MysqlFeedbackRepository(sessions, scope=VALIDATION_SCOPE)

        first = _feedback("turn-1", rating="up")
        await repository.upsert(first)
        async with engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text("SELECT COUNT(*) FROM answer_feedback WHERE turn_id = :turn_id"),
                        {"turn_id": "turn-1"},
                    )
                )
                .scalars()
                .all()
            )
            assert rows == [1]

        changed = _feedback(
            "turn-1", rating="down", comment="needs work", feedback_id=first.feedback_id
        )
        await repository.upsert(changed)
        async with engine.connect() as connection:
            count = (
                await connection.execute(
                    text("SELECT COUNT(*) FROM answer_feedback WHERE turn_id = :turn_id"),
                    {"turn_id": "turn-1"},
                )
            ).scalar_one()
            assert count == 1

        stored = await repository.get("turn-1", VALIDATION_SCOPE.actor_id)
        assert stored is not None
        assert stored.rating == "down"
        assert stored.comment == "needs work"

        await repository.upsert(_feedback("turn-2", rating="up"))
        batch = await repository.for_turns(["turn-1", "turn-2"], VALIDATION_SCOPE.actor_id)
        assert set(batch) == {"turn-1", "turn-2"}

        await engine.dispose()

    asyncio.run(scenario())


def test_upsert_keeps_updated_at_strictly_increasing_and_preserves_identity(
    owned_project_mysql,
):
    async def scenario():
        url = owned_project_database_url(owned_project_mysql).replace(
            "mysql+pymysql", "mysql+asyncmy"
        )
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in ("answer_feedback", "chat_turn", "conversation"):
                await connection.execute(text(f"DELETE FROM {table}"))
            await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))

        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE), scope=VALIDATION_SCOPE
        )
        await conversations.create("conversation-1", "turn-1", "request-1", _input())

        repository = MysqlFeedbackRepository(sessions, scope=VALIDATION_SCOPE)
        feedback_id = uuid4().hex
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)

        first = await repository.upsert(
            _feedback("turn-1", rating="up", feedback_id=feedback_id, now=base)
        )

        # A second write racing with the same wall-clock timestamp as the
        # first (e.g. two concurrent PUTs) must still strictly increase
        # updated_at, and must not mint a new feedback_id or created_at.
        second = await repository.upsert(
            _feedback(
                "turn-1", rating="down", comment="needs work", feedback_id=feedback_id, now=base
            )
        )
        assert second.feedback_id == first.feedback_id
        assert second.created_at == first.created_at
        assert second.updated_at > first.updated_at

        # A write that carries an earlier timestamp than what is already
        # stored (e.g. a retried request whose clock lagged) must not move
        # updated_at backwards either.
        earlier = base - timedelta(seconds=10)
        third = await repository.upsert(
            _feedback("turn-1", rating="up", feedback_id=feedback_id, now=earlier)
        )
        assert third.feedback_id == first.feedback_id
        assert third.created_at == first.created_at
        assert third.updated_at > second.updated_at

        # Retract (rating/comment cleared) is just another write through the
        # same path: identity survives and updated_at keeps increasing.
        retracted = await repository.upsert(
            replace(third, rating=None, comment=None, updated_at=earlier)
        )
        assert retracted.feedback_id == first.feedback_id
        assert retracted.created_at == first.created_at
        assert retracted.rating is None
        assert retracted.comment is None
        assert retracted.updated_at > third.updated_at

        await engine.dispose()

    asyncio.run(scenario())
