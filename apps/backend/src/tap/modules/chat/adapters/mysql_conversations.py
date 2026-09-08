"""Atomic MySQL Conversation ledger with immutable evidence and Project events."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
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
    text,
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
    FrozenResource,
    GraphContextStatus,
    RetrievalSummary,
    TurnInput,
    TurnInputSnapshot,
    citation_evidence_digest,
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
        "resolved_resources": [asdict(item) for item in value.resolved_resources],
        "skill_revision_ids": list(value.skill_revision_ids),
        "skill_revision_digests": list(value.skill_revision_digests),
        "agent_tool_allowlist": list(value.agent_tool_allowlist),
        "skill_instruction_templates": list(value.skill_instruction_templates),
        "skill_instruction_template_digests": list(value.skill_instruction_template_digests),
    }


class MysqlConversationRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext):
        self.sessions = sessions
        self.scope = require_project_scope(scope)

    async def resolve_citations(
        self, trace_id: str, citation_ids: tuple[str, ...]
    ) -> tuple[CitationEvidence, ...]:
        if not citation_ids or len(set(citation_ids)) != len(citation_ids):
            raise ValueError("citation identities must be nonempty and unique")
        async with self.sessions() as session:
            placeholders = ",".join(f":citation_{index}" for index in range(len(citation_ids)))
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT citation_id,source_id,trace_id,document_id,revision_id,"
                            "chunk_id,source_content_hash,chunk_content_hash,anchor_json "
                            "FROM knowledge_citation_snapshot WHERE enterprise_id=:enterprise_id "
                            "AND project_id=:project_id AND trace_id=:trace_id "
                            f"AND citation_id IN ({placeholders})"
                        ),
                        {
                            "enterprise_id": self.scope.enterprise_id,
                            "project_id": self.scope.project_id,
                            "trace_id": trace_id,
                            **{
                                f"citation_{index}": identity
                                for index, identity in enumerate(citation_ids)
                            },
                        },
                    )
                )
                .mappings()
                .all()
            )
        if {row["citation_id"] for row in rows} != set(citation_ids):
            raise ValueError("citation snapshot is not a trusted persisted fact")
        by_id = {
            row["citation_id"]: CitationEvidence(
                row["citation_id"],
                citation_evidence_digest(
                    citation_id=row["citation_id"],
                    trace_id=row["trace_id"],
                    source_id=row["source_id"],
                    document_id=row["document_id"],
                    revision_id=row["revision_id"],
                    chunk_id=row["chunk_id"],
                    source_content_hash=row["source_content_hash"],
                    chunk_content_hash=row["chunk_content_hash"],
                    anchor=row["anchor_json"],
                ),
            )
            for row in rows
        }
        return tuple(by_id[identity] for identity in citation_ids)

    async def _event(self, session, event, conversation_id, turn_id):
        await session.execute(
            insert(chat_event).values(
                **scope_values(self.scope),
                event_id=event.event_id,
                turn_id=turn_id,
                sequence=event.sequence,
                stream_sequence=event.sequence,
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
            select(chat_event.c.stream_sequence)
            .join(chat_turn, chat_turn.c.turn_id == chat_event.c.turn_id)
            .where(
                *scope_predicates(chat_event, self.scope),
                chat_turn.c.chat_id == conversation_id,
            )
            .order_by(chat_event.c.stream_sequence.desc())
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
                existing = await self._load_turn(session, row)
                if existing.input_snapshot.value != turn.input_snapshot.value:
                    raise ConversationConflict("idempotency-conflict")
                return existing
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
            .one_or_none()
        )
        if snapshot is None:
            value = TurnInput(
                message=row["message"],
                actor_id=row["actor_id"],
                identity_mode=row["identity_mode"],
                model_alias="tapper-chat",
            )
            input_value = TurnInputSnapshot.create(
                snapshot_id="legacy-"
                + hashlib.sha256(f"{self.scope.project_id}/{row['turn_id']}".encode()).hexdigest()[
                    :32
                ],
                project_id=self.scope.project_id,
                turn_id=row["turn_id"],
                value=value,
                now=row["created_at"].replace(tzinfo=timezone.utc),
            )
        else:
            raw = snapshot["snapshot"]
            value = TurnInput(
                message=raw["message"],
                actor_id=raw["actor_id"],
                identity_mode=raw["identity_mode"],
                model_alias=raw["model_alias"],
                source_revision_ids=tuple(raw["source_revision_ids"]),
                document_revision_ids=tuple(raw["document_revision_ids"]),
                resolved_resources=tuple(
                    FrozenResource(**item) for item in raw.get("resolved_resources", [])
                ),
                agent_revision_id=raw["agent_revision_id"],
                agent_revision_digest=raw["agent_revision_digest"],
                skill_revision_ids=tuple(raw["skill_revision_ids"]),
                skill_revision_digests=tuple(raw["skill_revision_digests"]),
                agent_system_instruction=raw.get("agent_system_instruction"),
                agent_system_instruction_digest=raw.get("agent_system_instruction_digest"),
                agent_tool_allowlist=tuple(raw.get("agent_tool_allowlist", [])),
                agent_output_schema_json=raw.get("agent_output_schema_json"),
                agent_output_schema_digest=raw.get("agent_output_schema_digest"),
                skill_instruction_templates=tuple(raw.get("skill_instruction_templates", [])),
                skill_instruction_template_digests=tuple(
                    raw.get("skill_instruction_template_digests", [])
                ),
                acl_digest=raw.get("acl_digest", "sha256:" + "0" * 64),
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
            row.get("processing_lease_token"),
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
                        .order_by(chat_event.c.stream_sequence)
                    )
                )
                .mappings()
                .all()
            )
        events = tuple(
            ConversationEvent(
                r["event_id"],
                r["stream_sequence"],
                r["event_type"],
                r["payload"],
                r["occurred_at"].replace(tzinfo=timezone.utc),
                r["turn_id"],
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

    async def complete(self, conversation_id, turn_id, snapshot, event, lease_token=None):
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
            if (
                row["state"] == "running"
                and snapshot.value.outcome != "canceled"
                and (not lease_token or lease_token != row["processing_lease_token"])
            ):
                raise ConversationConflict("generation lease lost")
            input_row = (
                (
                    await session.execute(
                        select(turn_input_snapshot).where(
                            *scope_predicates(turn_input_snapshot, self.scope),
                            turn_input_snapshot.c.turn_id == turn_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            input_digest = None if input_row is None else input_row["snapshot_digest"]
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
            if snapshot.value.graph_context_status is GraphContextStatus.APPLIED:
                raise ValueError("graph snapshot persistence is unavailable")
            frozen_resources = {
                (
                    item["source_id"],
                    item["document_id"],
                    item["revision_id"],
                    item["source_content_hash"],
                )
                for item in (
                    [] if input_row is None else input_row["snapshot"].get("resolved_resources", [])
                )
            }
            if snapshot.value.citations and not frozen_resources:
                raise ValueError("citation is outside the frozen Turn resources")
            for citation in snapshot.value.citations:
                citation_row = (
                    (
                        await session.execute(
                            text(
                                "SELECT citation_id,source_id,trace_id,document_id,revision_id,"
                                "chunk_id,source_content_hash,chunk_content_hash,anchor_json "
                                "FROM knowledge_citation_snapshot "
                                "WHERE enterprise_id=:enterprise_id AND project_id=:project_id "
                                "AND citation_id=:citation_id AND trace_id=:trace_id"
                            ),
                            {
                                "enterprise_id": self.scope.enterprise_id,
                                "project_id": self.scope.project_id,
                                "citation_id": citation.citation_snapshot_id,
                                "trace_id": snapshot.value.retrieval_summary.trace_id,
                            },
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if citation_row is None:
                    raise ValueError("citation snapshot is not a trusted persisted fact")
                trusted_digest = citation_evidence_digest(
                    citation_id=citation_row["citation_id"],
                    trace_id=citation_row["trace_id"],
                    source_id=citation_row["source_id"],
                    document_id=citation_row["document_id"],
                    revision_id=citation_row["revision_id"],
                    chunk_id=citation_row["chunk_id"],
                    source_content_hash=citation_row["source_content_hash"],
                    chunk_content_hash=citation_row["chunk_content_hash"],
                    anchor=citation_row["anchor_json"],
                )
                if trusted_digest != citation.citation_digest:
                    raise ValueError("citation snapshot digest differs from persisted fact")
                if (
                    citation_row["source_id"],
                    citation_row["document_id"],
                    citation_row["revision_id"],
                    citation_row["source_content_hash"],
                ) not in frozen_resources:
                    raise ValueError("citation is outside the frozen Turn resources")
                await session.execute(
                    insert(turn_artifact_link).values(
                        **scope_values(self.scope),
                        link_id=uuid4().hex,
                        turn_id=turn_id,
                        artifact_kind="citation",
                        artifact_id=citation.citation_snapshot_id,
                        artifact_digest=trusted_digest,
                        created_at=_naive(snapshot.created_at),
                    )
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
                .values(
                    state=snapshot.value.outcome,
                    last_sequence=event.sequence,
                    processing_lease_token=None,
                    processing_lease_expires_at=None,
                )
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
                            or_(
                                chat_turn.c.state == "queued",
                                (
                                    (chat_turn.c.state == "running")
                                    & (
                                        chat_turn.c.processing_lease_expires_at
                                        < datetime.now(timezone.utc).replace(tzinfo=None)
                                    )
                                ),
                            ),
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
                now = datetime.now(timezone.utc)
                if row is None or not (
                    row["state"] == "queued"
                    or (
                        row["state"] == "running"
                        and row["processing_lease_expires_at"] is not None
                        and row["processing_lease_expires_at"] < _naive(now)
                    )
                ):
                    continue
                lease_token = uuid4().hex
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
                        stream_sequence=event.sequence,
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
                    )
                    .values(
                        state="running",
                        last_sequence=event.sequence,
                        processing_attempt=row["processing_attempt"] + 1,
                        processing_lease_token=lease_token,
                        processing_lease_expires_at=_naive(now + timedelta(seconds=60)),
                    )
                )
                mutable = dict(row)
                mutable["state"] = "running"
                mutable["processing_attempt"] = row["processing_attempt"] + 1
                mutable["processing_lease_token"] = lease_token
                claimed.append((row["chat_id"], await self._load_turn(session, mutable)))
        return tuple(claimed)

    async def append_stream_event(self, conversation_id, turn_id, event, lease_token=None):
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
            if row["state"] == "running" and (
                not lease_token or lease_token != row["processing_lease_token"]
            ):
                raise ConversationConflict("generation lease lost")
            event = await self._next_event(session, conversation_id, event)
            await session.execute(
                insert(chat_event).values(
                    **scope_values(self.scope),
                    event_id=event.event_id,
                    turn_id=turn_id,
                    sequence=event.sequence,
                    stream_sequence=event.sequence,
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
