"""Explicit application ports around the framework-free Chat domain."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from tap.contracts.chat_stream import ChatEventEnvelope
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.assets import SkillRevision
from tap.modules.ai.domain.skill_activation import PromptSkill
from tap.modules.ai.domain.skill_metadata import ActivationMode
from tap.modules.ai.domain.tool_calls import AdmittedToolCall
from tap.modules.chat.domain.models import ChatId, CommandId, Turn, TurnId
from tap.modules.knowledge.api import AnswerResponse as _AnswerResponse
from tap.modules.knowledge.api import ConversationAnswerStep as _ConversationAnswerStep
from tap.modules.knowledge.api import ConversationExecutionContext as _ConversationExecutionContext
from tap.modules.knowledge.api import ConversationToolResult as _ConversationToolResult
from tap.modules.knowledge.api import DirectAnswer as _DirectAnswer


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


def draft_stream_key(turn_id: str) -> str:
    """The Redis Stream key for one Turn's draft chunks.

    The single source of truth for this key format: adapters (`RedisDraftStream`)
    and consumers (the `/stream` SSE route) must both import this rather than
    each reconstructing `tap:turn:{turnId}:draft` themselves (M-5)."""

    return f"tap:turn:{turn_id}:draft"


def wake_stream_key(conversation_id: str) -> str:
    """The Redis Stream key for one Conversation's wake-up hints; see `draft_stream_key`."""

    return f"tap:conv:{conversation_id}:events"


def cancel_stream_key(turn_id: str) -> str:
    """The Redis key for one Turn's cancel marker; see `draft_stream_key`."""

    return f"tap:turn:{turn_id}:cancel"


class DraftStream(Protocol):
    """Redis Stream adapter for one Turn's model-answer draft and the
    conversation-level wake-up stream that announces new authoritative events.
    Any Redis failure must be raised as `DraftStreamUnavailable`."""

    async def append(
        self, turn_id: str, *, attempt: int, seq: int, text: str, reset: bool = False
    ) -> None: ...

    async def replay(self, turn_id: str, *, attempt: int) -> tuple[tuple[DraftChunk, ...], str]:
        """Chunks for `attempt` ordered by `seq` (with every chunk before the
        last `reset: true` chunk, if any, discarded), paired with the raw id
        of the last entry seen anywhere in this Turn's draft stream (any
        attempt), or `"0-0"` if the stream is empty or missing.

        Callers must continue their live `read()` draft-key cursor from this
        id, never from `"$"`: `"$"` is resolved by Redis at the moment `read`
        is actually called, so an entry written between this call and the
        first `read()` would otherwise be silently lost (I-4)."""
        ...

    async def read(self, keys: Mapping[str, str], *, block_ms: int) -> tuple[StreamEntry, ...]:
        """Block until any of `keys` (Redis key -> starting entry id) has new
        entries, or `block_ms` elapses; return every new entry found."""
        ...

    async def partial_text(self, turn_id: str) -> tuple[int, str] | None:
        """The highest attempt seen for `turn_id` and its concatenated draft
        text, or `None` if no draft has been written yet."""
        ...

    async def request_cancel(self, turn_id: str) -> None:
        """Mark `turn_id` as canceled for later `cancel_requested` reads
        (`SET tap:turn:{turnId}:cancel 1 EX 86400`)."""
        ...

    async def cancel_requested(self, turn_id: str) -> bool:
        """Whether `request_cancel` has been called for `turn_id`."""
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


ConversationAnswerStep = _ConversationAnswerStep
ConversationToolResult = _ConversationToolResult


@dataclass(frozen=True, slots=True)
class TurnSkillUse:
    """One Skill a Turn used (spec 1.2): frozen manual/pinned Skills and those
    `skill.activate` loaded, merged for the Turn's "Skills used" view.
    `label` is the display name of the revision the Turn used."""

    skill_id: str
    skill_revision_id: str
    label: str
    mode: ActivationMode


class TurnSkillActivations(Protocol):
    """Reads the Skills each Turn used, for the conversation detail view."""

    async def for_turns(self, turn_ids: Sequence[str]) -> dict[str, tuple[TurnSkillUse, ...]]: ...


class SkillActivationRuntime(Protocol):
    """What the `tools` node needs to run `skill.activate`: load the frozen
    catalog revision's text, then record the auto activation idempotently
    (one row per Turn and Skill, so a resumed node never duplicates it)."""

    async def load(self, revision_id: str) -> SkillRevision: ...

    async def record_auto(
        self,
        *,
        turn_id: str,
        skill_id: str,
        skill_revision_id: str,
        bytes_: int,
        activated_at: datetime,
    ) -> None: ...


class ConversationAnswerSteps(Protocol):
    """The resumable phases of `ConversationAnswerExecutor.answer_conversation`;
    `answer_conversation` is one persisted-deadline-bounded composition of
    these four, and a LangGraph node may instead call each on its own."""

    async def prepare_conversation_answer(
        self, request, context: _ConversationExecutionContext, *, include_relations=True
    ) -> ConversationAnswerStep: ...

    async def generate_conversation_answer(
        self,
        step: ConversationAnswerStep,
        context: _ConversationExecutionContext,
        *,
        on_text: Callable[[str], Awaitable[None]] | None = None,
        tools: tuple[str, ...] = (),
        activated_skills: tuple[PromptSkill, ...] = (),
    ) -> ConversationAnswerStep: ...

    async def verify_conversation_answer(
        self,
        step: ConversationAnswerStep,
        context: _ConversationExecutionContext,
    ) -> ConversationAnswerStep: ...

    async def finalize_conversation_answer(
        self,
        step: ConversationAnswerStep,
        context: _ConversationExecutionContext,
    ) -> _AnswerResponse | _DirectAnswer: ...

    async def refuse_out_of_scope(self, frozen_input, *, answer_plan) -> _DirectAnswer: ...

    async def run_conversation_tool(
        self,
        step: ConversationAnswerStep,
        context: _ConversationExecutionContext,
        call: AdmittedToolCall,
    ) -> ConversationToolResult: ...

    async def add_insights_records(
        self,
        step: ConversationAnswerStep,
        records: Sequence[Mapping[str, object]],
    ) -> tuple[ConversationAnswerStep, list[str]]: ...
