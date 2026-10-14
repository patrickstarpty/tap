"""Per-turn answer feedback use cases and a deterministic in-memory adapter."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.application.conversations import ConversationNotFound, ConversationService
from tap.modules.chat.domain.feedback import AnswerFeedback, Rating
from tap.platform.telemetry import trace_id_of

_MAX_COMMENT_LENGTH = 500


class FeedbackNotAllowed(Exception):
    """Feedback was requested for a Turn that is not in the `completed` state."""


class FeedbackRepository(Protocol):
    async def upsert(self, feedback: AnswerFeedback) -> AnswerFeedback: ...
    async def get(self, turn_id: str, actor_id: str) -> AnswerFeedback | None: ...
    async def for_turns(
        self, turn_ids: Sequence[str], actor_id: str
    ) -> dict[str, AnswerFeedback]: ...


class InMemoryFeedbackRepository:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], AnswerFeedback] = {}

    async def upsert(self, feedback: AnswerFeedback) -> AnswerFeedback:
        self.values[(feedback.turn_id, feedback.actor_id)] = feedback
        return feedback

    async def get(self, turn_id: str, actor_id: str) -> AnswerFeedback | None:
        return self.values.get((turn_id, actor_id))

    async def for_turns(self, turn_ids: Sequence[str], actor_id: str) -> dict[str, AnswerFeedback]:
        wanted = set(turn_ids)
        return {
            feedback.turn_id: feedback
            for feedback in self.values.values()
            if feedback.turn_id in wanted
            and feedback.actor_id == actor_id
            and feedback.rating is not None
        }


class FeedbackService:
    def __init__(
        self,
        repository: FeedbackRepository,
        conversations: ConversationService,
        *,
        scope: ProjectScopeContext,
    ) -> None:
        self.repository = repository
        self.conversations = conversations
        self.scope = scope

    async def _owned_turn(self, conversation_id: str, turn_id: str):
        owner = await self.conversations._owned(conversation_id, self.scope.actor_id)
        if owner.deleted_at is not None:
            raise ConversationNotFound
        conversation = await self.conversations.load(conversation_id)
        turn = next((item for item in conversation.turns if item.turn_id == turn_id), None)
        if turn is None:
            raise ConversationNotFound
        return turn

    async def submit(
        self,
        conversation_id: str,
        turn_id: str,
        rating: Rating,
        comment: str | None,
    ) -> AnswerFeedback:
        if rating != "down":
            comment = None
        if comment is not None and len(comment) > _MAX_COMMENT_LENGTH:
            raise ValueError("feedback comment must be at most 500 characters")
        turn = await self._owned_turn(conversation_id, turn_id)
        if turn.state != "completed":
            raise FeedbackNotAllowed("feedback requires a completed Turn")
        now = datetime.now(timezone.utc)
        existing = await self.repository.get(turn_id, self.scope.actor_id)
        feedback = AnswerFeedback(
            feedback_id=existing.feedback_id if existing is not None else uuid4().hex,
            project_id=self.scope.project_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            actor_id=self.scope.actor_id,
            trace_id=trace_id_of(turn.traceparent),
            rating=rating,
            comment=comment,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        return await self.repository.upsert(feedback)

    async def retract(self, conversation_id: str, turn_id: str) -> None:
        await self._owned_turn(conversation_id, turn_id)
        existing = await self.repository.get(turn_id, self.scope.actor_id)
        if existing is None or existing.rating is None:
            return
        await self.repository.upsert(
            replace(existing, rating=None, comment=None, updated_at=datetime.now(timezone.utc))
        )

    async def mine(self, turn_ids: Sequence[str]) -> dict[str, AnswerFeedback]:
        if not turn_ids:
            return {}
        return await self.repository.for_turns(turn_ids, self.scope.actor_id)
