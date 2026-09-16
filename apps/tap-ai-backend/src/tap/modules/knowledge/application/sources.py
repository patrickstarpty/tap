"""Internal Source lifecycle; reservations create documents without reparenting."""

from dataclasses import replace
from datetime import datetime, timezone

from tap.contracts.http import (
    DocumentAccepted,
    SourceAccepted,
    SourceDetail,
    SourcePage,
    SourceRetryRequest,
)
from tap.modules.knowledge.application.documents import DocumentService
from tap.modules.knowledge.domain.sources import SourceCommand, SourceCommandReplay
from tap.modules.knowledge.ports.documents import (
    DocumentId,
    ReserveUpload,
    UploadReservation,
    UploadStream,
)
from tap.modules.knowledge.ports.sources import SourceRepository


class SourceService:
    def __init__(
        self, repository: SourceRepository, documents: DocumentService | None = None
    ) -> None:
        self._repository = repository
        self._documents = documents

    async def reserve_document(self, source_id: str, command: ReserveUpload) -> UploadReservation:
        return await self._repository.reserve_upload(replace(command, source_id=source_id))

    async def delete(self, source_id: str) -> None:
        await self._repository.delete_source(source_id)

    @property
    def scope(self):
        return self._repository.scope

    async def upload(
        self, upload: UploadStream, command: SourceCommand
    ) -> SourceAccepted | DocumentAccepted:
        if self._documents is None:
            raise RuntimeError("Source upload is not composed")
        try:
            await self._documents.upload(upload, command)
            result = await self._repository.source_command_result(command.key)
        except SourceCommandReplay as replay:
            result = replay.result
        if result.status != 202:
            raise SourceCommandReplay(result)
        model = DocumentAccepted if command.operation == "document.upload" else SourceAccepted
        return model.model_validate(result.body)

    async def list_sources(self, cursor: str | None, limit: int) -> SourcePage:
        return await self._repository.list_sources(cursor, limit)

    async def detail(self, source_id: str, cursor: str | None, limit: int) -> SourceDetail:
        return await self._repository.source_detail(source_id, cursor, limit)

    async def retry(
        self, source_id: str, request: SourceRetryRequest, command: SourceCommand
    ) -> SourceAccepted:
        try:
            await self._repository.retry_failed(
                DocumentId(request.document_id),
                datetime.now(timezone.utc),
                command=command,
                source_id=source_id,
                revision_id=request.revision_id,
                expected_attempt=request.expected_attempt,
            )
            result = await self._repository.source_command_result(command.key)
        except SourceCommandReplay as replay:
            result = replay.result
        if result.status != 202:
            raise SourceCommandReplay(result)
        return SourceAccepted.model_validate(result.body)

    async def delete_command(self, source_id: str, command: SourceCommand) -> None:
        await self._repository.delete_source(source_id, command=command)

    async def retry_document(self, document_id: str, command: SourceCommand) -> DocumentAccepted:
        try:
            await self._repository.retry_failed(
                DocumentId(document_id), datetime.now(timezone.utc), command=command
            )
            result = await self._repository.source_command_result(command.key)
        except SourceCommandReplay as replay:
            result = replay.result
        if result.status != 202:
            raise SourceCommandReplay(result)
        return DocumentAccepted.model_validate(result.body)

    async def delete_document(self, document_id: str, command: SourceCommand) -> None:
        try:
            await self._repository.request_delete(
                DocumentId(document_id), datetime.now(timezone.utc), command=command
            )
        except SourceCommandReplay as replay:
            if replay.result.status != 204:
                raise
