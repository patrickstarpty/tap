"""Explicit application ports around the framework-free Chat domain."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, TypedDict

from tap.contracts.chat_stream import ChatEventEnvelope
from tap.contracts.http import RetrievalAnswerResponse
from tap.modules.access.domain.context import ProjectScopeContext
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

    enterprise_id: str
    project_id: str
    command_id: CommandId
    outbox_id: str
    aggregate_type: str
    aggregate_id: str
    sequence: int | None


@dataclass(frozen=True, slots=True)
class ClaimedOutbox:
    message: DispatchMessage
    attempt_count: int
    claim_token: str


class TurnRepository(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def create_with_outbox(self, command: CreateTurnCommand) -> Turn: ...

    async def append_events(
        self,
        turn_id: TurnId,
        expected_sequence: int,
        events: Sequence[ChatEventEnvelope],
    ) -> None: ...


class OutboxRepository(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

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
        claim_token: str,
        published_at: datetime,
    ) -> None: ...

    async def mark_failed(
        self,
        *,
        outbox_id: str,
        claim_token: str,
        next_attempt_at: datetime,
        error: str,
    ) -> None: ...

    async def mark_terminal(
        self,
        *,
        outbox_id: str,
        claim_token: str,
        error: str,
    ) -> None: ...


class MessagePublisher(Protocol):
    async def publish_once(self, message: DispatchMessage) -> bool:
        """Publish atomically if the stable command identity has not been seen."""
        ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class ConversationAnswerStep(TypedDict, total=False):
    """A resumable, JSON-serializable snapshot of one phase of
    `KnowledgeHttpService.answer_conversation`, checkpointed between
    `prepare`/`generate`/`verify`/`finalize` so a LangGraph node can call each
    phase on its own.

    `kind` names the phase the step last completed: `"prepared"` (retrieval
    ran, ready for generation), `"generated"` (a model answer was produced,
    ready for verification), `"verified"` (a verified answer is ready to
    persist and map to HTTP -- either from the verify phase, or directly from
    an abstaining prepare), or `"final"` (a complete HTTP response for a
    route that never reaches retrieval; `generate`/`verify` pass it through
    unchanged).
    """

    kind: Literal["prepared", "generated", "verified", "final"]
    bundle: dict[str, object]
    generation: dict[str, object]
    response: dict[str, object]


class ConversationAnswerSteps(Protocol):
    """The resumable phases of `KnowledgeHttpService.answer_conversation`;
    `answer_conversation` is one persisted-deadline-bounded composition of
    these four, and a LangGraph node may instead call each on its own."""

    async def prepare_conversation_answer(
        self, request, frozen_input, *, answer_plan=None, authorize=None
    ) -> ConversationAnswerStep: ...

    async def generate_conversation_answer(
        self,
        step: ConversationAnswerStep,
        frozen_input,
        *,
        answer_plan=None,
        authorize=None,
    ) -> ConversationAnswerStep: ...

    async def verify_conversation_answer(
        self,
        step: ConversationAnswerStep,
        frozen_input,
        *,
        answer_plan=None,
        authorize=None,
    ) -> ConversationAnswerStep: ...

    async def finalize_conversation_answer(
        self,
        step: ConversationAnswerStep,
        frozen_input,
        *,
        answer_plan=None,
        authorize=None,
    ) -> RetrievalAnswerResponse: ...
