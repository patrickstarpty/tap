"""Async MySQL repositories for Turns, events, and the transactional Outbox."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import NoReturn, cast
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.contracts.chat_stream import ChatEventEnvelope
from tap.modules.chat.application.ports import (
    ClaimedOutbox,
    CreateTurnCommand,
    DispatchMessage,
    LeaseLost,
    SequenceConflict,
    TurnNotFound,
)
from tap.modules.chat.domain.models import ChatId, CommandId, Turn, TurnId, TurnState
from tap.platform.db.schema import metadata, outbox

chat_turn = Table(
    "chat_turn",
    metadata,
    Column("turn_id", String(64), primary_key=True),
    Column("chat_id", String(64), nullable=False),
    Column("client_request_id", String(128), nullable=False),
    Column("message", Text, nullable=False),
    Column("state", String(32), nullable=False),
    Column("last_sequence", BigInteger, nullable=False, server_default="0"),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint("chat_id", "client_request_id", name="uq_chat_turn_client_request"),
)

chat_event = Table(
    "chat_event",
    metadata,
    Column("event_id", String(64), primary_key=True),
    Column(
        "turn_id",
        String(64),
        ForeignKey("chat_turn.turn_id", name="fk_chat_event_turn"),
        nullable=False,
    ),
    Column("sequence", BigInteger, nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("occurred_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint("turn_id", "sequence", name="uq_chat_event_turn_sequence"),
)

turn_snapshot = Table(
    "turn_snapshot",
    metadata,
    Column(
        "turn_id",
        String(64),
        ForeignKey("chat_turn.turn_id", name="fk_turn_snapshot_turn"),
        primary_key=True,
    ),
    Column("last_sequence", BigInteger, nullable=False),
    Column("snapshot", JSON, nullable=False),
    Column("snapshot_version", BigInteger, nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
)

_TERMINAL_AND_RUNNING_STATES: Mapping[str, TurnState] = {
    "turn.started": TurnState.RUNNING,
    "turn.completed": TurnState.COMPLETED,
    "turn.abstained": TurnState.ABSTAINED,
    "turn.canceled": TurnState.CANCELED,
    "turn.failed": TurnState.FAILED,
}


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_contract_time(value: str) -> datetime:
    return _utc_naive(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _turn_from_row(row: RowMapping) -> Turn:
    return Turn(
        turn_id=TurnId(cast(str, row["turn_id"])),
        chat_id=ChatId(cast(str, row["chat_id"])),
        client_request_id=cast(str, row["client_request_id"]),
        message=cast(str, row["message"]),
        state=TurnState(cast(str, row["state"])),
        last_sequence=cast(int, row["last_sequence"]),
        created_at=cast(datetime, row["created_at"]),
    )


class MysqlTurnRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_with_outbox(self, command: CreateTurnCommand) -> Turn:
        created_at = _utc_naive(command.occurred_at)
        try:
            async with self._sessions() as session, session.begin():
                await session.execute(
                    insert(chat_turn).values(
                        turn_id=command.turn_id,
                        chat_id=command.chat_id,
                        client_request_id=command.client_request_id,
                        message=command.message,
                        state=TurnState.QUEUED.value,
                        last_sequence=0,
                        created_at=created_at,
                    )
                )
                await session.execute(
                    insert(outbox).values(
                        outbox_id=f"turn-command:{command.command_id}",
                        command_id=command.command_id,
                        aggregate_type="chat_turn",
                        aggregate_id=command.turn_id,
                        sequence=None,
                        message_type="turn.process_requested",
                        status="pending",
                        attempt_count=0,
                        next_attempt_at=created_at,
                        created_at=created_at,
                    )
                )
        except IntegrityError as error:
            existing = await self._find_by_client_request(
                chat_id=command.chat_id,
                client_request_id=command.client_request_id,
            )
            if existing is not None:
                return existing
            raise error

        return Turn(
            turn_id=command.turn_id,
            chat_id=command.chat_id,
            client_request_id=command.client_request_id,
            message=command.message,
            state=TurnState.QUEUED,
            last_sequence=0,
            created_at=created_at,
        )

    async def _find_by_client_request(
        self,
        *,
        chat_id: ChatId,
        client_request_id: str,
    ) -> Turn | None:
        async with self._sessions() as session:
            result = await session.execute(
                select(chat_turn).where(
                    chat_turn.c.chat_id == chat_id,
                    chat_turn.c.client_request_id == client_request_id,
                )
            )
            row = result.mappings().one_or_none()
        return None if row is None else _turn_from_row(row)

    async def append_events(
        self,
        turn_id: TurnId,
        expected_sequence: int,
        events: Sequence[ChatEventEnvelope],
    ) -> None:
        if not events:
            return
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                select(chat_turn).where(chat_turn.c.turn_id == turn_id).with_for_update()
            )
            turn_row = result.mappings().one_or_none()
            if turn_row is None:
                raise TurnNotFound(str(turn_id))
            current_sequence = cast(int, turn_row["last_sequence"])
            if current_sequence != expected_sequence:
                raise SequenceConflict(
                    f"expected sequence {expected_sequence}, found {current_sequence}"
                )

            chat_id = cast(str, turn_row["chat_id"])
            state = TurnState(cast(str, turn_row["state"]))
            for offset, envelope in enumerate(events, start=1):
                required_sequence = expected_sequence + offset
                if envelope.sequence != required_sequence:
                    raise SequenceConflict(
                        f"event sequence {envelope.sequence}, required {required_sequence}"
                    )
                if envelope.turn_id != turn_id or envelope.chat_id != chat_id:
                    raise ValueError("event aggregate identity does not match the locked Turn")
                occurred_at = _parse_contract_time(envelope.occurred_at)
                event_type = envelope.event.type
                await session.execute(
                    insert(chat_event).values(
                        event_id=envelope.event_id,
                        turn_id=turn_id,
                        sequence=envelope.sequence,
                        event_type=event_type,
                        payload=envelope.event.model_dump(mode="json", by_alias=True),
                        schema_version=envelope.schema_version,
                        occurred_at=occurred_at,
                    )
                )
                await session.execute(
                    insert(outbox).values(
                        outbox_id=f"chat-event:{envelope.event_id}",
                        command_id=f"chat-event:{envelope.event_id}",
                        aggregate_type="chat_turn",
                        aggregate_id=turn_id,
                        sequence=envelope.sequence,
                        message_type="chat.event_appended",
                        status="pending",
                        attempt_count=0,
                        next_attempt_at=occurred_at,
                        created_at=occurred_at,
                    )
                )
                state = _TERMINAL_AND_RUNNING_STATES.get(event_type, state)

            await session.execute(
                update(chat_turn)
                .where(chat_turn.c.turn_id == turn_id)
                .values(
                    last_sequence=expected_sequence + len(events),
                    state=state.value,
                )
            )


class OutboxStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def reconcile_expired(self, now: datetime, limit: int) -> int:
        if limit <= 0:
            return 0
        async with self._sessions() as session, session.begin():
            expired_ids = list(
                (
                    await session.execute(
                        select(outbox.c.outbox_id)
                        .where(
                            outbox.c.status == "publishing",
                            outbox.c.lease_until <= _utc_naive(now),
                        )
                        .order_by(outbox.c.lease_until, outbox.c.outbox_id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars()
            )
            if not expired_ids:
                return 0
            await session.execute(
                update(outbox)
                .where(outbox.c.outbox_id.in_(expired_ids))
                .values(
                    status="pending",
                    claimed_by=None,
                    claim_token=None,
                    lease_until=None,
                    next_attempt_at=_utc_naive(now),
                )
            )
            return len(expired_ids)

    async def claim_pending(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> list[ClaimedOutbox]:
        if limit <= 0:
            return []
        claim_time = _utc_naive(now)
        async with self._sessions() as session, session.begin():
            pending_ids = list(
                (
                    await session.execute(
                        select(outbox.c.outbox_id)
                        .where(
                            outbox.c.status == "pending",
                            outbox.c.next_attempt_at <= claim_time,
                        )
                        .order_by(outbox.c.created_at, outbox.c.outbox_id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars()
            )
            if not pending_ids:
                return []
            claim_tokens = {outbox_id: uuid4().hex for outbox_id in pending_ids}
            for outbox_id, claim_token in claim_tokens.items():
                await session.execute(
                    update(outbox)
                    .where(outbox.c.outbox_id == outbox_id)
                    .values(
                        status="publishing",
                        claimed_by=worker_id,
                        claim_token=claim_token,
                        lease_until=claim_time + lease_duration,
                        attempt_count=outbox.c.attempt_count + 1,
                        last_error=None,
                    )
                )
            rows = (
                await session.execute(select(outbox).where(outbox.c.outbox_id.in_(pending_ids)))
            ).mappings()
            return [
                ClaimedOutbox(
                    message=DispatchMessage(
                        command_id=CommandId(cast(str, row["command_id"])),
                        outbox_id=cast(str, row["outbox_id"]),
                        aggregate_type=cast(str, row["aggregate_type"]),
                        aggregate_id=cast(str, row["aggregate_id"]),
                        sequence=cast(int | None, row["sequence"]),
                    ),
                    attempt_count=cast(int, row["attempt_count"]),
                    claim_token=cast(str, row["claim_token"]),
                )
                for row in rows
            ]

    async def mark_published(
        self,
        *,
        outbox_id: str,
        claim_token: str,
        published_at: datetime,
    ) -> None:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(outbox)
                .where(
                    outbox.c.outbox_id == outbox_id,
                    outbox.c.status == "publishing",
                    outbox.c.claim_token == claim_token,
                )
                .values(
                    status="published",
                    published_at=_utc_naive(published_at),
                    claimed_by=None,
                    claim_token=None,
                    lease_until=None,
                    last_error=None,
                )
            )
            if result.rowcount != 1:
                self._raise_lease_lost(outbox_id)

    async def mark_failed(
        self,
        *,
        outbox_id: str,
        claim_token: str,
        next_attempt_at: datetime,
        error: str,
    ) -> None:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(outbox)
                .where(
                    outbox.c.outbox_id == outbox_id,
                    outbox.c.status == "publishing",
                    outbox.c.claim_token == claim_token,
                )
                .values(
                    status="pending",
                    next_attempt_at=_utc_naive(next_attempt_at),
                    claimed_by=None,
                    claim_token=None,
                    lease_until=None,
                    last_error=error[:2048],
                )
            )
            if result.rowcount != 1:
                self._raise_lease_lost(outbox_id)

    async def mark_terminal(
        self,
        *,
        outbox_id: str,
        claim_token: str,
        error: str,
    ) -> None:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(outbox)
                .where(
                    outbox.c.outbox_id == outbox_id,
                    outbox.c.status == "publishing",
                    outbox.c.claim_token == claim_token,
                )
                .values(
                    status="delivery_failed",
                    claimed_by=None,
                    claim_token=None,
                    lease_until=None,
                    last_error=error[:2048],
                )
            )
            if result.rowcount != 1:
                self._raise_lease_lost(outbox_id)

    @staticmethod
    def _raise_lease_lost(outbox_id: str) -> NoReturn:
        raise LeaseLost(outbox_id)
