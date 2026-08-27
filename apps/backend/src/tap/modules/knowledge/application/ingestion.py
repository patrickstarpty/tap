"""Recoverable provider-neutral Knowledge ingestion and deletion orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol, TypeVar

from tap.modules.knowledge.domain.documents import (
    ChunkDraft,
    DocumentId,
    DocumentParseRejected,
    DocumentSource,
    MediaType,
    RevisionId,
)
from tap.modules.knowledge.ports.documents import (
    ArtifactStore,
    ClaimedIngestionJob,
    DeletionTarget,
    DocumentChunker,
    DocumentEmbeddingPort,
    DocumentIndexPort,
    DocumentParser,
    DocumentRepository,
    IngestionWork,
    JobFailure,
    JobKind,
    JobLeaseLost,
    JobRetry,
    JobStage,
    JobStageCommit,
    ManifestChunk,
)

T = TypeVar("T")
LEASE_DURATION = timedelta(seconds=60)
HEARTBEAT_SECONDS = 20.0
MAX_WORKER_BATCH = 50
PROVIDER_SETTLE_SECONDS = 5.0

NEXT_STAGE = {
    JobStage.STORED: JobStage.PARSING,
    JobStage.PARSING: JobStage.CHUNKING,
    JobStage.CHUNKING: JobStage.EMBEDDING,
    JobStage.EMBEDDING: JobStage.PUBLISHING,
    JobStage.PUBLISHING: JobStage.READY,
}


class WorkerClock(Protocol):
    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemWorkerClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


@dataclass(frozen=True, slots=True)
class WorkerRun:
    claimed: int
    ready: int
    deleted: int
    failed: int
    lease_lost: int


class _SafeStageError(Exception):
    def __init__(self, stage: JobStage, code: str) -> None:
        self.stage = stage
        self.code = code
        super().__init__(code)


class IngestionWorker:
    """Runs a bounded MySQL-authoritative batch and reloads every durable stage."""

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        artifacts: ArtifactStore,
        parser: DocumentParser,
        chunker: DocumentChunker,
        embeddings: DocumentEmbeddingPort,
        index: DocumentIndexPort,
        worker_id: str,
        embedding_model_alias: str,
        embedding_dimension: int,
        index_version: str,
        clock: WorkerClock | None = None,
    ) -> None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must be nonblank")
        if not isinstance(embedding_model_alias, str) or not embedding_model_alias.strip():
            raise ValueError("embedding_model_alias must be nonblank")
        if type(embedding_dimension) is not int or embedding_dimension < 1:
            raise ValueError("embedding_dimension must be positive")
        if not isinstance(index_version, str) or not index_version.strip():
            raise ValueError("index_version must be nonblank")
        self._repository = repository
        self._artifacts = artifacts
        self._parser = parser
        self._chunker = chunker
        self._embeddings = embeddings
        self._index = index
        self._clock = clock or SystemWorkerClock()
        self._worker_id = worker_id
        self._embedding_model_alias = embedding_model_alias
        self._embedding_dimension = embedding_dimension
        self._index_version = index_version

    async def run_once(self, limit: int) -> WorkerRun:
        if type(limit) is not int or not 1 <= limit <= MAX_WORKER_BATCH:
            raise ValueError("limit must be between 1 and 50")
        jobs = await self._repository.claim_jobs(
            worker_id=self._worker_id,
            now=self._clock.now(),
            lease_duration=LEASE_DURATION,
            limit=limit,
        )
        ready = deleted = failed = lease_lost = 0
        for job in jobs:
            try:
                outcome = await self._process_claimed(job)
            except JobLeaseLost:
                lease_lost += 1
                continue
            except _SafeStageError as error:
                try:
                    if job.kind is JobKind.DELETION:
                        await self._repository.retry_job(
                            JobRetry(
                                job_id=job.job_id,
                                lease_token=job.lease_token,
                                expected_stage=error.stage,
                                error_code=error.code,
                                retry_at=self._clock.now(),
                            )
                        )
                    else:
                        await self._repository.fail_job(
                            JobFailure(
                                job_id=job.job_id,
                                lease_token=job.lease_token,
                                expected_stage=error.stage,
                                error_code=error.code,
                                failed_at=self._clock.now(),
                            )
                        )
                except JobLeaseLost:
                    lease_lost += 1
                    continue
                failed += 1
                continue
            if outcome == "ready":
                ready += 1
            else:
                deleted += 1
        return WorkerRun(
            claimed=len(jobs),
            ready=ready,
            deleted=deleted,
            failed=failed,
            lease_lost=lease_lost,
        )

    async def _process_claimed(self, job: ClaimedIngestionJob) -> str:
        stage = job.stage
        while True:
            work = await self._repository.load_ingestion_work(job.job_id, job.lease_token, stage)
            if work.kind is not job.kind or work.stage is not stage:
                raise JobLeaseLost(job.job_id)
            if job.kind is JobKind.DELETION:
                await self._run_deletion_stage(job, work)
            else:
                await self._run_ingestion_stage(job, work)
            if stage is JobStage.READY:
                return "deleted" if job.kind is JobKind.DELETION else "ready"
            stage = NEXT_STAGE[stage]

    async def _run_ingestion_stage(self, job: ClaimedIngestionJob, work: IngestionWork) -> None:
        stage = work.stage
        if stage is JobStage.STORED:
            await self._commit(job, stage)
            return
        if stage is JobStage.PARSING:
            source_bytes = await self._artifact_call(
                stage, self._artifacts.read_original(work.original_locator)
            )
            try:
                normalized = self._parser.parse(
                    DocumentSource(
                        filename=work.filename,
                        media_type=MediaType(work.media_type),
                        content=source_bytes,
                        document_id=DocumentId(work.document_id),
                        revision_id=RevisionId(work.revision_id),
                    )
                )
            except DocumentParseRejected as error:
                raise _SafeStageError(stage, _closed_parser_code(error.code)) from error
            except Exception as error:
                raise _SafeStageError(stage, "parser-unavailable") from error
            if normalized.source_hash != work.source_content_hash:
                raise _SafeStageError(stage, "invalid-document")
            locator = await self._artifact_call(
                stage, self._artifacts.write_normalized(work.revision_id, normalized)
            )
            await self._commit(job, stage, normalized_locator=locator)
            return
        if stage is JobStage.CHUNKING:
            normalized_locator = _required_locator(work.normalized_locator, stage)
            normalized = await self._artifact_call(
                stage, self._artifacts.read_normalized(normalized_locator)
            )
            try:
                chunks = self._chunker.chunk(normalized)
            except DocumentParseRejected as error:
                raise _SafeStageError(stage, _closed_parser_code(error.code)) from error
            except Exception as error:
                raise _SafeStageError(stage, "invalid-document") from error
            if not chunks:
                raise _SafeStageError(stage, "empty-document")
            manifest = self._manifest(work, chunks)
            locator = await self._artifact_call(
                stage, self._artifacts.write_chunks(work.revision_id, chunks)
            )
            await self._commit(
                job,
                stage,
                chunks_locator=locator,
                manifest=manifest,
            )
            return
        if stage is JobStage.EMBEDDING:
            chunks = await self._read_chunks(work, stage)
            try:
                artifact = await self._provider_call(
                    job,
                    stage,
                    lambda: self._embeddings.embed_documents(
                        tuple(chunk.content for chunk in chunks),
                        model_alias=self._embedding_model_alias,
                    ),
                )
            except JobLeaseLost:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as error:
                raise _SafeStageError(stage, "embedding-unavailable") from error
            if (
                artifact.model_alias != self._embedding_model_alias
                or artifact.dimension != self._embedding_dimension
                or len(artifact.vectors) != len(chunks)
            ):
                raise _SafeStageError(stage, "embedding-dimension-mismatch")
            locator = await self._artifact_call(
                stage, self._artifacts.write_embeddings(work.revision_id, artifact)
            )
            await self._commit(job, stage, embeddings_locator=locator)
            return
        if stage is JobStage.PUBLISHING:
            chunks = await self._read_chunks(work, stage)
            embeddings_locator = _required_locator(work.embeddings_locator, stage)
            embeddings = await self._artifact_call(
                stage, self._artifacts.read_embeddings(embeddings_locator)
            )
            if (
                embeddings.model_alias != self._embedding_model_alias
                or embeddings.dimension != self._embedding_dimension
                or len(embeddings.vectors) != len(work.manifest)
            ):
                raise _SafeStageError(stage, "embedding-dimension-mismatch")
            try:
                receipt = await self._provider_call(
                    job,
                    stage,
                    lambda: self._index.upsert_revision(
                        work,
                        chunks,
                        embeddings,
                        index_version=self._index_version,
                    ),
                )
            except JobLeaseLost:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as error:
                raise _SafeStageError(stage, "index-unavailable") from error
            if (
                receipt.revision_id != work.revision_id
                or receipt.index_version != self._index_version
                or receipt.indexed_count != len(work.manifest)
            ):
                raise _SafeStageError(stage, "index-reconciliation-failed")
            await self._commit(job, stage)
            return
        await self._commit(job, stage, chunk_count=len(work.manifest))

    async def _run_deletion_stage(self, job: ClaimedIngestionJob, work: IngestionWork) -> None:
        stage = work.stage
        target = _deletion_target(work)
        if stage is JobStage.STORED:
            try:
                await self._provider_call(job, stage, lambda: self._index.delete_revision(target))
                count = await self._provider_call(
                    job, stage, lambda: self._index.count_revision(target)
                )
            except JobLeaseLost:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as error:
                raise _SafeStageError(stage, "index-unavailable") from error
            if count != 0:
                raise _SafeStageError(stage, "index-reconciliation-failed")
            await self._commit(job, stage)
            return
        if stage is JobStage.PARSING:
            await self._artifact_call(stage, self._artifacts.delete_revision_artifacts(target))
            await self._commit(job, stage)
            return
        if stage is JobStage.READY:
            await self._commit(job, stage, chunk_count=0)
            return
        await self._commit(job, stage)

    def _manifest(
        self, work: IngestionWork, chunks: tuple[ChunkDraft, ...]
    ) -> tuple[ManifestChunk, ...]:
        manifest: list[ManifestChunk] = []
        for ordinal, chunk in enumerate(chunks):
            if (
                str(chunk.root_id) != work.document_id
                or chunk.source_content_hash != work.source_content_hash
            ):
                raise _SafeStageError(JobStage.CHUNKING, "invalid-document")
            manifest.append(
                ManifestChunk(
                    chunk_id=str(chunk.chunk_id),
                    logical_chunk_id=str(chunk.logical_chunk_id),
                    ordinal=ordinal,
                    root_id=str(chunk.root_id),
                    parent_id=chunk.parent_id,
                    anchor_json=chunk.anchor_json,
                    chunk_content_hash=chunk.chunk_content_hash,
                    embedding_model_version=self._embedding_model_alias,
                    index_version=self._index_version,
                )
            )
        return tuple(manifest)

    async def _read_chunks(self, work: IngestionWork, stage: JobStage) -> tuple[ChunkDraft, ...]:
        chunks_locator = _required_locator(work.chunks_locator, stage)
        chunks = await self._artifact_call(stage, self._artifacts.read_chunks(chunks_locator))
        if tuple(str(chunk.chunk_id) for chunk in chunks) != tuple(
            item.chunk_id for item in work.manifest
        ):
            raise _SafeStageError(stage, "artifact-unavailable")
        return chunks

    async def _commit(
        self,
        job: ClaimedIngestionJob,
        stage: JobStage,
        *,
        normalized_locator=None,  # type: ignore[no-untyped-def]
        chunks_locator=None,  # type: ignore[no-untyped-def]
        embeddings_locator=None,  # type: ignore[no-untyped-def]
        manifest: tuple[ManifestChunk, ...] = (),
        chunk_count: int | None = None,
    ) -> None:
        await self._repository.commit_stage(
            JobStageCommit(
                job_id=job.job_id,
                lease_token=job.lease_token,
                expected_stage=stage,
                completed_at=self._clock.now(),
                normalized_locator=normalized_locator,
                chunks_locator=chunks_locator,
                embeddings_locator=embeddings_locator,
                manifest=manifest,
                chunk_count=chunk_count,
            )
        )

    async def _artifact_call(self, stage: JobStage, operation: Awaitable[T]) -> T:
        try:
            return await operation
        except JobLeaseLost:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise _SafeStageError(stage, "artifact-unavailable") from error

    async def _provider_call(
        self,
        job: ClaimedIngestionJob,
        stage: JobStage,
        operation: Callable[[], Coroutine[object, object, T]],
    ) -> T:
        await self._repository.renew_lease(
            job.job_id,
            job.lease_token,
            stage,
            self._clock.now(),
            LEASE_DURATION,
        )
        provider = asyncio.create_task(operation())
        heartbeat = asyncio.create_task(self._heartbeat(job, stage, provider))
        try:
            done, _ = await asyncio.wait((provider, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                heartbeat.result()
            result = provider.result() if provider.done() else await provider
        except BaseException:
            await _cancel_and_settle(provider)
            raise
        finally:
            await _cancel_and_settle(heartbeat)
        await self._repository.renew_lease(
            job.job_id,
            job.lease_token,
            stage,
            self._clock.now(),
            LEASE_DURATION,
        )
        return result

    async def _heartbeat(
        self,
        job: ClaimedIngestionJob,
        stage: JobStage,
        provider: asyncio.Task[object],
    ) -> None:
        while not provider.done():
            await self._clock.sleep(HEARTBEAT_SECONDS)
            if provider.done():
                return
            await self._repository.renew_lease(
                job.job_id,
                job.lease_token,
                stage,
                self._clock.now(),
                LEASE_DURATION,
            )


async def _cancel_and_settle(task: asyncio.Task[object]) -> None:
    if not task.done():
        task.cancel()
    try:
        async with asyncio.timeout(PROVIDER_SETTLE_SECONDS):
            await asyncio.shield(task)
    except (asyncio.CancelledError, TimeoutError):
        if not task.done():
            task.cancel()


def _required_locator(locator, stage: JobStage):  # type: ignore[no-untyped-def]
    if locator is None:
        raise _SafeStageError(stage, "artifact-unavailable")
    return locator


def _closed_parser_code(code: str) -> str:
    if code in {
        "unsupported-document",
        "document-too-large",
        "empty-document",
        "invalid-document",
        "ocr-required",
        "document-too-complex",
    }:
        return code
    return "invalid-document"


def _deletion_target(work: IngestionWork) -> DeletionTarget:
    locators = tuple(
        locator
        for locator in (
            work.original_locator,
            work.normalized_locator,
            work.chunks_locator,
            work.embeddings_locator,
        )
        if locator is not None
    )
    return DeletionTarget(
        document_id=work.document_id,
        revision_id=work.revision_id,
        chunk_ids=tuple(item.chunk_id for item in work.manifest),
        artifact_locators=locators,
    )
