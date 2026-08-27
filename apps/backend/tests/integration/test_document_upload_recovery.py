from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterable
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tap.interfaces.http.dependencies import UploadInput
from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.knowledge.application.documents import DocumentService
from tap.modules.knowledge.domain.documents import canonical_sha256
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    DocumentId,
    ReserveUpload,
    StagedOriginal,
)
from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable
from tap.platform.db.session import create_engine_and_session_factory

DATABASE_URL = os.getenv(
    "TAP_DATABASE_URL",
    "mysql+asyncmy://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4",
)


async def content(value: bytes) -> AsyncIterable[bytes]:
    yield value


def markdown_upload(filename: str, value: bytes) -> UploadInput:
    return UploadInput(filename=filename, media_type="text/markdown", content=content(value))


class DurableArtifactFake:
    """Durable fake whose state survives service/repository reconstruction."""

    def __init__(self) -> None:
        self.staging: dict[str, tuple[StagedOriginal, bytes]] = {}
        self.formal: dict[str, bytes] = {}
        self.sequence = 0
        self.fail_cleanup = False
        self.block_cleanup = False
        self.cleanup_started = asyncio.Event()
        self.allow_cleanup = asyncio.Event()

    async def stage_original(self, upload: UploadInput, *, max_bytes: int) -> StagedOriginal:
        data = b"".join([part async for part in upload.content])
        if len(data) > max_bytes:
            raise AssertionError("test fixture must remain under the service limit")
        self.sequence += 1
        staged = StagedOriginal(
            staging_key=f"staging:{self.sequence}",
            filename=upload.filename,
            media_type=upload.media_type,
            size=len(data),
            source_content_hash=canonical_sha256(data),
        )
        self.staging[staged.staging_key] = (staged, data)
        return staged

    async def commit_original(self, staged: StagedOriginal, revision_id: str) -> ArtifactLocator:
        self.formal.setdefault(revision_id, self.staging[staged.staging_key][1])
        return ArtifactLocator(f"formal:{revision_id}")

    async def recover_original(self, staging_key: str, revision_id: str) -> ArtifactLocator:
        if revision_id not in self.formal:
            self.formal[revision_id] = self.staging[staging_key][1]
        return ArtifactLocator(f"formal:{revision_id}")

    async def discard_staged(self, staged: StagedOriginal) -> None:
        await self.discard_staging(staged.staging_key)

    async def discard_staging(self, staging_key: str) -> None:
        if self.fail_cleanup:
            raise RuntimeError("injected durable staging cleanup failure")
        if self.block_cleanup:
            self.cleanup_started.set()
            await asyncio.wait_for(self.allow_cleanup.wait(), timeout=5)
        self.staging.pop(staging_key, None)


async def clean(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM outbox WHERE aggregate_type='knowledge_document'")
        )
        await connection.execute(text("DELETE FROM knowledge_ingestion_job"))
        await connection.execute(text("UPDATE knowledge_document SET current_revision_id=NULL"))
        await connection.execute(text("DELETE FROM knowledge_document_revision"))
        await connection.execute(text("DELETE FROM knowledge_document"))


async def expire_reservations(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE knowledge_document "
                "SET reservation_expires_at=UTC_TIMESTAMP(6) - INTERVAL 1 SECOND"
            )
        )


