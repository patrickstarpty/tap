"""Explicit application ports around the framework-free Chat domain."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
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


class DraftStreamUnavailable(Exception):
    """A Redis-backed draft or wake-up stream could not complete an operation.

    Callers must degrade (stop streaming drafts, fall back to MySQL polling)
    rather than fail the Turn: Redis here is a latency optimization only.
    """


@dataclass(frozen=True, slots=True)
class DraftChunk:
    attempt: int
    seq: int
    text: str
    # Set by `DraftBatcher` (Task 5) when a redaction-driven rewrite changes
    # an already-sent prefix: `text` is empty and every earlier chunk for
    # this attempt must be discarded before accumulating what follows.
    reset: bool = False


@dataclass(frozen=True, slots=True)
class StreamEntry:
    """One raw entry read from a Redis Stream, identified by its source key."""

    key: str
    id: str
    fields: Mapping[str, str]


class DraftStream(Protocol):
    """Redis Stream adapter for one Turn's model-answer draft and the
    conversation-level wake-up stream that announces new authoritative events.
    Any Redis failure must be raised as `DraftStreamUnavailable`."""

    async def append(
        self, turn_id: str, *, attempt: int, seq: int, text: str, reset: bool = False
    ) -> None: ...

    async def replay(self, turn_id: str, *, attempt: int) -> tuple[DraftChunk, ...]:
        """Chunks for `attempt` ordered by `seq`, with every chunk before the
        last `reset: true` chunk (if any) discarded."""
        ...

    async def read(self, keys: Mapping[str, str], *, block_ms: int) -> tuple[StreamEntry, ...]:
        """Block until any of `keys` (Redis key -> starting entry id) has new
        entries, or `block_ms` elapses; return every new entry found."""
        ...

    async def partial_text(self, turn_id: str) -> tuple[int, str] | None:
        """The highest attempt seen for `turn_id` and its concatenated draft
        text, or `None` if no draft has been written yet."""
        ...


class EventWakeups(Protocol):
    """Announces that a new authoritative Conversation event committed, so an
    SSE reader blocked on Redis can re-read MySQL instead of polling it."""

    async def notify(self, conversation_id: str, sequence: int) -> None: ...


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
    # PR 3: present only on a `"prepared"` step, built from the evidence/
    # relation context `prepare_conversation_answer` just retrieved (before
    # it is encoded into `bundle`) -- the `retrieval.hits_ready` payload the
    # `seed_search` graph node emits immediately, without itself needing to
    # decode the opaque `bundle`.
    retrieval_hits: dict[str, object]


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
        on_text: Callable[[str], Awaitable[None]] | None = None,
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

    async def refuse_out_of_scope(
        self, frozen_input, *, answer_plan
    ) -> RetrievalAnswerResponse: ...
