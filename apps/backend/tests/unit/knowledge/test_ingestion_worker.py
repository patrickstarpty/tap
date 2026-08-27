from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from tap.modules.knowledge.application.ingestion import IngestionWorker
from tap.modules.knowledge.domain.documents import (
    BlockKind,
    ChunkDraft,
    DocumentId,
    DocumentParseRejected,
    DocumentSource,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    RevisionId,
    canonical_sha256,
)
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    ClaimedIngestionJob,
    DeletionTarget,
    DocumentState,
    EmbeddingArtifact,
    IndexReceipt,
    IngestionWork,
    JobFailure,
    JobKind,
    JobLeaseLost,
    JobRetry,
    JobStage,
    JobStageCommit,
    JobState,
    ManifestChunk,
    initial_stage_results,
)

NOW = datetime(2026, 8, 28, 9, 0, 0)
DOCUMENT_ID = "doc_" + "1" * 32
REVISION_ID = "rev_" + "2" * 64
SOURCE_BYTES = b"# Source\nPersistent fact"
SOURCE_HASH = canonical_sha256(SOURCE_BYTES)


class FakeClock:
    def __init__(self) -> None:
        self.current = NOW
        self.sleeps: asyncio.Queue[float] = asyncio.Queue()

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        await self.sleeps.put(seconds)
        self.current += timedelta(seconds=seconds)
        await asyncio.sleep(0)


def _claimed(*, kind: JobKind = JobKind.INGESTION) -> ClaimedIngestionJob:
    return ClaimedIngestionJob(
        job_id="job-1" if kind is JobKind.INGESTION else "delete-1",
        revision_id=REVISION_ID,
        kind=kind,
        attempt=1,
        status=JobState.PROCESSING,
        stage=JobStage.STORED,
        stages=initial_stage_results(NOW),
        lease_owner="worker-a",
        lease_token="lease-a",
        lease_until=NOW + timedelta(seconds=60),
    )


def _source_work(job: ClaimedIngestionJob) -> IngestionWork:
    return IngestionWork(
        job_id=job.job_id,
        lease_token=job.lease_token,
        kind=job.kind,
        stage=job.stage,
        document_id=DOCUMENT_ID,
        revision_id=REVISION_ID,
        filename="source.md",
        media_type=MediaType.MARKDOWN.value,
        source_content_hash=SOURCE_HASH,
        original_locator=ArtifactLocator("artifact:original"),
        normalized_locator=None,
        chunks_locator=None,
        embeddings_locator=None,
        parser_version="parser-v1",
        chunker_version="chunker-v1",
        pipeline_version="pipeline-v1",
        manifest=(),
    )


