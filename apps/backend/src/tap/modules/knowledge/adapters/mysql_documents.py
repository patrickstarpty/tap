"""MySQL-backed durable document ledger and ingestion-job ownership."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NoReturn, cast
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    and_,
    delete,
    exists,
    func,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from tap.modules.knowledge.domain.documents import (
    DocumentId,
    new_document_id,
    revision_id_for,
)
from tap.modules.knowledge.ports.answers import (
    AnswerSnapshot,
    AnswerSnapshotUnavailable,
    CitationSnapshot,
    DocumentStateChanged,
    ReadyDocumentRevision,
)
from tap.modules.knowledge.ports.citations import (
    CitationDocumentFacts,
    CitationLookup,
    CitationSnapshotCorrupt,
)
from tap.modules.knowledge.ports.documents import (
    DEFAULT_RESERVATION_LEASE,
    DEFAULT_UPLOAD_CLEANUP_LEASE,
    MAX_DOCUMENTS,
    MAX_JOB_LEASE,
    MAX_RESERVATION_LEASE,
    SAFE_ERROR_SUMMARIES,
    ArtifactLocator,
    ClaimedIngestionJob,
    DocumentCapacityExceeded,
    DocumentCursor,
    DocumentRecord,
    DocumentRecordPage,
    DocumentState,
    IngestionJob,
    IngestionWork,
    InvalidDocumentCursor,
    JobCheckpoint,
    JobFailure,
    JobKind,
    JobLeaseLost,
    JobRetry,
    JobStage,
    JobStageCommit,
    JobState,
    ManifestChunk,
    ReservationState,
    ReserveUpload,
    RetryNotAllowed,
    StageResult,
    StageState,
    UploadRecovery,
    UploadReservation,
    deserialize_stage_results,
    initial_stage_results,
    serialize_stage_results,
)
from tap.platform.db.schema import metadata, outbox

CANCELLED_OWNER_SETTLEMENT_LEASE = timedelta(seconds=60)
ANSWER_SNAPSHOT_RETENTION = 1_000
ANSWER_SNAPSHOT_LOCK_NAME = "tap:athena:answer-snapshot-retention:v1"
ANSWER_SNAPSHOT_LOCK_ACQUIRE_DEADLINE_SECONDS = 6.0
ANSWER_SNAPSHOT_LOCK_RELEASE_DEADLINE_SECONDS = 2.0

knowledge_document = Table(
    "knowledge_document",
    metadata,
    Column("document_id", String(64), primary_key=True),
    Column("filename", String(255), nullable=False),
    Column("media_type", String(128), nullable=False),
    Column("current_revision_id", String(128)),
    Column("source_content_hash", String(71), nullable=False),
    Column("dedupe_key", String(71)),
    Column("staging_blob_locator", String(1024)),
    Column("promoted_blob_locator", String(1024)),
    Column("reservation_owner_token", String(64)),
    Column("reservation_expires_at", DATETIME(fsp=6)),
    Column("reservation_parser_version", String(128), nullable=False),
    Column("reservation_chunker_version", String(128), nullable=False),
    Column("reservation_pipeline_version", String(128), nullable=False),
    Column("status", String(24), nullable=False),
    Column("stage", String(24), nullable=False),
    Column("chunk_count", Integer, nullable=False, server_default="0"),
    Column("error_code", String(64)),
    Column("error_summary", String(240)),
    Column("activated_at", DATETIME(fsp=6)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
    Column("deleted_at", DATETIME(fsp=6)),
    UniqueConstraint("dedupe_key", name="uq_knowledge_document_dedupe_key"),
)
Index(
    "ix_knowledge_document_status_updated_id",
    knowledge_document.c.status,
    knowledge_document.c.updated_at,
    knowledge_document.c.document_id,
)
Index(
    "ix_knowledge_document_reservation_recovery",
    knowledge_document.c.activated_at,
    knowledge_document.c.reservation_expires_at,
    knowledge_document.c.document_id,
)

knowledge_document_revision = Table(
    "knowledge_document_revision",
    metadata,
    Column("revision_id", String(128), primary_key=True),
    Column(
        "document_id",
        String(64),
        ForeignKey("knowledge_document.document_id", name="fk_knowledge_revision_document"),
        nullable=False,
    ),
    Column("source_content_hash", String(71), nullable=False),
    Column("original_blob_locator", String(1024), nullable=False),
    Column("normalized_blob_locator", String(1024)),
    Column("chunks_blob_locator", String(1024)),
    Column("embeddings_blob_locator", String(1024)),
    Column("parser_version", String(128), nullable=False),
    Column("chunker_version", String(128), nullable=False),
    Column("pipeline_version", String(128), nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint(
        "document_id",
        "source_content_hash",
        "parser_version",
        name="uq_knowledge_revision_source_parser",
    ),
)
knowledge_document.append_constraint(
    ForeignKeyConstraint(
        [knowledge_document.c.current_revision_id],
        [knowledge_document_revision.c.revision_id],
        name="fk_knowledge_document_current_revision",
        use_alter=True,
    )
)

knowledge_ingestion_job = Table(
    "knowledge_ingestion_job",
    metadata,
    Column("job_id", String(64), primary_key=True),
    Column(
        "revision_id",
        String(128),
        ForeignKey("knowledge_document_revision.revision_id", name="fk_knowledge_job_revision"),
        nullable=False,
    ),
    Column("kind", String(24), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("status", String(24), nullable=False),
    Column("stage", String(24), nullable=False),
    Column("stage_results_json", JSON, nullable=False),
    Column("lease_owner", String(128)),
    Column("lease_token", String(64)),
    Column("lease_until", DATETIME(fsp=6)),
    Column("next_attempt_at", DATETIME(fsp=6), nullable=False),
    Column("error_code", String(64)),
    Column("error_summary", String(240)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
    Column("completed_at", DATETIME(fsp=6)),
    UniqueConstraint("revision_id", "kind", name="uq_knowledge_job_revision_kind"),
)
Index(
    "ix_knowledge_job_status_retry_created",
    knowledge_ingestion_job.c.status,
    knowledge_ingestion_job.c.next_attempt_at,
    knowledge_ingestion_job.c.created_at,
)
Index(
    "ix_knowledge_job_status_lease",
    knowledge_ingestion_job.c.status,
    knowledge_ingestion_job.c.lease_until,
)

knowledge_chunk_manifest = Table(
    "knowledge_chunk_manifest",
    metadata,
    Column("chunk_id", String(128), primary_key=True),
    Column("logical_chunk_id", String(128), nullable=False),
    Column(
        "revision_id",
        String(128),
        ForeignKey(
            "knowledge_document_revision.revision_id", name="fk_knowledge_manifest_revision"
        ),
        nullable=False,
    ),
    Column("ordinal", Integer, nullable=False),
    Column("root_id", String(64), nullable=False),
    Column("parent_id", String(128)),
    Column("anchor_json", JSON, nullable=False),
    Column("chunk_content_hash", String(71), nullable=False),
    Column("embedding_model_version", String(128), nullable=False),
    Column("index_version", String(128), nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint("revision_id", "ordinal", name="uq_knowledge_manifest_revision_ordinal"),
)

knowledge_answer_snapshot = Table(
    "knowledge_answer_snapshot",
    metadata,
    Column("trace_id", String(64), primary_key=True),
    Column("query_hash", String(71), nullable=False),
    Column("selected_revisions_json", JSON, nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
)

knowledge_citation_snapshot = Table(
    "knowledge_citation_snapshot",
    metadata,
    Column("citation_id", String(64), primary_key=True),
    Column(
        "trace_id",
        String(64),
        ForeignKey(
            "knowledge_answer_snapshot.trace_id",
            name="fk_knowledge_citation_answer",
            ondelete="CASCADE",
        ),
        nullable=False,
    ),
    Column("document_id", String(64), nullable=False),
    Column("revision_id", String(128), nullable=False),
    Column("chunk_id", String(128), nullable=False),
    Column("source_content_hash", String(71), nullable=False),
    Column("chunk_content_hash", String(71), nullable=False),
    Column("anchor_json", JSON, nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    UniqueConstraint("trace_id", "citation_id", name="uq_knowledge_citation_trace_id"),
)


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.utc_timestamp(6)))
    if not isinstance(value, datetime):
        raise RuntimeError("MySQL did not return a database timestamp")
    return value


@dataclass(frozen=True, slots=True)
class _NamedLockOutcome:
    value: object | None
    error: BaseException | None
    cancellations: tuple[asyncio.CancelledError, ...]


def _observe_task(task: asyncio.Task[object]) -> None:
    try:
        task.exception()
    except BaseException:
        pass


async def _bounded_named_lock_query(
    connection: AsyncConnection,
    statement: str,
    parameters: dict[str, object],
    *,
    timeout_seconds: float,
) -> _NamedLockOutcome:
    task = asyncio.create_task(connection.scalar(text(statement), parameters))
    cancellations: list[asyncio.CancelledError] = []
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while not task.done():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            task.cancel()
            task.add_done_callback(_observe_task)
            return _NamedLockOutcome(
                value=None,
                error=TimeoutError("named lock operation exceeded its client deadline"),
                cancellations=tuple(cancellations),
            )
        try:
            done, _pending = await asyncio.wait({task}, timeout=remaining)
            if not done:
                task.cancel()
                task.add_done_callback(_observe_task)
                return _NamedLockOutcome(
                    value=None,
                    error=TimeoutError("named lock operation exceeded its client deadline"),
                    cancellations=tuple(cancellations),
                )
        except asyncio.CancelledError as cancellation:
            cancellations.append(cancellation)
    try:
        return _NamedLockOutcome(
            value=task.result(),
            error=None,
            cancellations=tuple(cancellations),
        )
    except BaseException as error:
        return _NamedLockOutcome(
            value=None,
            error=error,
            cancellations=tuple(cancellations),
        )


def _terminate_named_lock_connection(connection: AsyncConnection, error: BaseException) -> None:
    """Synchronously detach and terminate the exact pooled physical connection."""
    sync_connection = connection.sync_connection
    if sync_connection is None:
        raise AnswerSnapshotUnavailable("answer snapshot connection is not started")
    sync_connection.invalidate(error)
    # Invalidation deliberately retains SQLAlchemy's transaction marker. Clear it
    # without reconnecting so context-manager exit cannot procure a replacement.
    sync_connection.rollback()


async def _release_named_lock(
    connection: AsyncConnection, lock_name: str, *, ownership_confirmed: bool
) -> None:
    """Release a confirmed lock or terminate a connection with uncertain ownership."""
    if connection.invalidated:
        return
    if not ownership_confirmed:
        _terminate_named_lock_connection(
            connection,
            AnswerSnapshotUnavailable("answer snapshot lock ownership is uncertain"),
        )
        return
    outcome = await _bounded_named_lock_query(
        connection,
        "SELECT RELEASE_LOCK(:lock_name)",
        {"lock_name": lock_name},
        timeout_seconds=ANSWER_SNAPSHOT_LOCK_RELEASE_DEADLINE_SECONDS,
    )
    failure: AnswerSnapshotUnavailable | None = None
    if outcome.error is not None:
        failure = AnswerSnapshotUnavailable("answer snapshot serialization lock release failed")
    elif outcome.value != 1:
        failure = AnswerSnapshotUnavailable("answer snapshot serialization lock was lost")
    if failure is not None:
        try:
            _terminate_named_lock_connection(connection, outcome.error or failure)
        except BaseException as termination_error:
            if outcome.cancellations:
                raise outcome.cancellations[0]
            raise AnswerSnapshotUnavailable(
                "answer snapshot connection termination failed"
            ) from termination_error
        if outcome.cancellations:
            raise outcome.cancellations[0]
        raise failure from outcome.error
    if outcome.cancellations:
        raise outcome.cancellations[0]


def _job_id() -> str:
    return f"job_{uuid4().hex}"


def _owner_token(document_id: str) -> str:
    return f"owner:{document_id}"


def _job_from_row(row: RowMapping) -> IngestionJob:
    return IngestionJob(
        job_id=cast(str, row["job_id"]),
        revision_id=cast(str, row["revision_id"]),
        kind=JobKind(cast(str, row["kind"])),
        attempt=cast(int, row["attempt"]),
        status=JobState(cast(str, row["status"])),
        stage=JobStage(cast(str, row["stage"])),
        stages=deserialize_stage_results(row["stage_results_json"]),
    )


class MysqlDocumentRepository:
    """The MySQL document/revision/job facts and their transaction boundaries."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._engine = cast(AsyncEngine, sessions.kw["bind"])

    async def load_ready_revisions(
        self, document_ids: tuple[str, ...]
    ) -> tuple[ReadyDocumentRevision, ...]:
        if (
            not isinstance(document_ids, tuple)
            or not 1 <= len(document_ids) <= 20
            or len(set(document_ids)) != len(document_ids)
            or any(not isinstance(item, str) or not item for item in document_ids)
        ):
            raise ValueError("ready revision lookup requires one to twenty unique document IDs")
        ordered_ids = tuple(sorted(document_ids))
        async with self._sessions() as session, session.begin():
            rows = list(
                (
                    await session.execute(
                        select(
                            knowledge_document.c.document_id,
                            knowledge_document.c.current_revision_id,
                            knowledge_document.c.source_content_hash.label(
                                "document_source_content_hash"
                            ),
                            knowledge_document_revision.c.source_content_hash.label(
                                "revision_source_content_hash"
                            ),
                        )
                        .select_from(
                            knowledge_document.join(
                                knowledge_document_revision,
                                and_(
                                    knowledge_document_revision.c.revision_id
                                    == knowledge_document.c.current_revision_id,
                                    knowledge_document_revision.c.document_id
                                    == knowledge_document.c.document_id,
                                ),
                            )
                        )
                        .where(
                            knowledge_document.c.document_id.in_(ordered_ids),
                            knowledge_document.c.status == DocumentState.READY.value,
                            knowledge_document.c.activated_at.is_not(None),
                            knowledge_document.c.deleted_at.is_(None),
                        )
                        .order_by(knowledge_document.c.document_id)
                        .with_for_update()
                    )
                ).mappings()
            )
            return tuple(
                ReadyDocumentRevision(
                    document_id=cast(str, row["document_id"]),
                    revision_id=cast(str, row["current_revision_id"]),
                    source_content_hash=cast(str, row["document_source_content_hash"]),
                )
                for row in rows
                if row["document_source_content_hash"] == row["revision_source_content_hash"]
            )

    async def save_answer_with_citations(self, snapshot: AnswerSnapshot) -> None:
        if not isinstance(snapshot, AnswerSnapshot):
            raise TypeError("answer snapshot repository requires AnswerSnapshot")
        try:
            # Hold an explicit pooled connection so MySQL's connection-scoped lock
            # cannot migrate after the business transaction commits.
            async with self._engine.connect() as connection:
                ownership_confirmed = False
                acquisition_settled = False
                first_cancellation: asyncio.CancelledError | None = None
                try:
                    acquired = await self._acquire_answer_snapshot_lock(connection)
                    acquisition_settled = True
                    if acquired != 1:
                        raise AnswerSnapshotUnavailable(
                            "answer snapshot serialization is unavailable"
                        )
                    ownership_confirmed = True
                    # GET_LOCK is connection-scoped rather than transactional. End its implicit
                    # transaction before starting the snapshot transaction while retaining the
                    # lock; the finally also covers cancellation during this boundary commit.
                    await connection.commit()
                    async with self._sessions(bind=connection) as session, session.begin():
                        await self._save_answer_snapshot(session, snapshot)
                except asyncio.CancelledError as cancellation:
                    first_cancellation = cancellation
                finally:
                    # The GET_LOCK await can be cancelled after MySQL granted ownership but
                    # before an override returns. A settled non-grant owns no lock; every
                    # other state must release or physically terminate this connection.
                    if ownership_confirmed or not acquisition_settled:
                        try:
                            await _release_named_lock(
                                connection,
                                ANSWER_SNAPSHOT_LOCK_NAME,
                                ownership_confirmed=ownership_confirmed,
                            )
                        except asyncio.CancelledError as cancellation:
                            if first_cancellation is None:
                                first_cancellation = cancellation
                        except BaseException:
                            if first_cancellation is None:
                                raise
                    if first_cancellation is not None:
                        raise first_cancellation
        except (DocumentStateChanged, AnswerSnapshotUnavailable, asyncio.CancelledError):
            raise
        except Exception as error:
            raise AnswerSnapshotUnavailable("answer snapshot transaction failed") from error

    async def _acquire_answer_snapshot_lock(self, connection: AsyncConnection) -> object:
        outcome = await _bounded_named_lock_query(
            connection,
            "SELECT GET_LOCK(:lock_name, 5)",
            {"lock_name": ANSWER_SNAPSHOT_LOCK_NAME},
            timeout_seconds=ANSWER_SNAPSHOT_LOCK_ACQUIRE_DEADLINE_SECONDS,
        )
        malformed = outcome.error is None and outcome.value not in {None, 0, 1}
        if outcome.error is not None or malformed or outcome.cancellations:
            failure = AnswerSnapshotUnavailable(
                "answer snapshot serialization lock acquisition failed"
            )
            try:
                _terminate_named_lock_connection(connection, outcome.error or failure)
            except BaseException as termination_error:
                if outcome.cancellations:
                    raise outcome.cancellations[0]
                raise AnswerSnapshotUnavailable(
                    "answer snapshot connection termination failed"
                ) from termination_error
            if outcome.cancellations:
                raise outcome.cancellations[0]
            raise failure from outcome.error
        return outcome.value

    async def _save_answer_snapshot(self, session: AsyncSession, snapshot: AnswerSnapshot) -> None:
        expected = snapshot.selected_revisions
        document_ids = tuple(item.document_id for item in expected)
        rows = list(
            (
                await session.execute(
                    select(
                        knowledge_document.c.document_id,
                        knowledge_document.c.current_revision_id,
                        knowledge_document.c.source_content_hash,
                        knowledge_document.c.status,
                        knowledge_document.c.activated_at,
                        knowledge_document.c.deleted_at,
                    )
                    .where(knowledge_document.c.document_id.in_(document_ids))
                    .order_by(knowledge_document.c.document_id)
                    .with_for_update()
                )
            ).mappings()
        )
        actual = tuple(
            (
                cast(str, row["document_id"]),
                cast(str | None, row["current_revision_id"]),
                cast(str, row["source_content_hash"]),
                cast(str, row["status"]),
                row["activated_at"] is not None,
                row["deleted_at"] is None,
            )
            for row in rows
        )
        wanted = tuple(
            (
                item.document_id,
                item.revision_id,
                item.source_content_hash,
                DocumentState.READY.value,
                True,
                True,
            )
            for item in expected
        )
        if actual != wanted:
            raise DocumentStateChanged("selected document state changed before snapshot commit")
        revision_rows = list(
            (
                await session.execute(
                    select(
                        knowledge_document_revision.c.revision_id,
                        knowledge_document_revision.c.document_id,
                        knowledge_document_revision.c.source_content_hash,
                    ).where(
                        knowledge_document_revision.c.revision_id.in_(
                            tuple(item.revision_id for item in expected)
                        )
                    )
                )
            ).mappings()
        )
        revision_facts = {
            (
                cast(str, row["document_id"]),
                cast(str, row["revision_id"]),
                cast(str, row["source_content_hash"]),
            )
            for row in revision_rows
        }
        if revision_facts != {
            (item.document_id, item.revision_id, item.source_content_hash) for item in expected
        }:
            raise DocumentStateChanged("selected revision facts changed before snapshot commit")
        if snapshot.citations:
            manifest_rows = list(
                (
                    await session.execute(
                        select(
                            knowledge_chunk_manifest.c.chunk_id,
                            knowledge_chunk_manifest.c.revision_id,
                            knowledge_chunk_manifest.c.chunk_content_hash,
                            knowledge_chunk_manifest.c.anchor_json,
                        )
                        .where(
                            knowledge_chunk_manifest.c.chunk_id.in_(
                                tuple(item.chunk_id for item in snapshot.citations)
                            )
                        )
                        .order_by(knowledge_chunk_manifest.c.chunk_id)
                        .with_for_update()
                    )
                ).mappings()
            )
            manifest_facts = {
                (
                    cast(str, row["chunk_id"]),
                    cast(str, row["revision_id"]),
                    cast(str, row["chunk_content_hash"]),
                    _canonical_json_object(row["anchor_json"]),
                )
                for row in manifest_rows
            }
            citation_facts = {
                (
                    item.chunk_id,
                    item.revision_id,
                    item.chunk_content_hash,
                    item.anchor_json,
                )
                for item in snapshot.citations
            }
            if manifest_facts != citation_facts:
                raise AnswerSnapshotUnavailable(
                    "answer citation manifest does not match durable facts"
                )
        now = await _database_now(session)
        await session.execute(
            insert(knowledge_answer_snapshot).values(
                trace_id=snapshot.trace_id,
                query_hash=snapshot.query_hash,
                selected_revisions_json=[
                    {
                        "documentId": item.document_id,
                        "revisionId": item.revision_id,
                        "sourceContentHash": item.source_content_hash,
                    }
                    for item in expected
                ],
                created_at=now,
            )
        )
        if snapshot.citations:
            await session.execute(
                insert(knowledge_citation_snapshot),
                [
                    {
                        "citation_id": item.citation_id,
                        "trace_id": item.trace_id,
                        "document_id": item.document_id,
                        "revision_id": item.revision_id,
                        "chunk_id": item.chunk_id,
                        "source_content_hash": item.source_content_hash,
                        "chunk_content_hash": item.chunk_content_hash,
                        "anchor_json": json.loads(item.anchor_json),
                        "created_at": now,
                    }
                    for item in snapshot.citations
                ],
            )
        count = cast(
            int,
            await session.scalar(select(func.count()).select_from(knowledge_answer_snapshot)),
        )
        excess = count - ANSWER_SNAPSHOT_RETENTION
        if excess > 0:
            expired = tuple(
                await session.scalars(
                    select(knowledge_answer_snapshot.c.trace_id)
                    .order_by(
                        knowledge_answer_snapshot.c.created_at,
                        knowledge_answer_snapshot.c.trace_id,
                    )
                    .limit(excess)
                )
            )
            if expired:
                await session.execute(
                    delete(knowledge_answer_snapshot).where(
                        knowledge_answer_snapshot.c.trace_id.in_(expired)
                    )
                )

    async def load_citation(self, citation_id: str) -> CitationLookup | None:
        if not isinstance(citation_id, str) or not citation_id or len(citation_id) > 64:
            return None
        citation = knowledge_citation_snapshot
        answer = knowledge_answer_snapshot
        document = knowledge_document
        revision = knowledge_document_revision
        manifest = knowledge_chunk_manifest
        source = (
            citation.join(answer, answer.c.trace_id == citation.c.trace_id)
            .outerjoin(document, document.c.document_id == citation.c.document_id)
            .outerjoin(
                revision,
                and_(
                    revision.c.revision_id == citation.c.revision_id,
                    revision.c.document_id == citation.c.document_id,
                ),
            )
            .outerjoin(
                manifest,
                and_(
                    manifest.c.chunk_id == citation.c.chunk_id,
                    manifest.c.revision_id == citation.c.revision_id,
                ),
            )
        )
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(
                            citation.c.citation_id,
                            citation.c.trace_id.label("citation_trace_id"),
                            citation.c.document_id.label("citation_document_id"),
                            citation.c.revision_id.label("citation_revision_id"),
                            citation.c.chunk_id.label("citation_chunk_id"),
                            citation.c.source_content_hash.label("citation_source_hash"),
                            citation.c.chunk_content_hash.label("citation_chunk_hash"),
                            citation.c.anchor_json.label("citation_anchor"),
                            answer.c.trace_id.label("answer_trace_id"),
                            answer.c.selected_revisions_json,
                            document.c.document_id.label("document_id"),
                            document.c.filename,
                            document.c.status,
                            document.c.deleted_at,
                            document.c.current_revision_id,
                            document.c.source_content_hash.label("document_source_hash"),
                            revision.c.source_content_hash.label("revision_source_hash"),
                            revision.c.normalized_blob_locator,
                            revision.c.chunks_blob_locator,
                            manifest.c.chunk_id.label("manifest_chunk_id"),
                            manifest.c.logical_chunk_id,
                            manifest.c.ordinal,
                            manifest.c.root_id,
                            manifest.c.parent_id,
                            manifest.c.anchor_json.label("manifest_anchor"),
                            manifest.c.chunk_content_hash.label("manifest_chunk_hash"),
                            manifest.c.embedding_model_version,
                            manifest.c.index_version,
                        )
                        .select_from(source)
                        .where(citation.c.citation_id == citation_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        try:
            citation_anchor = _canonical_json_object(row["citation_anchor"])
            snapshot = CitationSnapshot(
                trace_id=cast(str, row["citation_trace_id"]),
                citation_id=cast(str, row["citation_id"]),
                document_id=cast(str, row["citation_document_id"]),
                revision_id=cast(str, row["citation_revision_id"]),
                chunk_id=cast(str, row["citation_chunk_id"]),
                source_content_hash=cast(str, row["citation_source_hash"]),
                chunk_content_hash=cast(str, row["citation_chunk_hash"]),
                anchor_json=citation_anchor,
            )
            selected = _selected_revisions(row["selected_revisions_json"])
            document_facts = None
            if row["document_id"] is not None:
                document_facts = CitationDocumentFacts(
                    document_id=cast(str, row["document_id"]),
                    filename=cast(str, row["filename"]),
                    status=DocumentState(cast(str, row["status"])),
                    deleted=row["deleted_at"] is not None,
                    current_revision_id=cast(str, row["current_revision_id"]),
                    current_source_content_hash=cast(str, row["document_source_hash"]),
                    revision_source_content_hash=cast(str, row["revision_source_hash"]),
                    normalized_locator=_optional_locator(row["normalized_blob_locator"]),
                    chunks_locator=_optional_locator(row["chunks_blob_locator"]),
                )
            manifest_fact = None
            if row["manifest_chunk_id"] is not None:
                manifest_fact = ManifestChunk(
                    chunk_id=cast(str, row["manifest_chunk_id"]),
                    logical_chunk_id=cast(str, row["logical_chunk_id"]),
                    ordinal=cast(int, row["ordinal"]),
                    root_id=cast(str, row["root_id"]),
                    parent_id=cast(str | None, row["parent_id"]),
                    anchor_json=_canonical_json_object(row["manifest_anchor"]),
                    chunk_content_hash=cast(str, row["manifest_chunk_hash"]),
                    embedding_model_version=cast(str, row["embedding_model_version"]),
                    index_version=cast(str, row["index_version"]),
                )
            return CitationLookup(
                citation=snapshot,
                answer_trace_id=cast(str | None, row["answer_trace_id"]),
                selected_revisions=selected,
                document=document_facts,
                manifest=manifest_fact,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise CitationSnapshotCorrupt("citation snapshot facts are malformed") from error

    async def citation_is_current(self, citation: CitationSnapshot) -> bool:
        if not isinstance(citation, CitationSnapshot):
            raise TypeError("citation current check requires CitationSnapshot")
        lookup = await self.load_citation(citation.citation_id)
        if lookup is None or lookup.citation != citation or lookup.document is None:
            return False
        document = lookup.document
        selected = {
            (item.document_id, item.revision_id, item.source_content_hash)
            for item in lookup.selected_revisions
        }
        return (
            lookup.answer_trace_id == citation.trace_id
            and document.status is DocumentState.READY
            and not document.deleted
            and document.document_id == citation.document_id
            and document.current_revision_id == citation.revision_id
            and document.current_source_content_hash == citation.source_content_hash
            and document.revision_source_content_hash == citation.source_content_hash
            and (
                citation.document_id,
                citation.revision_id,
                citation.source_content_hash,
            )
            in selected
        )

    async def reserve_upload(self, command: ReserveUpload) -> UploadReservation:
        for attempt in range(3):
            try:
                return await self._reserve_upload_once(command)
            except DBAPIError as error:
                if _mysql_error_code(error) not in {1062, 1213} or attempt == 2:
                    raise
                await asyncio.sleep(0)
        raise AssertionError("bounded reservation retry must return or raise")

    async def _reserve_upload_once(self, command: ReserveUpload) -> UploadReservation:
        async with self._sessions() as session, session.begin():
            now = await _database_now(session)
            active_rows = list(
                (
                    await session.execute(
                        select(knowledge_document)
                        .where(
                            knowledge_document.c.dedupe_key.is_not(None),
                            knowledge_document.c.deleted_at.is_(None),
                        )
                        .order_by(knowledge_document.c.dedupe_key)
                        .with_for_update()
                    )
                ).mappings()
            )
            duplicate = next(
                (row for row in active_rows if row["dedupe_key"] == command.dedupe_key), None
            )
            if duplicate is not None:
                document_id = cast(str, duplicate["document_id"])
                revision_id = (
                    cast(str, duplicate["current_revision_id"])
                    if duplicate["current_revision_id"] is not None
                    else revision_id_for(
                        DocumentId(document_id),
                        cast(str, duplicate["source_content_hash"]),
                        cast(str, duplicate["reservation_parser_version"]),
                    )
                )
                if duplicate["activated_at"] is not None:
                    record = await self._record_for_row(session, duplicate)
                    return UploadReservation(
                        state=ReservationState.DUPLICATE_ACTIVE,
                        reservation_id=document_id,
                        owner_token="",
                        document_id=document_id,
                        revision_id=revision_id,
                        dedupe_key=command.dedupe_key,
                        document=record,
                        parser_version=cast(str, duplicate["reservation_parser_version"]),
                        chunker_version=cast(str, duplicate["reservation_chunker_version"]),
                        pipeline_version=cast(str, duplicate["reservation_pipeline_version"]),
                        staging_key=cast(str | None, duplicate["staging_blob_locator"]),
                        promoted_locator=ArtifactLocator(duplicate["promoted_blob_locator"])
                        if duplicate["promoted_blob_locator"] is not None
                        else None,
                        expires_at=cast(datetime | None, duplicate["reservation_expires_at"]),
                    )
                return UploadReservation(
                    state=ReservationState.DUPLICATE_PENDING,
                    reservation_id=document_id,
                    owner_token="",
                    document_id=document_id,
                    revision_id=revision_id,
                    dedupe_key=command.dedupe_key,
                    document=None,
                    parser_version=cast(str, duplicate["reservation_parser_version"]),
                    chunker_version=cast(str, duplicate["reservation_chunker_version"]),
                    pipeline_version=cast(str, duplicate["reservation_pipeline_version"]),
                    staging_key=cast(str | None, duplicate["staging_blob_locator"]),
                    promoted_locator=ArtifactLocator(duplicate["promoted_blob_locator"])
                    if duplicate["promoted_blob_locator"] is not None
                    else None,
                    expires_at=cast(datetime | None, duplicate["reservation_expires_at"]),
                )
            if len(active_rows) >= MAX_DOCUMENTS:
                raise DocumentCapacityExceeded

            document_id = new_document_id(lambda: uuid4().hex)
            owner_token = uuid4().hex
            expires_at = now + DEFAULT_RESERVATION_LEASE
            revision_id = revision_id_for(
                document_id, command.source_content_hash, command.parser_version
            )
            await session.execute(
                insert(knowledge_document).values(
                    document_id=document_id,
                    filename=command.filename,
                    media_type=command.media_type,
                    current_revision_id=None,
                    source_content_hash=command.source_content_hash,
                    dedupe_key=command.dedupe_key,
                    staging_blob_locator=command.staging_key,
                    promoted_blob_locator=None,
                    reservation_owner_token=owner_token,
                    reservation_expires_at=expires_at,
                    reservation_parser_version=command.parser_version,
                    reservation_chunker_version=command.chunker_version,
                    reservation_pipeline_version=command.pipeline_version,
                    status=DocumentState.QUEUED.value,
                    stage=JobStage.STORED.value,
                    chunk_count=0,
                    activated_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            return UploadReservation(
                state=ReservationState.OWNED,
                reservation_id=document_id,
                owner_token=owner_token,
                document_id=document_id,
                revision_id=revision_id,
                dedupe_key=command.dedupe_key,
                document=None,
                parser_version=command.parser_version,
                chunker_version=command.chunker_version,
                pipeline_version=command.pipeline_version,
                staging_key=command.staging_key,
                promoted_locator=None,
                expires_at=expires_at,
            )

    async def activate_upload(
        self, reservation: UploadReservation, original: ArtifactLocator
    ) -> DocumentRecord:
        original = await self.record_upload_promotion(reservation, original)
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(knowledge_document)
                        .where(knowledge_document.c.document_id == reservation.document_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["dedupe_key"] != reservation.dedupe_key:
                raise JobLeaseLost(reservation.reservation_id)
            if row["activated_at"] is not None:
                return await self._record_for_row(session, row)
            if row["promoted_blob_locator"] is None:
                raise JobLeaseLost(reservation.reservation_id)
            parser_version = cast(str, row["reservation_parser_version"])
            expected_revision_id = revision_id_for(
                DocumentId(reservation.document_id),
                cast(str, row["source_content_hash"]),
                parser_version,
            )
            if reservation.revision_id != expected_revision_id:
                raise JobLeaseLost(reservation.reservation_id)

            now = await _database_now(session)
            await session.execute(
                insert(knowledge_document_revision).values(
                    revision_id=reservation.revision_id,
                    document_id=reservation.document_id,
                    source_content_hash=row["source_content_hash"],
                    original_blob_locator=str(original),
                    parser_version=parser_version,
                    chunker_version=row["reservation_chunker_version"],
                    pipeline_version=row["reservation_pipeline_version"],
                    created_at=now,
                )
            )
            job_id = _job_id()
            results = initial_stage_results(now)
            await session.execute(
                insert(knowledge_ingestion_job).values(
                    job_id=job_id,
                    revision_id=reservation.revision_id,
                    kind=JobKind.INGESTION.value,
                    attempt=1,
                    status=JobState.PENDING.value,
                    stage=JobStage.STORED.value,
                    stage_results_json=serialize_stage_results(results),
                    next_attempt_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            await self._insert_job_outbox(
                session,
                document_id=reservation.document_id,
                job_id=job_id,
                attempt=1,
                message_type="knowledge.ingestion_requested",
                now=now,
            )
            await session.execute(
                update(knowledge_document)
                .where(
                    knowledge_document.c.document_id == reservation.document_id,
                    knowledge_document.c.activated_at.is_(None),
                )
                .values(
                    current_revision_id=reservation.revision_id,
                    activated_at=now,
                    reservation_expires_at=now + DEFAULT_UPLOAD_CLEANUP_LEASE,
                    updated_at=now,
                )
            )
            activated_row = (
                (
                    await session.execute(
                        select(knowledge_document).where(
                            knowledge_document.c.document_id == reservation.document_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            return self._record(
                activated_row, _job_from_values(job_id, reservation.revision_id, results)
            )

    async def record_upload_promotion(
        self, reservation: UploadReservation, original: ArtifactLocator
    ) -> ArtifactLocator:
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(knowledge_document)
                        .where(knowledge_document.c.document_id == reservation.document_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                row is None
                or row["dedupe_key"] != reservation.dedupe_key
                or row["deleted_at"] is not None
            ):
                raise JobLeaseLost(reservation.reservation_id)
            if row["activated_at"] is not None:
                persisted_revision = await session.scalar(
                    select(knowledge_document_revision.c.original_blob_locator).where(
                        knowledge_document_revision.c.revision_id == row["current_revision_id"]
                    )
                )
                if not isinstance(persisted_revision, str):
                    raise RuntimeError("active document is missing its original artifact fact")
                return ArtifactLocator(persisted_revision)
            persisted = row["promoted_blob_locator"]
            if persisted is not None:
                return ArtifactLocator(cast(str, persisted))
            now = await _database_now(session)
            await session.execute(
                update(knowledge_document)
                .where(
                    knowledge_document.c.document_id == reservation.document_id,
                    knowledge_document.c.promoted_blob_locator.is_(None),
                )
                .values(promoted_blob_locator=str(original), updated_at=now)
            )
            return original

    async def abandon_upload(self, reservation_id: str, owner_token: str) -> None:
        async with self._sessions() as session, session.begin():
            now = await _database_now(session)
            await session.execute(
                update(knowledge_document)
                .where(
                    knowledge_document.c.document_id == reservation_id,
                    knowledge_document.c.reservation_owner_token == owner_token,
                    knowledge_document.c.activated_at.is_(None),
                )
                .values(reservation_expires_at=now, updated_at=now)
            )

    async def claim_upload_recoveries(
        self,
        *,
        worker_id: str,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[UploadRecovery, ...]:
        if not worker_id or not timedelta(0) < lease_duration <= MAX_RESERVATION_LEASE:
            raise ValueError("upload recovery requires a worker and bounded positive lease")
        if limit <= 0:
            return ()
        async with self._sessions() as session, session.begin():
            selection_now = await _database_now(session)
            rows = list(
                (
                    await session.execute(
                        select(knowledge_document)
                        .where(
                            knowledge_document.c.staging_blob_locator.is_not(None),
                            knowledge_document.c.deleted_at.is_(None),
                            knowledge_document.c.reservation_expires_at <= selection_now,
                        )
                        .order_by(
                            knowledge_document.c.reservation_expires_at,
                            knowledge_document.c.document_id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).mappings()
            )
            database_now = await _database_now(session)
            claimed: list[UploadRecovery] = []
            for row in rows:
                if cast(datetime, row["reservation_expires_at"]) > database_now:
                    continue
                token = uuid4().hex
                expires_at = database_now + lease_duration
                result = await session.execute(
                    update(knowledge_document)
                    .where(
                        knowledge_document.c.document_id == row["document_id"],
                        knowledge_document.c.staging_blob_locator == row["staging_blob_locator"],
                        knowledge_document.c.reservation_expires_at <= database_now,
                    )
                    .values(
                        reservation_owner_token=token,
                        reservation_expires_at=expires_at,
                        updated_at=database_now,
                    )
                )
                if result.rowcount != 1:
                    continue
                document_id = cast(str, row["document_id"])
                parser_version = cast(str, row["reservation_parser_version"])
                revision_id = revision_id_for(
                    DocumentId(document_id),
                    cast(str, row["source_content_hash"]),
                    parser_version,
                )
                reservation = UploadReservation(
                    state=ReservationState.OWNED,
                    reservation_id=document_id,
                    owner_token=token,
                    document_id=document_id,
                    revision_id=revision_id,
                    dedupe_key=cast(str, row["dedupe_key"]),
                    document=None,
                    parser_version=parser_version,
                    chunker_version=cast(str, row["reservation_chunker_version"]),
                    pipeline_version=cast(str, row["reservation_pipeline_version"]),
                    staging_key=cast(str, row["staging_blob_locator"]),
                    promoted_locator=ArtifactLocator(row["promoted_blob_locator"])
                    if row["promoted_blob_locator"] is not None
                    else None,
                    expires_at=expires_at,
                )
                claimed.append(
                    UploadRecovery(
                        reservation=reservation,
                        activated=row["activated_at"] is not None,
                    )
                )
            return tuple(claimed)

    async def complete_upload_cleanup(self, reservation_id: str, owner_token: str) -> None:
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(knowledge_document)
                        .where(knowledge_document.c.document_id == reservation_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["activated_at"] is None:
                raise JobLeaseLost(reservation_id)
            if row["staging_blob_locator"] is None:
                return
            if row["reservation_owner_token"] != owner_token:
                return
            now = await _database_now(session)
            result = await session.execute(
                update(knowledge_document)
                .where(
                    knowledge_document.c.document_id == reservation_id,
                    knowledge_document.c.activated_at.is_not(None),
                    knowledge_document.c.reservation_owner_token == owner_token,
                )
                .values(
                    staging_blob_locator=None,
                    promoted_blob_locator=None,
                    reservation_owner_token=None,
                    reservation_expires_at=None,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise JobLeaseLost(reservation_id)

    async def list_documents(self, cursor: DocumentCursor | None, limit: int) -> DocumentRecordPage:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("document page limit must be between 1 and 50")
        position = _decode_cursor(cursor) if cursor is not None else None
        async with self._sessions() as session:
            statement = select(knowledge_document).where(
                knowledge_document.c.activated_at.is_not(None),
                knowledge_document.c.deleted_at.is_(None),
                knowledge_document.c.status != DocumentState.DELETING.value,
            )
            if position is not None:
                created_at, document_id = position
                statement = statement.where(
                    or_(
                        knowledge_document.c.created_at < created_at,
                        and_(
                            knowledge_document.c.created_at == created_at,
                            knowledge_document.c.document_id < document_id,
                        ),
                    )
                )
            rows = list(
                (
                    await session.execute(
                        statement.order_by(
                            knowledge_document.c.created_at.desc(),
                            knowledge_document.c.document_id.desc(),
                        ).limit(limit + 1)
                    )
                ).mappings()
            )
            records = tuple([await self._record_for_row(session, row) for row in rows[:limit]])
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = _encode_cursor(
                cast(datetime, last["created_at"]), cast(str, last["document_id"])
            )
        return DocumentRecordPage(records, next_cursor)

    async def get_document(
        self, document_id: DocumentId, *, include_deleting: bool = False
    ) -> DocumentRecord | None:
        async with self._sessions() as session:
            statement = select(knowledge_document).where(
                knowledge_document.c.document_id == document_id,
                knowledge_document.c.activated_at.is_not(None),
                knowledge_document.c.deleted_at.is_(None),
            )
            if not include_deleting:
                statement = statement.where(
                    knowledge_document.c.status != DocumentState.DELETING.value
                )
            row = (await session.execute(statement)).mappings().one_or_none()
            return None if row is None else await self._record_for_row(session, row)

    async def retry_failed(self, document_id: DocumentId, now: datetime) -> IngestionJob:
        del now
        async with self._sessions() as session, session.begin():
            candidate_revision = await session.scalar(
                select(knowledge_document.c.current_revision_id).where(
                    knowledge_document.c.document_id == document_id,
                    knowledge_document.c.status == DocumentState.FAILED.value,
                    knowledge_document.c.activated_at.is_not(None),
                    knowledge_document.c.deleted_at.is_(None),
                )
            )
            if not isinstance(candidate_revision, str):
                raise RetryNotAllowed(str(document_id))
            job_row = (
                (
                    await session.execute(
                        select(knowledge_ingestion_job)
                        .where(
                            knowledge_ingestion_job.c.revision_id == candidate_revision,
                            knowledge_ingestion_job.c.kind == JobKind.INGESTION.value,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            document_row = (
                (
                    await session.execute(
                        select(knowledge_document)
                        .where(
                            knowledge_document.c.document_id == document_id,
                            knowledge_document.c.current_revision_id == candidate_revision,
                            knowledge_document.c.status == DocumentState.FAILED.value,
                            knowledge_document.c.activated_at.is_not(None),
                            knowledge_document.c.deleted_at.is_(None),
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                job_row is None
                or job_row["status"] != JobState.FAILED.value
                or document_row is None
            ):
                raise RetryNotAllowed(str(document_id))
            database_now = await _database_now(session)
            results = list(deserialize_stage_results(job_row["stage_results_json"]))
            first_incomplete = next(
                result.stage for result in results if result.state is not StageState.COMPLETED
            )
            results = [
                result
                if result.state is StageState.COMPLETED
                else StageResult(result.stage, StageState.PENDING)
                for result in results
            ]
            attempt = cast(int, job_row["attempt"]) + 1
            await session.execute(
                update(knowledge_ingestion_job)
                .where(
                    knowledge_ingestion_job.c.job_id == job_row["job_id"],
                    knowledge_ingestion_job.c.status == JobState.FAILED.value,
                )
                .values(
                    attempt=attempt,
                    status=JobState.PENDING.value,
                    stage=first_incomplete.value,
                    stage_results_json=serialize_stage_results(tuple(results)),
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                    next_attempt_at=database_now,
                    error_code=None,
                    error_summary=None,
                    updated_at=database_now,
                    completed_at=None,
                )
            )
            await session.execute(
                update(knowledge_document)
                .where(
                    knowledge_document.c.document_id == document_id,
                    knowledge_document.c.current_revision_id == candidate_revision,
                    knowledge_document.c.status == DocumentState.FAILED.value,
                )
                .values(
                    status=DocumentState.QUEUED.value,
                    stage=first_incomplete.value,
                    error_code=None,
                    error_summary=None,
                    updated_at=database_now,
                )
            )
            await self._insert_job_outbox(
                session,
                document_id=str(document_id),
                job_id=cast(str, job_row["job_id"]),
                attempt=attempt,
                message_type="knowledge.ingestion_requested",
                now=database_now,
            )
            return IngestionJob(
                job_id=cast(str, job_row["job_id"]),
                revision_id=cast(str, job_row["revision_id"]),
                kind=JobKind.INGESTION,
                attempt=attempt,
                status=JobState.PENDING,
                stage=first_incomplete,
                stages=tuple(results),
            )

    async def request_delete(self, document_id: DocumentId, now: datetime) -> IngestionJob:
        del now
        async with self._sessions() as session, session.begin():
            candidate = (
                (
                    await session.execute(
                        select(knowledge_document).where(
                            knowledge_document.c.document_id == document_id,
                            knowledge_document.c.activated_at.is_not(None),
                            knowledge_document.c.deleted_at.is_(None),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if candidate is None:
                raise RetryNotAllowed(str(document_id))
            revision_id = cast(str, candidate["current_revision_id"])
            job_rows = list(
                (
                    await session.execute(
                        select(knowledge_ingestion_job)
                        .where(knowledge_ingestion_job.c.revision_id == revision_id)
                        .order_by(knowledge_ingestion_job.c.kind)
                        .with_for_update()
                    )
                ).mappings()
            )
            document_row = (
                (
                    await session.execute(
                        select(knowledge_document)
                        .where(
                            knowledge_document.c.document_id == document_id,
                            knowledge_document.c.current_revision_id == revision_id,
                            knowledge_document.c.activated_at.is_not(None),
                            knowledge_document.c.deleted_at.is_(None),
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if document_row is None:
                raise RetryNotAllowed(str(document_id))
            database_now = await _database_now(session)
            ingestion = next(
                (row for row in job_rows if row["kind"] == JobKind.INGESTION.value), None
            )
            if ingestion is not None and ingestion["status"] != JobState.COMPLETED.value:
                lease_is_active = (
                    ingestion["status"] == JobState.PROCESSING.value
                    and ingestion["lease_token"] is not None
                    and ingestion["lease_until"] is not None
                    and cast(datetime, ingestion["lease_until"]) > database_now
                )
                cancelled_barrier_exists = (
                    ingestion["status"] == JobState.CANCELLED.value
                    and ingestion["lease_token"] is not None
                    and ingestion["lease_until"] is not None
                )
                if not cancelled_barrier_exists:
                    retained_until = None
                    if lease_is_active:
                        retained_until = max(
                            cast(datetime, ingestion["lease_until"]),
                            database_now + CANCELLED_OWNER_SETTLEMENT_LEASE,
                        )
                    await session.execute(
                        update(knowledge_ingestion_job)
                        .where(
                            knowledge_ingestion_job.c.job_id == ingestion["job_id"],
                            knowledge_ingestion_job.c.status != JobState.COMPLETED.value,
                        )
                        .values(
                            status=JobState.CANCELLED.value,
                            lease_owner=ingestion["lease_owner"] if lease_is_active else None,
                            lease_token=ingestion["lease_token"] if lease_is_active else None,
                            lease_until=retained_until,
                            updated_at=database_now,
                            completed_at=None if lease_is_active else database_now,
                        )
                    )
            await session.execute(
                update(knowledge_document)
                .where(knowledge_document.c.document_id == document_id)
                .values(
                    status=DocumentState.DELETING.value,
                    stage=JobStage.STORED.value,
                    error_code=None,
                    error_summary=None,
                    updated_at=database_now,
                )
            )
            existing = next(
                (row for row in job_rows if row["kind"] == JobKind.DELETION.value), None
            )
            if existing is not None:
                if existing["status"] == JobState.FAILED.value:
                    results = list(deserialize_stage_results(existing["stage_results_json"]))
                    first_incomplete = next(
                        result.stage
                        for result in results
                        if result.state is not StageState.COMPLETED
                    )
                    results = [
                        result
                        if result.state is StageState.COMPLETED
                        else StageResult(result.stage, StageState.PENDING)
                        for result in results
                    ]
                    attempt = cast(int, existing["attempt"]) + 1
                    await session.execute(
                        update(knowledge_ingestion_job)
                        .where(
                            knowledge_ingestion_job.c.job_id == existing["job_id"],
                            knowledge_ingestion_job.c.status == JobState.FAILED.value,
                        )
                        .values(
                            attempt=attempt,
                            status=JobState.PENDING.value,
                            stage=first_incomplete.value,
                            stage_results_json=serialize_stage_results(tuple(results)),
                            lease_owner=None,
                            lease_token=None,
                            lease_until=None,
                            next_attempt_at=database_now,
                            error_code=None,
                            error_summary=None,
                            updated_at=database_now,
                        )
                    )
                    await self._insert_job_outbox(
                        session,
                        document_id=str(document_id),
                        job_id=cast(str, existing["job_id"]),
                        attempt=attempt,
                        message_type="knowledge.deletion_requested",
                        now=database_now,
                    )
                    return IngestionJob(
                        job_id=cast(str, existing["job_id"]),
                        revision_id=cast(str, existing["revision_id"]),
                        kind=JobKind.DELETION,
                        attempt=attempt,
                        status=JobState.PENDING,
                        stage=first_incomplete,
                        stages=tuple(results),
                    )
                return _job_from_row(existing)
            job_id = _job_id()
            initial_results = initial_stage_results(database_now)
            await session.execute(
                insert(knowledge_ingestion_job).values(
                    job_id=job_id,
                    revision_id=revision_id,
                    kind=JobKind.DELETION.value,
                    attempt=1,
                    status=JobState.PENDING.value,
                    stage=JobStage.STORED.value,
                    stage_results_json=serialize_stage_results(initial_results),
                    next_attempt_at=database_now,
                    created_at=database_now,
                    updated_at=database_now,
                )
            )
            await self._insert_job_outbox(
                session,
                document_id=str(document_id),
                job_id=job_id,
                attempt=1,
                message_type="knowledge.deletion_requested",
                now=database_now,
            )
            return IngestionJob(
                job_id=job_id,
                revision_id=revision_id,
                kind=JobKind.DELETION,
                attempt=1,
                status=JobState.PENDING,
                stage=JobStage.STORED,
                stages=initial_results,
            )

    async def claim_jobs(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[ClaimedIngestionJob, ...]:
        del now
        if limit <= 0:
            return ()
        _validate_job_lease(worker_id, lease_duration)
        async with self._sessions() as session, session.begin():
            selection_now = await _database_now(session)
            cancelled_ingestion = knowledge_ingestion_job.alias("cancelled_ingestion")
            active_cancelled_owner = exists(
                select(1).where(
                    cancelled_ingestion.c.revision_id == knowledge_ingestion_job.c.revision_id,
                    cancelled_ingestion.c.kind == JobKind.INGESTION.value,
                    cancelled_ingestion.c.status == JobState.CANCELLED.value,
                    cancelled_ingestion.c.lease_token.is_not(None),
                    cancelled_ingestion.c.lease_until > selection_now,
                )
            )
            compatible_document_for_update = exists(
                select(1)
                .select_from(
                    knowledge_document_revision.join(
                        knowledge_document,
                        knowledge_document_revision.c.document_id
                        == knowledge_document.c.document_id,
                    )
                )
                .where(
                    knowledge_document_revision.c.revision_id
                    == knowledge_ingestion_job.c.revision_id,
                    knowledge_document.c.deleted_at.is_(None),
                    or_(
                        and_(
                            knowledge_ingestion_job.c.kind == JobKind.INGESTION.value,
                            knowledge_document.c.status != DocumentState.DELETING.value,
                        ),
                        and_(
                            knowledge_ingestion_job.c.kind == JobKind.DELETION.value,
                            knowledge_document.c.status == DocumentState.DELETING.value,
                        ),
                    ),
                )
            )
            initially_claimable = or_(
                and_(
                    knowledge_ingestion_job.c.status == JobState.PENDING.value,
                    knowledge_ingestion_job.c.next_attempt_at <= selection_now,
                ),
                and_(
                    knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                    knowledge_ingestion_job.c.lease_until <= selection_now,
                ),
            )
            compatible_document = exists(
                select(1)
                .select_from(
                    knowledge_document_revision.join(
                        knowledge_document,
                        knowledge_document_revision.c.document_id
                        == knowledge_document.c.document_id,
                    )
                )
                .where(
                    knowledge_document_revision.c.revision_id
                    == knowledge_ingestion_job.c.revision_id,
                    knowledge_document.c.deleted_at.is_(None),
                    or_(
                        and_(
                            knowledge_ingestion_job.c.kind == JobKind.INGESTION.value,
                            knowledge_document.c.status != DocumentState.DELETING.value,
                        ),
                        and_(
                            knowledge_ingestion_job.c.kind == JobKind.DELETION.value,
                            knowledge_document.c.status == DocumentState.DELETING.value,
                            ~active_cancelled_owner,
                        ),
                    ),
                )
            )
            rows = list(
                (
                    await session.execute(
                        select(knowledge_ingestion_job)
                        .where(initially_claimable, compatible_document)
                        .order_by(
                            knowledge_ingestion_job.c.created_at,
                            knowledge_ingestion_job.c.job_id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).mappings()
            )
            database_now = await _database_now(session)
            claimed: list[ClaimedIngestionJob] = []
            for row in rows:
                row_is_claimable = (
                    row["status"] == JobState.PENDING.value
                    and cast(datetime, row["next_attempt_at"]) <= database_now
                ) or (
                    row["status"] == JobState.PROCESSING.value
                    and cast(datetime, row["lease_until"]) <= database_now
                )
                if not row_is_claimable:
                    continue
                token = uuid4().hex
                lease_until = database_now + lease_duration
                freshly_claimable = or_(
                    and_(
                        knowledge_ingestion_job.c.status == JobState.PENDING.value,
                        knowledge_ingestion_job.c.next_attempt_at <= database_now,
                    ),
                    and_(
                        knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                        knowledge_ingestion_job.c.lease_until <= database_now,
                    ),
                )
                result = await session.execute(
                    update(knowledge_ingestion_job)
                    .where(
                        knowledge_ingestion_job.c.job_id == row["job_id"],
                        freshly_claimable,
                        compatible_document_for_update,
                    )
                    .values(
                        status=JobState.PROCESSING.value,
                        lease_owner=worker_id,
                        lease_token=token,
                        lease_until=lease_until,
                        updated_at=database_now,
                    )
                )
                if result.rowcount != 1:
                    continue
                job = _job_from_row(row)
                claimed.append(
                    ClaimedIngestionJob(
                        job_id=job.job_id,
                        revision_id=job.revision_id,
                        kind=job.kind,
                        attempt=job.attempt,
                        status=JobState.PROCESSING,
                        stage=job.stage,
                        stages=job.stages,
                        lease_owner=worker_id,
                        lease_token=token,
                        lease_until=lease_until,
                    )
                )
            return tuple(claimed)

    async def settle_cancelled_job(
        self,
        job_id: str,
        lease_token: str,
        expected_stage: JobStage,
        settled_at: datetime,
    ) -> None:
        del settled_at
        async with self._sessions() as session, session.begin():
            database_now = await _database_now(session)
            result = await session.execute(
                update(knowledge_ingestion_job)
                .where(
                    knowledge_ingestion_job.c.job_id == job_id,
                    knowledge_ingestion_job.c.kind == JobKind.INGESTION.value,
                    knowledge_ingestion_job.c.status == JobState.CANCELLED.value,
                    knowledge_ingestion_job.c.lease_token == lease_token,
                    knowledge_ingestion_job.c.stage == expected_stage.value,
                )
                .values(
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                    completed_at=database_now,
                    updated_at=database_now,
                )
            )
            if result.rowcount != 1:
                self._raise_lease_lost(job_id)

    async def renew_cancelled_job_settlement(
        self,
        job_id: str,
        lease_token: str,
        expected_stage: JobStage,
        now: datetime,
        lease_duration: timedelta,
    ) -> None:
        del now
        _validate_job_lease("cancelled-owner", lease_duration)
        async with self._sessions() as session, session.begin():
            database_now = await _database_now(session)
            result = await session.execute(
                update(knowledge_ingestion_job)
                .where(
                    knowledge_ingestion_job.c.job_id == job_id,
                    knowledge_ingestion_job.c.kind == JobKind.INGESTION.value,
                    knowledge_ingestion_job.c.status == JobState.CANCELLED.value,
                    knowledge_ingestion_job.c.lease_token == lease_token,
                    knowledge_ingestion_job.c.stage == expected_stage.value,
                    knowledge_ingestion_job.c.lease_until > database_now,
                )
                .values(
                    lease_until=func.greatest(
                        knowledge_ingestion_job.c.lease_until,
                        database_now + lease_duration,
                    ),
                    updated_at=database_now,
                )
            )
            if result.rowcount != 1:
                self._raise_lease_lost(job_id)

    async def renew_lease(
        self,
        job_id: str,
        lease_token: str,
        expected_stage: JobStage,
        now: datetime,
        lease_duration: timedelta,
    ) -> None:
        del now
        _validate_job_lease("lease-owner", lease_duration)
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(knowledge_ingestion_job)
                        .where(
                            knowledge_ingestion_job.c.job_id == job_id,
                            knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                            knowledge_ingestion_job.c.lease_token == lease_token,
                            knowledge_ingestion_job.c.stage == expected_stage.value,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                self._raise_lease_lost(job_id)
            database_now = await _database_now(session)
            if row["lease_until"] is None or row["lease_until"] <= database_now:
                self._raise_lease_lost(job_id)
            result = await session.execute(
                update(knowledge_ingestion_job)
                .where(
                    knowledge_ingestion_job.c.job_id == job_id,
                    knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                    knowledge_ingestion_job.c.lease_token == lease_token,
                    knowledge_ingestion_job.c.stage == expected_stage.value,
                    knowledge_ingestion_job.c.lease_until > database_now,
                )
                .values(
                    lease_until=database_now + lease_duration,
                    updated_at=database_now,
                )
            )
            if result.rowcount != 1:
                self._raise_lease_lost(job_id)

    async def checkpoint(self, checkpoint: JobCheckpoint) -> None:
        await self._commit_stage(
            JobStageCommit(
                job_id=checkpoint.job_id,
                lease_token=checkpoint.lease_token,
                expected_stage=checkpoint.expected_stage,
                completed_at=checkpoint.completed_at,
            ),
            require_stage_facts=False,
        )

    async def commit_stage(self, commit: JobStageCommit) -> None:
        await self._commit_stage(commit, require_stage_facts=True)

    async def _commit_stage(
        self,
        commit: JobStageCommit,
        *,
        require_stage_facts: bool,
    ) -> None:
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(knowledge_ingestion_job)
                        .where(
                            knowledge_ingestion_job.c.job_id == commit.job_id,
                            knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                            knowledge_ingestion_job.c.lease_token == commit.lease_token,
                            knowledge_ingestion_job.c.stage == commit.expected_stage.value,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                self._raise_lease_lost(commit.job_id)
            database_now = await _database_now(session)
            if row["lease_until"] is None or row["lease_until"] <= database_now:
                self._raise_lease_lost(commit.job_id)
            if require_stage_facts and row["kind"] == JobKind.INGESTION.value:
                required_fact = {
                    JobStage.PARSING: commit.normalized_locator,
                    JobStage.CHUNKING: commit.chunks_locator,
                    JobStage.EMBEDDING: commit.embeddings_locator,
                }.get(commit.expected_stage, True)
                if required_fact is None:
                    raise ValueError("ingestion stage commit is missing its durable artifact")
                if commit.expected_stage is JobStage.CHUNKING and not commit.manifest:
                    raise ValueError("chunking stage commit requires the complete manifest")
            results = list(deserialize_stage_results(row["stage_results_json"]))
            index = list(JobStage).index(commit.expected_stage)
            results[index] = StageResult(
                commit.expected_stage,
                StageState.COMPLETED,
                _utc_naive(commit.completed_at),
            )
            is_complete = commit.expected_stage is JobStage.READY
            next_stage = JobStage.READY if is_complete else list(JobStage)[index + 1]
            if not is_complete:
                results[index + 1] = StageResult(next_stage, StageState.PROCESSING)
            revision_values: dict[str, str] = {}
            if commit.normalized_locator is not None:
                revision_values["normalized_blob_locator"] = str(commit.normalized_locator)
            if commit.chunks_locator is not None:
                revision_values["chunks_blob_locator"] = str(commit.chunks_locator)
            if commit.embeddings_locator is not None:
                revision_values["embeddings_blob_locator"] = str(commit.embeddings_locator)
            if revision_values:
                updated_revision = await session.execute(
                    update(knowledge_document_revision)
                    .where(knowledge_document_revision.c.revision_id == row["revision_id"])
                    .values(**revision_values)
                )
                if updated_revision.rowcount != 1:
                    raise RuntimeError("job revision disappeared during stage commit")
            if commit.manifest:
                if tuple(item.ordinal for item in commit.manifest) != tuple(
                    range(len(commit.manifest))
                ):
                    raise ValueError("manifest ordinals must be contiguous")
                existing_manifest = list(
                    (
                        await session.execute(
                            select(knowledge_chunk_manifest)
                            .where(knowledge_chunk_manifest.c.revision_id == row["revision_id"])
                            .order_by(knowledge_chunk_manifest.c.ordinal)
                        )
                    ).mappings()
                )
                if existing_manifest:
                    if _manifest_from_rows(existing_manifest) != commit.manifest:
                        raise RuntimeError("persisted manifest differs from replayed stage output")
                else:
                    await session.execute(
                        insert(knowledge_chunk_manifest),
                        [
                            {
                                "chunk_id": item.chunk_id,
                                "logical_chunk_id": item.logical_chunk_id,
                                "revision_id": row["revision_id"],
                                "ordinal": item.ordinal,
                                "root_id": item.root_id,
                                "parent_id": item.parent_id,
                                "anchor_json": json.loads(item.anchor_json),
                                "chunk_content_hash": item.chunk_content_hash,
                                "embedding_model_version": item.embedding_model_version,
                                "index_version": item.index_version,
                                "created_at": database_now,
                            }
                            for item in commit.manifest
                        ],
                    )
            result = await session.execute(
                update(knowledge_ingestion_job)
                .where(
                    knowledge_ingestion_job.c.job_id == commit.job_id,
                    knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                    knowledge_ingestion_job.c.lease_token == commit.lease_token,
                    knowledge_ingestion_job.c.lease_until > database_now,
                    knowledge_ingestion_job.c.stage == commit.expected_stage.value,
                )
                .values(
                    status=JobState.COMPLETED.value if is_complete else JobState.PROCESSING.value,
                    stage=next_stage.value,
                    stage_results_json=serialize_stage_results(tuple(results)),
                    lease_owner=None if is_complete else row["lease_owner"],
                    lease_token=None if is_complete else commit.lease_token,
                    lease_until=None if is_complete else row["lease_until"],
                    updated_at=database_now,
                    completed_at=database_now if is_complete else None,
                )
            )
            if result.rowcount != 1:
                self._raise_lease_lost(commit.job_id)
            revision = knowledge_document_revision.alias("checkpoint_revision")
            document_id = await session.scalar(
                select(revision.c.document_id).where(revision.c.revision_id == row["revision_id"])
            )
            if row["kind"] == JobKind.DELETION.value and is_complete:
                await session.execute(
                    delete(knowledge_chunk_manifest).where(
                        knowledge_chunk_manifest.c.revision_id == row["revision_id"]
                    )
                )
                await session.execute(
                    update(knowledge_document)
                    .where(
                        knowledge_document.c.document_id == document_id,
                        knowledge_document.c.status == DocumentState.DELETING.value,
                    )
                    .values(dedupe_key=None, deleted_at=database_now, updated_at=database_now)
                )
            elif row["kind"] == JobKind.INGESTION.value:
                manifest_count = await session.scalar(
                    select(func.count())
                    .select_from(knowledge_chunk_manifest)
                    .where(knowledge_chunk_manifest.c.revision_id == row["revision_id"])
                )
                if (
                    require_stage_facts
                    and is_complete
                    and (commit.chunk_count is None or commit.chunk_count != manifest_count)
                ):
                    raise ValueError("ready stage chunk count must match the durable manifest")
                ready_chunk_count = (
                    cast(int, manifest_count) if commit.chunk_count is None else commit.chunk_count
                )
                await session.execute(
                    update(knowledge_document)
                    .where(
                        knowledge_document.c.document_id == document_id,
                        knowledge_document.c.status != DocumentState.DELETING.value,
                    )
                    .values(
                        status=DocumentState.READY.value
                        if is_complete
                        else DocumentState.PROCESSING.value,
                        stage=next_stage.value,
                        chunk_count=(
                            ready_chunk_count if is_complete else knowledge_document.c.chunk_count
                        ),
                        updated_at=database_now,
                    )
                )

    async def load_ingestion_work(
        self,
        job_id: str,
        lease_token: str,
        expected_stage: JobStage,
    ) -> IngestionWork:
        async with self._sessions() as session, session.begin():
            job = (
                (
                    await session.execute(
                        select(knowledge_ingestion_job)
                        .where(
                            knowledge_ingestion_job.c.job_id == job_id,
                            knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                            knowledge_ingestion_job.c.lease_token == lease_token,
                            knowledge_ingestion_job.c.stage == expected_stage.value,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if job is None:
                self._raise_lease_lost(job_id)
            database_now = await _database_now(session)
            if job["lease_until"] is None or job["lease_until"] <= database_now:
                self._raise_lease_lost(job_id)
            revision = (
                (
                    await session.execute(
                        select(knowledge_document_revision).where(
                            knowledge_document_revision.c.revision_id == job["revision_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
            document = (
                (
                    await session.execute(
                        select(knowledge_document).where(
                            knowledge_document.c.document_id == revision["document_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
            compatible = (
                job["kind"] == JobKind.INGESTION.value
                and document["status"] != DocumentState.DELETING.value
            ) or (
                job["kind"] == JobKind.DELETION.value
                and document["status"] == DocumentState.DELETING.value
            )
            if not compatible or document["deleted_at"] is not None:
                self._raise_lease_lost(job_id)
            manifest_rows = list(
                (
                    await session.execute(
                        select(knowledge_chunk_manifest)
                        .where(knowledge_chunk_manifest.c.revision_id == revision["revision_id"])
                        .order_by(knowledge_chunk_manifest.c.ordinal)
                    )
                ).mappings()
            )
            return IngestionWork(
                job_id=job_id,
                lease_token=lease_token,
                kind=JobKind(job["kind"]),
                stage=expected_stage,
                document_id=document["document_id"],
                revision_id=revision["revision_id"],
                filename=document["filename"],
                media_type=document["media_type"],
                source_content_hash=revision["source_content_hash"],
                original_locator=ArtifactLocator(revision["original_blob_locator"]),
                normalized_locator=_optional_locator(revision["normalized_blob_locator"]),
                chunks_locator=_optional_locator(revision["chunks_blob_locator"]),
                embeddings_locator=_optional_locator(revision["embeddings_blob_locator"]),
                parser_version=revision["parser_version"],
                chunker_version=revision["chunker_version"],
                pipeline_version=revision["pipeline_version"],
                manifest=_manifest_from_rows(manifest_rows),
            )

    async def retry_job(self, retry: JobRetry) -> None:
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(knowledge_ingestion_job)
                        .where(
                            knowledge_ingestion_job.c.job_id == retry.job_id,
                            knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                            knowledge_ingestion_job.c.lease_token == retry.lease_token,
                            knowledge_ingestion_job.c.stage == retry.expected_stage.value,
                            knowledge_ingestion_job.c.kind == JobKind.DELETION.value,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                self._raise_lease_lost(retry.job_id)
            database_now = await _database_now(session)
            if row["lease_until"] is None or row["lease_until"] <= database_now:
                self._raise_lease_lost(retry.job_id)
            results = list(deserialize_stage_results(row["stage_results_json"]))
            index = list(JobStage).index(retry.expected_stage)
            results[index] = StageResult(
                retry.expected_stage,
                StageState.FAILED,
                database_now,
                retry.error_code,
            )
            result = await session.execute(
                update(knowledge_ingestion_job)
                .where(
                    knowledge_ingestion_job.c.job_id == retry.job_id,
                    knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                    knowledge_ingestion_job.c.lease_token == retry.lease_token,
                    knowledge_ingestion_job.c.lease_until > database_now,
                    knowledge_ingestion_job.c.stage == retry.expected_stage.value,
                )
                .values(
                    status=JobState.PENDING.value,
                    stage_results_json=serialize_stage_results(tuple(results)),
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                    next_attempt_at=database_now,
                    error_code=retry.error_code,
                    error_summary=SAFE_ERROR_SUMMARIES[retry.error_code],
                    updated_at=database_now,
                )
            )
            if result.rowcount != 1:
                self._raise_lease_lost(retry.job_id)

    async def fail_job(self, failure: JobFailure) -> None:
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(knowledge_ingestion_job)
                        .where(
                            knowledge_ingestion_job.c.job_id == failure.job_id,
                            knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                            knowledge_ingestion_job.c.lease_token == failure.lease_token,
                            knowledge_ingestion_job.c.stage == failure.expected_stage.value,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                self._raise_lease_lost(failure.job_id)
            database_now = await _database_now(session)
            if row["lease_until"] is None or row["lease_until"] <= database_now:
                self._raise_lease_lost(failure.job_id)
            summary = SAFE_ERROR_SUMMARIES[failure.error_code]
            results = list(deserialize_stage_results(row["stage_results_json"]))
            index = list(JobStage).index(failure.expected_stage)
            results[index] = StageResult(
                failure.expected_stage,
                StageState.FAILED,
                _utc_naive(failure.failed_at),
                failure.error_code,
            )
            result = await session.execute(
                update(knowledge_ingestion_job)
                .where(
                    knowledge_ingestion_job.c.job_id == failure.job_id,
                    knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                    knowledge_ingestion_job.c.lease_token == failure.lease_token,
                    knowledge_ingestion_job.c.lease_until > database_now,
                    knowledge_ingestion_job.c.stage == failure.expected_stage.value,
                )
                .values(
                    status=JobState.FAILED.value,
                    stage_results_json=serialize_stage_results(tuple(results)),
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                    error_code=failure.error_code,
                    error_summary=summary,
                    updated_at=database_now,
                )
            )
            if result.rowcount != 1:
                self._raise_lease_lost(failure.job_id)
            document_id = await session.scalar(
                select(knowledge_document_revision.c.document_id).where(
                    knowledge_document_revision.c.revision_id == row["revision_id"]
                )
            )
            public_values = (
                {
                    "status": DocumentState.DELETING.value,
                    "stage": failure.expected_stage.value,
                    "error_code": None,
                    "error_summary": None,
                    "updated_at": database_now,
                }
                if row["kind"] == JobKind.DELETION.value
                else {
                    "status": DocumentState.FAILED.value,
                    "stage": failure.expected_stage.value,
                    "error_code": failure.error_code,
                    "error_summary": summary,
                    "updated_at": database_now,
                }
            )
            document_update = update(knowledge_document).where(
                knowledge_document.c.document_id == document_id
            )
            if row["kind"] == JobKind.INGESTION.value:
                document_update = document_update.where(
                    knowledge_document.c.status != DocumentState.DELETING.value
                )
            await session.execute(document_update.values(**public_values))

    async def _record_for_row(self, session: AsyncSession, row: RowMapping) -> DocumentRecord:
        job_row = (
            (
                await session.execute(
                    select(knowledge_ingestion_job)
                    .where(
                        knowledge_ingestion_job.c.revision_id == row["current_revision_id"],
                        knowledge_ingestion_job.c.kind == JobKind.INGESTION.value,
                    )
                    .order_by(knowledge_ingestion_job.c.created_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one()
        )
        return self._record(row, _job_from_row(job_row))

    @staticmethod
    def _record(row: RowMapping, job: IngestionJob) -> DocumentRecord:
        return DocumentRecord(
            document_id=cast(str, row["document_id"]),
            revision_id=cast(str, row["current_revision_id"]),
            filename=cast(str, row["filename"]),
            media_type=cast(str, row["media_type"]),
            source_content_hash=cast(str, row["source_content_hash"]),
            status=DocumentState(cast(str, row["status"])),
            stage=JobStage(cast(str, row["stage"])),
            chunk_count=cast(int, row["chunk_count"]),
            error_code=cast(str | None, row["error_code"]),
            error_summary=cast(str | None, row["error_summary"]),
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
            stages=job.stages,
            job_id=job.job_id,
        )

    @staticmethod
    async def _insert_job_outbox(
        session: AsyncSession,
        *,
        document_id: str,
        job_id: str,
        attempt: int,
        message_type: str,
        now: datetime,
    ) -> None:
        identity = f"knowledge-{message_type.split('.')[1]}:{job_id}:{attempt}"
        await session.execute(
            insert(outbox).values(
                outbox_id=identity,
                command_id=identity,
                aggregate_type="knowledge_document",
                aggregate_id=document_id,
                sequence=None,
                message_type=message_type,
                status="pending",
                attempt_count=0,
                next_attempt_at=now,
                created_at=now,
            )
        )

    @staticmethod
    def _raise_lease_lost(job_id: str) -> NoReturn:
        raise JobLeaseLost(job_id)


def _job_from_values(
    job_id: str, revision_id: str, results: tuple[StageResult, ...]
) -> IngestionJob:
    return IngestionJob(
        job_id=job_id,
        revision_id=revision_id,
        kind=JobKind.INGESTION,
        attempt=1,
        status=JobState.PENDING,
        stage=JobStage.STORED,
        stages=results,
    )


def _optional_locator(value: object) -> ArtifactLocator | None:
    return ArtifactLocator(value) if isinstance(value, str) else None


def _canonical_json_object(value: object) -> str:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
        raise ValueError("snapshot JSON value must be an object")
    return json.dumps(
        decoded,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _selected_revisions(value: object) -> tuple[ReadyDocumentRevision, ...]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, list) or not 1 <= len(decoded) <= 20:
        raise ValueError("selected revisions must be a bounded array")
    rows: list[ReadyDocumentRevision] = []
    for item in decoded:
        if not isinstance(item, dict) or set(item) != {
            "documentId",
            "revisionId",
            "sourceContentHash",
        }:
            raise ValueError("selected revision shape is not closed")
        rows.append(
            ReadyDocumentRevision(
                document_id=item["documentId"],
                revision_id=item["revisionId"],
                source_content_hash=item["sourceContentHash"],
            )
        )
    result = tuple(rows)
    if result != tuple(sorted(result, key=lambda item: item.document_id)) or len(
        {item.document_id for item in result}
    ) != len(result):
        raise ValueError("selected revisions must be sorted and unique")
    return result


def _manifest_from_rows(rows: list[RowMapping]) -> tuple[ManifestChunk, ...]:
    return tuple(
        ManifestChunk(
            chunk_id=cast(str, row["chunk_id"]),
            logical_chunk_id=cast(str, row["logical_chunk_id"]),
            ordinal=cast(int, row["ordinal"]),
            root_id=cast(str, row["root_id"]),
            parent_id=cast(str | None, row["parent_id"]),
            anchor_json=json.dumps(
                row["anchor_json"],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            chunk_content_hash=cast(str, row["chunk_content_hash"]),
            embedding_model_version=cast(str, row["embedding_model_version"]),
            index_version=cast(str, row["index_version"]),
        )
        for row in rows
    )


def _mysql_error_code(error: DBAPIError) -> int | None:
    args = getattr(error.orig, "args", ())
    return args[0] if args and isinstance(args[0], int) else None


def _validate_job_lease(worker_id: str, lease_duration: timedelta) -> None:
    if not worker_id or not timedelta(0) < lease_duration <= MAX_JOB_LEASE:
        raise ValueError("job lease requires a worker and bounded positive duration")


def _encode_cursor(created_at: datetime, document_id: str) -> DocumentCursor:
    payload = json.dumps(
        {
            "createdAt": created_at.isoformat(timespec="microseconds"),
            "documentId": document_id,
            "v": "v1",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return DocumentCursor(base64.urlsafe_b64encode(payload).decode("ascii"))


def _decode_cursor(cursor: DocumentCursor) -> tuple[datetime, str]:
    if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
        raise InvalidDocumentCursor("document cursor is invalid")
    try:
        decoded = base64.b64decode(cursor, altchars=b"-_", validate=True)
        payload = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as error:
        raise InvalidDocumentCursor("document cursor is invalid") from error
    if not isinstance(payload, dict) or set(payload) != {"createdAt", "documentId", "v"}:
        raise InvalidDocumentCursor("document cursor shape is invalid")
    if payload["v"] != "v1" or not isinstance(payload["documentId"], str):
        raise InvalidDocumentCursor("document cursor version is invalid")
    document_id = payload["documentId"]
    created_at_value = payload["createdAt"]
    if re.fullmatch(r"doc_[0-9a-f]{32}", document_id) is None:
        raise InvalidDocumentCursor("document cursor position is invalid")
    if (
        not isinstance(created_at_value, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}", created_at_value) is None
    ):
        raise InvalidDocumentCursor("document cursor position is invalid")
    try:
        created_at = datetime.fromisoformat(created_at_value)
    except (TypeError, ValueError) as error:
        raise InvalidDocumentCursor("document cursor position is invalid") from error
    if (
        created_at.tzinfo is not None
        or created_at.isoformat(timespec="microseconds") != created_at_value
        or _encode_cursor(created_at, document_id) != cursor
    ):
        raise InvalidDocumentCursor("document cursor position is invalid")
    return created_at, document_id
