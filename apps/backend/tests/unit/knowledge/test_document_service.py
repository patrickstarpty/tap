from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from dataclasses import replace
from datetime import datetime
from typing import cast

import pytest

from tap.interfaces.http.dependencies import KnowledgeRuntimeUnavailable, UploadInput
from tap.modules.knowledge.application.documents import DocumentService
from tap.modules.knowledge.domain.documents import MAX_UPLOAD_BYTES
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    DocumentCapacityExceeded,
    DocumentNotFound,
    DocumentRecord,
    DocumentRecordPage,
    DocumentState,
    IngestionJob,
    JobKind,
    JobStage,
    JobState,
    ReservationState,
    ReserveUpload,
    StagedOriginal,
    UploadReservation,
)
from tap.modules.knowledge.ports.errors import ArtifactUnavailable


async def _content(value: bytes) -> AsyncIterable[bytes]:
    yield value


def markdown_upload(filename: str, value: bytes) -> UploadInput:
    return UploadInput(filename=filename, media_type="text/markdown", content=_content(value))


class FakeArtifactStore:
    """A stateful artifact fake: assertions inspect owned artifacts, not fake calls."""

    def __init__(self) -> None:
        self.staged: dict[str, StagedOriginal] = {}
        self.originals: dict[str, bytes] = {}
        self._sequence = 0
        self.fail_commit = False

    async def stage_original(self, upload: UploadInput, *, max_bytes: int) -> StagedOriginal:
        from tap.modules.knowledge.domain.documents import DocumentParseRejected, canonical_sha256

        data = bytearray()
        async for chunk in upload.content:
            data.extend(chunk)
            if len(data) > max_bytes:
                raise DocumentParseRejected("document-too-large")
        self._sequence += 1
        key = f"staged-{self._sequence}"
        staged = StagedOriginal(
            staging_key=key,
            filename=upload.filename,
            media_type=upload.media_type,
            size=len(data),
            source_content_hash=canonical_sha256(bytes(data)),
        )
        self.staged[key] = staged
        self.originals[key] = bytes(data)
        return staged

    async def commit_original(self, staged: StagedOriginal, revision_id: str) -> ArtifactLocator:
        if self.fail_commit:
            raise RuntimeError("injected artifact promotion failure")
        self.originals[revision_id] = self.originals[staged.staging_key]
        return ArtifactLocator(f"artifact:{revision_id}")

    async def recover_original(self, staging_key: str, revision_id: str) -> ArtifactLocator:
        self.originals.setdefault(revision_id, self.originals[staging_key])
        return ArtifactLocator(f"artifact:{revision_id}")

    async def discard_staged(self, staged: StagedOriginal) -> None:
        await self.discard_staging(staged.staging_key)

    async def discard_staging(self, staging_key: str) -> None:
        self.staged.pop(staging_key, None)
        self.originals.pop(staging_key, None)


