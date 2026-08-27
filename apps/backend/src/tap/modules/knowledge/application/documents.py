"""Bounded document commands over provider-neutral artifact and ledger ports."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Literal, cast

from tap.contracts.http import (
    DocumentAccepted,
    DocumentDetail,
    DocumentPage,
    DocumentStageSnapshot,
    DocumentStageState,
    DocumentStatus,
    DocumentSummary,
    IngestionStage,
)
from tap.interfaces.http.dependencies import UploadInput
from tap.modules.knowledge.domain.documents import (
    MAX_UPLOAD_BYTES,
    DocumentId,
    DocumentParseRejected,
    MediaType,
    validate_filename_media_type,
)
from tap.modules.knowledge.ports.documents import (
    ArtifactStore,
    DocumentCursor,
    DocumentNotFound,
    DocumentRecord,
    DocumentRepository,
    ReservationState,
    ReserveUpload,
)

PublicMediaType = Literal[
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/markdown",
    "text/plain",
]


class DocumentService:
    """Application boundary for upload, durable status, retry, and deletion commands."""

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        artifacts: ArtifactStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._artifacts = artifacts
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def upload(self, upload: UploadInput) -> DocumentAccepted:
        try:
            media_type = MediaType(upload.media_type)
        except ValueError as error:
            raise DocumentParseRejected("unsupported-document") from error
        validate_filename_media_type(upload.filename, media_type)
        staged = await self._artifacts.stage_original(upload, max_bytes=MAX_UPLOAD_BYTES)
        if staged.size == 0:
            await self._artifacts.discard_staged(staged)
            raise DocumentParseRejected("empty-document")
        try:
            reservation = await self._repository.reserve_upload(
                ReserveUpload.from_staged(staged, now=self._clock())
            )
        except BaseException:
            await _settle_cleanup(self._artifacts.discard_staged(staged))
            raise
        if reservation.state is ReservationState.DUPLICATE_ACTIVE:
            await self._artifacts.discard_staged(staged)
            if reservation.document is None:
                raise RuntimeError("an active duplicate must include its durable document")
            return _accepted(reservation.document, duplicate=True)
        try:
            original = await self._artifacts.commit_original(staged, reservation.revision_id)
            record = await self._repository.activate_upload(reservation, original)
        except BaseException:
            cleanup: list[Awaitable[object]] = [self._artifacts.discard_staged(staged)]
            if reservation.state is ReservationState.OWNED:
                cleanup.append(
                    self._repository.abandon_upload(
                        reservation.reservation_id, reservation.owner_token
                    )
                )
            await _settle_cleanup(*cleanup)
            raise
        return _accepted(
            record,
            duplicate=reservation.state is ReservationState.DUPLICATE_PENDING,
        )

    async def list_documents(self, cursor: str | None, limit: int) -> DocumentPage:
        page = await self._repository.list_documents(
            DocumentCursor(cursor) if cursor is not None else None,
            limit,
        )
        return DocumentPage(
            items=[_summary(record) for record in page.items],
            next_cursor=page.next_cursor,
        )

    async def get_document(self, document_id: str) -> DocumentDetail:
        record = await self._repository.get_document(DocumentId(document_id), include_deleting=True)
        if record is None:
            raise DocumentNotFound(document_id)
        return _detail(record)

    async def retry_document(self, document_id: str) -> DocumentAccepted:
        job = await self._repository.retry_failed(DocumentId(document_id), self._clock())
        record = await self._repository.get_document(DocumentId(document_id), include_deleting=True)
        if record is None:
            raise DocumentNotFound(document_id)
        return DocumentAccepted(document=_summary(record), job_id=job.job_id, duplicate=False)

    async def delete_document(self, document_id: str) -> None:
        await self._repository.request_delete(DocumentId(document_id), self._clock())


async def _settle_cleanup(*operations: Awaitable[object]) -> None:
    tasks = [asyncio.ensure_future(operation) for operation in operations]
    gathering = asyncio.gather(*tasks, return_exceptions=True)
    try:
        await asyncio.shield(gathering)
    except asyncio.CancelledError:
        await gathering
        raise


def _iso(value: datetime) -> str:
    aware = (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
    return aware.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _summary(record: DocumentRecord) -> DocumentSummary:
    return DocumentSummary(
        document_id=record.document_id,
        filename=record.filename,
        media_type=cast(PublicMediaType, MediaType(record.media_type).value),
        status=DocumentStatus(record.status.value),
        stage=IngestionStage(record.stage.value),
        chunk_count=record.chunk_count,
        updated_at=_iso(record.updated_at),
        error_code=record.error_code,
        error_summary=record.error_summary,
    )


def _detail(record: DocumentRecord) -> DocumentDetail:
    summary = _summary(record)
    return DocumentDetail(
        **summary.model_dump(),
        revision_id=record.revision_id,
        source_content_hash=record.source_content_hash,
        stages=[
            DocumentStageSnapshot(
                stage=IngestionStage(result.stage.value),
                state=DocumentStageState(result.state.value),
                completed_at=_iso(result.completed_at) if result.completed_at else None,
                error_code=result.error_code,
            )
            for result in record.stages
        ],
        normalized_preview=None,
    )


def _accepted(record: DocumentRecord, *, duplicate: bool) -> DocumentAccepted:
    return DocumentAccepted(document=_summary(record), job_id=record.job_id, duplicate=duplicate)