class StatefulRepository:
    """Durable in-memory ledger; worker instances keep no stage state themselves."""

    def __init__(self, *, kind: JobKind = JobKind.INGESTION) -> None:
        self.job = _claimed(kind=kind)
        self.work = _source_work(self.job)
        self.pending = True
        self.failed: JobFailure | None = None
        self.retry: JobRetry | None = None
        self.document_status = (
            DocumentState.PROCESSING if kind is JobKind.INGESTION else DocumentState.DELETING
        )
        self.chunk_count = 0
        self.commits: list[JobStage] = []
        self.renewals = 0
        self.lose_on_renewal: int | None = None

    async def claim_jobs(self, **kwargs):  # type: ignore[no-untyped-def]
        if not self.pending:
            return ()
        self.pending = False
        return (self.job,)

    async def load_ingestion_work(
        self, job_id: str, lease_token: str, expected_stage: JobStage
    ) -> IngestionWork:
        if (
            job_id != self.job.job_id
            or lease_token != self.job.lease_token
            or expected_stage is not self.work.stage
        ):
            raise JobLeaseLost(job_id)
        return self.work

    async def commit_stage(self, commit: JobStageCommit) -> None:
        if commit.job_id != self.job.job_id or commit.lease_token != self.job.lease_token:
            raise JobLeaseLost(commit.job_id)
        if commit.expected_stage is not self.work.stage:
            raise JobLeaseLost(commit.job_id)
        self.commits.append(commit.expected_stage)
        if commit.normalized_locator is not None:
            self.work = replace(self.work, normalized_locator=commit.normalized_locator)
        if commit.chunks_locator is not None:
            self.work = replace(
                self.work,
                chunks_locator=commit.chunks_locator,
                manifest=commit.manifest,
            )
        if commit.embeddings_locator is not None:
            self.work = replace(self.work, embeddings_locator=commit.embeddings_locator)
        next_stage = {
            JobStage.STORED: JobStage.PARSING,
            JobStage.PARSING: JobStage.CHUNKING,
            JobStage.CHUNKING: JobStage.EMBEDDING,
            JobStage.EMBEDDING: JobStage.PUBLISHING,
            JobStage.PUBLISHING: JobStage.READY,
        }.get(commit.expected_stage)
        if next_stage is None:
            self.document_status = (
                DocumentState.READY
                if self.job.kind is JobKind.INGESTION
                else DocumentState.DELETING
            )
            self.chunk_count = commit.chunk_count or 0
            return
        self.work = replace(self.work, stage=next_stage)
        self.job = replace(self.job, stage=next_stage)

    async def renew_lease(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.renewals += 1
        if self.lose_on_renewal == self.renewals:
            self.job = replace(self.job, lease_token="lease-b", lease_owner="worker-b")
            self.work = replace(self.work, lease_token="lease-b")
            raise JobLeaseLost(self.job.job_id)

    async def settle_cancelled_job(
        self,
        job_id: str,
        lease_token: str,
        expected_stage: JobStage,
        settled_at: datetime,
    ) -> None:
        del expected_stage, settled_at
        if job_id != self.job.job_id or lease_token != self.job.lease_token:
            raise JobLeaseLost(job_id)

    async def renew_cancelled_job_settlement(
        self,
        job_id: str,
        lease_token: str,
        expected_stage: JobStage,
        now: datetime,
        lease_duration: timedelta,
    ) -> None:
        del expected_stage, now, lease_duration
        if job_id != self.job.job_id or lease_token != self.job.lease_token:
            raise JobLeaseLost(job_id)

    async def fail_job(self, failure: JobFailure) -> None:
        if failure.lease_token != self.work.lease_token:
            raise JobLeaseLost(failure.job_id)
        self.failed = failure
        self.document_status = DocumentState.FAILED

    async def retry_job(self, retry: JobRetry) -> None:
        if retry.lease_token != self.work.lease_token:
            raise JobLeaseLost(retry.job_id)
        self.retry = retry
        self.pending = True


class StatefulArtifacts:
    def __init__(self) -> None:
        self.values: dict[str, object] = {"artifact:original": SOURCE_BYTES}
        self.deleted: set[str] = set()
        self.fail_delete_once = False

    async def read_original(self, locator: ArtifactLocator) -> bytes:
        return self.values[str(locator)]  # type: ignore[return-value]

    async def write_normalized(
        self, revision_id: str, artifact: NormalizedArtifact
    ) -> ArtifactLocator:
        locator = ArtifactLocator(f"artifact:{revision_id}:normalized")
        self.values[str(locator)] = artifact
        return locator

    async def read_normalized(self, locator: ArtifactLocator) -> NormalizedArtifact:
        return self.values[str(locator)]  # type: ignore[return-value]

    async def write_chunks(
        self, revision_id: str, chunks: tuple[ChunkDraft, ...]
    ) -> ArtifactLocator:
        locator = ArtifactLocator(f"artifact:{revision_id}:chunks")
        self.values[str(locator)] = chunks
        return locator

    async def read_chunks(self, locator: ArtifactLocator) -> tuple[ChunkDraft, ...]:
        return self.values[str(locator)]  # type: ignore[return-value]

    async def write_embeddings(
        self,
        revision_id: str,
        artifact: EmbeddingArtifact,
        *,
        source_content_hash: str,
    ) -> ArtifactLocator:
        assert source_content_hash == SOURCE_HASH
        locator = ArtifactLocator(f"artifact:{revision_id}:embeddings")
        self.values[str(locator)] = artifact
        return locator

    async def read_embeddings(self, locator: ArtifactLocator) -> EmbeddingArtifact:
        return self.values[str(locator)]  # type: ignore[return-value]

    async def delete_revision_artifacts(self, target: DeletionTarget) -> None:
        if self.fail_delete_once:
            self.fail_delete_once = False
            raise RuntimeError("credential=secret provider path")
        for locator in target.artifact_locators:
            self.values.pop(str(locator), None)
            self.deleted.add(str(locator))


class BlockingWriteArtifacts(StatefulArtifacts):
    def __init__(self, blocked_write: str) -> None:
        super().__init__()
        self.blocked_write = blocked_write
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.settled = asyncio.Event()

    async def _block(self, write_name: str) -> None:
        if self.blocked_write != write_name:
            return
        self.started.set()
        try:
            await self.release.wait()
        finally:
            self.settled.set()

    async def write_normalized(
        self, revision_id: str, artifact: NormalizedArtifact
    ) -> ArtifactLocator:
        await self._block("normalized")
        return await super().write_normalized(revision_id, artifact)

    async def write_chunks(
        self, revision_id: str, chunks: tuple[ChunkDraft, ...]
    ) -> ArtifactLocator:
        await self._block("chunks")
        return await super().write_chunks(revision_id, chunks)

    async def write_embeddings(
        self,
        revision_id: str,
        artifact: EmbeddingArtifact,
        *,
        source_content_hash: str,
    ) -> ArtifactLocator:
        await self._block("embeddings")
        return await super().write_embeddings(
            revision_id,
            artifact,
            source_content_hash=source_content_hash,
        )


class Parser:
    def parse(self, source):  # type: ignore[no-untyped-def]
        text = source.content.decode()
        return NormalizedArtifact(
            filename=source.filename,
            media_type=source.media_type,
            source_hash=canonical_sha256(source.content),
            document_id=source.document_id,
            revision_id=source.revision_id,
            blocks=(
                NormalizedBlock(
                    block_id="block-1",
                    kind=BlockKind.PARAGRAPH,
                    text=text,
                    heading_path=(),
                    page=None,
                    paragraph_index=0,
                    start_offset=0,
                    end_offset=len(text),
                ),
            ),
        )


class RejectedParser(Parser):
    def parse(self, source):  # type: ignore[no-untyped-def]
        del source
        raise DocumentParseRejected("ocr-required")


class Chunker:
    def chunk(self, artifact: NormalizedArtifact) -> tuple[ChunkDraft, ...]:
        content = artifact.blocks[0].text
        anchor = json.dumps({"blockId": "block-1"}, separators=(",", ":"))
        return (
            ChunkDraft(
                chunk_id="chunk-1",  # type: ignore[arg-type]
                logical_chunk_id="logical-1",  # type: ignore[arg-type]
                root_id=DocumentId(DOCUMENT_ID),
                parent_id=None,
                content=content,
                anchor_json=anchor,
                source_content_hash=SOURCE_HASH,
                chunk_content_hash=canonical_sha256(content.encode()),
            ),
        )


class BrokenChunker(Chunker):
    def chunk(self, artifact: NormalizedArtifact) -> tuple[ChunkDraft, ...]:
        del artifact
        raise RuntimeError("private parser path and provider exception")


class Embeddings:
    def __init__(self, *, dimension: int = 3) -> None:
        self.dimension = dimension
        self.calls = 0
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None
        self.settled = asyncio.Event()

    async def embed_documents(
        self, texts: tuple[str, ...], *, model_alias: str
    ) -> EmbeddingArtifact:
        self.calls += 1
        self.started.set()
        try:
            if self.release is not None:
                await self.release.wait()
            return EmbeddingArtifact(
                model_alias=model_alias,
                dimension=self.dimension,
                vectors=tuple(
                    tuple(float(index) for index in range(self.dimension)) for _ in texts
                ),
            )
        finally:
            self.settled.set()


class CancellationResistantEmbeddings(Embeddings):
    def __init__(self) -> None:
        super().__init__()
        self.cancel_seen = asyncio.Event()
        self.finish_after_cancel = asyncio.Event()

    async def embed_documents(
        self, texts: tuple[str, ...], *, model_alias: str
    ) -> EmbeddingArtifact:
        self.calls += 1
        self.started.set()
        try:
            while not self.finish_after_cancel.is_set():
                try:
                    await self.finish_after_cancel.wait()
                except asyncio.CancelledError:
                    self.cancel_seen.set()
        finally:
            self.settled.set()
        return EmbeddingArtifact(
            model_alias=model_alias,
            dimension=3,
            vectors=tuple((0.0, 1.0, 2.0) for _ in texts),
        )


class Index:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[float, ...]] = {}
        self.fail_next_upsert = False
        self.fail_next_delete = False
        self.upsert_calls = 0
        self.events: list[str] = []

    async def fence_revision(self, target: DeletionTarget) -> None:
        del target
        self.events.append("fence-index")

    async def upsert_revision(
        self,
        work: IngestionWork,
        chunks: tuple[ChunkDraft, ...],
        embeddings: EmbeddingArtifact,
        *,
        index_version: str,
    ) -> IndexReceipt:
        self.upsert_calls += 1
        if self.fail_next_upsert:
            self.fail_next_upsert = False
            raise RuntimeError("milvus endpoint and token must stay private")
        self.rows = dict(
            zip(
                (str(chunk.chunk_id) for chunk in chunks),
                embeddings.vectors,
                strict=True,
            )
        )
        self.events.append("upsert")
        return IndexReceipt(
            revision_id=work.revision_id,
            index_version=index_version,
            indexed_count=len(self.rows),
        )

    async def delete_revision(self, target: DeletionTarget) -> None:
        self.events.append("delete-index")
        if self.fail_next_delete:
            self.fail_next_delete = False
            raise RuntimeError("injected index delete failure")
        for chunk_id in target.chunk_ids:
            self.rows.pop(chunk_id, None)

    async def count_revision(self, target: DeletionTarget) -> int:
        self.events.append("negative-probe")
        return sum(chunk_id in self.rows for chunk_id in target.chunk_ids)


