"""Provider-neutral HTTP mapping for the Tapper knowledge application."""

from __future__ import annotations

from typing import Any, Literal, Protocol, cast
from uuid import uuid4

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

    async def answer_frozen(self, *args, **kwargs) -> AnswerResponse: ...


class CitationOperations(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def resolve(self, citation_id: str) -> CitationPreviewResult: ...

    async def resolve_historical(self, citation_id: str) -> CitationPreviewResult: ...


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
        corpus_version: str = "tapper-demo-v1",
        graph_enricher=None,
        models: Any | None = None,
    ) -> None:
        if corpus_version not in {"tapper-demo-v1", "tapper-demo-v2"}:
            raise ValueError("unsupported projection corpus")
        self._documents = documents
        self._answers = answers
        self._citations = citations
        self._searches = searches
        self._sources = sources
        self._corpus_version = corpus_version
        self._graph_enricher = graph_enricher
        self._models = models

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

    async def resolve_conversation_selection(self, revision_ids: tuple[str, ...]):
        resolver = getattr(self._answers, "resolve_conversation_selection", None)
        if resolver is None:
            from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable

            raise KnowledgeRuntimeUnavailable
        return await resolver(revision_ids)

    async def answer_conversation(self, request: RetrievalAnswerRequest, frozen_input):
        from tap.modules.chat.domain.conversations import content_digest
        from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
        from tap.modules.knowledge.ports.answers import ReadyDocumentRevision

        supported_aliases = (
            frozenset({"tapper-chat"})
            if self._models is None
            else getattr(self._models, "chat_aliases", frozenset({"tapper-chat"}))
        )
        if frozen_input.model_alias not in supported_aliases:
            raise ValueError("accepted conversation model alias is unsupported")
        revisions = tuple(
            sorted(
                (
                    ReadyDocumentRevision(
                        item.document_id,
                        item.revision_id,
                        item.source_content_hash,
                        item.source_id,
                    )
                    for item in frozen_input.resolved_resources
                ),
                key=lambda item: item.document_id,
            )
        )
        governance = self._generation_governance(frozen_input)
        if not revisions:
            if self._models is None:
                raise ValueError("model-only conversation runtime is unavailable")
            expected_acl = content_digest({"mode": "model-only", "resources": []})
            expected_policy = content_digest({"mode": "model-only", "retrieval": "not-selected"})
            if (
                frozen_input.source_revision_ids
                or frozen_input.document_revision_ids
                or frozen_input.acl_digest != expected_acl
                or frozen_input.retrieval_policy_digest != expected_policy
            ):
                raise ValueError("accepted model-only authority changed")
            generation = await self._models.chat(
                request.query,
                model_alias=frozen_input.model_alias,
                governance=governance,
            )
            identity = generation.gateway_call_id or generation.provider_request_id or uuid4().hex
            return RetrievalAnswerResponse(
                trace_id=identity,
                query_plan_id=f"model-only-{identity}",
                context_snapshot_id=f"model-only-{identity}",
                corpus_version=self._corpus_version,
                retrieval_profile_id="direct-chat-v1",
                degraded_mode=False,
                answer=generation.text,
                abstained=False,
                claims=[],
                citations=[],
                graph_context_status="NOT_SELECTED",
            )
        policy = build_demo_policy_context(revisions, corpus_version=self._corpus_version)
        if frozen_input.acl_digest != policy.acl_digest or frozen_input.retrieval_policy_digest != (
            content_digest(
                {
                    "decisionId": policy.decision_id,
                    "policyVersion": policy.policy_version,
                    "corpusVersion": policy.active_corpus_version,
                }
            )
        ):
            raise ValueError("accepted retrieval authority changed")
        domain_request = answer_request_from_http(request)
        graph_context = None
        if self._graph_enricher is not None:
            graph_context = await self._graph_enricher.enrich(
                self.scope,
                tuple(frozen_input.source_revision_ids),
                domain_request.query,
            )
        response = await self._answers.answer_frozen(
            domain_request,
            revisions,
            policy,
            governance=governance,
            graph_context=() if graph_context is None else graph_context.facts,
            model_alias=frozen_input.model_alias,
        )
        return answer_response_to_http(
            response,
            graph_context_status=cast(
                Literal["APPLIED", "NOT_READY", "FAILED", "UNAVAILABLE", "NOT_SELECTED"],
                "UNAVAILABLE" if graph_context is None else graph_context.status.value,
            ),
            graph_snapshot_id=None if graph_context is None else graph_context.snapshot_id,
        )

    @staticmethod
    def _generation_governance(frozen_input):
        import json

        from tap.modules.ai.domain.models import GenerationGovernance, schema_digest, text_digest

        governance = None
        if frozen_input.agent_revision_id is not None:
            if (
                frozen_input.agent_system_instruction is None
                or frozen_input.agent_system_instruction_digest
                != text_digest(frozen_input.agent_system_instruction)
                or frozen_input.agent_output_schema_json is None
            ):
                raise ValueError("accepted agent execution content is invalid")
            output_schema = json.loads(frozen_input.agent_output_schema_json)
            if (
                not isinstance(output_schema, dict)
                or schema_digest(output_schema) != frozen_input.agent_output_schema_digest
            ):
                raise ValueError("accepted agent output schema is invalid")
            governance = GenerationGovernance(
                model_alias=frozen_input.model_alias,
                system_instruction=frozen_input.agent_system_instruction,
                system_instruction_digest=frozen_input.agent_system_instruction_digest,
                skill_instructions=frozen_input.skill_instruction_templates,
                skill_instruction_digests=frozen_input.skill_instruction_template_digests,
                tool_allowlist=frozenset(frozen_input.agent_tool_allowlist),
                output_schema=output_schema,
                output_schema_digest=frozen_input.agent_output_schema_digest,
                revision_digests=(
                    frozen_input.agent_revision_digest,
                    frozen_input.agent_system_instruction_digest,
                    frozen_input.agent_output_schema_digest,
                    *frozen_input.skill_revision_digests,
                    *frozen_input.skill_instruction_template_digests,
                ),
            )
        return governance

    async def search(self, request: RetrievalSearchRequest) -> RetrievalSearchResponse:
        """Expose real evidence only to trusted in-process verification, never an HTTP route."""

        if self._searches is None:
            raise RuntimeError("internal knowledge search is not configured")
        response = await self._searches.search(search_request_from_http(request))
        return search_response_to_http(response)

    async def citation(self, citation_id: str) -> CitationPreview:
        preview = await self._citations.resolve(citation_id)
        return self._citation_preview(preview)

    async def historical_citation(self, citation_id: str) -> CitationPreview:
        preview = await self._citations.resolve_historical(citation_id)
        return self._citation_preview(preview)

    @staticmethod
    def _citation_preview(preview) -> CitationPreview:  # type: ignore[no-untyped-def]
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
