from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError
from sqlalchemy.ext.asyncio import AsyncEngine

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


def command(
    number: int, *, digest: str | None = None, filename: str | None = None
) -> ReserveUpload:
    return ReserveUpload(
        filename=filename or f"document-{number}.md",
        media_type="text/markdown",
        source_content_hash="sha256:" + (digest or f"{number:064x}"),
        size=20,
        now=datetime(2026, 8, 27, 10, 0, number % 60),
    )


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
                    datetime(2026, 8, 27),
                    timedelta(minutes=2),
                )
            await repository.renew_lease(
                claimed.job_id,
                claimed.lease_token,
                datetime(2026, 8, 27),
                timedelta(minutes=2),
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


def test_inflight_ingestion_checkpoint_cannot_resurrect_a_deleting_document() -> None:
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
            await repository.request_delete(
                DocumentId(record.document_id), datetime(2026, 8, 27, 12, 11)
            )

            await repository.checkpoint(
                JobCheckpoint(
                    job_id=ingestion.job_id,
                    lease_token=ingestion.lease_token,
                    expected_stage=JobStage.STORED,
                    completed_at=datetime(2026, 8, 27, 12, 12),
                )
            )

            assert await repository.get_document(DocumentId(record.document_id)) is None
            deleting = await repository.get_document(
                DocumentId(record.document_id), include_deleting=True
            )
            assert deleting is not None and deleting.status is DocumentState.DELETING
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_failed_activation_and_owned_abandon_leave_no_queryable_partial_combination() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reserved = await repository.reserve_upload(command(50))
            with pytest.raises(DataError):
                await repository.activate_upload(reserved, ArtifactLocator("x" * 1025))
            await repository.abandon_upload(reserved.reservation_id, reserved.owner_token)

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
            assert counts == [0, 0, 0, 0]
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_late_outbox_failure_rolls_back_revision_and_job_before_owned_abandon() -> None:
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
                assert await connection.scalar(text("SELECT COUNT(*) FROM knowledge_document")) == 0
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())
