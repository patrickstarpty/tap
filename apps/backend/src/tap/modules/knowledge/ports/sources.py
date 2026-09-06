"""Internal Source operations; public selection APIs are a separate migration."""

from typing import Protocol

from tap.modules.knowledge.domain.sources import SourceRecord
from tap.modules.knowledge.ports.documents import ReserveUpload, UploadReservation


class SourceRepository(Protocol):
    async def get_source(self, source_id: str) -> SourceRecord | None: ...
    async def delete_source(self, source_id: str) -> None: ...
    async def reserve_upload(self, command: ReserveUpload) -> UploadReservation: ...