def worker_parts(
    *, kind: JobKind = JobKind.INGESTION, embedding_dimension: int = 3
) -> tuple[IngestionWorker, StatefulRepository, StatefulArtifacts, Embeddings, Index, FakeClock]:
    repository = StatefulRepository(kind=kind)
    artifacts = StatefulArtifacts()
    embeddings = Embeddings(dimension=embedding_dimension)
    index = Index()
    clock = FakeClock()
    worker = build_worker(repository, artifacts, embeddings, index, clock)
    return worker, repository, artifacts, embeddings, index, clock


def build_worker(
    repository: StatefulRepository,
    artifacts: StatefulArtifacts,
    embeddings: Embeddings,
    index: Index,
    clock: FakeClock,
    *,
    parser: Parser | None = None,
    chunker: Chunker | None = None,
) -> IngestionWorker:
    return IngestionWorker(
        repository=repository,
        artifacts=artifacts,
        parser=parser or Parser(),
        chunker=chunker or Chunker(),
        embeddings=embeddings,
        index=index,
        clock=clock,
        worker_id="worker-a",
        embedding_model_alias="athena-embedding",
        embedding_dimension=3,
        index_version="athena-doc-v1",
    )


async def wait_until_provider_started_or_worker_stopped(
    worker_task: asyncio.Task[object], started: asyncio.Event
) -> None:
    """Fail at the worker boundary instead of hanging when setup never reaches a provider."""

    started_task = asyncio.create_task(started.wait())
    done, _ = await asyncio.wait((worker_task, started_task), return_when=asyncio.FIRST_COMPLETED)
    if worker_task in done:
        if not started_task.done():
            started_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await started_task
        await worker_task
        raise AssertionError("worker stopped before starting the provider operation")
    await started_task


