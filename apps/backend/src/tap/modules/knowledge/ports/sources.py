"""Project-bound Source lifecycle, views and durable command results."""

from datetime import datetime
from typing import Protocol

from tap.contracts.http import SourceDetail, SourcePage
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.knowledge.domain.sources import SourceCommand, SourceCommandResult, SourceRecord
from tap.modules.knowledge.ports.documents import (
    DocumentId,
    IngestionJob,
    ReserveUpload,
    UploadReservation,
)


class SourceRepository(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def get_source(self, source_id: str) -> SourceRecord | None: ...
    async def delete_source(
        self, source_id: str, *, command: SourceCommand | None = None
    ) -> None: ...
    async def reserve_upload(self, command: ReserveUpload) -> UploadReservation: ...
    async def source_command_result(self, key: str) -> SourceCommandResult: ...
    async def list_sources(self, cursor: str | None, limit: int) -> SourcePage: ...
    async def source_detail(
        self, source_id: str, cursor: str | None, limit: int
    ) -> SourceDetail: ...
    async def retry_failed(
        self,
        document_id: DocumentId,
        now: datetime,
        *,
        command: SourceCommand | None = None,
        source_id: str | None = None,
        revision_id: str | None = None,
        expected_attempt: int | None = None,
    ) -> IngestionJob: ...

    async def request_delete(
        self, document_id: DocumentId, now: datetime, *, command: SourceCommand | None = None
    ) -> IngestionJob: ...
