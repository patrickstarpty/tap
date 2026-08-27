"""Provider-neutral HTTP mapping for the Athena knowledge application."""

from __future__ import annotations

from typing import Protocol

from tap.contracts.http import (
    CitationPreview,
    DocumentAccepted,
    DocumentAnchor,
    DocumentDetail,
    DocumentPage,
    RetrievalAnswerRequest,
    RetrievalAnswerResponse,
    StructuralAnchor,
)
from tap.interfaces.http.dependencies import UploadInput
from tap.modules.knowledge.api import answer_request_from_http, answer_response_to_http
from tap.modules.knowledge.application.answers import validate_answer_selection
from tap.modules.knowledge.application.citations import CitationPreviewResult
from tap.modules.knowledge.domain.models import AnswerRequest, AnswerResponse


class DocumentOperations(Protocol):
    async def upload(self, upload: UploadInput) -> DocumentAccepted: ...

    async def list_documents(self, cursor: str | None, limit: int) -> DocumentPage: ...

    async def get_document(self, document_id: str) -> DocumentDetail: ...

    async def retry_document(self, document_id: str) -> DocumentAccepted: ...

    async def delete_document(self, document_id: str) -> None: ...


class AnswerOperations(Protocol):
    async def answer(self, request: AnswerRequest) -> AnswerResponse: ...


class CitationOperations(Protocol):
    async def resolve(self, citation_id: str) -> CitationPreviewResult: ...


class KnowledgeHttpService:
    """Keep framework DTO conversion at the public edge of the application."""

    def __init__(
        self,
        *,
        documents: DocumentOperations,
        answers: AnswerOperations,
        citations: CitationOperations,
    ) -> None:
        self._documents = documents
        self._answers = answers
        self._citations = citations

    async def upload(self, upload: UploadInput) -> DocumentAccepted:
        return await self._documents.upload(upload)

    async def list_documents(self, cursor: str | None, limit: int) -> DocumentPage:
        return await self._documents.list_documents(cursor, limit)

    async def get_document(self, document_id: str) -> DocumentDetail:
        return await self._documents.get_document(document_id)

    async def retry_document(self, document_id: str) -> DocumentAccepted:
        return await self._documents.retry_document(document_id)

    async def delete_document(self, document_id: str) -> None:
        await self._documents.delete_document(document_id)

    async def answer(self, request: RetrievalAnswerRequest) -> RetrievalAnswerResponse:
        domain_request = answer_request_from_http(request)
        validate_answer_selection(domain_request)
        response = await self._answers.answer(domain_request)
        return answer_response_to_http(response)

    async def citation(self, citation_id: str) -> CitationPreview:
        preview = await self._citations.resolve(citation_id)
        anchor = preview.anchor
        return CitationPreview(
            citation_id=preview.citation_id,
            document_id=preview.document_id,
            revision_id=preview.revision_id,
            filename=preview.filename,
            source_content_hash=preview.source_content_hash,
            chunk_content_hash=preview.chunk_content_hash,
            anchor=StructuralAnchor(
                root=DocumentAnchor(
                    type="document",
                    heading_path=list(anchor.heading_path) or None,
                    page=anchor.page,
                    bbox=list(anchor.bbox) or None,
                    start_offset=anchor.start_offset,
                    end_offset=anchor.end_offset,
                )
            ),
            quote=preview.quote,
            prefix=preview.prefix,
            suffix=preview.suffix,
        )