@pytest.mark.asyncio
async def test_ingestion_persists_every_stage_and_ready_projection() -> None:
    """Skipping any durable transition would hide restart progress or its artifact."""

    worker, repository, artifacts, embeddings, index, _ = worker_parts()

    result = await worker.run_once(limit=1)

    assert result.claimed == result.ready == 1
    assert result.failed == result.lease_lost == result.deleted == 0
    assert repository.commits == list(JobStage)
    assert repository.document_status is DocumentState.READY
    assert repository.chunk_count == 1
    assert repository.work.normalized_locator is not None
    assert repository.work.chunks_locator is not None
    assert repository.work.embeddings_locator is not None
    assert embeddings.calls == index.upsert_calls == 1
    assert set(index.rows) == {"chunk-1"}
    assert all(
        str(locator) in artifacts.values
        for locator in (
            repository.work.normalized_locator,
            repository.work.chunks_locator,
            repository.work.embeddings_locator,
        )
    )


@pytest.mark.asyncio
async def test_publish_failure_retries_from_embedding_artifact() -> None:
    """Re-running embedding after a publish failure wastes money and can drift vectors."""

    worker, repository, artifacts, embeddings, index, clock = worker_parts()
    index.fail_next_upsert = True

    first = await worker.run_once(limit=1)

    assert first.failed == 1
    assert repository.failed is not None
    assert repository.failed.expected_stage is JobStage.PUBLISHING
    assert repository.failed.error_code == "index-unavailable"
    assert embeddings.calls == 1
    repository.failed = None
    repository.pending = True

    second_worker = build_worker(
        repository,
        artifacts,
        embeddings,
        index,
        clock,
    )
    second = await second_worker.run_once(limit=1)

    assert second.ready == 1
    assert embeddings.calls == 1
    assert index.upsert_calls == 2


