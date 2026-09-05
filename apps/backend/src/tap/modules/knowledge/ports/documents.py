"""Provider-neutral document ledger, artifact, parsing, and chunking ports."""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol

from tap.modules.knowledge.domain.documents import (
    CHUNKER_VERSION,
    PARSER_VERSION,
    ChunkDraft,
    DocumentId,
    DocumentSource,
    NormalizedArtifact,
    canonical_sha256,
)

PIPELINE_VERSION = "tapper-ingestion-v1"
MAX_DOCUMENTS = 50
DEFAULT_RESERVATION_LEASE = timedelta(minutes=5)
DEFAULT_UPLOAD_CLEANUP_LEASE = timedelta(minutes=5)
MAX_RESERVATION_LEASE = timedelta(minutes=15)
MAX_JOB_LEASE = timedelta(minutes=15)
STAGE_ORDER = ("stored", "parsing", "chunking", "embedding", "publishing", "ready")
SAFE_JOB_ERRORS = frozenset(
    {
        "unsupported-document",
        "document-too-large",
        "empty-document",
        "invalid-document",
        "ocr-required",
        "document-too-complex",
        "parser-unavailable",
        "embedding-unavailable",
        "embedding-dimension-mismatch",
        "index-unavailable",
        "index-reconciliation-failed",
        "artifact-unavailable",
    }
)
SAFE_ERROR_SUMMARIES = {
    "unsupported-document": "当前不支持这种文档格式。",
    "document-too-large": "文档超过允许的大小限制。",
    "empty-document": "文档内容为空。",
    "invalid-document": "文档内容无效，无法继续处理。",
    "ocr-required": "文档没有可提取文本，需要先进行 OCR。",
    "document-too-complex": "文档结构过于复杂，无法在当前限制内处理。",
    "parser-unavailable": "文档解析服务暂时不可用，请稍后重试。",
    "embedding-unavailable": "向量生成服务暂时不可用，请稍后重试。",
    "embedding-dimension-mismatch": "向量维度与当前索引不一致。",
    "index-unavailable": "索引服务暂时不可用，请稍后重试。",
    "index-reconciliation-failed": "索引发布校验失败，请重试。",
    "artifact-unavailable": "文档产物暂时不可用，请稍后重试。",
}


class ArtifactLocator(str):
    """Internal artifact address; public DTO mapping must never expose it."""


class UploadStream(Protocol):
    """Structural upload input accepted without depending on HTTP or framework types."""

    @property
    def filename(self) -> str: ...

    @property
    def media_type(self) -> str: ...

    @property
    def content(self) -> AsyncIterable[bytes]: ...


class DocumentCursor(str):
    """Opaque stable document-page position."""


class ReservationState(str, Enum):
    OWNED = "owned"
    DUPLICATE_PENDING = "duplicate_pending"
    DUPLICATE_ACTIVE = "duplicate_active"


