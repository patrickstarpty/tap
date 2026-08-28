from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from datetime import datetime, timedelta

from sqlalchemy import text

from tap.entrypoints import athena_ingestion_worker
from tap.modules.knowledge.adapters import mysql_documents
from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.knowledge.application import ingestion
from tap.modules.knowledge.application.ingestion import IngestionWorker
from tap.modules.knowledge.domain.documents import (
    ChunkDraft,
    DocumentId,
    RevisionId,
    canonical_sha256,
    chunk_id_for,
    logical_chunk_id_for,
)
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
    def __init__(self, embeddings: EmbeddingArtifact, chunk: ChunkDraft) -> None:
        self.embeddings = embeddings
        self.chunk = chunk

    async def read_chunks(self, locator):  # type: ignore[no-untyped-def]
        del locator
        return (self.chunk,)

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

    async def embed_documents(  # type: ignore[no-untyped-def]
        self, texts, *, model_alias, chunk_ids
    ):
        del texts, model_alias, chunk_ids
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
        self.cancel_seen = asyncio.Event()
        self.release = asyncio.Event()
        self.settled = asyncio.Event()
        self.fenced_revisions: set[str] = set()

    async def upsert_revision(self, work, chunks, embeddings, *, index_version):  # type: ignore[no-untyped-def]
        self.started.set()
        try:
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancel_seen.set()
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


class BlockingChunkArtifacts:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.release = asyncio.Event()
        self.settled = asyncio.Event()
        self.revision_artifacts: set[str] = set()

    async def read_normalized(self, locator):  # type: ignore[no-untyped-def]
        del locator
        return object()

    async def write_chunks(self, revision_id, chunks):  # type: ignore[no-untyped-def]
        del chunks
        self.started.set()
        try:
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancel_seen.set()
            self.revision_artifacts.add(str(revision_id))
            return ArtifactLocator(f"artifact:{revision_id}:chunks")
        finally:
            self.settled.set()

    async def delete_revision_artifacts(self, target):  # type: ignore[no-untyped-def]
        self.revision_artifacts.discard(target.revision_id)