@pytest.mark.asyncio
async def test_embedding_dimension_mismatch_fails_before_index_write() -> None:
    """A provider dimension drift must never reach the durable index projection."""

    worker, repository, _, _, index, _ = worker_parts(embedding_dimension=2)

    result = await worker.run_once(limit=1)

    assert result.failed == 1
    assert repository.failed is not None
    assert repository.failed.expected_stage is JobStage.EMBEDDING
    assert repository.failed.error_code == "embedding-dimension-mismatch"
    assert index.upsert_calls == 0
    assert index.rows == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [JobStage.EMBEDDING, JobStage.PUBLISHING])
@pytest.mark.parametrize(
    ("drift", "error_code"),
    [
        ("model", "embedding-dimension-mismatch"),
        ("index", "index-reconciliation-failed"),
    ],
)
async def test_durable_manifest_version_drift_stops_before_provider_write(
    stage: JobStage,
    drift: str,
    error_code: str,
) -> None:
    """A restarted worker must reject model/index drift before model or index I/O."""

    worker, repository, artifacts, embeddings, index, _ = worker_parts()
    chunks = Chunker().chunk(
        Parser().parse(
            DocumentSource(
                content=SOURCE_BYTES,
                filename="source.md",
                media_type=MediaType.MARKDOWN,
                document_id=DocumentId(DOCUMENT_ID),
                revision_id=RevisionId(REVISION_ID),
            )
        )
    )
    chunks_locator = await artifacts.write_chunks(REVISION_ID, chunks)
    manifest = (
        ManifestChunk(
            chunk_id=str(chunks[0].chunk_id),
            logical_chunk_id=str(chunks[0].logical_chunk_id),
            ordinal=0,
            root_id=DOCUMENT_ID,
            parent_id=None,
            anchor_json=chunks[0].anchor_json,
            chunk_content_hash=chunks[0].chunk_content_hash,
            embedding_model_version=("old-model-alias" if drift == "model" else "athena-embedding"),
            index_version="old-index-version" if drift == "index" else "athena-doc-v1",
        ),
    )
    repository.job = replace(repository.job, stage=stage)
    repository.work = replace(
        repository.work,
        stage=stage,
        chunks_locator=chunks_locator,
        manifest=manifest,
    )
    if stage is JobStage.PUBLISHING:
        repository.work = replace(
            repository.work,
            embeddings_locator=await artifacts.write_embeddings(
                REVISION_ID,
                EmbeddingArtifact("athena-embedding", 3, ((0.0, 1.0, 2.0),)),
                source_content_hash=SOURCE_HASH,
            ),
        )

    result = await worker.run_once(limit=1)

    assert result.failed == 1
    assert repository.failed is not None
    assert repository.failed.error_code == error_code
    assert embeddings.calls == 0
    assert index.upsert_calls == 0


