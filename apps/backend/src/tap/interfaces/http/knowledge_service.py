"""Provider-neutral HTTP mapping for the Tapper knowledge application."""

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
    RetrievalSearchRequest,
    RetrievalSearchResponse,
    SourceAccepted,
    SourceDetail,
    SourcePage,
    SourceRetryRequest,
    StructuralAnchor,
)
from tap.interfaces.http.dependencies import UploadInput
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.knowledge.api import (
    answer_request_from_http,
    answer_response_to_http,
    search_request_from_http,
    search_response_to_http,
)
from tap.modules.knowledge.application.answers import validate_answer_selection
from tap.modules.knowledge.application.citations import CitationPreviewResult
from tap.modules.knowledge.application.sources import SourceService
from tap.modules.knowledge.domain.models import (
    AnswerRequest,
    AnswerResponse,
    SearchRequest,
    SearchResponse,
)
from tap.modules.knowledge.domain.sources import SourceCommand


class DocumentOperations(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def upload(self, upload: UploadInput) -> DocumentAccepted: ...

    async def list_documents(self, cursor: str | None, limit: int) -> DocumentPage: ...

    async def get_document(self, document_id: str) -> DocumentDetail: ...

    async def retry_document(self, document_id: str) -> DocumentAccepted: ...

    async def delete_document(self, document_id: str) -> None: ...


class AnswerOperations(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def answer(self, request: AnswerRequest) -> AnswerResponse: ...


class CitationOperations(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def resolve(self, citation_id: str) -> CitationPreviewResult: ...


class SearchOperations(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def search(self, request: SearchRequest) -> SearchResponse: ...


class KnowledgeHttpService:
    """Keep framework DTO conversion at the public edge of the application."""

    def __init__(
        self,
        *,
        documents: DocumentOperations,
        answers: AnswerOperations,
        citations: CitationOperations,
        searches: SearchOperations | None = None,
        sources: SourceService | None = None,
    ) -> None:
        self._documents = documents
        self._answers = answers
        self._citations = citations
        self._searches = searches
        self._sources = sources

    @property
    def scope(self) -> ProjectScopeContext:
        """All operations must be bound to the same concrete repository scope."""
        scope = getattr(self._documents, "scope", None)
        if not isinstance(scope, ProjectScopeContext):
            raise AuthorizationDenied("scope-mismatch")
        for operation in (self._answers, self._citations, self._searches, self._sources):
            if operation is not None and getattr(operation, "scope", None) != scope:
                raise AuthorizationDenied("scope-mismatch")
        return scope

    def _source_service(self) -> SourceService:
        if self._sources is None:
            from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable

            raise KnowledgeRuntimeUnavailable
        return self._sources

    async def upload_source(
        self, upload: UploadInput, key: str, correlation: str
    ) -> SourceAccepted:
        result = await self._source_service().upload(
            upload, SourceCommand(key, "source.upload", correlation)
        )
        assert isinstance(result, SourceAccepted)
        return result

    async def list_sources(self, cursor: str | None, limit: int) -> SourcePage:
        return await self._source_service().list_sources(cursor, limit)

    async def get_source(self, source_id: str, cursor: str | None, limit: int) -> SourceDetail:
        return await self._source_service().detail(source_id, cursor, limit)

    async def retry_source(
        self, source_id: str, body: SourceRetryRequest, key: str, correlation: str
    ) -> SourceAccepted:
        return await self._source_service().retry(
            source_id, body, SourceCommand(key, "source.retry", correlation)
        )

    async def delete_source(self, source_id: str, key: str, correlation: str) -> None:
        await self._source_service().delete_command(
            source_id, SourceCommand(key, "source.delete", correlation)
        )

    async def upload(
        self, upload: UploadInput, key: str | None = None, correlation: str | None = None
    ) -> DocumentAccepted:
        if key is None:
            return await self._documents.upload(upload)
        result = await self._source_service().upload(
            upload, SourceCommand(key, "document.upload", correlation or "missing")
        )
        assert isinstance(result, DocumentAccepted)
        return result

    async def list_documents(self, cursor: str | None, limit: int) -> DocumentPage:
        return await self._documents.list_documents(cursor, limit)

    async def get_document(self, document_id: str) -> DocumentDetail:
        return await self._documents.get_document(document_id)

    async def retry_document(
        self, document_id: str, key: str | None = None, correlation: str | None = None
    ) -> DocumentAccepted:
        if key is None:
            return await self._documents.retry_document(document_id)
        return await self._source_service().retry_document(
            document_id, SourceCommand(key, "document.retry", correlation or "missing")
        )

    async def delete_document(
        self, document_id: str, key: str | None = None, correlation: str | None = None
    ) -> None:
        if key is None:
            await self._documents.delete_document(document_id)
            return
        await self._source_service().delete_document(
            document_id, SourceCommand(key, "document.delete", correlation or "missing")
        )

    async def answer(self, request: RetrievalAnswerRequest) -> RetrievalAnswerResponse:
        domain_request = answer_request_from_http(request)
        validate_answer_selection(domain_request)
        response = await self._answers.answer(domain_request)
        return answer_response_to_http(response)

    async def search(self, request: RetrievalSearchRequest) -> RetrievalSearchResponse:
        """Expose real evidence only to trusted in-process verification, never an HTTP route."""

        if self._searches is None:
            raise RuntimeError("internal knowledge search is not configured")
        response = await self._searches.search(search_request_from_http(request))
        return search_response_to_http(response)

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