class OneChunk:
    def chunk(self, artifact):  # type: ignore[no-untyped-def]
        del artifact
        return (
            ChunkDraft(
                chunk_id="chunk-blocked-artifact",  # type: ignore[arg-type]
                logical_chunk_id="logical-blocked-artifact",  # type: ignore[arg-type]
                root_id=DocumentId(self.document_id),
                parent_id=None,
                content="late artifact",
                anchor_json='{"blockId":"blocked"}',
                source_content_hash="sha256:" + "7" * 64,
                chunk_content_hash="sha256:" + "6" * 64,
            ),
        )


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
            content = "durable fact"
            anchor_json = '{"blockId":"b-1"}'
            chunk_hash = canonical_sha256(content.encode("utf-8"))
            chunk_identity = str(
                chunk_id_for(
                    RevisionId(reservation.revision_id),
                    anchor_json,
                    chunk_hash,
                )
            )
            logical_identity = str(
                logical_chunk_id_for(DocumentId(reservation.document_id), anchor_json)
            )
            manifest = (
                ManifestChunk(
                    chunk_id=chunk_identity,
                    logical_chunk_id=logical_identity,
                    ordinal=0,
                    root_id=reservation.document_id,
                    parent_id=None,
                    anchor_json=anchor_json,
                    chunk_content_hash=chunk_hash,
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
                chunk_ids=(manifest[0].chunk_id,),
            )
            artifact_chunk = ChunkDraft(
                chunk_id=chunk_id_for(
                    RevisionId(reservation.revision_id),
                    anchor_json,
                    chunk_hash,
                ),
                logical_chunk_id=logical_chunk_id_for(
                    DocumentId(reservation.document_id), anchor_json
                ),
                root_id=DocumentId(reservation.document_id),
                parent_id=None,
                content=content,
                anchor_json=anchor_json,
                source_content_hash="sha256:" + "a" * 64,
                chunk_content_hash=chunk_hash,
            )
            artifacts = DurableArtifacts(embeddings, artifact_chunk)
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
                settings=athena_ingestion_worker.WorkerSettings(
                    database_url=DATABASE_URL,
                    redis_url="redis://127.0.0.1:16379/0",
                    job_batch_size=10,
                    poll_seconds=0.01,
                    wakeup_seconds=0.01,
                    stream_name="tap-athena-e2e:commands",
                    group_name="athena-ingestion",
                    worker_id="recovery-worker",
                ),
                stop=asyncio.Event(),
                max_iterations=1,
            )

            assert completed_stage.calls == 0
            assert index.rows == {chunk_identity}
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
            async with engine.connect() as connection:
                barrier_after_first_delete = tuple(
                    (
                        await connection.execute(
                            text(
                                "SELECT lease_token, lease_until "
                                "FROM knowledge_ingestion_job WHERE job_id=:job_id"
                            ),
                            {"job_id": ingestion.job_id},
                        )
                    ).one()
                )
            repeated = await repository.request_delete(
                DocumentId(reservation.document_id), datetime.now()
            )
            assert repeated.job_id == deletion.job_id
            async with engine.connect() as connection:
                barrier_after_repeated_delete = tuple(
                    (
                        await connection.execute(
                            text(
                                "SELECT lease_token, lease_until "
                                "FROM knowledge_ingestion_job WHERE job_id=:job_id"
                            ),
                            {"job_id": ingestion.job_id},
                        )
                    ).one()
                )
            assert barrier_after_repeated_delete == barrier_after_first_delete

            blocked = await repository.claim_jobs(
                worker_id="deletion-owner",
                now=datetime.now(),
                lease_duration=timedelta(seconds=60),
                limit=10,
            )
            assert blocked == ()

            async with engine.connect() as connection:
                lease_before_renewal = (
                    await connection.execute(
                        text(
                            "SELECT lease_until FROM knowledge_ingestion_job WHERE job_id=:job_id"
                        ),
                        {"job_id": ingestion.job_id},
                    )
                ).scalar_one()
            await repository.renew_cancelled_job_settlement(
                ingestion.job_id,
                ingestion.lease_token,
                ingestion.stage,
                datetime.now(),
                timedelta(seconds=1),
            )
            async with engine.connect() as connection:
                lease_after_renewal = (
                    await connection.execute(
                        text(
                            "SELECT lease_until FROM knowledge_ingestion_job WHERE job_id=:job_id"
                        ),
                        {"job_id": ingestion.job_id},
                    )
                ).scalar_one()
            assert lease_after_renewal >= lease_before_renewal

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


