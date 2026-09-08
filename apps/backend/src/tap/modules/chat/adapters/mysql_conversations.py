"""Atomic MySQL Conversation ledger with immutable evidence and Project events."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Column,
    ForeignKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.contracts.events import ProjectEventEnvelope
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.adapters.mysql import chat_event, chat_turn
from tap.modules.chat.application.conversations import (
    ConversationConflict,
    ConversationNotFound,
    InvalidConversationCursor,
)
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    AnswerEvidenceSnapshot,
    CitationEvidence,
    Conversation,
    ConversationEvent,
    ConversationTurn,
    GraphContextStatus,
    RetrievalSummary,
    TurnInput,
    TurnInputSnapshot,
)
from tap.modules.governance.adapters.mysql_audit import MysqlProjectAudit
from tap.modules.governance.domain.audit import (
    AuditAction,
    AuditOutcome,
    AuditResource,
    SafeAuditMetadata,
)
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import metadata
from tap.platform.messaging.mysql_outbox import scoped_outbox_id, write_project_event


def _scoped(name, *columns, uniques=(), parents=()):
    return Table(
        name,
        metadata,
        *columns,
        Column("enterprise_id", String(128), nullable=False),
        Column("project_id", String(128), nullable=False),
        Column("actor_id", String(128), nullable=False),
        Column("identity_mode", String(16), nullable=False),
        Column("identity_origin", String(16), nullable=False),
        UniqueConstraint("project_id", columns[0].name, name=f"uq_{name}_project_pk"),
        *(
            UniqueConstraint("project_id", *fields, name=constraint)
            for fields, constraint in uniques
        ),
        *(
            ForeignKeyConstraint(
                ["project_id", source],
                [f"{target}.project_id", f"{target}.{destination}"],
                name=constraint,
            )
            for source, target, destination, constraint in parents
        ),
        ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name=f"fk_{name}_scope_project",
        ),
        ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name=f"fk_{name}_scope_actor",
        ),
    )


conversation = Table(
    "conversation",
    metadata,
    Column("conversation_id", String(64), primary_key=True),
    Column("title", String(120), nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
    Column("enterprise_id", String(128), nullable=False),
    Column("project_id", String(128), primary_key=True),
    Column("actor_id", String(128), nullable=False),
    Column("identity_mode", String(16), nullable=False),
    Column("identity_origin", String(16), nullable=False),
    UniqueConstraint("project_id", "conversation_id", name="uq_conversation_project_pk"),
    ForeignKeyConstraint(
        ["enterprise_id", "project_id"],
        ["project.enterprise_id", "project.project_id"],
        name="fk_conversation_scope_project",
    ),
    ForeignKeyConstraint(
        ["enterprise_id", "actor_id"],
        ["actor_principal.enterprise_id", "actor_principal.actor_id"],
        name="fk_conversation_scope_actor",
    ),
)
turn_input_snapshot = _scoped(
    "turn_input_snapshot",
    Column("snapshot_id", String(64), primary_key=True),
    Column("turn_id", String(64), nullable=False),
    Column("snapshot_digest", String(71), nullable=False),
    Column("snapshot", JSON, nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    uniques=((("turn_id",), "uq_turn_input_snapshot_turn"),),
    parents=(("turn_id", "chat_turn", "turn_id", "fk_turn_input_snapshot_turn"),),
)
turn_answer_evidence_snapshot = _scoped(
    "turn_answer_evidence_snapshot",
    Column("snapshot_id", String(64), primary_key=True),
    Column("turn_id", String(64), nullable=False),
    Column("input_snapshot_digest", String(71), nullable=False),
    Column("answer_digest", String(71), nullable=False),
    Column("snapshot_digest", String(71), nullable=False),
    Column("snapshot", JSON, nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    uniques=((("turn_id",), "uq_turn_answer_evidence_snapshot_turn"),),
    parents=(("turn_id", "chat_turn", "turn_id", "fk_turn_answer_evidence_snapshot_turn"),),
)
turn_artifact_link = _scoped(
    "turn_artifact_link",
    Column("link_id", String(64), primary_key=True),
    Column("turn_id", String(64), nullable=False),
    Column("artifact_kind", String(32), nullable=False),
    Column("artifact_id", String(128), nullable=False),
    Column("artifact_digest", String(71), nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    uniques=((("turn_id", "artifact_kind", "artifact_id"), "uq_turn_artifact_link_identity"),),
    parents=(("turn_id", "chat_turn", "turn_id", "fk_turn_artifact_link_turn"),),
)


def _naive(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _input_json(value):
    return {
        **asdict(value),
        "source_revision_ids": list(value.source_revision_ids),
        "document_revision_ids": list(value.document_revision_ids),
        "skill_revision_ids": list(value.skill_revision_ids),
        "skill_revision_digests": list(value.skill_revision_digests),
    }


class MysqlConversationRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext):
        self.sessions = sessions
        self.scope = require_project_scope(scope)

    async def _event(self, session, event, conversation_id, turn_id):
        await session.execute(
            insert(chat_event).values(
                **scope_values(self.scope),
                event_id=event.event_id,
                turn_id=turn_id,
                sequence=event.sequence,
                event_type=event.event_type,
                payload=dict(event.payload),
                schema_version=1,
                occurred_at=_naive(event.occurred_at),
            )
        )
        envelope = ProjectEventEnvelope(
            event_id=scoped_outbox_id(self.scope, kind="conversation", identity=event.event_id),
            event_type=event.event_type,
            schema_version=1,
            occurred_at=event.occurred_at,
            scope_kind="PROJECT",
            enterprise_id=self.scope.enterprise_id,
            project_id=self.scope.project_id,
            actor_id=self.scope.actor_id,
            identity_mode=self.scope.identity_mode.value,
            aggregate_type="Turn",
            aggregate_id=turn_id,
            aggregate_version=event.sequence,
            correlation_id=conversation_id,
            causation_id=None,
            idempotency_key=f"conversation:{event.event_id}",
            payload=dict(event.payload),
        )
        await write_project_event(session, scope=self.scope, envelope=envelope)
        digest = (
            event.payload["inputSnapshotDigest"]
            if event.event_type == "conversation.turn.requested"
            else event.payload["answerEvidenceSnapshotDigest"]
        )
        await MysqlProjectAudit(await session.connection(), scope=self.scope).append(
            self.scope,
            AuditAction.CONVERSATION_TURN_REQUESTED
            if event.event_type == "conversation.turn.requested"
            else AuditAction.CONVERSATION_TURN_COMPLETED,
            AuditResource.CONVERSATION_TURN,
            AuditOutcome.COMPLETED,
            SafeAuditMetadata({"content_digest": str(digest).removeprefix("sha256:")}),
            correlation_id=conversation_id,
            idempotency_key=f"audit:{event.event_id}",
            resource_id=turn_id,
        )

    async def _insert_turn(self, session, conversation_id, turn, event):
        if (
            turn.input_snapshot.project_id != self.scope.project_id
            or turn.input_snapshot.turn_id != turn.turn_id
            or dict(event.payload)
            != {
                "conversationId": conversation_id,
                "turnId": turn.turn_id,
                "inputSnapshotDigest": turn.input_snapshot.digest,
            }
        ):
            raise ValueError("Turn input snapshot/event binding differs from trusted scope")
        now = _naive(turn.input_snapshot.created_at)
        await session.execute(
            insert(chat_turn).values(
                **scope_values(self.scope),
                turn_id=turn.turn_id,
                chat_id=conversation_id,
                client_request_id=turn.client_request_id,
                message=turn.input_snapshot.value.message,
                state="queued",
                processing_attempt=turn.attempt,
                last_sequence=event.sequence,
                created_at=now,
            )
        )
        await session.execute(
            insert(turn_input_snapshot).values(
                **scope_values(self.scope),
                snapshot_id=turn.input_snapshot.snapshot_id,
                turn_id=turn.turn_id,
                snapshot_digest=turn.input_snapshot.digest,
                snapshot=_input_json(turn.input_snapshot.value),
                created_at=now,
            )
        )
        await self._event(session, event, conversation_id, turn.turn_id)

    async def _next_event(self, session, conversation_id, event):
        latest = await session.scalar(
            select(chat_event.c.sequence)
            .join(chat_turn, chat_turn.c.turn_id == chat_event.c.turn_id)
            .where(
                *scope_predicates(chat_event, self.scope),
                chat_turn.c.chat_id == conversation_id,
            )
            .order_by(chat_event.c.sequence.desc())
            .limit(1)
        )
        return replace(event, sequence=(0 if latest is None else latest) + 1)

    async def create_with_first_turn(self, value):
        try:
            async with self.sessions() as session, session.begin():
                await session.execute(
                    insert(conversation).values(
                        **scope_values(self.scope),
                        conversation_id=value.conversation_id,
                        title=value.title,
                        created_at=_naive(value.created_at),
                        updated_at=_naive(value.updated_at),
                    )
                )
                await self._insert_turn(
                    session, value.conversation_id, value.turns[0], value.events[0]
                )
        except IntegrityError as error:
            try:
                existing = await self.load(value.conversation_id)
            except ConversationNotFound:
                raise error
            original = existing.turns[0]
            if (
                original.client_request_id != value.turns[0].client_request_id
                or original.input_snapshot.value != value.turns[0].input_snapshot.value
            ):
                raise ConversationConflict("idempotency-conflict") from error
            return original
        return value.turns[0]

    async def append_turn(self, conversation_id, turn, event):
        async with self.sessions() as session, session.begin():
            parent = (
                await session.execute(
                    select(conversation.c.conversation_id)
                    .where(
                        *scope_predicates(conversation, self.scope),
                        conversation.c.conversation_id == conversation_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if parent is None:
                raise ConversationNotFound
            row = (
                (
                    await session.execute(
                        select(chat_turn)
                        .where(
                            *scope_predicates(chat_turn, self.scope),
                            chat_turn.c.chat_id == conversation_id,
                            chat_turn.c.client_request_id == turn.client_request_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is not None:
                if row["message"] != turn.input_snapshot.value.message:
                    raise ConversationConflict("idempotency-conflict")
                return await self._load_turn(session, row)
            event = await self._next_event(session, conversation_id, event)
            await self._insert_turn(session, conversation_id, turn, event)
            await session.execute(
                update(conversation)
                .where(
                    *scope_predicates(conversation, self.scope),
                    conversation.c.conversation_id == conversation_id,
                )
                .values(updated_at=_naive(event.occurred_at))
            )
        return turn

    async def _load_turn(self, session, row):
        snapshot = (
            (
                await session.execute(
                    select(turn_input_snapshot).where(
                        *scope_predicates(turn_input_snapshot, self.scope),
                        turn_input_snapshot.c.turn_id == row["turn_id"],
                    )
                )
            )
            .mappings()
            .one()
        )
        raw = snapshot["snapshot"]
        value = TurnInput(
            message=raw["message"],
            actor_id=raw["actor_id"],
            identity_mode=raw["identity_mode"],
            model_alias=raw["model_alias"],
            source_revision_ids=tuple(raw["source_revision_ids"]),
            document_revision_ids=tuple(raw["document_revision_ids"]),
            agent_revision_id=raw["agent_revision_id"],
            agent_revision_digest=raw["agent_revision_digest"],
            skill_revision_ids=tuple(raw["skill_revision_ids"]),
            skill_revision_digests=tuple(raw["skill_revision_digests"]),
            retrieval_policy_digest=raw["retrieval_policy_digest"],
        )
        input_value = TurnInputSnapshot(
            snapshot["snapshot_id"],
            self.scope.project_id,
            row["turn_id"],
            value,
            snapshot["snapshot_digest"],
            snapshot["created_at"].replace(tzinfo=timezone.utc),
        )
        answer_row = (
            (
                await session.execute(
                    select(turn_answer_evidence_snapshot).where(
                        *scope_predicates(turn_answer_evidence_snapshot, self.scope),
                        turn_answer_evidence_snapshot.c.turn_id == row["turn_id"],
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        answer = None
        if answer_row:
            raw = answer_row["snapshot"]
            rs = raw["retrieval_summary"]
            evidence = AnswerEvidence(
                raw["answer"],
                raw["outcome"],
                RetrievalSummary(**rs),
                GraphContextStatus(raw["graph_context_status"]),
                raw["graph_snapshot_id"],
                tuple(CitationEvidence(**c) for c in raw["citations"]),
                tuple(raw["diagnostics"]),
            )
            answer = AnswerEvidenceSnapshot(
                answer_row["snapshot_id"],
                self.scope.project_id,
                row["turn_id"],
                answer_row["input_snapshot_digest"],
                evidence,
                answer_row["answer_digest"],
                answer_row["snapshot_digest"],
                answer_row["created_at"].replace(tzinfo=timezone.utc),
            )
        return ConversationTurn(
            row["turn_id"],
            row["client_request_id"],
            row["processing_attempt"],
            row["state"],
            input_value,
            answer,
        )

    async def load(self, conversation_id):
        async with self.sessions() as session:
            parent = (
                (
                    await session.execute(
                        select(conversation).where(
                            *scope_predicates(conversation, self.scope),
                            conversation.c.conversation_id == conversation_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if parent is None:
                raise ConversationNotFound
            rows = (
                (
                    await session.execute(
                        select(chat_turn)
                        .where(
                            *scope_predicates(chat_turn, self.scope),
                            chat_turn.c.chat_id == conversation_id,
                        )
                        .order_by(chat_turn.c.created_at, chat_turn.c.turn_id)
                    )
                )
                .mappings()
                .all()
            )
            turns = tuple([await self._load_turn(session, row) for row in rows])
            erows = (
                (
                    await session.execute(
                        select(chat_event)
                        .join(chat_turn, chat_turn.c.turn_id == chat_event.c.turn_id)
                        .where(
                            *scope_predicates(chat_event, self.scope),
                            chat_turn.c.chat_id == conversation_id,
                        )
                        .order_by(chat_event.c.sequence)
                    )
                )
                .mappings()
                .all()
            )
        events = tuple(
            ConversationEvent(
                r["event_id"],
                r["sequence"],
                r["event_type"],
                r["payload"],
                r["occurred_at"].replace(tzinfo=timezone.utc),
            )
            for r in erows
        )
        return Conversation(
            parent["conversation_id"],
            self.scope.project_id,
            parent["title"],
            parent["created_at"].replace(tzinfo=timezone.utc),
            parent["updated_at"].replace(tzinfo=timezone.utc),
            turns,
            events,
        )

    async def list(self, *, limit, cursor):
        async with self.sessions() as session:
            query = (
                select(conversation)
                .where(*scope_predicates(conversation, self.scope))
                .order_by(conversation.c.updated_at.desc(), conversation.c.conversation_id.desc())
                .limit(limit + 1)
            )
            if cursor:
                try:
                    timestamp_value, identity = cursor.rsplit("|", 1)
                    timestamp = datetime.fromisoformat(timestamp_value)
                except (ValueError, TypeError) as error:
                    raise InvalidConversationCursor("invalid conversation cursor") from error
                query = query.where(
                    or_(
                        conversation.c.updated_at < _naive(timestamp),
                        (
                            (conversation.c.updated_at == _naive(timestamp))
                            & (conversation.c.conversation_id < identity)
                        ),
                    )
                )
            rows = (await session.execute(query)).mappings().all()
        values = tuple(
            Conversation(
                r["conversation_id"],
                self.scope.project_id,
                r["title"],
                r["created_at"].replace(tzinfo=timezone.utc),
                r["updated_at"].replace(tzinfo=timezone.utc),
            )
            for r in rows[:limit]
        )
        return values, (
            f"{values[-1].updated_at.isoformat()}|{values[-1].conversation_id}"
            if len(rows) > limit
            else None
        )

    async def complete(self, conversation_id, turn_id, snapshot, event):
        async with self.sessions() as session, session.begin():
            parent = await session.scalar(
                select(conversation.c.conversation_id)
                .where(
                    *scope_predicates(conversation, self.scope),
                    conversation.c.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            if parent is None:
                raise ConversationNotFound
            row = (
                (
                    await session.execute(
                        select(chat_turn)
                        .where(
                            *scope_predicates(chat_turn, self.scope),
                            chat_turn.c.turn_id == turn_id,
                            chat_turn.c.chat_id == conversation_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ConversationNotFound
            if row["state"] in {"completed", "abstained", "failed", "canceled"}:
                return await self._load_turn(session, row)
            input_digest = await session.scalar(
                select(turn_input_snapshot.c.snapshot_digest).where(
                    *scope_predicates(turn_input_snapshot, self.scope),
                    turn_input_snapshot.c.turn_id == turn_id,
                )
            )
            if (
                snapshot.project_id != self.scope.project_id
                or snapshot.turn_id != turn_id
                or snapshot.input_snapshot_digest != input_digest
                or dict(event.payload)
                != {
                    "turnId": turn_id,
                    "answerEvidenceSnapshotId": snapshot.snapshot_id,
                    "answerEvidenceSnapshotDigest": snapshot.digest,
                    "outcome": snapshot.value.outcome,
                }
            ):
                raise ValueError(
                    "Answer evidence snapshot/event binding differs from accepted Turn"
                )
            event = await self._next_event(session, conversation_id, event)
            raw = asdict(snapshot.value)
            raw["graph_context_status"] = snapshot.value.graph_context_status.value
            await session.execute(
                insert(turn_answer_evidence_snapshot).values(
                    **scope_values(self.scope),
                    snapshot_id=snapshot.snapshot_id,
                    turn_id=turn_id,
                    input_snapshot_digest=snapshot.input_snapshot_digest,
                    answer_digest=snapshot.answer_digest,
                    snapshot_digest=snapshot.digest,
                    snapshot=raw,
                    created_at=_naive(snapshot.created_at),
                )
            )
            await self._event(session, event, conversation_id, turn_id)
            await session.execute(
                update(chat_turn)
                .where(*scope_predicates(chat_turn, self.scope), chat_turn.c.turn_id == turn_id)
                .values(state=snapshot.value.outcome, last_sequence=event.sequence)
            )
        persisted = await self.load(conversation_id)
        return next(turn for turn in persisted.turns if turn.turn_id == turn_id)

    async def claim_queued(self, *, limit: int) -> tuple[tuple[str, ConversationTurn], ...]:
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        claimed: list[tuple[str, ConversationTurn]] = []
        async with self.sessions() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        select(chat_turn)
                        .where(
                            *scope_predicates(chat_turn, self.scope),
                            chat_turn.c.state == "queued",
                        )
                        .order_by(chat_turn.c.created_at, chat_turn.c.turn_id)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
            for candidate in rows:
                await session.execute(
                    select(conversation.c.conversation_id)
                    .where(
                        *scope_predicates(conversation, self.scope),
                        conversation.c.conversation_id == candidate["chat_id"],
                    )
                    .with_for_update()
                )
                row = (
                    (
                        await session.execute(
                            select(chat_turn)
                            .where(
                                *scope_predicates(chat_turn, self.scope),
                                chat_turn.c.turn_id == candidate["turn_id"],
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None or row["state"] != "queued":
                    continue
                event = await self._next_event(
                    session,
                    row["chat_id"],
                    ConversationEvent(
                        uuid4().hex,
                        1,
                        "turn.started",
                        {"state": "running"},
                        datetime.now(timezone.utc),
                    ),
                )
                await session.execute(
                    insert(chat_event).values(
                        **scope_values(self.scope),
                        event_id=event.event_id,
                        turn_id=row["turn_id"],
                        sequence=event.sequence,
                        event_type=event.event_type,
                        payload=dict(event.payload),
                        schema_version=1,
                        occurred_at=_naive(event.occurred_at),
                    )
                )
                await session.execute(
                    update(chat_turn)
                    .where(
                        *scope_predicates(chat_turn, self.scope),
                        chat_turn.c.turn_id == row["turn_id"],
                        chat_turn.c.state == "queued",
                    )
                    .values(state="running", last_sequence=event.sequence)
                )
                mutable = dict(row)
                mutable["state"] = "running"
                claimed.append((row["chat_id"], await self._load_turn(session, mutable)))
        return tuple(claimed)

    async def append_stream_event(self, conversation_id, turn_id, event):
        if event.event_type in {
            "conversation.turn.requested",
            "conversation.turn.completed",
        }:
            raise ValueError("lifecycle events require their atomic snapshot transaction")
        async with self.sessions() as session, session.begin():
            await session.execute(
                select(conversation.c.conversation_id)
                .where(
                    *scope_predicates(conversation, self.scope),
                    conversation.c.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            row = (
                (
                    await session.execute(
                        select(chat_turn)
                        .where(
                            *scope_predicates(chat_turn, self.scope),
                            chat_turn.c.chat_id == conversation_id,
                            chat_turn.c.turn_id == turn_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ConversationNotFound
            if row["state"] in {"completed", "abstained", "failed", "canceled"}:
                raise ConversationConflict("terminal Turn cannot accept stream events")
            event = await self._next_event(session, conversation_id, event)
            await session.execute(
                insert(chat_event).values(
                    **scope_values(self.scope),
                    event_id=event.event_id,
                    turn_id=turn_id,
                    sequence=event.sequence,
                    event_type=event.event_type,
                    payload=dict(event.payload),
                    schema_version=1,
                    occurred_at=_naive(event.occurred_at),
                )
            )
            await session.execute(
                update(chat_turn)
                .where(
                    *scope_predicates(chat_turn, self.scope),
                    chat_turn.c.turn_id == turn_id,
                )
                .values(last_sequence=event.sequence)
            )
        return event
