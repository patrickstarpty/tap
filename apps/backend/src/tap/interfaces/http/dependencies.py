"""HTTP-only service contracts and deferred runtime resolution."""

from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request

from tap.contracts.http import (
    CitationPreview,
    DocumentAccepted,
    DocumentDetail,
    DocumentPage,
    RetrievalAnswerRequest,
    RetrievalAnswerResponse,
)


@dataclass(frozen=True, slots=True)
class UploadInput:
    """A bounded byte stream and safe public metadata, never a filesystem path."""

    filename: str
    media_type: str
    content: AsyncIterable[bytes]


class KnowledgeHttpService(Protocol):
    async def upload(self, upload: UploadInput) -> DocumentAccepted: ...

    async def list_documents(self, cursor: str | None, limit: int) -> DocumentPage: ...

    async def get_document(self, document_id: str) -> DocumentDetail: ...

    async def retry_document(self, document_id: str) -> DocumentAccepted: ...

    async def delete_document(self, document_id: str) -> None: ...

    async def answer(self, request: RetrievalAnswerRequest) -> RetrievalAnswerResponse: ...

    async def citation(self, citation_id: str) -> CitationPreview: ...


@dataclass(frozen=True, slots=True)
class HttpServices:
    """Optional service assembly used by routes without eager infrastructure startup."""

    knowledge: KnowledgeHttpService | None = None


class KnowledgeRuntimeUnavailable(Exception):
    """The contract-only HTTP application has not been wired to a runtime service."""


def knowledge_service(request: Request) -> KnowledgeHttpService:
    services = getattr(request.app.state, "http_services", None)
    service = services.knowledge if isinstance(services, HttpServices) else None
    if service is None:
        raise KnowledgeRuntimeUnavailable
    return service
