"""Internal Source lifecycle; reservations create documents without reparenting."""

from dataclasses import replace

from tap.modules.knowledge.ports.documents import ReserveUpload, UploadReservation
from tap.modules.knowledge.ports.sources import SourceRepository


class SourceService:
    def __init__(self, repository: SourceRepository) -> None:
        self._repository = repository

    async def reserve_document(self, source_id: str, command: ReserveUpload) -> UploadReservation:
        return await self._repository.reserve_upload(replace(command, source_id=source_id))

    async def delete(self, source_id: str) -> None:
        await self._repository.delete_source(source_id)