def test_claimed_job_survives_repository_reconstruction() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        try:
            first_repository = MysqlDocumentRepository(sessions)
            reservation = await first_repository.reserve_upload(
                ReserveUpload(
                    filename="restart.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "f" * 64,
                    size=12,
                    now=datetime(2026, 8, 27, 9, 0),
                    staging_key="staging:restart",
                )
            )
            created = await first_repository.activate_upload(
                reservation, ArtifactLocator("blob:restart")
            )
            del first_repository

            claimed = await MysqlDocumentRepository(sessions).claim_jobs(
                worker_id="worker-b",
                now=datetime(2026, 8, 27, 9, 1),
                lease_duration=timedelta(seconds=30),
                limit=10,
            )
            assert [job.job_id for job in claimed] == [created.job_id]
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_promotion_crash_is_taken_over_after_service_and_repository_reconstruction() -> None:
    """Losing the process after formal promotion must not strand hidden capacity forever."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        artifacts = DurableArtifactFake()
        try:
            staged = await artifacts.stage_original(
                markdown_upload("crash.md", b"# survives crash"), max_bytes=1024
            )
            first_repository = MysqlDocumentRepository(sessions)
            reservation = await first_repository.reserve_upload(
                ReserveUpload.from_staged(staged, now=datetime(2026, 8, 27, 9, 0))
            )
            await artifacts.commit_original(staged, reservation.revision_id)
            del first_repository

            await expire_reservations(engine)
            reconstructed = DocumentService(
                repository=MysqlDocumentRepository(sessions), artifacts=artifacts
            )
            recovered = await reconstructed.recover_uploads(
                worker_id="recovery-a", lease_duration=timedelta(seconds=30), limit=10
            )

            assert recovered == 1
            assert artifacts.staging == {}
            assert artifacts.formal[reservation.revision_id] == b"# survives crash"
            visible = await MysqlDocumentRepository(sessions).get_document(
                DocumentId(reservation.document_id)
            )
            assert visible is not None
            async with engine.connect() as connection:
                revision_count = await connection.scalar(
                    text("SELECT COUNT(*) FROM knowledge_document_revision")
                )
                job_count = await connection.scalar(
                    text("SELECT COUNT(*) FROM knowledge_ingestion_job")
                )
                outbox_count = await connection.scalar(
                    text("SELECT COUNT(*) FROM outbox WHERE aggregate_type='knowledge_document'")
                )
            assert (revision_count, job_count, outbox_count) == (1, 1, 1)
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_cleanup_failure_remains_a_durable_fact_until_reconstructed_recovery() -> None:
    """A swallowed cleanup error would orphan staging with no restart-visible work."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        artifacts = DurableArtifactFake()
        artifacts.fail_cleanup = True
        try:
            service = DocumentService(
                repository=MysqlDocumentRepository(sessions), artifacts=artifacts
            )
            with pytest.raises(KnowledgeRuntimeUnavailable) as caught:
                await service.upload(markdown_upload("cleanup.md", b"# cleanup fact"))
            assert "durable staging cleanup failure" not in str(caught.value)

            async with engine.connect() as connection:
                recovery_locator = await connection.scalar(
                    text("SELECT staging_blob_locator FROM knowledge_document")
                )
            assert recovery_locator in artifacts.staging

            artifacts.fail_cleanup = False
            await expire_reservations(engine)
            reconstructed = DocumentService(
                repository=MysqlDocumentRepository(sessions), artifacts=artifacts
            )
            assert (
                await reconstructed.recover_uploads(
                    worker_id="recovery-b", lease_duration=timedelta(seconds=30), limit=10
                )
                == 1
            )
            assert artifacts.staging == {}
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text("SELECT staging_blob_locator FROM knowledge_document")
                    )
                    is None
                )
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_activation_holds_cleanup_lease_until_owner_clears_staging_fact() -> None:
    """Recovery must not steal ownership between activation commit and external cleanup."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        artifacts = DurableArtifactFake()
        artifacts.block_cleanup = True
        try:
            repository = MysqlDocumentRepository(sessions)
            upload = asyncio.create_task(
                DocumentService(repository=repository, artifacts=artifacts).upload(
                    markdown_upload("cleanup-window.md", b"# cleanup window")
                )
            )
            await asyncio.wait_for(artifacts.cleanup_started.wait(), timeout=5)

            claims = await repository.claim_upload_recoveries(
                worker_id="premature-recovery",
                lease_duration=timedelta(seconds=30),
                limit=10,
            )
            assert claims == ()

            artifacts.allow_cleanup.set()
            accepted = await upload
            assert accepted.document.status.value == "queued"
            async with engine.connect() as connection:
                recovery_fact = (
                    await connection.execute(
                        text(
                            "SELECT staging_blob_locator, reservation_owner_token, "
                            "reservation_expires_at FROM knowledge_document"
                        )
                    )
                ).one()
            assert tuple(recovery_fact) == (None, None, None)
        finally:
            artifacts.allow_cleanup.set()
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_expired_cleanup_takeover_does_not_turn_activated_upload_into_failure() -> None:
    """An old owner losing its cleanup token delegates recovery without failing upload."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        artifacts = DurableArtifactFake()
        artifacts.block_cleanup = True
        try:
            repository = MysqlDocumentRepository(sessions)
            upload = asyncio.create_task(
                DocumentService(repository=repository, artifacts=artifacts).upload(
                    markdown_upload("cleanup-takeover.md", b"# cleanup takeover")
                )
            )
            await asyncio.wait_for(artifacts.cleanup_started.wait(), timeout=5)
            await expire_reservations(engine)
            takeover = await repository.claim_upload_recoveries(
                worker_id="cleanup-takeover",
                lease_duration=timedelta(seconds=30),
                limit=10,
            )
            assert len(takeover) == 1 and takeover[0].activated is True

            artifacts.allow_cleanup.set()
            accepted = await upload
            assert accepted.document.status.value == "queued"

            await artifacts.discard_staging(takeover[0].reservation.staging_key)
            await repository.complete_upload_cleanup(
                takeover[0].reservation.reservation_id,
                takeover[0].reservation.owner_token,
            )
            async with engine.connect() as connection:
                recovery_fact = await connection.scalar(
                    text("SELECT staging_blob_locator FROM knowledge_document")
                )
            assert recovery_fact is None
        finally:
            artifacts.allow_cleanup.set()
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_duplicate_helper_activates_owner_staging_without_a_second_dispatch_path() -> None:
    """A helper may finish the durable owner copy but must not create another formal path."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        artifacts = DurableArtifactFake()
        try:
            owner_staged = await artifacts.stage_original(
                markdown_upload("owner.md", b"# shared"), max_bytes=1024
            )
            owner = await MysqlDocumentRepository(sessions).reserve_upload(
                ReserveUpload.from_staged(owner_staged, now=datetime(2026, 8, 27, 9, 0))
            )

            helper = DocumentService(
                repository=MysqlDocumentRepository(sessions), artifacts=artifacts
            )
            accepted = await helper.upload(markdown_upload("renamed.md", b"# shared"))

            assert accepted.duplicate is True
            assert accepted.document.document_id == owner.document_id
            assert set(artifacts.formal) == {owner.revision_id}
            assert set(artifacts.staging) == {owner_staged.staging_key}
            async with engine.connect() as connection:
                job_count = await connection.scalar(
                    text("SELECT COUNT(*) FROM knowledge_ingestion_job")
                )
                outbox_count = await connection.scalar(
                    text("SELECT COUNT(*) FROM outbox WHERE aggregate_type='knowledge_document'")
                )
            assert job_count == outbox_count == 1

            await expire_reservations(engine)
            assert (
                await DocumentService(
                    repository=MysqlDocumentRepository(sessions), artifacts=artifacts
                ).recover_uploads(
                    worker_id="helper-cleanup",
                    lease_duration=timedelta(seconds=30),
                    limit=10,
                )
                == 1
            )
            assert artifacts.staging == {}
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_duplicate_helper_uses_own_bytes_when_owner_staging_is_missing() -> None:
    """Destroying helper bytes before promotion would make the hidden document unrecoverable."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await clean(engine)
        artifacts = DurableArtifactFake()
        try:
            owner_staged = await artifacts.stage_original(
                markdown_upload("owner-missing.md", b"# shared fallback"), max_bytes=1024
            )
            owner = await MysqlDocumentRepository(sessions).reserve_upload(
                ReserveUpload.from_staged(owner_staged, now=datetime(2026, 8, 27, 9, 0))
            )
            artifacts.staging.pop(owner_staged.staging_key)

            accepted = await DocumentService(
                repository=MysqlDocumentRepository(sessions), artifacts=artifacts
            ).upload(markdown_upload("helper.md", b"# shared fallback"))

            assert accepted.duplicate is True
            assert accepted.document.document_id == owner.document_id
            assert artifacts.formal[owner.revision_id] == b"# shared fallback"
            assert artifacts.staging == {}
            async with engine.connect() as connection:
                counts = (
                    await connection.scalar(
                        text("SELECT COUNT(*) FROM knowledge_document_revision")
                    ),
                    await connection.scalar(text("SELECT COUNT(*) FROM knowledge_ingestion_job")),
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM outbox WHERE aggregate_type='knowledge_document'"
                        )
                    ),
                )
            assert counts == (1, 1, 1)
        finally:
            await clean(engine)
            await engine.dispose()

    asyncio.run(scenario())
