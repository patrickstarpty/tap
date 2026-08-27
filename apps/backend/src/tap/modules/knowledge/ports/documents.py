"""Provider-neutral document ledger, artifact, parsing, and chunking ports."""

from __future__ import annotations

import json
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

PIPELINE_VERSION = "athena-ingestion-v1"
MAX_DOCUMENTS = 50
DEFAULT_RESERVATION_LEASE = timedelta(minutes=5)
MAX_RESERVATION_LEASE = timedelta(minutes=15)
MAX_JOB_LEASE = timedelta(minutes=15)
STAGE_ORDER = ("stored", "parsing", "chunking", "embedding", "publishing", "ready")
SAFE_JOB_ERRORS = frozenset(
    {
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
    parser_version: str = PARSER_VERSION
    chunker_version: str = CHUNKER_VERSION
    pipeline_version: str = PIPELINE_VERSION
    staging_key: str | None = None

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


class ArtifactStore(Protocol):
    async def stage_original(self, upload: UploadStream, *, max_bytes: int) -> StagedOriginal: ...

    async def commit_original(
        self, staged: StagedOriginal, revision_id: str
    ) -> ArtifactLocator: ...

    async def discard_staged(self, staged: StagedOriginal) -> None: ...

    async def recover_original(self, staging_key: str, revision_id: str) -> ArtifactLocator: ...

    async def discard_staging(self, staging_key: str) -> None: ...


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

    async def checkpoint(self, checkpoint: JobCheckpoint) -> None: ...

    async def fail_job(self, failure: JobFailure) -> None: ...


class DocumentParser(Protocol):
    """Converts one closed media type into a safe normalized artifact."""

    def parse(self, source: DocumentSource) -> NormalizedArtifact: ...


class DocumentChunker(Protocol):
    """Converts normalized, addressable text into vector-free manifest drafts."""

    def chunk(self, artifact: NormalizedArtifact) -> tuple[ChunkDraft, ...]: ...
