"""MySQL-backed durable document ledger and ingestion-job ownership."""

from __future__ import annotations

import base64
import json
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
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.knowledge.domain.documents import (
    PARSER_VERSION,
    DocumentId,
    new_document_id,
    revision_id_for,
)
from tap.modules.knowledge.ports.documents import (
    MAX_DOCUMENTS,
    SAFE_ERROR_SUMMARIES,
    ArtifactLocator,
    ClaimedIngestionJob,
    DocumentCapacityExceeded,
    DocumentCursor,
    DocumentRecord,
    DocumentRecordPage,
    DocumentState,
    IngestionJob,
    InvalidDocumentCursor,
    JobCheckpoint,
    JobFailure,
    JobKind,
    JobLeaseLost,
    JobStage,
    JobState,
    ReservationState,
    ReserveUpload,
    RetryNotAllowed,
    StageResult,
    StageState,
    UploadReservation,
    deserialize_stage_results,
    initial_stage_results,
    serialize_stage_results,
)
from tap.platform.db.schema import metadata, outbox

knowledge_document = Table(
    "knowledge_document",
    metadata,
    Column("document_id", String(64), primary_key=True),
    Column("filename", String(255), nullable=False),
    Column("media_type", String(128), nullable=False),
    Column("current_revision_id", String(128)),
    Column("source_content_hash", String(71), nullable=False),
    Column("dedupe_key", String(71)),
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

    async def reserve_upload(self, command: ReserveUpload) -> UploadReservation:
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
                        PARSER_VERSION,
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
                    )
                return UploadReservation(
                    state=ReservationState.DUPLICATE_PENDING,
                    reservation_id=document_id,
                    owner_token="",
                    document_id=document_id,
                    revision_id=revision_id,
                    dedupe_key=command.dedupe_key,
                    document=None,
                )
            if len(active_rows) >= MAX_DOCUMENTS:
                raise DocumentCapacityExceeded

            document_id = new_document_id(lambda: uuid4().hex)
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
                owner_token=_owner_token(document_id),
                document_id=document_id,
                revision_id=revision_id,
                dedupe_key=command.dedupe_key,
                document=None,
            )

    async def activate_upload(
        self, reservation: UploadReservation, original: ArtifactLocator
    ) -> DocumentRecord:
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

            now = await _database_now(session)
            await session.execute(
                insert(knowledge_document_revision).values(
                    revision_id=reservation.revision_id,
                    document_id=reservation.document_id,
                    source_content_hash=row["source_content_hash"],
                    original_blob_locator=str(original),
                    parser_version="athena-parser-v1",
                    chunker_version="athena-structure-512-v1",
                    pipeline_version="athena-ingestion-v1",
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

    async def abandon_upload(self, reservation_id: str, owner_token: str) -> None:
        if owner_token != _owner_token(reservation_id):
            return
        async with self._sessions() as session, session.begin():
            await session.execute(
                delete(knowledge_document).where(
                    knowledge_document.c.document_id == reservation_id,
                    knowledge_document.c.activated_at.is_(None),
                )
            )

    async def list_documents(self, cursor: DocumentCursor | None, limit: int) -> DocumentRecordPage:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("document page limit must be between 1 and 50")
        position = _decode_cursor(cursor) if cursor is not None else None
        async with self._sessions() as session:
            statement = select(knowledge_document).where(
                knowledge_document.c.activated_at.is_not(None),
                knowledge_document.c.deleted_at.is_(None),
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
            document_row = (
                (
                    await session.execute(
                        select(knowledge_document)
                        .where(knowledge_document.c.document_id == document_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if document_row is None or document_row["status"] != DocumentState.FAILED.value:
                raise RetryNotAllowed(str(document_id))
            job_row = (
                (
                    await session.execute(
                        select(knowledge_ingestion_job)
                        .where(
                            knowledge_ingestion_job.c.revision_id
                            == document_row["current_revision_id"],
                            knowledge_ingestion_job.c.kind == JobKind.INGESTION.value,
                            knowledge_ingestion_job.c.status == JobState.FAILED.value,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if job_row is None:
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
                .where(knowledge_document.c.document_id == document_id)
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
            document_row = (
                (
                    await session.execute(
                        select(knowledge_document)
                        .where(
                            knowledge_document.c.document_id == document_id,
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
            existing = (
                (
                    await session.execute(
                        select(knowledge_ingestion_job).where(
                            knowledge_ingestion_job.c.revision_id
                            == document_row["current_revision_id"],
                            knowledge_ingestion_job.c.kind == JobKind.DELETION.value,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["status"] == JobState.FAILED.value:
                    database_now = await _database_now(session)
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
            database_now = await _database_now(session)
            job_id = _job_id()
            initial_results = initial_stage_results(database_now)
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
            await session.execute(
                insert(knowledge_ingestion_job).values(
                    job_id=job_id,
                    revision_id=document_row["current_revision_id"],
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
                revision_id=cast(str, document_row["current_revision_id"]),
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
        if not worker_id or lease_duration <= timedelta(0):
            raise ValueError("job claim requires a worker and positive lease")
        async with self._sessions() as session, session.begin():
            database_now = await _database_now(session)
            claimable = or_(
                and_(
                    knowledge_ingestion_job.c.status == JobState.PENDING.value,
                    knowledge_ingestion_job.c.next_attempt_at <= database_now,
                ),
                and_(
                    knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                    knowledge_ingestion_job.c.lease_until <= database_now,
                ),
            )
            rows = list(
                (
                    await session.execute(
                        select(knowledge_ingestion_job)
                        .where(claimable)
                        .order_by(
                            knowledge_ingestion_job.c.created_at,
                            knowledge_ingestion_job.c.job_id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).mappings()
            )
            claimed: list[ClaimedIngestionJob] = []
            for row in rows:
                token = uuid4().hex
                lease_until = database_now + lease_duration
                result = await session.execute(
                    update(knowledge_ingestion_job)
                    .where(knowledge_ingestion_job.c.job_id == row["job_id"], claimable)
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

    async def renew_lease(
        self,
        job_id: str,
        lease_token: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> None:
        del now
        async with self._sessions() as session, session.begin():
            database_now = await _database_now(session)
            result = await session.execute(
                update(knowledge_ingestion_job)
                .where(
                    knowledge_ingestion_job.c.job_id == job_id,
                    knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                    knowledge_ingestion_job.c.lease_token == lease_token,
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
        async with self._sessions() as session, session.begin():
            database_now = await _database_now(session)
            row = (
                (
                    await session.execute(
                        select(knowledge_ingestion_job)
                        .where(
                            knowledge_ingestion_job.c.job_id == checkpoint.job_id,
                            knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                            knowledge_ingestion_job.c.lease_token == checkpoint.lease_token,
                            knowledge_ingestion_job.c.lease_until > database_now,
                            knowledge_ingestion_job.c.stage == checkpoint.expected_stage.value,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                self._raise_lease_lost(checkpoint.job_id)
            results = list(deserialize_stage_results(row["stage_results_json"]))
            index = list(JobStage).index(checkpoint.expected_stage)
            results[index] = StageResult(
                checkpoint.expected_stage,
                StageState.COMPLETED,
                _utc_naive(checkpoint.completed_at),
            )
            is_complete = checkpoint.expected_stage is JobStage.READY
            next_stage = JobStage.READY if is_complete else list(JobStage)[index + 1]
            if not is_complete:
                results[index + 1] = StageResult(next_stage, StageState.PROCESSING)
            result = await session.execute(
                update(knowledge_ingestion_job)
                .where(
                    knowledge_ingestion_job.c.job_id == checkpoint.job_id,
                    knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                    knowledge_ingestion_job.c.lease_token == checkpoint.lease_token,
                    knowledge_ingestion_job.c.lease_until > database_now,
                    knowledge_ingestion_job.c.stage == checkpoint.expected_stage.value,
                )
                .values(
                    status=JobState.COMPLETED.value if is_complete else JobState.PROCESSING.value,
                    stage=next_stage.value,
                    stage_results_json=serialize_stage_results(tuple(results)),
                    lease_owner=None if is_complete else row["lease_owner"],
                    lease_token=None if is_complete else checkpoint.lease_token,
                    lease_until=None if is_complete else row["lease_until"],
                    updated_at=database_now,
                    completed_at=database_now if is_complete else None,
                )
            )
            if result.rowcount != 1:
                self._raise_lease_lost(checkpoint.job_id)
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
                        updated_at=database_now,
                    )
                )

    async def fail_job(self, failure: JobFailure) -> None:
        async with self._sessions() as session, session.begin():
            database_now = await _database_now(session)
            row = (
                (
                    await session.execute(
                        select(knowledge_ingestion_job)
                        .where(
                            knowledge_ingestion_job.c.job_id == failure.job_id,
                            knowledge_ingestion_job.c.status == JobState.PROCESSING.value,
                            knowledge_ingestion_job.c.lease_token == failure.lease_token,
                            knowledge_ingestion_job.c.lease_until > database_now,
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
    try:
        created_at = datetime.fromisoformat(payload["createdAt"])
    except (TypeError, ValueError) as error:
        raise InvalidDocumentCursor("document cursor position is invalid") from error
    return created_at, payload["documentId"]