@pytest.mark.asyncio
async def test_cancellation_waits_for_resistant_provider_terminal_state() -> None:
    """Cancellation cannot return while a cancellation-resistant provider remains alive."""

    worker, repository, artifacts, _, index, clock = worker_parts()
    embeddings = CancellationResistantEmbeddings()
    worker = build_worker(repository, artifacts, embeddings, index, clock)
    task = asyncio.create_task(worker.run_once(limit=1))
    await wait_until_provider_started_or_worker_stopped(task, embeddings.started)

    task.cancel("caller-cancelled")
    await embeddings.cancel_seen.wait()
    task.cancel("caller-cancelled-again-during-cleanup")
    await asyncio.sleep(0)
    assert not task.done()
    embeddings.finish_after_cancel.set()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert embeddings.settled.is_set()
        assert not [
            child
            for child in asyncio.all_tasks()
            if child is not asyncio.current_task()
            and child.get_name().startswith(("athena-provider:", "athena-heartbeat:"))
        ]
    finally:
        embeddings.finish_after_cancel.set()
        await embeddings.settled.wait()


@pytest.mark.asyncio
async def test_textless_parser_failure_is_closed_and_stays_at_parsing() -> None:
    """A parser rejection must expose only its safe closed code at the exact stage."""

    _, repository, artifacts, embeddings, index, clock = worker_parts()
    worker = build_worker(
        repository,
        artifacts,
        embeddings,
        index,
        clock,
        parser=RejectedParser(),
    )

    result = await worker.run_once(limit=1)

    assert result.failed == 1
    assert repository.failed is not None
    assert repository.failed.expected_stage is JobStage.PARSING
    assert repository.failed.error_code == "ocr-required"
    assert repository.work.normalized_locator is None


@pytest.mark.asyncio
async def test_chunker_exception_is_sanitized_and_stays_at_chunking() -> None:
    """Raw chunker details must not enter the durable public failure projection."""

    _, repository, artifacts, embeddings, index, clock = worker_parts()
    worker = build_worker(
        repository,
        artifacts,
        embeddings,
        index,
        clock,
        chunker=BrokenChunker(),
    )

    result = await worker.run_once(limit=1)

    assert result.failed == 1
    assert repository.failed is not None
    assert repository.failed.expected_stage is JobStage.CHUNKING
    assert repository.failed.error_code == "invalid-document"
    assert repository.work.chunks_locator is None


@pytest.mark.asyncio
async def test_repeated_run_once_does_not_replay_completed_job() -> None:
    """A completed MySQL job must not be re-run merely because a wake-up duplicates."""

    worker, _, _, embeddings, index, _ = worker_parts()

    assert (await worker.run_once(limit=1)).ready == 1
    second = await worker.run_once(limit=1)

    assert second.claimed == 0
    assert embeddings.calls == index.upsert_calls == 1


@pytest.mark.asyncio
async def test_lease_lost_during_embedding_settles_provider_and_writes_no_result() -> None:
    """An old owner must stop before artifact/checkpoint/index writes after takeover."""

    worker, repository, _, embeddings, index, _ = worker_parts()
    # Normalized/chunks writes now consume two fenced renewals each; the sixth
    # renewal is the embedding heartbeat after the provider has started.
    repository.lose_on_renewal = 6
    embeddings.release = asyncio.Event()

    task = asyncio.create_task(worker.run_once(limit=1))
    await wait_until_provider_started_or_worker_stopped(task, embeddings.started)
    result = await task

    assert embeddings.settled.is_set()
    assert result.lease_lost == 1
    assert repository.work.embeddings_locator is None
    assert repository.commits == [JobStage.STORED, JobStage.PARSING, JobStage.CHUNKING]
    assert index.rows == {}