class MemoryDocumentRepository:
    def __init__(self) -> None:
        self.records_by_key: dict[str, DocumentRecord] = {}
        self.reservations_by_key: dict[str, UploadReservation] = {}
        self.jobs_by_document: dict[str, set[str]] = {}
        self._sequence = 0

    async def reserve_upload(self, command):  # type: ignore[no-untyped-def]
        existing = self.records_by_key.get(command.dedupe_key)
        if existing is not None:
            return UploadReservation.duplicate_active(existing)
        pending = self.reservations_by_key.get(command.dedupe_key)
        if pending is not None:
            return UploadReservation(
                state=ReservationState.DUPLICATE_PENDING,
                reservation_id=pending.reservation_id,
                owner_token="",
                document_id=pending.document_id,
                revision_id=pending.revision_id,
                dedupe_key=pending.dedupe_key,
                document=None,
                staging_key=pending.staging_key,
            )
        if len(self.records_by_key) >= 50:
            raise DocumentCapacityExceeded
        self._sequence += 1
        reservation = UploadReservation(
            state=ReservationState.OWNED,
            reservation_id=f"doc-{self._sequence}",
            owner_token=f"owner-{self._sequence}",
            document_id=f"doc-{self._sequence}",
            revision_id=f"rev-{self._sequence}",
            dedupe_key=command.dedupe_key,
            document=None,
            parser_version=command.parser_version,
            chunker_version=command.chunker_version,
            pipeline_version=command.pipeline_version,
            staging_key=command.staging_key,
        )
        self.reservations_by_key[command.dedupe_key] = reservation
        return reservation

    async def activate_upload(
        self, reservation: UploadReservation, original: ArtifactLocator
    ) -> DocumentRecord:
        del original
        existing = self.records_by_key.get(reservation.dedupe_key)
        if existing is not None:
            return existing
        now = datetime(2026, 8, 27, 10, 0, 0)
        job_id = f"job-{reservation.document_id}"
        record = DocumentRecord.queued(
            document_id=reservation.document_id,
            revision_id=reservation.revision_id,
            filename="policy.md",
            media_type="text/markdown",
            source_content_hash="sha256:" + "a" * 64,
            job_id=job_id,
            now=now,
        )
        self.records_by_key[reservation.dedupe_key] = record
        self.jobs_by_document.setdefault(reservation.document_id, set()).add(job_id)
        return record

    async def abandon_upload(self, reservation_id: str, owner_token: str) -> None:
        for key, reservation in tuple(self.reservations_by_key.items()):
            if (
                reservation.reservation_id == reservation_id
                and reservation.owner_token == owner_token
            ):
                self.reservations_by_key.pop(key)

    async def complete_upload_cleanup(self, reservation_id: str, owner_token: str) -> None:
        for key, reservation in tuple(self.reservations_by_key.items()):
            if (
                reservation.reservation_id == reservation_id
                and reservation.owner_token == owner_token
            ):
                self.reservations_by_key.pop(key)

    async def list_documents(self, cursor, limit):  # type: ignore[no-untyped-def]
        del cursor
        return DocumentRecordPage(tuple(self.records_by_key.values())[:limit], None)

    async def get_document(self, document_id, *, include_deleting=False):  # type: ignore[no-untyped-def]
        del include_deleting
        return next(
            (row for row in self.records_by_key.values() if row.document_id == document_id), None
        )

    async def retry_failed(self, document_id, now):  # type: ignore[no-untyped-def]
        del now
        key, record = next(
            (item for item in self.records_by_key.items() if item[1].document_id == document_id)
        )
        queued = replace(
            record,
            status=DocumentState.QUEUED,
            error_code=None,
            error_summary=None,
        )
        self.records_by_key[key] = queued
        return IngestionJob(
            job_id=record.job_id,
            revision_id=record.revision_id,
            kind=JobKind.INGESTION,
            attempt=2,
            status=JobState.PENDING,
            stage=JobStage.STORED,
            stages=record.stages,
        )

    async def request_delete(self, document_id, now):  # type: ignore[no-untyped-def]
        del now
        key, record = next(
            (item for item in self.records_by_key.items() if item[1].document_id == document_id)
        )
        self.records_by_key[key] = replace(record, status=DocumentState.DELETING)
        return IngestionJob(
            job_id=f"delete-{record.job_id}",
            revision_id=record.revision_id,
            kind=JobKind.DELETION,
            attempt=1,
            status=JobState.PENDING,
            stage=JobStage.STORED,
            stages=record.stages,
        )


@pytest.mark.parametrize("staging_key", [None, "", "   "])
def test_upload_commands_reject_blank_durable_staging_locator(staging_key: object) -> None:
    """Allowing a blank locator makes an owned reservation impossible to recover."""

    with pytest.raises(ValueError, match="staging locator"):
        ReserveUpload(
            filename="policy.md",
            media_type="text/markdown",
            source_content_hash="sha256:" + "a" * 64,
            size=12,
            now=datetime(2026, 8, 27),
            staging_key=cast(str, staging_key),
        )


@pytest.mark.parametrize("state", [ReservationState.OWNED, ReservationState.DUPLICATE_PENDING])
def test_recoverable_reservation_requires_nonblank_staging_locator(
    state: ReservationState,
) -> None:
    """A hidden reservation without its locator permanently consumes capacity."""

    with pytest.raises(ValueError, match="staging locator"):
        UploadReservation(
            state=state,
            reservation_id="doc_" + "1" * 32,
            owner_token="owner" if state is ReservationState.OWNED else "",
            document_id="doc_" + "1" * 32,
            revision_id="rev_" + "2" * 32,
            dedupe_key="sha256:" + "3" * 64,
            document=None,
            staging_key=None,
        )


def test_duplicate_upload_returns_same_document_without_second_job() -> None:
    async def scenario() -> None:
        repository = MemoryDocumentRepository()
        artifacts = FakeArtifactStore()
        service = DocumentService(repository=repository, artifacts=artifacts)

        first = await service.upload(markdown_upload("policy.md", b"# Policy\nTwo approvers"))
        second = await service.upload(markdown_upload("renamed.md", b"# Policy\nTwo approvers"))

        assert second.duplicate is True
        assert second.document.document_id == first.document.document_id
        assert len(repository.jobs_by_document[first.document.document_id]) == 1
        assert artifacts.staged == {}

    asyncio.run(scenario())


@pytest.mark.parametrize("size", [MAX_UPLOAD_BYTES, MAX_UPLOAD_BYTES + 1])
def test_upload_enforces_the_byte_limit_before_creating_a_document(size: int) -> None:
    async def scenario() -> None:
        repository = MemoryDocumentRepository()
        service = DocumentService(repository=repository, artifacts=FakeArtifactStore())
        upload = markdown_upload("limit.md", b"x" * size)

        if size == MAX_UPLOAD_BYTES:
            accepted = await service.upload(upload)
            assert accepted.document.status == "queued"
            assert len(repository.records_by_key) == 1
        else:
            from tap.modules.knowledge.domain.documents import DocumentParseRejected

            with pytest.raises(DocumentParseRejected) as error:
                await service.upload(upload)
            assert error.value.code == "document-too-large"
            assert repository.records_by_key == {}

    asyncio.run(scenario())