def test_real_mysql_cancelled_owner_crash_releases_barrier_only_after_expiry(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Without a live heartbeat, a crashed cancelled owner releases deletion by lease expiry."""

    short_lease = timedelta(milliseconds=180)
    monkeypatch.setattr(
        mysql_documents,
        "CANCELLED_OWNER_SETTLEMENT_LEASE",
        short_lease,
    )

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await _clean(engine)
        try:
            repository = MysqlDocumentRepository(sessions)
            reservation = await repository.reserve_upload(
                ReserveUpload(
                    filename="crashed-owner.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "5" * 64,
                    size=12,
                    now=datetime.now(),
                    staging_key="staging:crashed-owner",
                )
            )
            await repository.activate_upload(reservation, ArtifactLocator("artifact:original"))
            await repository.claim_jobs(
                worker_id="owner-that-crashes",
                now=datetime.now(),
                lease_duration=short_lease,
                limit=1,
            )
            deletion = await repository.request_delete(
                DocumentId(reservation.document_id), datetime.now()
            )
            assert (
                await repository.claim_jobs(
                    worker_id="deletion-before-expiry",
                    now=datetime.now(),
                    lease_duration=timedelta(seconds=30),
                    limit=1,
                )
            ) == ()
            async with engine.connect() as connection:
                retained_until = (
                    await connection.execute(
                        text(
                            "SELECT lease_until FROM knowledge_ingestion_job "
                            "WHERE revision_id=:revision_id AND kind='ingestion'"
                        ),
                        {"revision_id": reservation.revision_id},
                    )
                ).scalar_one()
            async with asyncio.timeout(1):
                while True:
                    async with engine.connect() as connection:
                        database_now = (
                            await connection.execute(text("SELECT UTC_TIMESTAMP(6)"))
                        ).scalar_one()
                    if database_now > retained_until:
                        break
                    await asyncio.sleep(0.01)

            claimed = await repository.claim_jobs(
                worker_id="deletion-after-crash-expiry",
                now=datetime.now(),
                lease_duration=timedelta(seconds=30),
                limit=1,
            )
            assert [job.job_id for job in claimed] == [deletion.job_id]
        finally:
            await _clean(engine)
            await engine.dispose()

    asyncio.run(scenario())


def test_real_mysql_blocked_publish_cannot_resurrect_after_delete(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Delete may finish only after the old publisher settles, then fencing keeps zero durable."""

    short_lease = timedelta(milliseconds=180)
    monkeypatch.setattr(ingestion, "LEASE_DURATION", short_lease)
    monkeypatch.setattr(ingestion, "HEARTBEAT_SECONDS", 0.03)
    monkeypatch.setattr(
        mysql_documents,
        "CANCELLED_OWNER_SETTLEMENT_LEASE",
        short_lease,
    )

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
            content = "durable fact"
            anchor_json = '{"blockId":"b-1"}'
            chunk_hash = canonical_sha256(content.encode("utf-8"))
            chunk_identity = str(
                chunk_id_for(
                    RevisionId(reservation.revision_id),
                    anchor_json,
                    chunk_hash,
                )
            )
            logical_identity = str(
                logical_chunk_id_for(DocumentId(reservation.document_id), anchor_json)
            )
            artifact_chunk = ChunkDraft(
                chunk_id=chunk_id_for(
                    RevisionId(reservation.revision_id),
                    anchor_json,
                    chunk_hash,
                ),
                logical_chunk_id=logical_chunk_id_for(
                    DocumentId(reservation.document_id), anchor_json
                ),
                root_id=DocumentId(reservation.document_id),
                parent_id=None,
                content=content,
                anchor_json=anchor_json,
                source_content_hash="sha256:" + "a" * 64,
                chunk_content_hash=chunk_hash,
            )
            manifest = (
                ManifestChunk(
                    chunk_id=chunk_identity,
                    logical_chunk_id=logical_identity,
                    ordinal=0,
                    root_id=reservation.document_id,
                    parent_id=None,
                    anchor_json=anchor_json,
                    chunk_content_hash=chunk_hash,
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
                EmbeddingArtifact(
                    "athena-embedding",
                    3,
                    ((0.0, 1.0, 2.0),),
                    (manifest[0].chunk_id,),
                ),
                artifact_chunk,
            )
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
            async with engine.connect() as connection:
                retained_until = (
                    await connection.execute(
                        text(
                            "SELECT lease_until FROM knowledge_ingestion_job WHERE job_id=:job_id"
                        ),
                        {"job_id": preparation.job_id},
                    )
                ).scalar_one()
            async with asyncio.timeout(1):
                while True:
                    async with engine.connect() as connection:
                        database_now = (
                            await connection.execute(text("SELECT UTC_TIMESTAMP(6)"))
                        ).scalar_one()
                    if database_now > retained_until:
                        break
                    await asyncio.sleep(0.01)
            assert (await deleting_worker.run_once(limit=1)).claimed == 0
            assert index.cancel_seen.is_set()

            index.release.set()
            publish_result = await publish_task
            assert publish_result.lease_lost == 1
            assert index.settled.is_set()
            assert index.rows == {chunk_identity}

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


def test_real_mysql_blocked_artifact_write_renews_delete_barrier_until_terminal(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """A late chunks write must stay fenced past its original lease and be cleaned after settle."""

    short_lease = timedelta(milliseconds=180)
    monkeypatch.setattr(ingestion, "LEASE_DURATION", short_lease)
    monkeypatch.setattr(ingestion, "HEARTBEAT_SECONDS", 0.03)
    monkeypatch.setattr(
        mysql_documents,
        "CANCELLED_OWNER_SETTLEMENT_LEASE",
        short_lease,
        raising=False,
    )

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await _clean(engine)
        old_task: asyncio.Task | None = None
        artifacts = BlockingChunkArtifacts()
        try:
            repository = MysqlDocumentRepository(sessions)
            reservation = await repository.reserve_upload(
                ReserveUpload(
                    filename="blocked-artifact.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "7" * 64,
                    size=12,
                    now=datetime.now(),
                    staging_key="staging:blocked-artifact",
                )
            )
            await repository.activate_upload(reservation, ArtifactLocator("artifact:original"))
            preparation = (
                await repository.claim_jobs(
                    worker_id="artifact-preparation",
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
            await repository.fail_job(
                JobFailure(
                    preparation.job_id,
                    preparation.lease_token,
                    JobStage.CHUNKING,
                    "artifact-unavailable",
                    datetime.now(),
                )
            )
            await repository.retry_failed(DocumentId(reservation.document_id), datetime.now())

            completed = NeverCalled()
            chunker = OneChunk()
            chunker.document_id = reservation.document_id
            index = BlockingIndex()
            old_owner = IngestionWorker(
                repository=repository,
                artifacts=artifacts,  # type: ignore[arg-type]
                parser=completed,
                chunker=chunker,
                embeddings=completed,
                index=index,  # type: ignore[arg-type]
                worker_id="blocked-artifact-owner",
                embedding_model_alias="athena-embedding",
                embedding_dimension=3,
                index_version="athena-doc-v1",
            )
            deletion_owner = IngestionWorker(
                repository=MysqlDocumentRepository(sessions),
                artifacts=artifacts,  # type: ignore[arg-type]
                parser=completed,
                chunker=chunker,
                embeddings=completed,
                index=index,  # type: ignore[arg-type]
                worker_id="artifact-deletion-owner",
                embedding_model_alias="athena-embedding",
                embedding_dimension=3,
                index_version="athena-doc-v1",
            )

            old_task = asyncio.create_task(old_owner.run_once(limit=1))
            await artifacts.started.wait()
            await repository.request_delete(DocumentId(reservation.document_id), datetime.now())
            async with engine.connect() as connection:
                retained_until = (
                    await connection.execute(
                        text(
                            "SELECT lease_until FROM knowledge_ingestion_job WHERE job_id=:job_id"
                        ),
                        {"job_id": preparation.job_id},
                    )
                ).scalar_one()

            async with asyncio.timeout(1):
                while True:
                    async with engine.connect() as connection:
                        database_now = (
                            await connection.execute(text("SELECT UTC_TIMESTAMP(6)"))
                        ).scalar_one()
                    if database_now > retained_until:
                        break
                    await asyncio.sleep(0.01)

            assert (await deletion_owner.run_once(limit=1)).claimed == 0
            assert artifacts.cancel_seen.is_set()

            artifacts.release.set()
            old_result = await old_task
            assert old_result.lease_lost == 1
            assert artifacts.settled.is_set()
            assert artifacts.revision_artifacts == {reservation.revision_id}

            delete_result = await deletion_owner.run_once(limit=1)
            assert delete_result.deleted == 1
            assert artifacts.revision_artifacts == set()
            assert index.rows == set()
            assert (
                await repository.get_document(
                    DocumentId(reservation.document_id), include_deleting=True
                )
                is None
            )
        finally:
            artifacts.release.set()
            if old_task is not None and not old_task.done():
                old_task.cancel()
            if old_task is not None:
                with suppress(BaseException):
                    await old_task
            await _clean(engine)
            await engine.dispose()

    asyncio.run(scenario())
