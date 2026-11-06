"""Chat-owned persisted workflow link and cancellation guard."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.adapters.mysql import chat_turn
from tap.modules.chat.adapters.mysql_conversations import (
    MysqlConversationRepository,
    conversation,
    turn_answer_evidence_snapshot,
    turn_input_snapshot,
)
from tap.modules.chat.application.conversations import ConversationConflict, ConversationNotFound
from tap.modules.chat.application.ports import EventWakeups
from tap.modules.chat.application.workflow_bridge import CommittedWorkflowFact
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    AnswerEvidenceSnapshot,
    ConversationEvent,
    GraphContextStatus,
    RetrievalSummary,
)
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values


class MysqlWorkflowChatBridge:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        scope: ProjectScopeContext,
        wakeups: EventWakeups | None = None,
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)
        self._repository = MysqlConversationRepository(sessions, scope=scope)
        self._wakeups = wakeups

    async def _turn(self, session: AsyncSession, conversation_id: str, turn_id: str, *, lock: bool):
        query = select(chat_turn).where(
            *scope_predicates(chat_turn, self._scope),
            chat_turn.c.chat_id == conversation_id,
            chat_turn.c.turn_id == turn_id,
            chat_turn.c.execution_owner == "workflow",
        )
        if lock:
            query = query.with_for_update()
        return (await session.execute(query)).mappings().one_or_none()

    async def link_task(self, conversation_id: str, turn_id: str, task_id: str) -> bool:
        async with self._sessions.begin() as session:
            row = await self._turn(session, conversation_id, turn_id, lock=True)
            if row is None:
                raise ConversationNotFound
            if row["workflow_task_id"] not in (None, task_id):
                raise ConversationConflict("workflow task link conflict")
            if row["workflow_task_id"] is None:
                await session.execute(
                    update(chat_turn)
                    .where(chat_turn.c.turn_id == turn_id)
                    .values(workflow_task_id=task_id)
                )
            return row["cancel_requested_at"] is not None

    async def authorize_dispatch(self, conversation_id: str, turn_id: str, task_id: str) -> bool:
        async with self._sessions() as session:
            row = await self._turn(session, conversation_id, turn_id, lock=False)
            return bool(
                row is not None
                and row["workflow_task_id"] == task_id
                and row["cancel_requested_at"] is None
                and row["state"] in ("queued", "running")
            )

    async def is_workflow_turn(self, conversation_id: str, turn_id: str) -> bool:
        async with self._sessions() as session:
            return await self._turn(session, conversation_id, turn_id, lock=False) is not None

    async def request_cancel(self, conversation_id: str, turn_id: str) -> str | None:
        async with self._sessions.begin() as session:
            row = await self._turn(session, conversation_id, turn_id, lock=True)
            if row is None:
                raise ConversationNotFound
            if row["state"] in ("completed", "abstained", "failed", "canceled"):
                return None
            if row["cancel_requested_at"] is None:
                await session.execute(
                    update(chat_turn)
                    .where(chat_turn.c.turn_id == turn_id)
                    .values(cancel_requested_at=datetime.now(UTC).replace(tzinfo=None))
                )
            return row["workflow_task_id"]

    async def repair_candidates(self, limit: int) -> tuple[tuple[str, str, str | None, bool], ...]:
        if limit < 1:
            raise ValueError("repair limit must be positive")
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        chat_turn.c.chat_id,
                        chat_turn.c.turn_id,
                        chat_turn.c.workflow_task_id,
                        chat_turn.c.cancel_requested_at,
                    )
                    .where(
                        *scope_predicates(chat_turn, self._scope),
                        chat_turn.c.execution_owner == "workflow",
                        (
                            chat_turn.c.workflow_task_id.is_(None)
                            | chat_turn.c.cancel_requested_at.is_not(None)
                        ),
                    )
                    .order_by(chat_turn.c.created_at, chat_turn.c.turn_id)
                    .limit(limit)
                )
            ).all()
        return tuple((row[0], row[1], row[2], row[3] is not None) for row in rows)

    async def client_request_id(self, conversation_id: str, turn_id: str, task_id: str) -> str:
        async with self._sessions() as session:
            row = await self._turn(session, conversation_id, turn_id, lock=False)
            if row is None or row["workflow_task_id"] != task_id:
                raise ConversationConflict("workflow Turn link changed")
            return str(row["client_request_id"])

    async def claim_projection(
        self, conversation_id: str, turn_id: str, task_id: str
    ) -> tuple[str, int] | None:
        async with self._sessions.begin() as session:
            row = await self._turn(session, conversation_id, turn_id, lock=True)
            if row is None or row["workflow_task_id"] != task_id:
                raise ConversationConflict("workflow Turn link changed")
            if row["state"] in ("completed", "abstained", "failed", "canceled"):
                return None
            now = datetime.now(UTC).replace(tzinfo=None)
            if (
                row["processing_lease_token"]
                and row["processing_lease_expires_at"]
                and row["processing_lease_expires_at"] > now
            ):
                return None
            token = uuid4().hex
            await session.execute(
                update(chat_turn)
                .where(chat_turn.c.turn_id == turn_id)
                .values(
                    processing_lease_token=token,
                    processing_lease_expires_at=now + timedelta(minutes=5),
                    processing_attempt=row["processing_attempt"] + 1,
                )
            )
            return token, int(row["workflow_projection_sequence"])

    async def release_projection(self, conversation_id: str, turn_id: str, token: str) -> None:
        async with self._sessions.begin() as session:
            row = await self._turn(session, conversation_id, turn_id, lock=True)
            if row is not None and row["processing_lease_token"] == token:
                await session.execute(
                    update(chat_turn)
                    .where(chat_turn.c.turn_id == turn_id)
                    .values(
                        processing_lease_token=None,
                        processing_lease_expires_at=None,
                    )
                )

    @staticmethod
    def _id(*parts: str) -> str:
        return hashlib.sha256(":\x00".join(parts).encode()).hexdigest()

    async def project_fact(
        self, conversation_id: str, turn_id: str, token: str, fact: CommittedWorkflowFact
    ) -> None:
        notify_sequence: int | None = None
        async with self._sessions.begin() as session:
            parent = await session.scalar(
                select(conversation.c.conversation_id)
                .where(
                    *scope_predicates(conversation, self._scope),
                    conversation.c.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            if parent is None:
                raise ConversationNotFound
            row = await self._turn(session, conversation_id, turn_id, lock=True)
            if row is None or row["workflow_task_id"] != fact.task_id:
                raise ConversationConflict("workflow Turn link changed")
            now = datetime.now(UTC).replace(tzinfo=None)
            if (
                row["processing_lease_token"] != token
                or row["processing_lease_expires_at"] is None
                or row["processing_lease_expires_at"] <= now
            ):
                raise ConversationConflict("workflow projection lease lost")
            cursor = int(row["workflow_projection_sequence"])
            if fact.sequence <= cursor:
                return
            if fact.sequence != cursor + 1:
                raise ConversationConflict("workflow projection sequence gap")
            if row["state"] in ("completed", "abstained", "failed", "canceled"):
                raise ConversationConflict("terminal workflow Turn cannot accept facts")
            changes: dict[str, object] = {"workflow_projection_sequence": fact.sequence}
            occurred_at = fact.occurred_at.astimezone(UTC)
            if fact.kind == "task.claimed" and row["state"] == "queued":
                event = ConversationEvent(
                    self._id(turn_id, str(fact.sequence), "started"),
                    1,
                    "turn.started",
                    {"state": "running"},
                    occurred_at,
                    turn_id,
                )
                event = await self._repository._next_event(session, conversation_id, event)
                await self._repository._stream_event(session, event, turn_id)
                changes.update(state="running", last_sequence=event.sequence)
            elif fact.kind == "task.awaiting_input":
                question = str(fact.payload.get("question") or "")
                event = ConversationEvent(
                    self._id(turn_id, str(fact.sequence), "clarification"),
                    1,
                    "task.step",
                    {
                        "stepId": f"{turn_id}:workflow:clarification:{fact.sequence}",
                        "tool": "workflow.route",
                        "status": "completed",
                        "summary": question[:200],
                        "durationMs": 0,
                        "toolRound": 1,
                    },
                    occurred_at,
                    turn_id,
                )
                event = await self._repository._next_event(session, conversation_id, event)
                await self._repository._stream_event(session, event, turn_id)
                changes.update(state="running", last_sequence=event.sequence)
            elif fact.kind in (
                "task.completed",
                "task.canceled",
                "task.failed",
                "task.continuation_ack",
            ):
                outcome = (
                    "completed"
                    if fact.kind in ("task.completed", "task.continuation_ack")
                    else fact.kind.removeprefix("task.")
                )
                answer = str(fact.payload.get("answer") or "") if outcome == "completed" else ""
                evidence = AnswerEvidence(
                    answer,
                    outcome,
                    RetrievalSummary(outcome),
                    GraphContextStatus.NOT_SELECTED,
                )
                input_digest = await session.scalar(
                    select(turn_input_snapshot.c.snapshot_digest).where(
                        *scope_predicates(turn_input_snapshot, self._scope),
                        turn_input_snapshot.c.turn_id == turn_id,
                    )
                )
                if input_digest is None:
                    raise ConversationConflict("workflow Turn input is unavailable")
                snapshot = AnswerEvidenceSnapshot.create(
                    snapshot_id=self._id(turn_id, str(fact.sequence), "answer"),
                    project_id=self._scope.project_id,
                    turn_id=turn_id,
                    input_digest=input_digest,
                    value=evidence,
                    now=occurred_at,
                )
                await session.execute(
                    insert(turn_answer_evidence_snapshot).values(
                        **scope_values(self._scope),
                        snapshot_id=snapshot.snapshot_id,
                        turn_id=turn_id,
                        input_snapshot_digest=snapshot.input_snapshot_digest,
                        answer_digest=snapshot.answer_digest,
                        snapshot_digest=snapshot.digest,
                        snapshot=asdict(snapshot.value),
                        created_at=now,
                    )
                )
                stream_type = "turn.completed" if outcome == "completed" else f"turn.{outcome}"
                stream_payload: dict[str, object]
                if outcome == "completed":
                    stream_payload = {"state": "completed"}
                elif outcome == "canceled":
                    stream_payload = {"partialAnswerRetained": False}
                else:
                    stream_payload = {
                        "problem": {
                            "type": "about:blank",
                            "title": "Workflow failed",
                            "status": 500,
                        }
                    }
                stream = ConversationEvent(
                    self._id(turn_id, str(fact.sequence), "stream"),
                    1,
                    stream_type,
                    stream_payload,
                    occurred_at,
                    turn_id,
                )
                stream = await self._repository._next_event(session, conversation_id, stream)
                await self._repository._stream_event(session, stream, turn_id)
                lifecycle = ConversationEvent(
                    self._id(turn_id, str(fact.sequence), "lifecycle"),
                    1,
                    "conversation.turn.completed",
                    {
                        "turnId": turn_id,
                        "answerEvidenceSnapshotId": snapshot.snapshot_id,
                        "answerEvidenceSnapshotDigest": snapshot.digest,
                        "outcome": outcome,
                    },
                    occurred_at,
                    turn_id,
                )
                lifecycle = await self._repository._next_event(session, conversation_id, lifecycle)
                await self._repository._event(session, lifecycle, conversation_id, turn_id)
                changes.update(
                    state=outcome,
                    last_sequence=lifecycle.sequence,
                    processing_lease_token=None,
                    processing_lease_expires_at=None,
                )
            await session.execute(
                update(chat_turn).where(chat_turn.c.turn_id == turn_id).values(**changes)
            )
            if "last_sequence" in changes:
                notify_sequence = int(str(changes["last_sequence"]))
        if self._wakeups is not None and notify_sequence is not None:
            try:
                await self._wakeups.notify(conversation_id, notify_sequence)
            except Exception:
                # MySQL projection is authoritative; SSE also polls on a bound.
                pass

    async def projection_candidates(self, limit: int) -> tuple[tuple[str, str, str], ...]:
        if limit < 1:
            raise ValueError("projection scan limit must be positive")
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        chat_turn.c.chat_id,
                        chat_turn.c.turn_id,
                        chat_turn.c.workflow_task_id,
                    )
                    .where(
                        *scope_predicates(chat_turn, self._scope),
                        chat_turn.c.execution_owner == "workflow",
                        chat_turn.c.workflow_task_id.is_not(None),
                        chat_turn.c.state.in_(("queued", "running")),
                    )
                    .order_by(chat_turn.c.created_at)
                    .limit(limit)
                )
            ).all()
        return tuple((str(row[0]), str(row[1]), str(row[2])) for row in rows)
