from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import datetime, timedelta

from sqlalchemy import text

from tap.entrypoints import athena_ingestion_worker
from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.knowledge.application.ingestion import IngestionWorker
from tap.modules.knowledge.domain.documents import DocumentId
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    EmbeddingArtifact,
    IndexReceipt,
    JobFailure,
    JobStage,
    JobStageCommit,
    ManifestChunk,
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


async def _clean(engine) -> None:  # type: ignore[no-untyped-def]
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM outbox WHERE aggregate_type = 'knowledge_document'")
        )
        await connection.execute(text("UPDATE knowledge_document SET current_revision_id = NULL"))
        for table in KNOWLEDGE_TABLES:
            await connection.execute(text(f"DELETE FROM {table}"))


class DurableArtifacts:
    def __init__(self, embeddings: EmbeddingArtifact) -> None:
        self.embeddings = embeddings

    async def read_chunks(self, locator):  # type: ignore[no-untyped-def]
        from tap.modules.knowledge.domain.documents import ChunkDraft, DocumentId

        del locator
        return (
            ChunkDraft(
                chunk_id="chunk-real-recovery",  # type: ignore[arg-type]
                logical_chunk_id="logical-real-recovery",  # type: ignore[arg-type]
                root_id=DocumentId(self.document_id),
                parent_id=None,
                content="durable fact",
                anchor_json='{"blockId":"b-1"}',
                source_content_hash="sha256:" + "a" * 64,
                chunk_content_hash="sha256:" + "b" * 64,
            ),
        )

    async def read_embeddings(self, locator):  # type: ignore[no-untyped-def]
        del locator
        return self.embeddings

    async def delete_revision_artifacts(self, target):  # type: ignore[no-untyped-def]
        del target


class NeverCalled:
    def __init__(self) -> None:
        self.calls = 0

    def parse(self, source):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise AssertionError("completed parse stage was repeated")

    def chunk(self, artifact):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise AssertionError("completed chunk stage was repeated")

    async def embed_documents(self, texts, *, model_alias):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise AssertionError("completed embedding stage was repeated")


class UnavailableWakeups:
    async def wait(self, *, max_wait_seconds: float):
        del max_wait_seconds
        raise ConnectionError("redis unavailable during recovery")

    async def ack(self, wakeup):  # type: ignore[no-untyped-def]
        raise AssertionError(f"unavailable Redis cannot produce wakeup {wakeup}")


class RecordingIndex:
    def __init__(self) -> None:
        self.rows: set[str] = set()

    async def upsert_revision(self, work, chunks, embeddings, *, index_version):  # type: ignore[no-untyped-def]
        self.rows = {str(chunk.chunk_id) for chunk in chunks}
        return IndexReceipt(work.revision_id, index_version, len(self.rows))


class BlockingIndex(RecordingIndex):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.settled = asyncio.Event()
        self.fenced_revisions: set[str] = set()

    async def upsert_revision(self, work, chunks, embeddings, *, index_version):  # type: ignore[no-untyped-def]
        self.started.set()
        try:
            await self.release.wait()
            if work.revision_id not in self.fenced_revisions:
                self.rows = {str(chunk.chunk_id) for chunk in chunks}
            return IndexReceipt(work.revision_id, index_version, len(chunks))
        finally:
            self.settled.set()

    async def fence_revision(self, target):  # type: ignore[no-untyped-def]
        self.fenced_revisions.add(target.revision_id)

    async def delete_revision(self, target):  # type: ignore[no-untyped-def]
        for chunk_id in target.chunk_ids:
            self.rows.discard(chunk_id)

    async def count_revision(self, target):  # type: ignore[no-untyped-def]
        return sum(chunk_id in self.rows for chunk_id in target.chunk_ids)


