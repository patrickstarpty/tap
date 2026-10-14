"""MysqlFeedbackRepository integration tests: one active row per (turn, actor), batch read."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