class DocumentState(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"


class JobStage(str, Enum):
    STORED = "stored"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    PUBLISHING = "publishing"
    READY = "ready"


class StageState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobKind(str, Enum):
    INGESTION = "ingestion"
    DELETION = "deletion"


class JobState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class DocumentCapacityExceeded(Exception):
    """The fixed local knowledge-space cap has been reached."""


class InvalidDocumentCursor(ValueError):
    """A document cursor is malformed or outside the closed v1 shape."""


class DocumentNotFound(Exception):
    """No visible document has this opaque identity."""


class RetryNotAllowed(Exception):
    """Only a failed ingestion job is eligible for manual retry."""


class JobLeaseLost(Exception):
    """The current worker no longer owns the requested job transition."""


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: JobStage
    state: StageState
    completed_at: datetime | None = None
    error_code: str | None = None


def initial_stage_results(now: datetime) -> tuple[StageResult, ...]:
    return tuple(
        StageResult(
            stage=JobStage(stage),
            state=StageState.COMPLETED if stage == JobStage.STORED.value else StageState.PENDING,
            completed_at=now if stage == JobStage.STORED.value else None,
        )
        for stage in STAGE_ORDER
    )


def serialize_stage_results(results: tuple[StageResult, ...]) -> list[dict[str, str | None]]:
    if tuple(result.stage.value for result in results) != STAGE_ORDER:
        raise ValueError("stage results must contain the closed six-stage projection")
    return [
        {
            "stage": result.stage.value,
            "state": result.state.value,
            "completedAt": result.completed_at.isoformat(timespec="microseconds")
            if result.completed_at
            else None,
            "errorCode": result.error_code,
        }
        for result in results
    ]


def deserialize_stage_results(value: object) -> tuple[StageResult, ...]:
    if not isinstance(value, list) or len(value) != len(STAGE_ORDER):
        raise ValueError("persisted stage results are not the closed six-stage projection")
    results: list[StageResult] = []
    for expected, item in zip(STAGE_ORDER, value, strict=True):
        if not isinstance(item, dict) or set(item) != {
            "stage",
            "state",
            "completedAt",
            "errorCode",
        }:
            raise ValueError("persisted stage result has an invalid shape")
        if item["stage"] != expected:
            raise ValueError("persisted stage results are out of order")
        completed = item["completedAt"]
        if completed is not None and not isinstance(completed, str):
            raise ValueError("persisted stage completion time is invalid")
        error_code = item["errorCode"]
        if error_code is not None and not isinstance(error_code, str):
            raise ValueError("persisted stage error code is invalid")
        results.append(
            StageResult(
                stage=JobStage(expected),
                state=StageState(item["state"]),
                completed_at=datetime.fromisoformat(completed) if completed else None,
                error_code=error_code,
            )
        )
    return tuple(results)


@dataclass(frozen=True, slots=True)
class StagedOriginal:
    staging_key: str
    filename: str
    media_type: str
    size: int
    source_content_hash: str


@dataclass(frozen=True, slots=True)
class ReserveUpload:
    filename: str
    media_type: str
    source_content_hash: str
    size: int
    now: datetime
    staging_key: str
    parser_version: str = PARSER_VERSION
    chunker_version: str = CHUNKER_VERSION
    pipeline_version: str = PIPELINE_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.staging_key, str) or not self.staging_key.strip():
            raise ValueError("upload reservation requires a nonblank staging locator")

    @classmethod
    def from_staged(cls, staged: StagedOriginal, *, now: datetime) -> ReserveUpload:
        return cls(
            filename=staged.filename,
            media_type=staged.media_type,
            source_content_hash=staged.source_content_hash,
            size=staged.size,
            now=now,
            staging_key=staged.staging_key,
        )

    @property
    def dedupe_key(self) -> str:
        preimage = json.dumps(
            {"mediaType": self.media_type, "sourceContentHash": self.source_content_hash},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return canonical_sha256(preimage)


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    document_id: str
    revision_id: str
    filename: str
    media_type: str
    source_content_hash: str
    status: DocumentState
    stage: JobStage
    chunk_count: int
    error_code: str | None
    error_summary: str | None
    created_at: datetime
    updated_at: datetime
    stages: tuple[StageResult, ...]
    job_id: str

    @classmethod
    def queued(
        cls,
        *,
        document_id: str,
        revision_id: str,
        filename: str,
        media_type: str,
        source_content_hash: str,
        job_id: str,
        now: datetime,
    ) -> DocumentRecord:
        return cls(
            document_id=document_id,
            revision_id=revision_id,
            filename=filename,
            media_type=media_type,
            source_content_hash=source_content_hash,
            status=DocumentState.QUEUED,
            stage=JobStage.STORED,
            chunk_count=0,
            error_code=None,
            error_summary=None,
            created_at=now,
            updated_at=now,
            stages=initial_stage_results(now),
            job_id=job_id,
        )


@dataclass(frozen=True, slots=True)
class DocumentRecordPage:
    items: tuple[DocumentRecord, ...]
    next_cursor: DocumentCursor | None


@dataclass(frozen=True, slots=True)
class UploadReservation:
    state: ReservationState
    reservation_id: str
    owner_token: str
    document_id: str
    revision_id: str
    dedupe_key: str
    document: DocumentRecord | None
    parser_version: str = PARSER_VERSION
    chunker_version: str = CHUNKER_VERSION
    pipeline_version: str = PIPELINE_VERSION
    staging_key: str | None = None
    promoted_locator: ArtifactLocator | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.state in {ReservationState.OWNED, ReservationState.DUPLICATE_PENDING} and (
            self.staging_key is None or not self.staging_key.strip()
        ):
            raise ValueError("recoverable upload reservation requires a nonblank staging locator")

    @classmethod
    def duplicate_active(cls, document: DocumentRecord) -> UploadReservation:
        return cls(
            state=ReservationState.DUPLICATE_ACTIVE,
            reservation_id=document.document_id,
            owner_token="",
            document_id=document.document_id,
            revision_id=document.revision_id,
            dedupe_key="",
            document=document,
        )


@dataclass(frozen=True, slots=True)
class IngestionJob:
    job_id: str
    revision_id: str
    kind: JobKind
    attempt: int
    status: JobState
    stage: JobStage
    stages: tuple[StageResult, ...]


@dataclass(frozen=True, slots=True)
class UploadRecovery:
    reservation: UploadReservation
    activated: bool


@dataclass(frozen=True, slots=True)
class ClaimedIngestionJob(IngestionJob):
    lease_owner: str
    lease_token: str
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class JobCheckpoint:
    job_id: str
    lease_token: str
    expected_stage: JobStage
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class JobFailure:
    job_id: str
    lease_token: str
    expected_stage: JobStage
    error_code: str
    failed_at: datetime

    def __post_init__(self) -> None:
        if self.error_code not in SAFE_JOB_ERRORS:
            raise ValueError("job failure must use a safe closed error code")


@dataclass(frozen=True, slots=True)
class ManifestChunk:
    """One durable vector-free manifest fact for an immutable revision chunk."""

    chunk_id: str
    logical_chunk_id: str
    ordinal: int
    root_id: str
    parent_id: str | None
    anchor_json: str
    chunk_content_hash: str
    embedding_model_version: str
    index_version: str

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("manifest ordinal must be a non-negative integer")
        if not all(
            isinstance(value, str) and value
            for value in (
                self.chunk_id,
                self.logical_chunk_id,
                self.root_id,
                self.anchor_json,
                self.chunk_content_hash,
                self.embedding_model_version,
                self.index_version,
            )
        ):
            raise ValueError("manifest identities and versions must be nonblank")
        try:
            anchor = json.loads(self.anchor_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("manifest anchor must be canonical JSON") from error
        if (
            not isinstance(anchor, dict)
            or json.dumps(
                anchor,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            != self.anchor_json
        ):
            raise ValueError("manifest anchor must be a canonical JSON object")


@dataclass(frozen=True, slots=True)
class EmbeddingArtifact:
    """A durable provider-neutral batch produced before index publication."""

    model_alias: str
    dimension: int
    vectors: tuple[tuple[float, ...], ...]
    chunk_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.model_alias, str) or not self.model_alias:
            raise ValueError("embedding artifact requires a model alias")
        if type(self.dimension) is not int or self.dimension < 1:
            raise ValueError("embedding dimension must be positive")
        if not isinstance(self.vectors, tuple):
            raise TypeError("embedding vectors must be immutable")
        if (
            not isinstance(self.chunk_ids, tuple)
            or len(self.chunk_ids) != len(self.vectors)
            or len(set(self.chunk_ids)) != len(self.chunk_ids)
            or any(not isinstance(chunk_id, str) or not chunk_id for chunk_id in self.chunk_ids)
        ):
            raise ValueError("embedding chunk identities must be exact, unique, and ordered")
        for vector in self.vectors:
            if (
                not isinstance(vector, tuple)
                or len(vector) != self.dimension
                or any(type(value) is not float or not math.isfinite(value) for value in vector)
            ):
                raise ValueError("embedding vectors must be finite floats of the fixed dimension")


@dataclass(frozen=True, slots=True)
class IndexReceipt:
    revision_id: str
    index_version: str
    indexed_count: int

    def __post_init__(self) -> None:
        if not self.revision_id or not self.index_version:
            raise ValueError("index receipt identities must be nonblank")
        if type(self.indexed_count) is not int or self.indexed_count < 0:
            raise ValueError("index receipt count must be non-negative")


@dataclass(frozen=True, slots=True)
class IngestionWork:
    """All durable revision facts reloaded for one fenced stage execution."""

    job_id: str
    lease_token: str
    kind: JobKind
    stage: JobStage
    document_id: str
    revision_id: str
    filename: str
    media_type: str
    source_content_hash: str
    original_locator: ArtifactLocator
    normalized_locator: ArtifactLocator | None
    chunks_locator: ArtifactLocator | None
    embeddings_locator: ArtifactLocator | None
    parser_version: str
    chunker_version: str
    pipeline_version: str
    manifest: tuple[ManifestChunk, ...]


@dataclass(frozen=True, slots=True)
class DeletionTarget:
    document_id: str
    revision_id: str
    chunk_ids: tuple[str, ...]
    artifact_locators: tuple[ArtifactLocator, ...]


@dataclass(frozen=True, slots=True)
class JobStageCommit(JobCheckpoint):
    """A stage checkpoint plus the exact durable facts it made usable."""

    normalized_locator: ArtifactLocator | None = None
    chunks_locator: ArtifactLocator | None = None
    embeddings_locator: ArtifactLocator | None = None
    manifest: tuple[ManifestChunk, ...] = ()
    chunk_count: int | None = None

    def __post_init__(self) -> None:
        if self.chunk_count is not None and (
            type(self.chunk_count) is not int or self.chunk_count < 0
        ):
            raise ValueError("chunk count must be non-negative")


@dataclass(frozen=True, slots=True)
class JobRetry:
    job_id: str
    lease_token: str
    expected_stage: JobStage
    error_code: str
    retry_at: datetime

    def __post_init__(self) -> None:
        if self.error_code not in SAFE_JOB_ERRORS:
            raise ValueError("job retry must use a safe closed error code")


class ArtifactStore(Protocol):
    async def stage_original(self, upload: UploadStream, *, max_bytes: int) -> StagedOriginal: ...

    async def commit_original(
        self, staged: StagedOriginal, revision_id: str
    ) -> ArtifactLocator: ...

    async def discard_staged(self, staged: StagedOriginal) -> None: ...

    async def recover_original(self, staging_key: str, revision_id: str) -> ArtifactLocator: ...

    async def discard_staging(self, staging_key: str) -> None: ...

    async def read_original(self, locator: ArtifactLocator) -> bytes: ...

    async def write_normalized(
        self, revision_id: str, artifact: NormalizedArtifact
    ) -> ArtifactLocator: ...

    async def read_normalized(self, locator: ArtifactLocator) -> NormalizedArtifact: ...

    async def write_chunks(
        self, revision_id: str, chunks: tuple[ChunkDraft, ...]
    ) -> ArtifactLocator: ...

    async def read_chunks(self, locator: ArtifactLocator) -> tuple[ChunkDraft, ...]: ...

    async def write_embeddings(
        self,
        revision_id: str,
        artifact: EmbeddingArtifact,
        *,
        source_content_hash: str,
    ) -> ArtifactLocator: ...

    async def read_embeddings(self, locator: ArtifactLocator) -> EmbeddingArtifact: ...

    async def delete_revision_artifacts(self, target: DeletionTarget) -> None: ...


class DocumentRepository(Protocol):
    async def reserve_upload(self, command: ReserveUpload) -> UploadReservation: ...

    async def activate_upload(
        self, reservation: UploadReservation, original: ArtifactLocator
    ) -> DocumentRecord: ...

    async def record_upload_promotion(
        self, reservation: UploadReservation, original: ArtifactLocator
    ) -> ArtifactLocator: ...

    async def abandon_upload(self, reservation_id: str, owner_token: str) -> None: ...

    async def claim_upload_recoveries(
        self,
        *,
        worker_id: str,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[UploadRecovery, ...]: ...

    async def complete_upload_cleanup(self, reservation_id: str, owner_token: str) -> None: ...

    async def list_documents(
        self, cursor: DocumentCursor | None, limit: int
    ) -> DocumentRecordPage: ...

    async def get_document(
        self, document_id: DocumentId, *, include_deleting: bool = False
    ) -> DocumentRecord | None: ...

    async def retry_failed(self, document_id: DocumentId, now: datetime) -> IngestionJob: ...

    async def request_delete(self, document_id: DocumentId, now: datetime) -> IngestionJob: ...

    async def claim_jobs(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[ClaimedIngestionJob, ...]: ...

    async def renew_lease(
        self,
        job_id: str,
        lease_token: str,
        expected_stage: JobStage,
        now: datetime,
        lease_duration: timedelta,
    ) -> None: ...

    async def settle_cancelled_job(
        self,
        job_id: str,
        lease_token: str,
        expected_stage: JobStage,
        settled_at: datetime,
    ) -> None: ...

    async def renew_cancelled_job_settlement(
        self,
        job_id: str,
        lease_token: str,
        expected_stage: JobStage,
        now: datetime,
        lease_duration: timedelta,
    ) -> None: ...

    async def checkpoint(self, checkpoint: JobCheckpoint) -> None: ...

    async def fail_job(self, failure: JobFailure) -> None: ...

    async def load_ingestion_work(
        self,
        job_id: str,
        lease_token: str,
        expected_stage: JobStage,
    ) -> IngestionWork: ...

    async def commit_stage(self, commit: JobStageCommit) -> None: ...

    async def retry_job(self, retry: JobRetry) -> None: ...


class DocumentParser(Protocol):
    """Converts one closed media type into a safe normalized artifact."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact: ...


class DocumentChunker(Protocol):
    """Converts normalized, addressable text into vector-free manifest drafts."""

    def chunk(self, artifact: NormalizedArtifact) -> tuple[ChunkDraft, ...]: ...


class DocumentEmbeddingPort(Protocol):
    async def embed_documents(
        self,
        texts: tuple[str, ...],
        *,
        model_alias: str,
        chunk_ids: tuple[str, ...],
    ) -> EmbeddingArtifact: ...


class DocumentIndexPort(Protocol):
    async def fence_revision(self, target: DeletionTarget) -> None:
        """Durably prevent current and future stale writes for one revision."""
        ...

    async def upsert_revision(
        self,
        work: IngestionWork,
        chunks: tuple[ChunkDraft, ...],
        embeddings: EmbeddingArtifact,
        *,
        index_version: str,
    ) -> IndexReceipt: ...

    async def delete_revision(self, target: DeletionTarget) -> None: ...

    async def count_revision(self, target: DeletionTarget) -> int: ...
