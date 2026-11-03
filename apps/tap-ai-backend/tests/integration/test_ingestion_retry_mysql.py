from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tap.entrypoints.tapper_runtime import create_project_audit
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    ClaimedIngestionJob,
    DocumentId,
    DocumentState,
    JobFailure,
    JobStage,
    ReserveUpload,
)
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.retry import RetryPolicy

KNOWLEDGE_TABLES = (
    "knowledge_citation_snapshot",
    "knowledge_answer_source",
    "knowledge_answer_snapshot",
    "knowledge_chunk_manifest",
    "knowledge_parse_inventory",
    "knowledge_ingestion_job",
    "knowledge_document_revision",
    "knowledge_source_legacy_map",
    "knowledge_document",
    "knowledge_source",
)


class FixedDelayPolicy(RetryPolicy):
    def next_delay(self, attempt: int, *, rng: random.Random | None = None) -> timedelta:
        del attempt, rng
        return timedelta(seconds=60)


async def clean(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "DELETE FROM outbox WHERE aggregate_type IN "
                "('knowledge_document', 'DocumentRevision')"
            )
        )
        await connection.execute(text("UPDATE knowledge_document SET current_revision_id = NULL"))
        for table in KNOWLEDGE_TABLES:
            await connection.execute(text(f"DELETE FROM {table}"))


def reserve(number: int) -> ReserveUpload:
    return ReserveUpload(
        filename=f"retry-{number}.md",
        media_type="text/markdown",
        source_content_hash="sha256:" + f"{number:064x}",
        size=20,
        now=datetime(2026, 11, 3, 10, 0, number % 60),
        staging_key=f"staging:retry:{number}",
    )


async def activated(repository: MysqlDocumentRepository, number: int) -> str:
    reserved = await repository.reserve_upload(reserve(number))
    record = await repository.activate_upload(reserved, ArtifactLocator(f"blob:retry-{number}"))
    return record.document_id


async def claim(repository: MysqlDocumentRepository) -> tuple[ClaimedIngestionJob, ...]:
    return await repository.claim_jobs(
        worker_id="retry-worker",
        now=datetime(2026, 11, 3, 10, 1),
        lease_duration=timedelta(seconds=30),
        limit=1,
    )


async def job_row(engine: AsyncEngine, job_id: str) -> dict[str, object]:
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT status, delivery_count, error_code, error_summary, "
                        "next_attempt_at, lease_token, UTC_TIMESTAMP(6) AS db_now "
                        "FROM knowledge_ingestion_job WHERE job_id=:job_id"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


async def expire_lease(engine: AsyncEngine, job_id: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE knowledge_ingestion_job "
                "SET lease_until = UTC_TIMESTAMP(6) - INTERVAL 1 SECOND WHERE job_id=:job_id"
            ),
            {"job_id": job_id},
        )


async def set_delivery_count(engine: AsyncEngine, job_id: str, count: int) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE knowledge_ingestion_job SET delivery_count=:count WHERE job_id=:job_id"),
            {"job_id": job_id, "count": count},
        )


def failure(job: ClaimedIngestionJob, error_code: str) -> JobFailure:
    return JobFailure(
        job_id=job.job_id,
        lease_token=job.lease_token,
        expected_stage=JobStage.STORED,
        error_code=error_code,
        failed_at=datetime(2026, 11, 3, 10, 2),
    )


def run(owned_module_database_url: str, body) -> None:  # type: ignore[no-untyped-def]
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(owned_module_database_url)
        await clean(engine)
        try:
            await body(engine, sessions)
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def repository_for(sessions, policy: RetryPolicy | None = None) -> MysqlDocumentRepository:  # type: ignore[no-untyped-def]
    if policy is None:
        return MysqlDocumentRepository(
            sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )
    return MysqlDocumentRepository(
        sessions,
        scope=VALIDATION_SCOPE,
        audit_factory=create_project_audit,
        retry_policy=policy,
    )


def test_each_claim_including_lease_recovery_counts_one_delivery(
    owned_module_database_url: str,
) -> None:
    async def body(engine, sessions) -> None:  # type: ignore[no-untyped-def]
        repository = repository_for(sessions)
        await activated(repository, 1)
        first = (await claim(repository))[0]
        assert (await job_row(engine, first.job_id))["delivery_count"] == 1

        await expire_lease(engine, first.job_id)
        recovered = await claim(repository)
        assert [job.job_id for job in recovered] == [first.job_id]
        assert (await job_row(engine, first.job_id))["delivery_count"] == 2

    run(owned_module_database_url, body)


