"""Conversation use cases and a deterministic test adapter."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import uuid4

from opentelemetry import trace

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.chat.application.ports import DraftStream, EventWakeups
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    AnswerEvidenceSnapshot,
    Conversation,
    ConversationEvent,
    ConversationTurn,
    GraphContextStatus,
    RetrievalSummary,
    TurnInputSnapshot,
    conversation_title,
)

_TERMINAL_TURN_STATES = frozenset({"completed", "abstained", "failed", "canceled"})

_LOGGER = logging.getLogger(__name__)


class ConversationNotFound(Exception):
    pass


class ConversationConflict(ValueError):
    pass


class InvalidConversationCursor(ValueError):
    pass


class ConversationIntegrityError(ValueError):
    """Persisted Conversation facts are permanently inconsistent; retrying cannot help."""


@dataclass(frozen=True, slots=True)
class ConversationOwnership:
    """Minimal owner facts, visible even after a soft delete, for owner-only commands."""

    actor_id: str
    deleted_at: datetime | None


def history_query(value: str | None) -> str | None:
    """Canonical title search text: stripped, empty means no filter."""
    if value is None:
        return None
    stripped = value.strip()
    if len(stripped) > 120:
        raise ValueError("conversation search must be at most 120 characters")
    return stripped or None


class ClaimBatch(tuple[tuple[str, ConversationTurn], ...]):
    """`claim_queued`'s result.

    Behaves exactly like the legacy `tuple[tuple[str, ConversationTurn],
    ...]` of claimed `(conversation_id, turn)` pairs -- iterates, indexes,
    and compares equal to one -- so every existing caller (and every fake
    `claim_queued` in the test suite) needs no change. `.wakeups` adds the
    `(conversation_id, committed sequence)` pairs for every `turn.started`
    (newly claimed) or `turn.failed` (settled-as-unclaimable) event this
    call committed; both bypass `emit`/`complete_evidence`, so their wake-up
    notify must be fired by the caller (`GenerationWorker.run_once`), never
    from inside the repository -- and, since a batch can commit several of
    these in one call, fired concurrently rather than one at a time.
    """

    wakeups: tuple[tuple[str, int], ...]

    def __new__(
        cls,
        claimed: Sequence[tuple[str, ConversationTurn]] = (),
        *,
        wakeups: Sequence[tuple[str, int]] = (),
    ) -> "ClaimBatch":
        self = super().__new__(cls, claimed)
        self.wakeups = tuple(wakeups)
        return self

    @property
    def claimed(self) -> tuple[tuple[str, ConversationTurn], ...]:
        return tuple(self)


class ConversationRepository(Protocol):
    async def create_with_first_turn(self, conversation: Conversation) -> ConversationTurn: ...
    async def claim_queued(self, *, limit: int) -> ClaimBatch: ...
    async def append_turn(
        self, conversation_id: str, turn: ConversationTurn, event: ConversationEvent
    ) -> tuple[ConversationTurn, int | None]:
        """The persisted Turn and the committed event's sequence, or `None`
        when an idempotent replay persisted nothing new."""
        ...

    async def list(
        self, *, limit: int, cursor: str | None, query: str | None = None
    ) -> tuple[tuple[Conversation, ...], str | None]: ...
    async def load(self, conversation_id: str) -> Conversation: ...
    async def ownership(self, conversation_id: str) -> ConversationOwnership: ...
    async def rename(
        self, conversation_id: str, title: str, *, updated_at: datetime
    ) -> Conversation: ...
    async def soft_delete(
        self, conversation_id: str, *, actor_id: str, deleted_at: datetime
    ) -> None: ...
    async def complete(
        self,
        conversation_id: str,
        turn_id: str,
        snapshot: AnswerEvidenceSnapshot,
        event: ConversationEvent,
        lease_token: str | None = None,
        terminal_event: ConversationEvent | None = None,
        stream_events: tuple[ConversationEvent, ...] = (),
    ) -> tuple[ConversationTurn, int | None]:
        """The persisted Turn and the highest sequence committed by this call,
        or `None` when the Turn was already terminal (no-op)."""
        ...

    async def append_stream_event(
        self,
        conversation_id: str,
        turn_id: str,
        event: ConversationEvent,
        lease_token: str | None = None,
    ) -> ConversationEvent: ...
    async def citation_linked(
        self,
        conversation_id: str,
        turn_id: str,
        citation_id: str,
        citation_digest: str,
    ) -> bool: ...
    async def renew_processing_lease(
        self,
        conversation_id: str,
        turn_id: str,
        lease_token: str,
        *,
        lease_duration: timedelta,
    ) -> None: ...


class InMemoryConversationRepository:
    def __init__(self):
        self.values: dict[str, Conversation] = {}

    async def create_with_first_turn(self, conversation):
        if conversation.conversation_id in self.values:
            raise ConversationConflict("idempotency-conflict")
        self.values[conversation.conversation_id] = conversation
        return conversation.turns[0]

    async def append_turn(self, conversation_id, turn, event):
        conversation = await self.load(conversation_id)
        for existing in conversation.turns:
            if existing.client_request_id == turn.client_request_id:
                if existing.input_snapshot.value != turn.input_snapshot.value:
                    raise ConversationConflict("idempotency-conflict")
                return existing, None
        self.values[conversation_id] = replace(
            conversation,
            turns=(*conversation.turns, turn),
            events=(*conversation.events, event),
            updated_at=event.occurred_at,
        )
        return turn, event.sequence

    async def claim_queued(self, *, limit: int) -> ClaimBatch:
        """A simplified double of `MysqlConversationRepository.claim_queued`:
        claims up to `limit` Turns currently `"queued"`, writing
        `turn.started`. Unlike the real repository, it never reclaims an
        expired `"running"` lease and never settles an unclaimable Turn
        (`turn.failed`) -- those depend on MySQL-only state (lease
        expiry columns, retry-attempt exhaustion) this in-memory domain
        model does not carry.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        claimed: list[tuple[str, ConversationTurn]] = []
        wakeups: list[tuple[str, int]] = []
        for conversation_id, conversation in list(self.values.items()):
            for turn in conversation.turns:
                if len(claimed) >= limit:
                    break
                if turn.state != "queued":
                    continue
                now = datetime.now(timezone.utc)
                event = ConversationEvent(
                    uuid4().hex,
                    max((item.sequence for item in conversation.events), default=0) + 1,
                    "turn.started",
                    {"state": "running"},
                    now,
                    turn.turn_id,
                )
                updated_turn = replace(
                    turn,
                    state="running",
                    attempt=turn.attempt + 1,
                    lease_token=uuid4().hex,
                )
                conversation = replace(
                    conversation,
                    turns=tuple(
                        updated_turn if item.turn_id == turn.turn_id else item
                        for item in conversation.turns
                    ),
                    events=(*conversation.events, event),
                    updated_at=now,
                )
                self.values[conversation_id] = conversation
                claimed.append((conversation_id, updated_turn))
                wakeups.append((conversation_id, event.sequence))
            if len(claimed) >= limit:
                break
        return ClaimBatch(claimed, wakeups=wakeups)

    async def list(self, *, limit, cursor, query=None):
        needle = None if query is None else query.casefold()
        values = sorted(
            (
                item
                for item in self.values.values()
                if item.deleted_at is None and (needle is None or needle in item.title.casefold())
            ),
            key=lambda item: (item.updated_at, item.conversation_id),
            reverse=True,
        )
        cursor_id = None if cursor is None else cursor.rsplit("|", 1)[-1]
        start = (
            0
            if cursor_id is None
            else next(
                (i + 1 for i, item in enumerate(values) if item.conversation_id == cursor_id),
                len(values),
            )
        )
        page = tuple(values[start : start + limit])
        next_cursor = (
            f"{page[-1].updated_at.isoformat()}|{page[-1].conversation_id}"
            if start + limit < len(values)
            else None
        )
        return page, next_cursor

    async def load(self, conversation_id):
        value = self.values.get(conversation_id)
        if value is None or value.deleted_at is not None:
            raise ConversationNotFound
        return value

    async def ownership(self, conversation_id):
        value = self.values.get(conversation_id)
        if value is None:
            raise ConversationNotFound
        return ConversationOwnership(value.actor_id or "", value.deleted_at)

    async def rename(self, conversation_id, title, *, updated_at):
        value = await self.load(conversation_id)
        renamed = replace(value, title=title, updated_at=updated_at)
        self.values[conversation_id] = renamed
        return renamed

    async def soft_delete(self, conversation_id, *, actor_id, deleted_at):
        value = self.values.get(conversation_id)
        if value is None:
            raise ConversationNotFound
        if value.deleted_at is None:
            self.values[conversation_id] = replace(
                value, deleted_at=deleted_at, deleted_by=actor_id
            )

    async def complete(
        self,
        conversation_id,
        turn_id,
        snapshot,
        event,
        lease_token=None,
        terminal_event=None,
        stream_events=(),
    ):
        conversation = await self.load(conversation_id)
        current = next((turn for turn in conversation.turns if turn.turn_id == turn_id), None)
        if current is None:
            raise ConversationNotFound
        if current.state in {"completed", "abstained", "failed", "canceled"}:
            return current, None
        turns = []
        found = None
        for turn in conversation.turns:
            if turn.turn_id == turn_id:
                found = replace(turn, state=snapshot.value.outcome, answer_snapshot=snapshot)
                turns.append(found)
            else:
                turns.append(turn)
        if found is None:
            raise ConversationNotFound
        committed_sequence = (
            len(conversation.events) + len(stream_events) + (2 if terminal_event else 1)
        )
        self.values[conversation_id] = replace(
            conversation,
            turns=tuple(turns),
            events=(
                *conversation.events,
                *tuple(
                    replace(
                        stream_event,
                        sequence=len(conversation.events) + offset,
                        turn_id=turn_id,
                    )
                    for offset, stream_event in enumerate(stream_events, start=1)
                ),
                *(
                    (
                        replace(
                            terminal_event,
                            sequence=len(conversation.events) + len(stream_events) + 1,
                            turn_id=turn_id,
                        ),
                    )
                    if terminal_event
                    else ()
                ),
                replace(event, sequence=committed_sequence),
            ),
            updated_at=event.occurred_at,
        )
        return found, committed_sequence

    async def append_stream_event(self, conversation_id, turn_id, event, lease_token=None):
        conversation = await self.load(conversation_id)
        turn = next((item for item in conversation.turns if item.turn_id == turn_id), None)
        if turn is None or turn.state in {"completed", "abstained", "failed", "canceled"}:
            raise ConversationConflict("terminal Turn cannot accept stream events")
        event = replace(
            event,
            sequence=max(item.sequence for item in conversation.events) + 1,
            turn_id=turn_id,
        )
        self.values[conversation_id] = replace(
            conversation, events=(*conversation.events, event), updated_at=event.occurred_at
        )
        return event

    async def citation_linked(self, conversation_id, turn_id, citation_id, citation_digest):
        conversation = await self.load(conversation_id)
        turn = next((item for item in conversation.turns if item.turn_id == turn_id), None)
        return bool(
            turn is not None
            and turn.answer_snapshot is not None
            and any(
                item.citation_snapshot_id == citation_id and item.citation_digest == citation_digest
                for item in turn.answer_snapshot.value.citations
            )
        )

    async def renew_processing_lease(
        self, conversation_id, turn_id, lease_token, *, lease_duration
    ):
        del lease_token, lease_duration
        conversation = await self.load(conversation_id)
        turn = next((item for item in conversation.turns if item.turn_id == turn_id), None)
        if turn is None or turn.state in {"completed", "abstained", "failed", "canceled"}:
            raise ConversationConflict("generation lease lost")


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository,
        *,
        scope: ProjectScopeContext,
        wakeups: EventWakeups | None = None,
        drafts: DraftStream | None = None,
    ):
        self.repository = repository
        self.scope = scope
        self._wakeups = wakeups
        self._drafts = drafts

    async def _notify_wakeups(self, conversation_id: str, sequence: int) -> None:
        """Best-effort: a wake-up only shortens SSE latency, so a Redis
        failure here must never fail the caller's already-committed write."""
        if self._wakeups is None:
            return
        try:
            await self._wakeups.notify(conversation_id, sequence)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.warning(
                "conversation stream wakeup failed: conversation_id=%s",
                conversation_id,
                exc_info=True,
            )
            trace.get_current_span().set_attribute("tap.stream.wakeup_failed", True)

    async def notify_external_write(self, conversation_id: str, sequence: int) -> None:
        """For a write made directly through `repository`, bypassing `emit`/
        `complete_evidence` -- e.g. the generation worker's `claim_queued`
        (`turn.started`) or `_fail_unclaimable_turn` (`turn.failed`) -- where
        the caller already knows the committed sequence."""
        await self._notify_wakeups(conversation_id, sequence)

    def _turn(self, conversation_id, turn_id, request_id, value, *, attempt=0, now=None):
        now = now or datetime.now(timezone.utc)
        snapshot = TurnInputSnapshot.create(
            snapshot_id=uuid4().hex,
            project_id=self.scope.project_id,
            turn_id=turn_id,
            value=value,
            now=now,
        )
        event = ConversationEvent(
            uuid4().hex,
            1,
            "conversation.turn.requested",
            {
                "conversationId": conversation_id,
                "turnId": turn_id,
                "inputSnapshotDigest": snapshot.digest,
            },
            now,
            turn_id,
        )
        return ConversationTurn(turn_id, request_id, attempt, "queued", snapshot), event

    async def create(self, conversation_id, turn_id, request_id, value):
        turn, event = self._turn(conversation_id, turn_id, request_id, value)
        now = event.occurred_at
        conversation = Conversation(
            conversation_id,
            self.scope.project_id,
            value.message[:120],
            now,
            now,
            (turn,),
            (event,),
            actor_id=self.scope.actor_id,
        )
        created = await self.repository.create_with_first_turn(conversation)
        # Trivial: a brand-new conversation's first event is always sequence
        # 1; an idempotent-replay no-op notifying anyway is harmless (the
        # sequence is only a hint -- see `RedisEventWakeups.notify`).
        await self._notify_wakeups(conversation_id, 1)
        return created

    async def append(self, conversation_id, turn_id, request_id, value):
        conversation = await self.load(conversation_id)
        turn, event = self._turn(conversation_id, turn_id, request_id, value)
        event = replace(event, sequence=len(conversation.events) + 1)
        persisted_turn, sequence = await self.repository.append_turn(conversation_id, turn, event)
        if sequence is not None:
            await self._notify_wakeups(conversation_id, sequence)
        return persisted_turn

    async def list(self, *, limit, cursor, query: str | None = None):
        return await self.repository.list(limit=limit, cursor=cursor, query=history_query(query))

    async def _owned(self, conversation_id: str, actor_id: str) -> ConversationOwnership:
        owner = await self.repository.ownership(conversation_id)
        if owner.actor_id != actor_id:
            raise AuthorizationDenied("conversation-owner-required")
        return owner

    async def rename(self, conversation_id: str, title: str, *, actor_id: str) -> Conversation:
        canonical = conversation_title(title)
        owner = await self._owned(conversation_id, actor_id)
        if owner.deleted_at is not None:
            raise ConversationNotFound
        return await self.repository.rename(
            conversation_id, canonical, updated_at=datetime.now(timezone.utc)
        )

    async def delete(self, conversation_id: str, *, actor_id: str) -> None:
        owner = await self._owned(conversation_id, actor_id)
        if owner.deleted_at is not None:
            return
        conversation = await self.load(conversation_id)
        for turn in conversation.turns:
            if turn.state not in _TERMINAL_TURN_STATES:
                await self.cancel(conversation_id, turn.turn_id)
        await self.repository.soft_delete(
            conversation_id, actor_id=actor_id, deleted_at=datetime.now(timezone.utc)
        )

    async def load(self, conversation_id):
        value = await self.repository.load(conversation_id)
        if value.project_id != self.scope.project_id:
            raise ConversationNotFound
        return value

    async def replay(self, conversation_id: str, request_id: str):
        try:
            conversation = await self.load(conversation_id)
        except ConversationNotFound:
            return None
        return next(
            (turn for turn in conversation.turns if turn.client_request_id == request_id), None
        )

    async def cancel(self, conversation_id, turn_id):
        conversation = await self.load(conversation_id)
        turn = next((item for item in conversation.turns if item.turn_id == turn_id), None)
        if turn is None:
            raise ConversationNotFound
        if turn.state in _TERMINAL_TURN_STATES:
            return turn
        partial_answer = await self._partial_draft_text(turn_id)
        payload: dict[str, object] = {"partialAnswerRetained": partial_answer is not None}
        if partial_answer is not None:
            payload["partialAnswer"] = partial_answer
        result = await self.complete_evidence(
            conversation_id,
            turn_id,
            AnswerEvidence(
                "",
                "canceled",
                RetrievalSummary("canceled"),
                GraphContextStatus.NOT_REQUESTED,
            ),
            terminal_event=("turn.canceled", payload),
        )
        if self._drafts is not None:
            try:
                await self._drafts.request_cancel(turn_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.warning("draft cancel marker failed: turn_id=%s", turn_id, exc_info=True)
        return result

    async def _partial_draft_text(self, turn_id: str) -> str | None:
        """The latest draft text for `turn_id`, or `None` if no draft was
        written or Redis could not be read (failure is ignored here -- the
        draft is only a latency optimization, and `cancel` must still
        terminate the Turn without it)."""
        if self._drafts is None:
            return None
        try:
            partial = await self._drafts.partial_text(turn_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        if partial is None:
            return None
        _attempt, text = partial
        return text or None

    async def retry(self, conversation_id, turn_id, new_turn_id, request_id):
        old = next(
            turn for turn in (await self.load(conversation_id)).turns if turn.turn_id == turn_id
        )
        return await self.append(conversation_id, new_turn_id, request_id, old.input_snapshot.value)

    async def authorize_citation(self, conversation_id, turn_id, citation_id):
        conversation = await self.load(conversation_id)
        turn = next((item for item in conversation.turns if item.turn_id == turn_id), None)
        if turn is None or turn.answer_snapshot is None:
            raise ConversationNotFound
        evidence = next(
            (
                item
                for item in turn.answer_snapshot.value.citations
                if item.citation_snapshot_id == citation_id
            ),
            None,
        )
        if evidence is None or not await self.repository.citation_linked(
            conversation_id, turn_id, citation_id, evidence.citation_digest
        ):
            raise ConversationNotFound
        return evidence

    async def complete_for_test(
        self, conversation_id, turn_id, *, answer, graph_status
    ) -> ConversationTurn:
        conversation = await self.load(conversation_id)
        turn = next(t for t in conversation.turns if t.turn_id == turn_id)
        now = datetime.now(timezone.utc)
        evidence = AnswerEvidence(
            answer, "completed", RetrievalSummary("completed"), GraphContextStatus(graph_status)
        )
        snapshot = AnswerEvidenceSnapshot.create(
            snapshot_id=uuid4().hex,
            project_id=self.scope.project_id,
            turn_id=turn_id,
            input_digest=turn.input_snapshot.digest,
            value=evidence,
            now=now,
        )
        event = ConversationEvent(
            uuid4().hex,
            len(conversation.events) + 1,
            "conversation.turn.completed",
            {
                "turnId": turn_id,
                "answerEvidenceSnapshotId": snapshot.snapshot_id,
                "answerEvidenceSnapshotDigest": snapshot.digest,
                "outcome": "completed",
            },
            now,
        )
        turn, sequence = await self.repository.complete(conversation_id, turn_id, snapshot, event)
        if sequence is not None:
            await self._notify_wakeups(conversation_id, sequence)
        return turn

    async def complete_evidence(
        self,
        conversation_id: str,
        turn_id: str,
        evidence: AnswerEvidence,
        *,
        lease_token: str | None = None,
        terminal_event: tuple[str, dict[str, object]] | None = None,
        stream_events: tuple[tuple[str, dict[str, object]], ...] = (),
    ) -> ConversationTurn:
        conversation = await self.load(conversation_id)
        turn = next((item for item in conversation.turns if item.turn_id == turn_id), None)
        if turn is None:
            raise ConversationNotFound
        now = datetime.now(timezone.utc)
        snapshot = AnswerEvidenceSnapshot.create(
            snapshot_id=uuid4().hex,
            project_id=self.scope.project_id,
            turn_id=turn_id,
            input_digest=turn.input_snapshot.digest,
            value=evidence,
            now=now,
        )
        event = ConversationEvent(
            uuid4().hex,
            max((item.sequence for item in conversation.events), default=0) + 1,
            "conversation.turn.completed",
            {
                "turnId": turn_id,
                "answerEvidenceSnapshotId": snapshot.snapshot_id,
                "answerEvidenceSnapshotDigest": snapshot.digest,
                "outcome": evidence.outcome,
            },
            now,
        )
        stream_event = (
            None
            if terminal_event is None
            else ConversationEvent(uuid4().hex, 1, terminal_event[0], terminal_event[1], now)
        )
        persisted_stream_events = tuple(
            ConversationEvent(uuid4().hex, 1, event_type, payload, now)
            for event_type, payload in stream_events
        )
        turn, sequence = await self.repository.complete(
            conversation_id,
            turn_id,
            snapshot,
            event,
            lease_token=lease_token,
            terminal_event=stream_event,
            stream_events=persisted_stream_events,
        )
        if sequence is not None:
            await self._notify_wakeups(conversation_id, sequence)
        return turn

    async def emit(
        self,
        conversation_id: str,
        turn_id: str,
        event_type: str,
        payload: dict[str, object],
        *,
        lease_token: str | None = None,
    ) -> ConversationEvent:
        event = await self.repository.append_stream_event(
            conversation_id,
            turn_id,
            ConversationEvent(uuid4().hex, 1, event_type, payload, datetime.now(timezone.utc)),
            lease_token=lease_token,
        )
        await self._notify_wakeups(conversation_id, event.sequence)
        return event
