from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from tap.contracts.chat_stream import (
    ChatEventEnvelope,
    TurnStartedEvent,
    TurnStartedPayload,
)
from tap.modules.chat.adapters.mysql import MysqlTurnRepository
from tap.modules.chat.application.ports import CreateTurnCommand, SequenceConflict
from tap.modules.chat.domain.models import ChatId, CommandId, EventId, TurnId, TurnState
from tap.platform.db.session import create_engine_and_session_factory

DATABASE_URL = os.getenv(
    "TAP_DATABASE_URL",
    "mysql+asyncmy://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4",
)
OWNED_TABLES = ("outbox", "turn_snapshot", "chat_event", "chat_turn")


async def _clean_owned_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        for table in OWNED_TABLES:
            await connection.execute(text(f"DELETE FROM {table}"))


def _run_with_clean_database(
    scenario: Callable[[AsyncEngine, MysqlTurnRepository], Awaitable[None]],
) -> None:
    async def run() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        repository = MysqlTurnRepository(sessions)
        await _clean_owned_tables(engine)
        try:
            await scenario(engine, repository)
        finally:
            await _clean_owned_tables(engine)
            await engine.dispose()

    asyncio.run(run())


def _command(
    *,
    command_id: str,
    turn_id: str,
    chat_id: str,
    client_request_id: str,
) -> CreateTurnCommand:
    return CreateTurnCommand(
        command_id=CommandId(command_id),
        turn_id=TurnId(turn_id),
        chat_id=ChatId(chat_id),
        client_request_id=client_request_id,
        message="How does checkout recovery work?",
        occurred_at=datetime(2026, 8, 23, 10, 0, 0),
    )


def _started_event(
    *, event_id: str, sequence: int, turn_id: str, chat_id: str
) -> ChatEventEnvelope:
    return ChatEventEnvelope(
        event_id=EventId(event_id),
        sequence=sequence,
        chat_id=chat_id,
        turn_id=turn_id,
        occurred_at="2026-08-23T10:01:00Z",
        schema_version=1,
        event=TurnStartedEvent(
            type="turn.started",
            payload=TurnStartedPayload(state="running"),
        ),
    )


def test_create_turn_commits_turn_and_dispatch_command_together() -> None:
    async def scenario(engine: AsyncEngine, repository: MysqlTurnRepository) -> None:
        command = _command(
            command_id="command-create-1",
            turn_id="turn-1",
            chat_id="chat-1",
            client_request_id="request-1",
        )

        turn = await repository.create_with_outbox(command)

        async with engine.connect() as connection:
            turn_row = (
                (
                    await connection.execute(
                        text(
                            "SELECT turn_id, state, last_sequence FROM chat_turn "
                            "WHERE turn_id = :turn_id"
                        ),
                        {"turn_id": "turn-1"},
                    )
                )
                .mappings()
                .one()
            )
            outbox_row = (
                (
                    await connection.execute(
                        text(
                            "SELECT command_id, aggregate_id, message_type FROM outbox "
                            "WHERE aggregate_id = :turn_id"
                        ),
                        {"turn_id": "turn-1"},
                    )
                )
                .mappings()
                .one()
            )

        assert turn.turn_id == TurnId("turn-1")
        assert turn.state is TurnState.QUEUED
        assert dict(turn_row) == {
            "turn_id": "turn-1",
            "state": "queued",
            "last_sequence": 0,
        }
        assert dict(outbox_row) == {
            "command_id": "command-create-1",
            "aggregate_id": "turn-1",
            "message_type": "turn.process_requested",
        }

    _run_with_clean_database(scenario)


def test_outbox_constraint_failure_rolls_back_the_new_turn() -> None:
    async def scenario(engine: AsyncEngine, repository: MysqlTurnRepository) -> None:
        await repository.create_with_outbox(
            _command(
                command_id="shared-command",
                turn_id="existing-turn",
                chat_id="existing-chat",
                client_request_id="existing-request",
            )
        )

        with pytest.raises(IntegrityError):
            await repository.create_with_outbox(
                _command(
                    command_id="shared-command",
                    turn_id="rolled-back-turn",
                    chat_id="other-chat",
                    client_request_id="other-request",
                )
            )

        async with engine.connect() as connection:
            rolled_back_turns = await connection.scalar(
                text("SELECT COUNT(*) FROM chat_turn WHERE turn_id = 'rolled-back-turn'")
            )
            shared_commands = await connection.scalar(
                text("SELECT COUNT(*) FROM outbox WHERE command_id = 'shared-command'")
            )

        assert rolled_back_turns == 0
        assert shared_commands == 1

    _run_with_clean_database(scenario)


def test_duplicate_client_request_in_one_chat_returns_original_turn() -> None:
    async def scenario(engine: AsyncEngine, repository: MysqlTurnRepository) -> None:
        original = await repository.create_with_outbox(
            _command(
                command_id="original-command",
                turn_id="original-turn",
                chat_id="chat-idempotent",
                client_request_id="same-request",
            )
        )
        replay = await repository.create_with_outbox(
            _command(
                command_id="replay-command",
                turn_id="discarded-turn",
                chat_id="chat-idempotent",
                client_request_id="same-request",
            )
        )

        async with engine.connect() as connection:
            turn_count = await connection.scalar(
                text("SELECT COUNT(*) FROM chat_turn WHERE chat_id = 'chat-idempotent'")
            )
            outbox_count = await connection.scalar(
                text(
                    "SELECT COUNT(*) FROM outbox "
                    "WHERE aggregate_id IN ('original-turn', 'discarded-turn')"
                )
            )

        assert replay.turn_id == original.turn_id == TurnId("original-turn")
        assert turn_count == 1
        assert outbox_count == 1

    _run_with_clean_database(scenario)


def test_append_events_requires_the_current_monotonic_sequence() -> None:
    async def scenario(engine: AsyncEngine, repository: MysqlTurnRepository) -> None:
        await repository.create_with_outbox(
            _command(
                command_id="sequence-command",
                turn_id="sequence-turn",
                chat_id="sequence-chat",
                client_request_id="sequence-request",
            )
        )
        first = _started_event(
            event_id="event-1", sequence=1, turn_id="sequence-turn", chat_id="sequence-chat"
        )
        second = _started_event(
            event_id="event-2", sequence=2, turn_id="sequence-turn", chat_id="sequence-chat"
        )

        await repository.append_events(TurnId("sequence-turn"), 0, [first, second])

        stale_event = _started_event(
            event_id="event-stale",
            sequence=2,
            turn_id="sequence-turn",
            chat_id="sequence-chat",
        )
        with pytest.raises(SequenceConflict):
            await repository.append_events(TurnId("sequence-turn"), 1, [stale_event])

        async with engine.connect() as connection:
            sequences = list(
                (
                    await connection.execute(
                        text(
                            "SELECT sequence FROM chat_event WHERE turn_id = 'sequence-turn' "
                            "ORDER BY sequence"
                        )
                    )
                ).scalars()
            )
            last_sequence = await connection.scalar(
                text("SELECT last_sequence FROM chat_turn WHERE turn_id = 'sequence-turn'")
            )
            event_outbox_count = await connection.scalar(
                text("SELECT COUNT(*) FROM outbox WHERE message_type = 'chat.event_appended'")
            )

        assert sequences == [1, 2]
        assert last_sequence == 2
        assert event_outbox_count == 2

    _run_with_clean_database(scenario)
