from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql.dml import Insert
from sqlalchemy.sql.selectable import Select

from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    DocumentCapacityExceeded,
    DocumentCursor,
    DocumentId,
    DocumentState,
    InvalidDocumentCursor,
    JobCheckpoint,
    JobFailure,
    JobLeaseLost,
    JobStage,
    JobState,
    ReservationState,
    ReserveUpload,
    RetryNotAllowed,
)
from tap.platform.db.session import create_engine_and_session_factory

DATABASE_URL = os.getenv(
    "TAP_DATABASE_URL",
    "mysql+asyncmy://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4",
)
KNOWLEDGE_TABLES = (
    "knowledge_citation_snapshot",
    "knowledge_answer_snapshot",
    "knowledge_chunk_manifest",
    "knowledge_ingestion_job",
    "knowledge_document_revision",
    "knowledge_document",
)


class OutboxFailureRepository(MysqlDocumentRepository):
    @staticmethod
    async def _insert_job_outbox(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        del args, kwargs
        raise RuntimeError("injected transactional outbox failure")


class ReservationInsertBarrierSession(AsyncSession):
    """Force both real transactions past duplicate lookup before either insert."""

    arrivals = 0
    both_arrived = asyncio.Event()

    async def execute(self, statement, *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(statement, Insert) and statement.table.name == "knowledge_document":
            type(self).arrivals += 1
            if type(self).arrivals == 2:
                type(self).both_arrived.set()
            await asyncio.wait_for(type(self).both_arrived.wait(), timeout=5)
        return await super().execute(statement, *args, **kwargs)


class ClaimSelectionDelaySession(AsyncSession):
    """Hold the real selected job lock past the claim's first timestamp sample."""

    delayed = False

    async def execute(self, statement, *args, **kwargs):  # type: ignore[no-untyped-def]
        result = await super().execute(statement, *args, **kwargs)
        if (
            not type(self).delayed
            and isinstance(statement, Select)
            and statement._for_update_arg is not None
            and any(
                table.name == "knowledge_ingestion_job" for table in statement.get_final_froms()
            )
        ):
            type(self).delayed = True
            await asyncio.sleep(0.7)
        return result


class RetryLockOrderBarrierSession(AsyncSession):
    """Expose whether retry's first durable lock is the job or document row."""

    document_locked = asyncio.Event()
    job_locked = asyncio.Event()
    allow_job_owner = asyncio.Event()
    delete_job_locked = asyncio.Event()

    async def execute(self, statement, *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(statement, Select) and statement._for_update_arg is not None:
            table_names = {table.name for table in statement.get_final_froms()}
            result = await super().execute(statement, *args, **kwargs)
            if "knowledge_document" in table_names:
                type(self).document_locked.set()
                if not type(self).job_locked.is_set():
                    await asyncio.wait_for(type(self).delete_job_locked.wait(), timeout=5)
            elif "knowledge_ingestion_job" in table_names:
                type(self).job_locked.set()
                await asyncio.wait_for(type(self).allow_job_owner.wait(), timeout=5)
            return result
        return await super().execute(statement, *args, **kwargs)


class DeleteLockOrderBarrierSession(AsyncSession):
    """Signal only after delete actually owns the real ingestion-job lock."""

    job_attempted = asyncio.Event()

    async def execute(self, statement, *args, **kwargs):  # type: ignore[no-untyped-def]
        if (
            isinstance(statement, Select)
            and statement._for_update_arg is not None
            and any(
                table.name == "knowledge_ingestion_job" for table in statement.get_final_froms()
            )
        ):
            type(self).job_attempted.set()
            result = await super().execute(statement, *args, **kwargs)
            RetryLockOrderBarrierSession.delete_job_locked.set()
            return result
        return await super().execute(statement, *args, **kwargs)


def command(
    number: int, *, digest: str | None = None, filename: str | None = None
) -> ReserveUpload:
    return ReserveUpload(
        filename=filename or f"document-{number}.md",
        media_type="text/markdown",
        source_content_hash="sha256:" + (digest or f"{number:064x}"),
        size=20,
        now=datetime(2026, 8, 27, 10, 0, number % 60),
        staging_key=f"staging:test:{number}",
    )


def encoded_cursor(payload: object, *, canonical_json: bool = True) -> DocumentCursor:
    encoded = json.dumps(
        payload,
        separators=(",", ":") if canonical_json else None,
        sort_keys=True,
    ).encode("utf-8")
    return DocumentCursor(base64.urlsafe_b64encode(encoded).decode("ascii"))


async def clean(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM outbox WHERE aggregate_type = 'knowledge_document'")
        )
        await connection.execute(text("UPDATE knowledge_document SET current_revision_id = NULL"))
        for table in KNOWLEDGE_TABLES:
            await connection.execute(text(f"DELETE FROM {table}"))


def test_activated_upload_is_one_durable_document_revision_job_and_outbox() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reservation = await repository.reserve_upload(command(1))
            record = await repository.activate_upload(
                reservation, ArtifactLocator("blob:original/document-1")
            )

            async with engine.connect() as connection:
                counts = {
                    table: await connection.scalar(text(f"SELECT COUNT(*) FROM {table}"))
                    for table in (
                        "knowledge_document",
                        "knowledge_document_revision",
                        "knowledge_ingestion_job",
                    )
                }
                counts["outbox"] = await connection.scalar(
                    text("SELECT COUNT(*) FROM outbox WHERE aggregate_type='knowledge_document'")
                )
            assert record.document_id == reservation.document_id
            assert counts == {
                "knowledge_document": 1,
                "knowledge_document_revision": 1,
                "knowledge_ingestion_job": 1,
                "outbox": 1,
            }
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_activation_persists_the_reserved_nondefault_pipeline_versions() -> None:
    """Hard-coded adapter versions would drift from the revision identity inputs."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reservation = await repository.reserve_upload(
                ReserveUpload(
                    filename="versioned.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "7" * 64,
                    size=20,
                    now=datetime(2026, 8, 27, 10, 0),
                    parser_version="parser-review-v7",
                    chunker_version="chunker-review-v8",
                    pipeline_version="pipeline-review-v9",
                    staging_key="staging:test:versioned",
                )
            )
            await repository.activate_upload(reservation, ArtifactLocator("blob:versioned"))

            async with engine.connect() as connection:
                versions = (
                    await connection.execute(
                        text(
                            "SELECT parser_version, chunker_version, pipeline_version "
                            "FROM knowledge_document_revision WHERE revision_id=:revision_id"
                        ),
                        {"revision_id": reservation.revision_id},
                    )
                ).one()
            assert tuple(versions) == (
                "parser-review-v7",
                "chunker-review-v8",
                "pipeline-review-v9",
            )
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_concurrent_renamed_duplicate_has_one_identity_and_one_dispatch_path() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            first_repo = MysqlDocumentRepository(sessions)
            second_repo = MysqlDocumentRepository(sessions)
            digest = "d" * 64

            async def submit(repository: MysqlDocumentRepository, filename: str):
                reserved = await repository.reserve_upload(
                    command(2, digest=digest, filename=filename)
                )
                record = await repository.activate_upload(
                    reserved, ArtifactLocator(f"blob:original/{reserved.revision_id}")
                )
                return reserved, record

            (first, first_record), (second, second_record) = await asyncio.gather(
                submit(first_repo, "first.md"), submit(second_repo, "renamed.md")
            )

            async with engine.connect() as connection:
                job_count = await connection.scalar(
                    text("SELECT COUNT(*) FROM knowledge_ingestion_job")
                )
                outbox_count = await connection.scalar(
                    text("SELECT COUNT(*) FROM outbox WHERE aggregate_type='knowledge_document'")
                )
            assert first_record.document_id == second_record.document_id
            assert {first.state, second.state} == {
                ReservationState.OWNED,
                ReservationState.DUPLICATE_PENDING,
            }
            assert job_count == outbox_count == 1
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_late_helper_cannot_reopen_cleanup_fact_after_active_cleanup() -> None:
    """An idempotent helper arriving after activation must leave recovery columns closed."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reservation = await repository.reserve_upload(command(72))
            first = await repository.activate_upload(
                reservation, ArtifactLocator("blob:canonical-original")
            )
            await repository.complete_upload_cleanup(
                reservation.reservation_id, reservation.owner_token
            )

            late = await repository.activate_upload(
                reservation, ArtifactLocator("blob:late-helper")
            )
            async with engine.connect() as connection:
                recovery_columns = (
                    await connection.execute(
                        text(
                            "SELECT staging_blob_locator, promoted_blob_locator, "
                            "reservation_owner_token, reservation_expires_at "
                            "FROM knowledge_document WHERE document_id=:document_id"
                        ),
                        {"document_id": reservation.document_id},
                    )
                ).one()
                original = await connection.scalar(
                    text(
                        "SELECT original_blob_locator FROM knowledge_document_revision "
                        "WHERE revision_id=:revision_id"
                    ),
                    {"revision_id": reservation.revision_id},
                )
            assert late.job_id == first.job_id
            assert tuple(recovery_columns) == (None, None, None, None)
            assert original == "blob:canonical-original"
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_empty_dedupe_range_deadlock_is_retried_as_one_owned_and_one_pending() -> None:
    """A 1213 after two empty-range reads must not escape the repository boundary."""

    async def scenario() -> None:
        engine, _ = create_engine_and_session_factory(DATABASE_URL)
        sessions = async_sessionmaker(
            engine,
            class_=ReservationInsertBarrierSession,
            expire_on_commit=False,
        )
        await clean(engine)
        ReservationInsertBarrierSession.arrivals = 0
        ReservationInsertBarrierSession.both_arrived = asyncio.Event()
        try:
            digest = "b" * 64
            first, second = await asyncio.gather(
                MysqlDocumentRepository(sessions).reserve_upload(
                    command(70, digest=digest, filename="first.md")
                ),
                MysqlDocumentRepository(sessions).reserve_upload(
                    command(71, digest=digest, filename="renamed.md")
                ),
            )

            assert {first.state, second.state} == {
                ReservationState.OWNED,
                ReservationState.DUPLICATE_PENDING,
            }
            assert first.document_id == second.document_id
            async with engine.connect() as connection:
                assert await connection.scalar(text("SELECT COUNT(*) FROM knowledge_document")) == 1
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_capacity_lock_allows_exactly_one_of_two_concurrent_unique_fiftieth_documents() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            for number in range(49):
                reserved = await repository.reserve_upload(command(number + 100))
                await repository.activate_upload(reserved, ArtifactLocator(f"blob:{number}"))

            results = await asyncio.gather(
                MysqlDocumentRepository(sessions).reserve_upload(command(900)),
                MysqlDocumentRepository(sessions).reserve_upload(command(901)),
                return_exceptions=True,
            )
            assert sum(isinstance(item, DocumentCapacityExceeded) for item in results) == 1
            assert sum(not isinstance(item, BaseException) for item in results) == 1
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_lease_token_ownership_and_expired_lease_recovery_are_conditional() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reserved = await repository.reserve_upload(command(3))
            await repository.activate_upload(reserved, ArtifactLocator("blob:lease"))
            first = (
                await repository.claim_jobs(
                    worker_id="worker-a",
                    now=datetime(2026, 8, 27, 10, 1),
                    lease_duration=timedelta(seconds=30),
                    limit=1,
                )
            )[0]

            with pytest.raises(JobLeaseLost):
                await repository.checkpoint(
                    JobCheckpoint(
                        job_id=first.job_id,
                        lease_token="stolen-token",
                        expected_stage=JobStage.STORED,
                        completed_at=datetime(2026, 8, 27, 10, 1, 1),
                    )
                )

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE knowledge_ingestion_job "
                        "SET lease_until = UTC_TIMESTAMP(6) - INTERVAL 1 SECOND "
                        "WHERE job_id=:job_id"
                    ),
                    {"job_id": first.job_id},
                )
            with pytest.raises(JobLeaseLost):
                await repository.checkpoint(
                    JobCheckpoint(
                        job_id=first.job_id,
                        lease_token=first.lease_token,
                        expected_stage=JobStage.STORED,
                        completed_at=datetime(2026, 8, 27, 10, 1, 2),
                    )
                )
            recovered = await MysqlDocumentRepository(sessions).claim_jobs(
                worker_id="worker-b",
                now=datetime(2026, 8, 27, 10, 2),
                lease_duration=timedelta(seconds=30),
                limit=1,
            )
            assert [job.job_id for job in recovered] == [first.job_id]
            assert recovered[0].lease_token != first.lease_token
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_active_duplicate_is_returned_before_capacity_and_keeps_one_job() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            original = await repository.reserve_upload(command(4, digest="a" * 64))
            activated = await repository.activate_upload(original, ArtifactLocator("blob:active"))
            for number in range(49):
                reserved = await repository.reserve_upload(command(number + 1000))
                await repository.activate_upload(reserved, ArtifactLocator(f"blob:cap-{number}"))

            duplicate = await repository.reserve_upload(
                command(5, digest="a" * 64, filename="renamed.md")
            )

            assert duplicate.state is ReservationState.DUPLICATE_ACTIVE
            assert duplicate.document is not None
            assert duplicate.document.document_id == activated.document_id
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(text("SELECT COUNT(*) FROM knowledge_ingestion_job"))
                    == 50
                )
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_cursor_is_stable_and_malformed_or_widened_values_fail_closed() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            created: list[str] = []
            for number in range(3):
                reserved = await repository.reserve_upload(command(number + 20))
                row = await repository.activate_upload(
                    reserved, ArtifactLocator(f"blob:page-{number}")
                )
                created.append(row.document_id)
            first_page = await repository.list_documents(None, 2)
            assert len(first_page.items) == 2
            assert first_page.next_cursor is not None

            newer = await repository.reserve_upload(command(99))
            await repository.activate_upload(newer, ArtifactLocator("blob:newer"))
            second_page = await repository.list_documents(first_page.next_cursor, 2)

            assert [row.document_id for row in first_page.items + second_page.items] == list(
                reversed(created)
            )
            with pytest.raises(InvalidDocumentCursor):
                await repository.list_documents(DocumentCursor("x" * 513), 2)
            widened = base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "createdAt": "2026-08-27T10:00:00.000000",
                        "documentId": "doc",
                        "unexpected": True,
                        "v": "v1",
                    }
                ).encode()
            ).decode()
            with pytest.raises(InvalidDocumentCursor):
                await repository.list_documents(DocumentCursor(widened), 2)
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "cursor",
    [
        DocumentCursor(""),
        DocumentCursor("%not-base64%"),
        DocumentCursor("x" * 513),
        encoded_cursor(
            {
                "createdAt": "2026-08-27T10:00:00.000000",
                "documentId": "doc_" + "a" * 32,
                "v": "v1",
            },
            canonical_json=False,
        ),
        DocumentCursor(
            str(
                encoded_cursor(
                    {
                        "createdAt": "2026-08-27T10:00:00.000000",
                        "documentId": "doc_" + "a" * 32,
                        "v": "v1",
                    }
                )
            ).rstrip("=")
        ),
        encoded_cursor(
            {
                "createdAt": "2026-08-27T10:00:00.000000",
                "documentId": "",
                "v": "v1",
            }
        ),
        encoded_cursor({"createdAt": "2026-08-27T10:00:00.000000", "documentId": "doc", "v": "v1"}),
        encoded_cursor(
            {
                "createdAt": "2026-08-27T10:00:00.000000",
                "documentId": "doc_" + "A" * 32,
                "v": "v1",
            }
        ),
        encoded_cursor(
            {
                "createdAt": "2026-08-27T10:00:00.000000",
                "documentId": "doc_" + "a" * 33,
                "v": "v1",
            }
        ),
        *[
            encoded_cursor(
                {
                    "createdAt": value,
                    "documentId": "doc_" + "a" * 32,
                    "v": "v1",
                }
            )
            for value in (
                "2026-08-27",
                "2026-08-27T10:00:00",
                "2026-08-27 10:00:00.000000",
                "2026-08-27T10:00:00.000000Z",
                "2026-08-27T10:00:00.000000+00:00",
                "2026-08-27T10:00:00.0000000",
            )
        ],
        encoded_cursor(
            {
                "createdAt": "2026-08-27T10:00:00.000000",
                "documentId": "doc_" + "a" * 32,
                "v": "v2",
            }
        ),
        encoded_cursor(
            {
                "createdAt": "2026-08-27T10:00:00.000000",
                "documentId": "doc_" + "a" * 32,
                "unexpected": False,
                "v": "v1",
            }
        ),
    ],
    ids=lambda value: str(value)[:40],
)
def test_cursor_v1_rejects_every_noncanonical_position_mutation(cursor: DocumentCursor) -> None:
    """Relaxing any v1 position byte would admit ambiguous or widened pagination state."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        try:
            repository = MysqlDocumentRepository(sessions)
            with pytest.raises(InvalidDocumentCursor):
                await repository.list_documents(cursor, 1)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_only_failed_ingestion_retries_from_first_incomplete_stage_without_secret_text() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reserved = await repository.reserve_upload(command(30))
            record = await repository.activate_upload(reserved, ArtifactLocator("blob:retry"))
            with pytest.raises(Exception) as ineligible:
                await repository.retry_failed(DocumentId(record.document_id), datetime(2026, 8, 27))
            assert ineligible.type.__name__ == "RetryNotAllowed"

            claimed = (
                await repository.claim_jobs(
                    worker_id="worker-retry",
                    now=datetime(2026, 8, 27),
                    lease_duration=timedelta(seconds=30),
                    limit=1,
                )
            )[0]
            await repository.checkpoint(
                JobCheckpoint(
                    job_id=claimed.job_id,
                    lease_token=claimed.lease_token,
                    expected_stage=JobStage.STORED,
                    completed_at=datetime(2026, 8, 27, 10, 29),
                )
            )
            await repository.fail_job(
                JobFailure(
                    job_id=claimed.job_id,
                    lease_token=claimed.lease_token,
                    expected_stage=JobStage.PARSING,
                    error_code="embedding-unavailable",
                    failed_at=datetime(2026, 8, 27, 10, 30),
                )
            )
            retried = await repository.retry_failed(
                DocumentId(record.document_id), datetime(2026, 8, 27, 10, 31)
            )

            visible = await repository.get_document(DocumentId(record.document_id))
            assert visible is not None
            assert visible.status.value == "queued"
            assert visible.error_code is visible.error_summary is None
            assert retried.job_id == claimed.job_id
            assert retried.attempt == 2
            assert retried.stage is JobStage.PARSING
            async with engine.connect() as connection:
                rows = list(
                    (
                        await connection.execute(
                            text(
                                "SELECT error_code, error_summary FROM knowledge_ingestion_job "
                                "WHERE job_id=:job_id"
                            ),
                            {"job_id": claimed.job_id},
                        )
                    ).mappings()
                )
                dispatches = await connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM outbox WHERE aggregate_id=:document_id "
                        "AND message_type='knowledge.ingestion_requested'"
                    ),
                    {"document_id": record.document_id},
                )
            assert [dict(row) for row in rows] == [{"error_code": None, "error_summary": None}]
            assert dispatches == 2
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_lease_renewal_requires_the_current_unexpired_token() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reserved = await repository.reserve_upload(command(35))
            await repository.activate_upload(reserved, ArtifactLocator("blob:renew"))
            claimed = (
                await repository.claim_jobs(
                    worker_id="renew-worker",
                    now=datetime(2026, 8, 27),
                    lease_duration=timedelta(seconds=30),
                    limit=1,
                )
            )[0]

            with pytest.raises(JobLeaseLost):
                await repository.renew_lease(
                    claimed.job_id,
                    "wrong-token",
                    expected_stage=JobStage.STORED,
                    now=datetime(2026, 8, 27),
                    lease_duration=timedelta(minutes=2),
                )
            with pytest.raises(JobLeaseLost):
                await repository.renew_lease(
                    claimed.job_id,
                    claimed.lease_token,
                    expected_stage=JobStage.PARSING,
                    now=datetime(2026, 8, 27),
                    lease_duration=timedelta(minutes=2),
                )
            await repository.renew_lease(
                claimed.job_id,
                claimed.lease_token,
                expected_stage=JobStage.STORED,
                now=datetime(2026, 8, 27),
                lease_duration=timedelta(minutes=2),
            )

            async with engine.connect() as connection:
                row = (
                    (
                        await connection.execute(
                            text(
                                "SELECT lease_owner, lease_token, lease_until "
                                "FROM knowledge_ingestion_job WHERE job_id=:job_id"
                            ),
                            {"job_id": claimed.job_id},
                        )
                    )
                    .mappings()
                    .one()
                )
            assert row["lease_owner"] == "renew-worker"
            assert row["lease_token"] == claimed.lease_token
            assert row["lease_until"] > claimed.lease_until
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["renew", "checkpoint", "fail"])
def test_old_lease_token_loses_when_real_row_lock_wait_crosses_expiry(operation: str) -> None:
    """A timestamp sampled before a row-lock wait must not extend stale ownership."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        blocker = await engine.connect()
        transaction = None
        try:
            repository = MysqlDocumentRepository(sessions)
            reserved = await repository.reserve_upload(command(80))
            await repository.activate_upload(reserved, ArtifactLocator("blob:stale-clock"))
            claimed = (
                await repository.claim_jobs(
                    worker_id="stale-clock-worker",
                    now=datetime(2026, 8, 27),
                    lease_duration=timedelta(milliseconds=600),
                    limit=1,
                )
            )[0]
            transaction = await blocker.begin()
            await blocker.execute(
                text("SELECT job_id FROM knowledge_ingestion_job WHERE job_id=:job_id FOR UPDATE"),
                {"job_id": claimed.job_id},
            )

            if operation == "renew":
                pending = asyncio.create_task(
                    repository.renew_lease(
                        claimed.job_id,
                        claimed.lease_token,
                        expected_stage=JobStage.STORED,
                        now=datetime(2026, 8, 27),
                        lease_duration=timedelta(seconds=30),
                    )
                )
            elif operation == "checkpoint":
                pending = asyncio.create_task(
                    repository.checkpoint(
                        JobCheckpoint(
                            job_id=claimed.job_id,
                            lease_token=claimed.lease_token,
                            expected_stage=JobStage.STORED,
                            completed_at=datetime(2026, 8, 27),
                        )
                    )
                )
            else:
                pending = asyncio.create_task(
                    repository.fail_job(
                        JobFailure(
                            job_id=claimed.job_id,
                            lease_token=claimed.lease_token,
                            expected_stage=JobStage.STORED,
                            error_code="parser-unavailable",
                            failed_at=datetime(2026, 8, 27),
                        )
                    )
                )
            await asyncio.sleep(0.1)
            assert not pending.done()
            await asyncio.sleep(0.7)
            await transaction.commit()
            transaction = None

            with pytest.raises(JobLeaseLost):
                await pending
        finally:
            if transaction is not None:
                await transaction.rollback()
            await blocker.close()
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("lease_duration", [timedelta(0), timedelta(minutes=16)])
def test_job_lease_duration_is_strict_positive_and_bounded(
    lease_duration: timedelta,
) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        try:
            with pytest.raises(ValueError):
                await MysqlDocumentRepository(sessions).claim_jobs(
                    worker_id="bounded-worker",
                    now=datetime(2026, 8, 27),
                    lease_duration=lease_duration,
                    limit=1,
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_claim_lease_is_based_on_post_lock_database_time() -> None:
    """A claim returned after a long lock hold must still own a future lease."""

    async def scenario() -> None:
        engine, setup_sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            setup_repository = MysqlDocumentRepository(setup_sessions)
            reserved = await setup_repository.reserve_upload(command(81))
            await setup_repository.activate_upload(reserved, ArtifactLocator("blob:claim-clock"))
            ClaimSelectionDelaySession.delayed = False
            delayed_sessions = async_sessionmaker(
                engine,
                class_=ClaimSelectionDelaySession,
                expire_on_commit=False,
            )

            claimed = (
                await MysqlDocumentRepository(delayed_sessions).claim_jobs(
                    worker_id="post-lock-clock",
                    now=datetime(2026, 8, 27),
                    lease_duration=timedelta(milliseconds=600),
                    limit=1,
                )
            )[0]
            async with engine.connect() as connection:
                database_now = await connection.scalar(text("SELECT UTC_TIMESTAMP(6)"))
            assert claimed.lease_until > database_now
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_delete_is_immediately_unselectable_and_final_delete_releases_dedupe_identity() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            source = command(40, digest="b" * 64)
            reserved = await repository.reserve_upload(source)
            record = await repository.activate_upload(reserved, ArtifactLocator("blob:delete"))

            deletion = await repository.request_delete(
                DocumentId(record.document_id), datetime(2026, 8, 27, 11, 0)
            )
            assert await repository.get_document(DocumentId(record.document_id)) is None
            deleting = await repository.get_document(
                DocumentId(record.document_id), include_deleting=True
            )
            assert deleting is not None and deleting.status.value == "deleting"

            claimed = await repository.claim_jobs(
                worker_id="delete-worker",
                now=datetime(2026, 8, 27, 11, 1),
                lease_duration=timedelta(minutes=5),
                limit=10,
            )
            delete_claim = next(job for job in claimed if job.job_id == deletion.job_id)
            for stage in JobStage:
                await repository.checkpoint(
                    JobCheckpoint(
                        job_id=delete_claim.job_id,
                        lease_token=delete_claim.lease_token,
                        expected_stage=stage,
                        completed_at=datetime(2026, 8, 27, 11, 2),
                    )
                )

            assert (
                await repository.get_document(DocumentId(record.document_id), include_deleting=True)
                is None
            )
            replacement = await repository.reserve_upload(source)
            assert replacement.state is ReservationState.OWNED
            assert replacement.document_id != record.document_id
            async with engine.connect() as connection:
                old = (
                    (
                        await connection.execute(
                            text(
                                "SELECT dedupe_key, deleted_at FROM knowledge_document "
                                "WHERE document_id=:document_id"
                            ),
                            {"document_id": record.document_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                deletion_outbox = await connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM outbox WHERE aggregate_id=:document_id "
                        "AND message_type='knowledge.deletion_requested'"
                    ),
                    {"document_id": record.document_id},
                )
            assert old["dedupe_key"] is None
            assert old["deleted_at"] is not None
            assert deletion_outbox == 1
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_failed_deletion_stays_unselectable_and_reissued_delete_requeues_cleanup() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reserved = await repository.reserve_upload(command(45))
            record = await repository.activate_upload(reserved, ArtifactLocator("blob:delete-fail"))
            deletion = await repository.request_delete(
                DocumentId(record.document_id), datetime(2026, 8, 27, 12, 0)
            )
            claimed = await repository.claim_jobs(
                worker_id="delete-failure-worker",
                now=datetime(2026, 8, 27, 12, 1),
                lease_duration=timedelta(minutes=1),
                limit=10,
            )
            delete_claim = next(job for job in claimed if job.job_id == deletion.job_id)
            await repository.fail_job(
                JobFailure(
                    job_id=delete_claim.job_id,
                    lease_token=delete_claim.lease_token,
                    expected_stage=JobStage.STORED,
                    error_code="artifact-unavailable",
                    failed_at=datetime(2026, 8, 27, 12, 2),
                )
            )

            assert await repository.get_document(DocumentId(record.document_id)) is None
            deleting = await repository.get_document(
                DocumentId(record.document_id), include_deleting=True
            )
            assert deleting is not None
            assert deleting.status is DocumentState.DELETING
            assert deleting.error_code is deleting.error_summary is None

            requeued = await repository.request_delete(
                DocumentId(record.document_id), datetime(2026, 8, 27, 12, 3)
            )
            assert requeued.job_id == deletion.job_id
            assert requeued.status is JobState.PENDING
            assert requeued.attempt == 2
            async with engine.connect() as connection:
                dispatches = await connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM outbox WHERE aggregate_id=:document_id "
                        "AND message_type='knowledge.deletion_requested'"
                    ),
                    {"document_id": record.document_id},
                )
            assert dispatches == 2
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_retry_and_delete_share_job_then_document_lock_order_without_deadlock() -> None:
    """Opposite job/document lock order would leak MySQL 1213 from this forced race."""

    async def scenario() -> None:
        engine, setup_sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            setup = MysqlDocumentRepository(setup_sessions)
            reserved = await setup.reserve_upload(command(47))
            record = await setup.activate_upload(reserved, ArtifactLocator("blob:retry-delete"))
            ingestion = (
                await setup.claim_jobs(
                    worker_id="retry-delete-setup",
                    now=datetime(2026, 8, 27, 12, 20),
                    lease_duration=timedelta(minutes=1),
                    limit=1,
                )
            )[0]
            await setup.fail_job(
                JobFailure(
                    job_id=ingestion.job_id,
                    lease_token=ingestion.lease_token,
                    expected_stage=JobStage.STORED,
                    error_code="parser-unavailable",
                    failed_at=datetime(2026, 8, 27, 12, 21),
                )
            )

            RetryLockOrderBarrierSession.document_locked = asyncio.Event()
            RetryLockOrderBarrierSession.job_locked = asyncio.Event()
            RetryLockOrderBarrierSession.allow_job_owner = asyncio.Event()
            RetryLockOrderBarrierSession.delete_job_locked = asyncio.Event()
            DeleteLockOrderBarrierSession.job_attempted = asyncio.Event()
            retry_sessions = async_sessionmaker(
                engine, class_=RetryLockOrderBarrierSession, expire_on_commit=False
            )
            delete_sessions = async_sessionmaker(
                engine, class_=DeleteLockOrderBarrierSession, expire_on_commit=False
            )
            retry = asyncio.create_task(
                MysqlDocumentRepository(retry_sessions).retry_failed(
                    DocumentId(record.document_id), datetime(2026, 8, 27, 12, 22)
                )
            )
            first_lock = asyncio.create_task(RetryLockOrderBarrierSession.document_locked.wait())
            correct_lock = asyncio.create_task(RetryLockOrderBarrierSession.job_locked.wait())
            done, waiting = await asyncio.wait(
                {first_lock, correct_lock}, timeout=5, return_when=asyncio.FIRST_COMPLETED
            )
            assert done
            for waiter in waiting:
                waiter.cancel()

            deleting = asyncio.create_task(
                MysqlDocumentRepository(delete_sessions).request_delete(
                    DocumentId(record.document_id), datetime(2026, 8, 27, 12, 23)
                )
            )
            await asyncio.wait_for(DeleteLockOrderBarrierSession.job_attempted.wait(), timeout=5)
            if RetryLockOrderBarrierSession.job_locked.is_set():
                RetryLockOrderBarrierSession.allow_job_owner.set()

            outcomes = await asyncio.wait_for(
                asyncio.gather(retry, deleting, return_exceptions=True), timeout=5
            )
            assert not any(isinstance(outcome, DBAPIError) for outcome in outcomes)
            assert not any(
                isinstance(outcome, BaseException) and not isinstance(outcome, RetryNotAllowed)
                for outcome in outcomes
            )

            visible = await setup.get_document(
                DocumentId(record.document_id), include_deleting=True
            )
            assert visible is not None and visible.status is DocumentState.DELETING
            async with engine.connect() as connection:
                job_counts = (
                    (
                        await connection.execute(
                            text(
                                "SELECT kind, COUNT(*) AS count FROM knowledge_ingestion_job "
                                "GROUP BY kind ORDER BY kind"
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                outbox_rows = (
                    (
                        await connection.execute(
                            text(
                                "SELECT outbox_id, message_type FROM outbox "
                                "WHERE aggregate_id=:document_id ORDER BY outbox_id"
                            ),
                            {"document_id": record.document_id},
                        )
                    )
                    .mappings()
                    .all()
                )
            assert [(row["kind"], row["count"]) for row in job_counts] == [
                ("deletion", 1),
                ("ingestion", 1),
            ]
            assert len({row["outbox_id"] for row in outbox_rows}) == len(outbox_rows)
            assert [row["message_type"] for row in outbox_rows].count(
                "knowledge.deletion_requested"
            ) == 1
            assert [row["message_type"] for row in outbox_rows].count(
                "knowledge.ingestion_requested"
            ) == 2
        finally:
            RetryLockOrderBarrierSession.allow_job_owner.set()
            RetryLockOrderBarrierSession.delete_job_locked.set()
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_delete_waits_for_inflight_job_then_fences_every_old_ingestion_transition() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reserved = await repository.reserve_upload(command(46))
            record = await repository.activate_upload(reserved, ArtifactLocator("blob:inflight"))
            ingestion = (
                await repository.claim_jobs(
                    worker_id="inflight-ingestion",
                    now=datetime(2026, 8, 27, 12, 10),
                    lease_duration=timedelta(minutes=1),
                    limit=1,
                )
            )[0]
            blocker = await engine.connect()
            transaction = await blocker.begin()
            await blocker.execute(
                text("SELECT job_id FROM knowledge_ingestion_job WHERE job_id=:job_id FOR UPDATE"),
                {"job_id": ingestion.job_id},
            )
            try:
                deleting_task = asyncio.create_task(
                    repository.request_delete(
                        DocumentId(record.document_id), datetime(2026, 8, 27, 12, 11)
                    )
                )
                await asyncio.sleep(0.1)
                assert not deleting_task.done()
            finally:
                await transaction.commit()
                await blocker.close()
            deletion = await deleting_task

            assert await repository.get_document(DocumentId(record.document_id)) is None
            deleting = await repository.get_document(
                DocumentId(record.document_id), include_deleting=True
            )
            assert deleting is not None and deleting.status is DocumentState.DELETING
            with pytest.raises(JobLeaseLost):
                await repository.checkpoint(
                    JobCheckpoint(
                        job_id=ingestion.job_id,
                        lease_token=ingestion.lease_token,
                        expected_stage=JobStage.STORED,
                        completed_at=datetime(2026, 8, 27, 12, 12),
                    )
                )
            with pytest.raises(JobLeaseLost):
                await repository.fail_job(
                    JobFailure(
                        job_id=ingestion.job_id,
                        lease_token=ingestion.lease_token,
                        expected_stage=JobStage.STORED,
                        error_code="parser-unavailable",
                        failed_at=datetime(2026, 8, 27, 12, 12),
                    )
                )
            with pytest.raises(JobLeaseLost):
                await repository.renew_lease(
                    ingestion.job_id,
                    ingestion.lease_token,
                    expected_stage=JobStage.STORED,
                    now=datetime(2026, 8, 27, 12, 12),
                    lease_duration=timedelta(seconds=30),
                )
            claimable = await repository.claim_jobs(
                worker_id="deletion-only",
                now=datetime(2026, 8, 27, 12, 12),
                lease_duration=timedelta(seconds=30),
                limit=10,
            )
            assert [job.job_id for job in claimable] == [deletion.job_id]
            async with engine.connect() as connection:
                ingestion_state = (
                    await connection.execute(
                        text(
                            "SELECT status, lease_token FROM knowledge_ingestion_job "
                            "WHERE job_id=:job_id"
                        ),
                        {"job_id": ingestion.job_id},
                    )
                ).one()
                assert tuple(ingestion_state) == ("cancelled", None)
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_failed_promotion_is_hidden_and_fenced_for_durable_takeover() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reserved = await repository.reserve_upload(command(50))
            with pytest.raises(DataError):
                await repository.activate_upload(reserved, ArtifactLocator("x" * 1025))
            await repository.abandon_upload(reserved.reservation_id, reserved.owner_token)

            assert await repository.get_document(DocumentId(reserved.document_id)) is None
            async with engine.connect() as connection:
                counts = [
                    await connection.scalar(text(f"SELECT COUNT(*) FROM {table}"))
                    for table in (
                        "knowledge_document",
                        "knowledge_document_revision",
                        "knowledge_ingestion_job",
                    )
                ]
                counts.append(
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM outbox WHERE aggregate_type='knowledge_document'"
                        )
                    )
                )
                recovery = (
                    await connection.execute(
                        text(
                            "SELECT staging_blob_locator, promoted_blob_locator, "
                            "reservation_expires_at <= UTC_TIMESTAMP(6) AS expired "
                            "FROM knowledge_document"
                        )
                    )
                ).one()
            assert counts == [1, 0, 0, 0]
            assert tuple(recovery) == (reserved.staging_key, None, 1)
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_late_outbox_failure_keeps_promoted_locator_for_durable_takeover() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = OutboxFailureRepository(sessions)
            reserved = await repository.reserve_upload(command(55))
            with pytest.raises(RuntimeError, match="injected transactional outbox failure"):
                await repository.activate_upload(reserved, ArtifactLocator("blob:late-failure"))

            assert await repository.get_document(DocumentId(reserved.document_id)) is None
            async with engine.connect() as connection:
                before_abandon = [
                    await connection.scalar(text(f"SELECT COUNT(*) FROM {table}"))
                    for table in (
                        "knowledge_document",
                        "knowledge_document_revision",
                        "knowledge_ingestion_job",
                    )
                ]
                before_abandon.append(
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM outbox WHERE aggregate_type='knowledge_document'"
                        )
                    )
                )
            assert before_abandon == [1, 0, 0, 0]

            await repository.abandon_upload(reserved.reservation_id, reserved.owner_token)
            async with engine.connect() as connection:
                recovery = (
                    await connection.execute(
                        text(
                            "SELECT staging_blob_locator, promoted_blob_locator, "
                            "reservation_expires_at <= UTC_TIMESTAMP(6) AS expired "
                            "FROM knowledge_document"
                        )
                    )
                ).one()
            assert tuple(recovery) == (
                reserved.staging_key,
                "blob:late-failure",
                1,
            )
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())
