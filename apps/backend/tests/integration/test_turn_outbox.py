from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import datetime

import pytest
from apps.backend.tests.owned_mysql import owned_project_database_url
from scripts.migration_support import IsolatedMysql
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from tap.contracts.chat_stream import (
    ChatEventEnvelope,
    TurnStartedEvent,
    TurnStartedPayload,
)
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql import MysqlTurnRepository
from tap.modules.chat.application.ports import CreateTurnCommand, SequenceConflict
from tap.modules.chat.domain.models import ChatId, CommandId, EventId, TurnId, TurnState
from tap.platform.db.session import create_engine_and_session_factory

DATABASE_URL = os.getenv("TAP_DATABASE_URL", "")
OWNED_TABLES = ("outbox", "turn_snapshot", "chat_event", "chat_turn")


async def _clean_owned_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        for table in OWNED_TABLES:
            await connection.execute(text(f"DELETE FROM {table}"))


def _run_with_clean_database(
    scenario: Callable[[AsyncEngine, MysqlTurnRepository], Awaitable[None]],
) -> None:
    if not DATABASE_URL:
        pytest.skip("requires isolated TAP_DATABASE_URL")

    async def run() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        repository = MysqlTurnRepository(sessions, scope=VALIDATION_SCOPE)
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


def test_project_scopes_isolate_turn_idempotency_events_and_outbox_leases(
    owned_project_mysql: IsolatedMysql,
) -> None:
    database_url = owned_project_database_url(owned_project_mysql)
    from dataclasses import replace
    from datetime import timedelta

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.chat.adapters.mysql import OutboxStore
    from tap.modules.chat.application.ports import LeaseLost, TurnNotFound

    async def scenario(engine: AsyncEngine, repository: MysqlTurnRepository) -> None:
        other_scope = replace(VALIDATION_SCOPE, project_id="scope-other")
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO project (project_id, enterprise_id) "
                    "VALUES ('scope-other', 'local')"
                )
            )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        other = MysqlTurnRepository(sessions, scope=other_scope)
        now = datetime(2026, 8, 23, 12)
        command = _command(
            command_id="same-command",
            turn_id="scope-first",
            chat_id="same-chat",
            client_request_id="same-request",
        )
        try:
            first_turn = await repository.create_with_outbox(command)
            second_turn = await other.create_with_outbox(
                replace(command, turn_id=TurnId("scope-second"))
            )
            assert first_turn.turn_id != second_turn.turn_id
            assert (
                await other._find_by_client_request(
                    chat_id=ChatId("missing-chat"), client_request_id="same-request"
                )
                is None
            )
            with pytest.raises(ValueError, match="idempotency-conflict"):
                await repository.create_with_outbox(replace(command, message="Different request"))
            with pytest.raises(TurnNotFound):
                await other.append_events(
                    first_turn.turn_id,
                    0,
                    [
                        _started_event(
                            event_id="foreign-event",
                            sequence=1,
                            turn_id=first_turn.turn_id,
                            chat_id="same-chat",
                        )
                    ],
                )
            with pytest.raises(IntegrityError):
                await other.create_with_outbox(replace(command, chat_id=ChatId("other-chat")))
            first_store = OutboxStore(sessions, scope=VALIDATION_SCOPE)
            second_store = OutboxStore(sessions, scope=other_scope)
            first_claim = await first_store.claim_pending(
                worker_id="same-worker", now=now, lease_duration=timedelta(seconds=1), limit=10
            )
            second_claim = await second_store.claim_pending(
                worker_id="same-worker", now=now, lease_duration=timedelta(seconds=1), limit=10
            )
            assert [item.message.aggregate_id for item in first_claim] == ["scope-first"]
            assert [item.message.aggregate_id for item in second_claim] == ["scope-second"]
            assert first_claim[0].message.outbox_id != second_claim[0].message.outbox_id
            assert len(first_claim[0].message.outbox_id) <= 128
            with pytest.raises(LeaseLost):
                await second_store.mark_published(
                    outbox_id=first_claim[0].message.outbox_id,
                    claim_token=first_claim[0].claim_token,
                    published_at=now,
                )
            assert await first_store.reconcile_expired(now + timedelta(seconds=2), 10) == 1
            with pytest.raises(LeaseLost):
                await first_store.mark_terminal(
                    outbox_id=second_claim[0].message.outbox_id,
                    claim_token=second_claim[0].claim_token,
                    error="foreign",
                )
            await second_store.mark_published(
                outbox_id=second_claim[0].message.outbox_id,
                claim_token=second_claim[0].claim_token,
                published_at=now,
            )
        finally:
            await _clean_owned_tables(engine)
            async with engine.begin() as connection:
                await connection.execute(text("DELETE FROM project WHERE project_id='scope-other'"))

    async def run() -> None:
        engine, sessions = create_engine_and_session_factory(database_url)
        repository = MysqlTurnRepository(sessions, scope=VALIDATION_SCOPE)
        try:
            await scenario(engine, repository)
        finally:
            await engine.dispose()

    asyncio.run(run())
