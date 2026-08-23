"""Explicit application ports around the framework-free Chat domain."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from tap.contracts.chat_stream import ChatEventEnvelope
from tap.modules.chat.domain.models import ChatId, CommandId, Turn, TurnId


class SequenceConflict(Exception):
    """The aggregate sequence changed before an append could commit."""


class TurnNotFound(Exception):
    """The requested Turn does not exist."""


class LeaseLost(Exception):
    """A relay tried to settle a claim that it no longer owns."""


@dataclass(frozen=True, slots=True)
class CreateTurnCommand:
    command_id: CommandId
    turn_id: TurnId
    chat_id: ChatId
    client_request_id: str
    message: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class DispatchMessage:
    """Non-authoritative Redis notification containing lookup identities only."""

    command_id: CommandId
    outbox_id: str
    aggregate_type: str
    aggregate_id: str
    sequence: int | None


@dataclass(frozen=True, slots=True)
class ClaimedOutbox:
    message: DispatchMessage
    attempt_count: int


class TurnRepository(Protocol):
    async def create_with_outbox(self, command: CreateTurnCommand) -> Turn: ...

    async def append_events(
        self,
        turn_id: TurnId,
        expected_sequence: int,
        events: Sequence[ChatEventEnvelope],
    ) -> None: ...


class OutboxRepository(Protocol):
    async def reconcile_expired(self, now: datetime, limit: int) -> int: ...

    async def claim_pending(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> list[ClaimedOutbox]: ...

    async def mark_published(
        self,
        *,
        outbox_id: str,
        worker_id: str,
        published_at: datetime,
    ) -> None: ...

    async def mark_failed(
        self,
        *,
        outbox_id: str,
        worker_id: str,
        next_attempt_at: datetime,
        error: str,
    ) -> None: ...


class MessagePublisher(Protocol):
    async def publish_once(self, message: DispatchMessage) -> bool:
        """Publish atomically if the stable command identity has not been seen."""
        ...


class Clock(Protocol):
    def now(self) -> datetime: ...