def test_capacity_rejection_discards_only_the_current_staging_artifact() -> None:
    async def scenario() -> None:
        repository = MemoryDocumentRepository()
        now = datetime(2026, 8, 27, 10, 0)
        for number in range(50):
            repository.records_by_key[f"existing-{number}"] = DocumentRecord.queued(
                document_id=f"existing-doc-{number}",
                revision_id=f"existing-rev-{number}",
                filename=f"existing-{number}.md",
                media_type="text/markdown",
                source_content_hash="sha256:" + f"{number:064x}",
                job_id=f"existing-job-{number}",
                now=now,
            )
        artifacts = FakeArtifactStore()
        service = DocumentService(repository=repository, artifacts=artifacts)

        with pytest.raises(DocumentCapacityExceeded):
            await service.upload(markdown_upload("overflow.md", b"unique overflow"))

        assert artifacts.staged == {}
        assert set(artifacts.originals) == set()
        assert len(repository.records_by_key) == 50

    asyncio.run(scenario())


def test_artifact_promotion_failure_keeps_durable_reservation_and_staging_for_takeover() -> None:
    async def scenario() -> None:
        repository = MemoryDocumentRepository()
        artifacts = FakeArtifactStore()
        artifacts.fail_commit = True
        service = DocumentService(repository=repository, artifacts=artifacts)

        with pytest.raises(KnowledgeRuntimeUnavailable) as caught:
            await service.upload(markdown_upload("failure.md", b"failed promotion"))

        assert "injected artifact promotion failure" not in str(caught.value)
        assert set(artifacts.staged) == {"staged-1"}
        assert set(artifacts.originals) == {"staged-1"}
        assert len(repository.reservations_by_key) == 1
        assert repository.records_by_key == {}

    asyncio.run(scenario())


def test_list_get_retry_and_delete_preserve_closed_public_document_semantics() -> None:
    async def scenario() -> None:
        repository = MemoryDocumentRepository()
        service = DocumentService(repository=repository, artifacts=FakeArtifactStore())
        accepted = await service.upload(markdown_upload("commands.md", b"# Commands"))
        key, record = next(iter(repository.records_by_key.items()))
        repository.records_by_key[key] = replace(
            record,
            status=DocumentState.FAILED,
            error_code="parser-unavailable",
            error_summary="文档解析服务暂时不可用，请稍后重试。",
        )

        detail = await service.get_document(accepted.document.document_id)
        page = await service.list_documents(None, 25)
        retried = await service.retry_document(accepted.document.document_id)
        await service.delete_document(accepted.document.document_id)
        deleting = await repository.get_document(
            accepted.document.document_id, include_deleting=True
        )

        assert detail.status.value == "failed"
        assert len(detail.stages) == 6
        assert page.items[0].document_id == accepted.document.document_id
        assert retried.document.status.value == "queued"
        assert retried.document.error_code is retried.document.error_summary is None
        assert deleting is not None and deleting.status is DocumentState.DELETING
        assert "locator" not in detail.model_dump_json()

    asyncio.run(scenario())


def test_missing_document_commands_fail_as_not_found_before_mutation() -> None:
    async def scenario() -> None:
        service = DocumentService(
            repository=MemoryDocumentRepository(), artifacts=FakeArtifactStore()
        )

        with pytest.raises(DocumentNotFound):
            await service.retry_document("missing")
        with pytest.raises(DocumentNotFound):
            await service.delete_document("missing")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("operation", "failing_method"),
    [
        ("upload", "reserve_upload"),
        ("list", "list_documents"),
        ("detail", "get_document"),
        ("retry", "get_document"),
        ("delete", "get_document"),
    ],
)
def test_document_repository_outages_cross_one_provider_neutral_boundary(
    operation: str,
    failing_method: str,
) -> None:
    async def scenario() -> None:
        repository = MemoryDocumentRepository()

        async def unavailable(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("mysql password=secret")

        setattr(repository, failing_method, unavailable)
        service = DocumentService(repository=repository, artifacts=FakeArtifactStore())

        with pytest.raises(KnowledgeRuntimeUnavailable) as caught:
            if operation == "upload":
                await service.upload(markdown_upload("policy.md", b"# Policy"))
            elif operation == "list":
                await service.list_documents(None, 25)
            elif operation == "detail":
                await service.get_document("doc-a")
            elif operation == "retry":
                await service.retry_document("doc-a")
            else:
                await service.delete_document("doc-a")

        assert "secret" not in str(caught.value)

    asyncio.run(scenario())


def test_document_artifact_outage_crosses_the_same_provider_neutral_boundary() -> None:
    async def scenario() -> None:
        artifacts = FakeArtifactStore()

        async def unavailable(*_args: object, **_kwargs: object) -> None:
            raise ArtifactUnavailable("azure credential=secret")

        artifacts.stage_original = unavailable  # type: ignore[method-assign]
        service = DocumentService(repository=MemoryDocumentRepository(), artifacts=artifacts)

        with pytest.raises(KnowledgeRuntimeUnavailable) as caught:
            await service.upload(markdown_upload("policy.md", b"# Policy"))

        assert "secret" not in str(caught.value)

    asyncio.run(scenario())
