import asyncio
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
from tap.modules.chat.application.conversations import ConversationNotFound, ConversationService
from tap.modules.chat.domain.conversations import TurnInput
from tests.owned_mysql import owned_project_database_url


def _input(message: str) -> TurnInput:
    return TurnInput(
        message=message,
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
    )


def test_mysql_rename_soft_delete_and_title_search(owned_project_mysql):
    async def scenario():
        url = owned_project_database_url(owned_project_mysql)
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            service = ConversationService(
                MysqlConversationRepository(
                    sessions, scope=VALIDATION_SCOPE, default_chat_model="qwen-plus"
                ),
                scope=VALIDATION_SCOPE,
            )
            titles = ("Release plan", "100% coverage", "snake_case plan", "Other", "Plan! b")
            for index, title in enumerate(titles):
                await service.create(f"c-{index}", f"t-{index}", f"r-{index}", _input(title))
                await asyncio.sleep(0.002)

            async def found(query, *, limit=10, cursor=None):
                values, next_cursor = await service.list(limit=limit, cursor=cursor, query=query)
                return [item.title for item in values], next_cursor

            assert (await found("PLAN"))[0] == ["Plan! b", "snake_case plan", "Release plan"]
            assert (await found("%"))[0] == ["100% coverage"]
            assert (await found("_"))[0] == ["snake_case plan"]
            assert (await found("!"))[0] == ["Plan! b"]
            assert (await found("e_c"))[0] == ["snake_case plan"]
            assert len((await found("  "))[0]) == 5
            first, cursor = await found("plan", limit=2)
            assert first == ["Plan! b", "snake_case plan"] and cursor is not None
            assert await found("plan", limit=2, cursor=cursor) == (["Release plan"], None)

            before = (await service.load("c-0")).updated_at
            renamed = await service.rename(
                "c-0", "  Renamed release  ", actor_id=VALIDATION_SCOPE.actor_id
            )
            assert renamed.title == "Renamed release" and renamed.updated_at > before
            assert (await service.load("c-0")).title == "Renamed release"
            with pytest.raises(AuthorizationDenied):
                await service.rename("c-0", "Other", actor_id="someone-else")
            with pytest.raises(AuthorizationDenied):
                await service.delete("c-0", actor_id="someone-else")
            with pytest.raises(ConversationNotFound):
                await service.rename("missing", "Title", actor_id=VALIDATION_SCOPE.actor_id)
            with pytest.raises(ConversationNotFound):
                await service.delete("missing", actor_id=VALIDATION_SCOPE.actor_id)

            await service.append("c-0", "t-0b", "r-0b", _input("second"))
            await service.delete("c-0", actor_id=VALIDATION_SCOPE.actor_id)
            await service.delete("c-0", actor_id=VALIDATION_SCOPE.actor_id)

            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            "SELECT deleted_at, deleted_by FROM conversation "
                            "WHERE conversation_id='c-0'"
                        )
                    )
                ).one()
                assert isinstance(row.deleted_at, datetime)
                assert row.deleted_by == VALIDATION_SCOPE.actor_id
                states = (
                    await connection.execute(
                        text("SELECT state FROM chat_turn WHERE chat_id='c-0' ORDER BY turn_id")
                    )
                ).scalars()
                assert list(states) == ["canceled", "canceled"]
                evidence = await connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM turn_answer_evidence_snapshot s "
                        "JOIN chat_turn t ON t.turn_id=s.turn_id WHERE t.chat_id='c-0'"
                    )
                )
                assert evidence == 2

            with pytest.raises(ConversationNotFound):
                await service.load("c-0")
            with pytest.raises(ConversationNotFound):
                await service.rename("c-0", "Back", actor_id=VALIDATION_SCOPE.actor_id)
            with pytest.raises(ConversationNotFound):
                await service.append("c-0", "t-0c", "r-0c", _input("third"))
            assert "Renamed release" not in (await found(None))[0]

            # A queued Turn left behind on a deleted Conversation is never claimed.
            async with engine.begin() as connection:
                await connection.execute(
                    text("UPDATE chat_turn SET state='queued' WHERE turn_id='t-0b'")
                )
            claimed = await service.repository.claim_queued(limit=10)
            assert "c-0" not in {conversation_id for conversation_id, _ in claimed}
            assert {conversation_id for conversation_id, _ in claimed} == {
                "c-1",
                "c-2",
                "c-3",
                "c-4",
            }
        finally:
            await engine.dispose()

    asyncio.run(scenario())
