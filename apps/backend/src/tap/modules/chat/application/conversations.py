"""Conversation use cases and a deterministic test adapter."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    AnswerEvidenceSnapshot,
    Conversation,
    ConversationEvent,
    ConversationTurn,
    GraphContextStatus,
    RetrievalSummary,
    TurnInputSnapshot,
)


class ConversationNotFound(Exception):
    pass


class ConversationConflict(ValueError):
    pass


class InvalidConversationCursor(ValueError):
    pass


class ConversationRepository(Protocol):
    async def create_with_first_turn(self, conversation: Conversation) -> ConversationTurn: ...
    async def append_turn(
        self, conversation_id: str, turn: ConversationTurn, event: ConversationEvent
    ) -> ConversationTurn: ...
    async def list(
        self, *, limit: int, cursor: str | None
    ) -> tuple[tuple[Conversation, ...], str | None]: ...
    async def load(self, conversation_id: str) -> Conversation: ...
    async def complete(
        self,
        conversation_id: str,
        turn_id: str,
        snapshot: AnswerEvidenceSnapshot,
        event: ConversationEvent,
        lease_token: str | None = None,
    ) -> ConversationTurn: ...
    async def append_stream_event(
        self,
        conversation_id: str,
        turn_id: str,
        event: ConversationEvent,
        lease_token: str | None = None,
    ) -> ConversationEvent: ...


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
                return existing
        self.values[conversation_id] = replace(
            conversation,
            turns=(*conversation.turns, turn),
            events=(*conversation.events, event),
            updated_at=event.occurred_at,
        )
        return turn

    async def list(self, *, limit, cursor):
        values = sorted(
            self.values.values(),
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
        try:
            return self.values[conversation_id]
        except KeyError as error:
            raise ConversationNotFound from error

    async def complete(self, conversation_id, turn_id, snapshot, event, lease_token=None):
        conversation = await self.load(conversation_id)
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
        self.values[conversation_id] = replace(
            conversation,
            turns=tuple(turns),
            events=(*conversation.events, event),
            updated_at=event.occurred_at,
        )
        return found

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


class ConversationService:
    def __init__(self, repository: ConversationRepository, *, scope: ProjectScopeContext):
        self.repository = repository
        self.scope = scope

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
            conversation_id, self.scope.project_id, value.message[:120], now, now, (turn,), (event,)
        )
        return await self.repository.create_with_first_turn(conversation)

    async def append(self, conversation_id, turn_id, request_id, value):
        conversation = await self.load(conversation_id)
        turn, event = self._turn(conversation_id, turn_id, request_id, value)
        event = replace(event, sequence=len(conversation.events) + 1)
        return await self.repository.append_turn(conversation_id, turn, event)

    async def list(self, *, limit, cursor):
        return await self.repository.list(limit=limit, cursor=cursor)

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
        if turn.state in {"completed", "abstained", "failed", "canceled"}:
            return turn
        return await self.complete_evidence(
            conversation_id,
            turn_id,
            AnswerEvidence(
                "",
                "canceled",
                RetrievalSummary("canceled"),
                GraphContextStatus.NOT_REQUESTED,
            ),
        )

    async def retry(self, conversation_id, turn_id, new_turn_id, request_id):
        old = next(
            turn for turn in (await self.load(conversation_id)).turns if turn.turn_id == turn_id
        )
        return await self.append(conversation_id, new_turn_id, request_id, old.input_snapshot.value)

    async def complete_for_test(self, conversation_id, turn_id, *, answer, graph_status):
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
        return await self.repository.complete(conversation_id, turn_id, snapshot, event)

    async def complete_evidence(
        self,
        conversation_id: str,
        turn_id: str,
        evidence: AnswerEvidence,
        *,
        lease_token: str | None = None,
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
        return await self.repository.complete(
            conversation_id, turn_id, snapshot, event, lease_token=lease_token
        )

    async def emit(
        self,
        conversation_id: str,
        turn_id: str,
        event_type: str,
        payload: dict[str, object],
        *,
        lease_token: str | None = None,
    ) -> ConversationEvent:
        return await self.repository.append_stream_event(
            conversation_id,
            turn_id,
            ConversationEvent(uuid4().hex, 1, event_type, payload, datetime.now(timezone.utc)),
            lease_token=lease_token,
        )
