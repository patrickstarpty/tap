from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta

from sqlalchemy import text

from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.knowledge.application.ingestion import IngestionWorker
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


class RecordingIndex:
    def __init__(self) -> None:
        self.rows: set[str] = set()

    async def upsert_revision(self, work, chunks, embeddings, *, index_version):  # type: ignore[no-untyped-def]
        self.rows = {str(chunk.chunk_id) for chunk in chunks}
        return IndexReceipt(work.revision_id, index_version, len(self.rows))


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
                    lease_duration=timedelta(seconds=60),
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
            await repository.fail_job(
                JobFailure(
                    first.job_id,
                    first.lease_token,
                    JobStage.PUBLISHING,
                    "index-unavailable",
                    datetime.now(),
                )
            )
            await repository.retry_failed(reservation.document_id, datetime.now())  # type: ignore[arg-type]

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

            result = await restarted.run_once(limit=1)

            assert result.ready == 1
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