def test_real_mysql_restart_resumes_from_persisted_embedding_artifact() -> None:
    """A fresh process must publish from MySQL/blob facts without repeating model work."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await _clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reservation = await repository.reserve_upload(
                ReserveUpload(
                    filename="source.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "a" * 64,
                    size=12,
                    now=datetime(2026, 8, 28, 9, 0),
                    staging_key="staging:worker-recovery",
                )
            )
            await repository.activate_upload(reservation, ArtifactLocator("artifact:original"))
            first = (
                await repository.claim_jobs(
                    worker_id="worker-before-crash",
                    now=datetime(2026, 8, 28, 9, 0),
                    lease_duration=timedelta(seconds=1),
                    limit=1,
                )
            )[0]
            await repository.commit_stage(
                JobStageCommit(first.job_id, first.lease_token, JobStage.STORED, datetime.now())
            )
            await repository.commit_stage(
                JobStageCommit(
                    first.job_id,
                    first.lease_token,
                    JobStage.PARSING,
                    datetime.now(),
                    normalized_locator=ArtifactLocator("artifact:normalized"),
                )
            )
            manifest = (
                ManifestChunk(
                    chunk_id="chunk-real-recovery",
                    logical_chunk_id="logical-real-recovery",
                    ordinal=0,
                    root_id=reservation.document_id,
                    parent_id=None,
                    anchor_json='{"blockId":"b-1"}',
                    chunk_content_hash="sha256:" + "b" * 64,
                    embedding_model_version="athena-embedding",
                    index_version="athena-doc-v1",
                ),
            )
            await repository.commit_stage(
                JobStageCommit(
                    first.job_id,
                    first.lease_token,
                    JobStage.CHUNKING,
                    datetime.now(),
                    chunks_locator=ArtifactLocator("artifact:chunks"),
                    manifest=manifest,
                )
            )
            await repository.commit_stage(
                JobStageCommit(
                    first.job_id,
                    first.lease_token,
                    JobStage.EMBEDDING,
                    datetime.now(),
                    embeddings_locator=ArtifactLocator("artifact:embeddings"),
                )
            )
            # Simulate a process crash: no failure/retry mutation is made. The new
            # process must discover the stale processing lease from MySQL alone.
            await asyncio.sleep(1.05)

            embeddings = EmbeddingArtifact(
                model_alias="athena-embedding",
                dimension=3,
                vectors=((0.0, 1.0, 2.0),),
            )
            artifacts = DurableArtifacts(embeddings)
            artifacts.document_id = reservation.document_id
            completed_stage = NeverCalled()
            index = RecordingIndex()
            restarted = IngestionWorker(
                repository=MysqlDocumentRepository(sessions),
                artifacts=artifacts,  # type: ignore[arg-type]
                parser=completed_stage,
                chunker=completed_stage,
                embeddings=completed_stage,
                index=index,  # type: ignore[arg-type]
                worker_id="worker-after-crash",
                embedding_model_alias="athena-embedding",
                embedding_dimension=3,
                index_version="athena-doc-v1",
            )

            await athena_ingestion_worker.run_worker_loop(
                worker=restarted,
                wakeups=UnavailableWakeups(),  # type: ignore[arg-type]
                settings=replace(
                    athena_ingestion_worker.load_settings({}),
                    poll_seconds=0.01,
                    wakeup_seconds=0.01,
                ),
                stop=asyncio.Event(),
                max_iterations=1,
            )

            assert completed_stage.calls == 0
            assert index.rows == {"chunk-real-recovery"}
            record = await repository.get_document(reservation.document_id)  # type: ignore[arg-type]
            assert record is not None
            assert record.chunk_count == 1
            assert tuple(stage.state.value for stage in record.stages) == (
                "completed",
                "completed",
                "completed",
                "completed",
                "completed",
                "completed",
            )
        finally:
            await _clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_real_mysql_ordinary_list_excludes_deleting_document_immediately() -> None:
    """The ordinary list is a selection source and must not expose deleting rows."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await _clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reservation = await repository.reserve_upload(
                ReserveUpload(
                    filename="delete.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "d" * 64,
                    size=12,
                    now=datetime(2026, 8, 28, 9, 0),
                    staging_key="staging:list-delete",
                )
            )
            await repository.activate_upload(reservation, ArtifactLocator("artifact:original"))
            await repository.request_delete(DocumentId(reservation.document_id), datetime.now())

            page = await repository.list_documents(None, 10)

            assert page.items == ()
        finally:
            await _clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_real_mysql_deletion_waits_for_cancelled_owner_settlement() -> None:
    """A deletion claim must not race an active owner whose provider call may still finish."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await _clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reservation = await repository.reserve_upload(
                ReserveUpload(
                    filename="race.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "e" * 64,
                    size=12,
                    now=datetime.now(),
                    staging_key="staging:delete-race",
                )
            )
            await repository.activate_upload(reservation, ArtifactLocator("artifact:original"))
            ingestion = (
                await repository.claim_jobs(
                    worker_id="publishing-owner",
                    now=datetime.now(),
                    lease_duration=timedelta(seconds=60),
                    limit=1,
                )
            )[0]
            deletion = await repository.request_delete(
                DocumentId(reservation.document_id), datetime.now()
            )

            blocked = await repository.claim_jobs(
                worker_id="deletion-owner",
                now=datetime.now(),
                lease_duration=timedelta(seconds=60),
                limit=10,
            )
            assert blocked == ()

            await repository.settle_cancelled_job(
                ingestion.job_id,
                ingestion.lease_token,
                ingestion.stage,
                datetime.now(),
            )
            claimable = await repository.claim_jobs(
                worker_id="deletion-owner",
                now=datetime.now(),
                lease_duration=timedelta(seconds=60),
                limit=10,
            )
            assert [job.job_id for job in claimable] == [deletion.job_id]
        finally:
            await _clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_real_mysql_blocked_publish_cannot_resurrect_after_delete() -> None:
    """Delete may finish only after the old publisher settles, then fencing keeps zero durable."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await _clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reservation = await repository.reserve_upload(
                ReserveUpload(
                    filename="blocked.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "a" * 64,
                    size=12,
                    now=datetime.now(),
                    staging_key="staging:blocked-publish-delete",
                )
            )
            await repository.activate_upload(reservation, ArtifactLocator("artifact:original"))
            preparation = (
                await repository.claim_jobs(
                    worker_id="preparation-owner",
                    now=datetime.now(),
                    lease_duration=timedelta(seconds=60),
                    limit=1,
                )
            )[0]
            await repository.commit_stage(
                JobStageCommit(
                    preparation.job_id,
                    preparation.lease_token,
                    JobStage.STORED,
                    datetime.now(),
                )
            )
            await repository.commit_stage(
                JobStageCommit(
                    preparation.job_id,
                    preparation.lease_token,
                    JobStage.PARSING,
                    datetime.now(),
                    normalized_locator=ArtifactLocator("artifact:normalized"),
                )
            )
            manifest = (
                ManifestChunk(
                    chunk_id="chunk-real-recovery",
                    logical_chunk_id="logical-real-recovery",
                    ordinal=0,
                    root_id=reservation.document_id,
                    parent_id=None,
                    anchor_json='{"blockId":"b-1"}',
                    chunk_content_hash="sha256:" + "b" * 64,
                    embedding_model_version="athena-embedding",
                    index_version="athena-doc-v1",
                ),
            )
            await repository.commit_stage(
                JobStageCommit(
                    preparation.job_id,
                    preparation.lease_token,
                    JobStage.CHUNKING,
                    datetime.now(),
                    chunks_locator=ArtifactLocator("artifact:chunks"),
                    manifest=manifest,
                )
            )
            await repository.commit_stage(
                JobStageCommit(
                    preparation.job_id,
                    preparation.lease_token,
                    JobStage.EMBEDDING,
                    datetime.now(),
                    embeddings_locator=ArtifactLocator("artifact:embeddings"),
                )
            )
            await repository.fail_job(
                JobFailure(
                    preparation.job_id,
                    preparation.lease_token,
                    JobStage.PUBLISHING,
                    "index-unavailable",
                    datetime.now(),
                )
            )
            await repository.retry_failed(DocumentId(reservation.document_id), datetime.now())

            artifacts = DurableArtifacts(
                EmbeddingArtifact("athena-embedding", 3, ((0.0, 1.0, 2.0),))
            )
            artifacts.document_id = reservation.document_id
            completed_stage = NeverCalled()
            index = BlockingIndex()
            publisher = IngestionWorker(
                repository=repository,
                artifacts=artifacts,  # type: ignore[arg-type]
                parser=completed_stage,
                chunker=completed_stage,
                embeddings=completed_stage,
                index=index,  # type: ignore[arg-type]
                worker_id="blocked-publisher",
                embedding_model_alias="athena-embedding",
                embedding_dimension=3,
                index_version="athena-doc-v1",
            )
            deleting_worker = IngestionWorker(
                repository=MysqlDocumentRepository(sessions),
                artifacts=artifacts,  # type: ignore[arg-type]
                parser=completed_stage,
                chunker=completed_stage,
                embeddings=completed_stage,
                index=index,  # type: ignore[arg-type]
                worker_id="deleting-worker",
                embedding_model_alias="athena-embedding",
                embedding_dimension=3,
                index_version="athena-doc-v1",
            )

            publish_task = asyncio.create_task(publisher.run_once(limit=1))
            await index.started.wait()
            await repository.request_delete(DocumentId(reservation.document_id), datetime.now())
            assert (await deleting_worker.run_once(limit=1)).claimed == 0

            index.release.set()
            publish_result = await publish_task
            assert publish_result.lease_lost == 1
            assert index.settled.is_set()
            assert index.rows == {"chunk-real-recovery"}

            delete_result = await deleting_worker.run_once(limit=1)
            assert delete_result.deleted == 1
            assert index.rows == set()
            await asyncio.sleep(0)
            assert index.rows == set()
            assert (
                await repository.get_document(
                    DocumentId(reservation.document_id), include_deleting=True
                )
                is None
            )
        finally:
            await _clean(engine)
            await engine.dispose()

    asyncio.run(scenario())