def test_exhausted_deliveries_fail_the_job_instead_of_handing_it_out(
    owned_module_database_url: str,
) -> None:
    async def body(engine, sessions) -> None:  # type: ignore[no-untyped-def]
        repository = repository_for(sessions)
        document_id = await activated(repository, 2)
        first = (await claim(repository))[0]
        await set_delivery_count(engine, first.job_id, 5)
        await expire_lease(engine, first.job_id)

        assert await claim(repository) == ()
        row = await job_row(engine, first.job_id)
        assert row["status"] == "failed"
        assert row["error_code"] == "lease-exhausted"
        assert row["error_summary"] == "处理多次中断，已停止重试"
        assert row["lease_token"] is None
        assert row["delivery_count"] == 5
        visible = await repository.get_document(DocumentId(document_id))
        assert visible is not None
        assert visible.status is DocumentState.FAILED
        assert visible.error_code == "lease-exhausted"

    run(owned_module_database_url, body)


def test_transient_failure_returns_job_to_pending_with_backoff(
    owned_module_database_url: str,
) -> None:
    async def body(engine, sessions) -> None:  # type: ignore[no-untyped-def]
        repository = repository_for(sessions, FixedDelayPolicy())
        document_id = await activated(repository, 3)
        job = (await claim(repository))[0]
        await repository.fail_job(failure(job, "embedding-unavailable"))

        row = await job_row(engine, job.job_id)
        assert row["status"] == "pending"
        assert row["error_code"] == "embedding-unavailable"
        assert row["lease_token"] is None
        assert row["next_attempt_at"] > row["db_now"] + timedelta(seconds=50)
        assert await claim(repository) == ()
        visible = await repository.get_document(DocumentId(document_id))
        assert visible is not None
        assert visible.status is not DocumentState.FAILED
        assert visible.error_code is None

    run(owned_module_database_url, body)


def test_default_transient_backoff_is_bounded_by_the_first_ceiling(
    owned_module_database_url: str,
) -> None:
    async def body(engine, sessions) -> None:  # type: ignore[no-untyped-def]
        repository = repository_for(sessions)
        await activated(repository, 4)
        job = (await claim(repository))[0]
        await repository.fail_job(failure(job, "index-unavailable"))

        row = await job_row(engine, job.job_id)
        assert row["status"] == "pending"
        assert row["next_attempt_at"] <= row["db_now"] + timedelta(seconds=2)

    run(owned_module_database_url, body)


def test_transient_failure_on_the_last_delivery_is_terminal(
    owned_module_database_url: str,
) -> None:
    async def body(engine, sessions) -> None:  # type: ignore[no-untyped-def]
        repository = repository_for(sessions, FixedDelayPolicy())
        document_id = await activated(repository, 5)
        job = (await claim(repository))[0]
        await set_delivery_count(engine, job.job_id, 5)
        await repository.fail_job(failure(job, "parser-unavailable"))

        row = await job_row(engine, job.job_id)
        assert row["status"] == "failed"
        assert row["error_code"] == "parser-unavailable"
        visible = await repository.get_document(DocumentId(document_id))
        assert visible is not None and visible.status is DocumentState.FAILED

    run(owned_module_database_url, body)


def test_non_transient_failure_stays_terminal(owned_module_database_url: str) -> None:
    async def body(engine, sessions) -> None:  # type: ignore[no-untyped-def]
        repository = repository_for(sessions)
        document_id = await activated(repository, 6)
        job = (await claim(repository))[0]
        await repository.fail_job(failure(job, "invalid-document"))

        row = await job_row(engine, job.job_id)
        assert row["status"] == "failed"
        assert row["error_code"] == "invalid-document"
        visible = await repository.get_document(DocumentId(document_id))
        assert visible is not None
        assert visible.status is DocumentState.FAILED
        assert visible.error_code == "invalid-document"

    run(owned_module_database_url, body)


def test_user_retry_resets_the_delivery_count(owned_module_database_url: str) -> None:
    async def body(engine, sessions) -> None:  # type: ignore[no-untyped-def]
        repository = repository_for(sessions)
        document_id = await activated(repository, 7)
        first = (await claim(repository))[0]
        await set_delivery_count(engine, first.job_id, 5)
        await expire_lease(engine, first.job_id)
        assert await claim(repository) == ()

        retried = await repository.retry_failed(
            DocumentId(document_id), datetime(2026, 11, 3, 10, 5)
        )
        assert retried.attempt == 2
        row = await job_row(engine, first.job_id)
        assert row["status"] == "pending"
        assert row["delivery_count"] == 0
        assert [job.job_id for job in await claim(repository)] == [first.job_id]
        assert (await job_row(engine, first.job_id))["delivery_count"] == 1

    run(owned_module_database_url, body)