@pytest.mark.parametrize(
    ("blocked_write", "lost_renewal", "locator_name"),
    [
        ("normalized", 2, "normalized_locator"),
        ("chunks", 4, "chunks_locator"),
        ("embeddings", 8, "embeddings_locator"),
    ],
)
@pytest.mark.asyncio
async def test_every_artifact_write_is_cancelled_and_settled_on_lease_loss(
    blocked_write: str,
    lost_renewal: int,
    locator_name: str,
) -> None:
    """Bypassing the shared external-write boundary permits late revision artifacts."""

    _, repository, _, embeddings, index, clock = worker_parts()
    artifacts = BlockingWriteArtifacts(blocked_write)
    repository.lose_on_renewal = lost_renewal
    worker = build_worker(repository, artifacts, embeddings, index, clock)
    task = asyncio.create_task(worker.run_once(limit=1))
    await wait_until_provider_started_or_worker_stopped(task, artifacts.started)

    result = await task

    assert result.lease_lost == 1
    assert artifacts.settled.is_set()
    assert getattr(repository.work, locator_name) is None
    assert index.rows == {}


@pytest.mark.asyncio
async def test_cancellation_settles_provider_without_recording_failure() -> None:
    """Treating cancellation as a normal provider failure would falsely invite retry."""

    worker, repository, _, embeddings, index, _ = worker_parts()
    embeddings.release = asyncio.Event()
    task = asyncio.create_task(worker.run_once(limit=1))
    await wait_until_provider_started_or_worker_stopped(task, embeddings.started)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert embeddings.settled.is_set()
    assert repository.failed is None
    assert repository.work.embeddings_locator is None
    assert index.rows == {}


@pytest.mark.asyncio
async def test_delete_failure_stays_deleting_and_automatically_retries_in_order() -> None:
    """Finalizing the ledger before projection/artifact cleanup leaves searchable ghosts."""

    worker, repository, artifacts, _, index, _ = worker_parts(kind=JobKind.DELETION)
    repository.work = replace(
        repository.work,
        manifest=(
            ManifestChunk(
                chunk_id="chunk-1",
                logical_chunk_id="logical-1",
                ordinal=0,
                root_id=DOCUMENT_ID,
                parent_id=None,
                anchor_json='{"blockId":"block-1"}',
                chunk_content_hash="sha256:" + "4" * 64,
                embedding_model_version="athena-embedding",
                index_version="athena-doc-v1",
            ),
        ),
        normalized_locator=ArtifactLocator("artifact:normalized"),
        chunks_locator=ArtifactLocator("artifact:chunks"),
        embeddings_locator=ArtifactLocator("artifact:embeddings"),
    )
    artifacts.values.update(
        {
            "artifact:normalized": object(),
            "artifact:chunks": object(),
            "artifact:embeddings": object(),
        }
    )
    index.rows["chunk-1"] = (0.0, 1.0, 2.0)
    artifacts.fail_delete_once = True

    first = await worker.run_once(limit=1)

    assert first.failed == 1
    assert repository.document_status is DocumentState.DELETING
    assert repository.retry is not None
    assert repository.retry.expected_stage is JobStage.PARSING
    assert index.rows == {}
    assert index.events[:3] == ["fence-index", "delete-index", "negative-probe"]

    second = await worker.run_once(limit=1)

    assert second.deleted == 1
    assert index.rows == {}
    assert repository.commits == list(JobStage)
    assert set(artifacts.deleted) == {
        "artifact:original",
        "artifact:normalized",
        "artifact:chunks",
        "artifact:embeddings",
    }


@pytest.mark.parametrize("limit", [0, -1, 51, True, 1.5])
@pytest.mark.asyncio
async def test_run_once_rejects_unbounded_or_non_integer_limits(limit: object) -> None:
    worker, *_ = worker_parts()

    with pytest.raises(ValueError, match="between 1 and 50"):
        await worker.run_once(limit=limit)  # type: ignore[arg-type]
