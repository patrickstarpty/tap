"""Sole public application boundary for authorized Knowledge operations."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from tap.contracts.http import AbstentionReason as HttpAbstentionReason
from tap.contracts.http import (
    BddAnchor as HttpBddAnchor,
)
from tap.contracts.http import (
    CodeAnchor as HttpCodeAnchor,
)
from tap.contracts.http import ContentRole as HttpContentRole
from tap.contracts.http import (
    DocumentAnchor as HttpDocumentAnchor,
)
from tap.contracts.http import (
    FailureAnchor as HttpFailureAnchor,
)
from tap.contracts.http import (
    OpenApiAnchor as HttpOpenApiAnchor,
)
from tap.contracts.http import (
    ResourceRef as HttpResourceRef,
)
from tap.contracts.http import (
    RetrievalAnswerRequest as HttpAnswerRequest,
)
from tap.contracts.http import (
    RetrievalAnswerResponse as HttpAnswerResponse,
)
from tap.contracts.http import (
    RetrievalCitation as HttpCitation,
)
from tap.contracts.http import (
    RetrievalClaim as HttpClaim,
)
from tap.contracts.http import (
    RetrievalHit as HttpHit,
)
from tap.contracts.http import RetrievalScores as HttpRetrievalScores
from tap.contracts.http import (
    RetrievalSearchRequest as HttpSearchRequest,
)
from tap.contracts.http import (
    RetrievalSearchResponse as HttpSearchResponse,
)
from tap.contracts.http import (
    RetrievalSourceRevision as HttpSourceRevision,
)
from tap.contracts.http import SourceFamily as HttpSourceFamily
from tap.modules.access.application.ports import CurrentPolicyVerificationPort
from tap.modules.access.domain.policy import RetrievalPolicyContext
from tap.modules.knowledge.application.retrieve import AuthorizedRetrieval
from tap.modules.knowledge.domain.models import (
    AnswerMode,
    AnswerRequest,
    AnswerResponse,
    BddAnchor,
    Citation,
    CodeAnchor,
    DocumentAnchor,
    Evidence,
    FailureAnchor,
    OpenApiAnchor,
    ResourceMode,
    ResourceRef,
    SearchRequest,
    SearchResponse,
    SourceFamily,
    SourceRevisionRef,
    StructuralAnchor,
)
from tap.modules.knowledge.ports.redaction import EgressRedactionPort
from tap.modules.knowledge.ports.search import ModelPort, SearchPort

__all__ = [
    "AnswerRequest",
    "AnswerResponse",
    "KnowledgeAPI",
    "RetrievalPolicyContext",
    "SearchRequest",
    "SearchResponse",
]


class KnowledgeAPI:
    """The only supported application entry point for Chat and future modules."""

    def __init__(
        self,
        *,
        search: SearchPort,
        model: ModelPort,
        policy_verifier: CurrentPolicyVerificationPort,
        redactor: EgressRedactionPort,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._retrieval = AuthorizedRetrieval(
            search=search,
            model=model,
            policy_verifier=policy_verifier,
            redactor=redactor,
            id_factory=id_factory or (lambda: str(uuid4())),
        )

    async def search(
        self,
        request: SearchRequest,
        policy: RetrievalPolicyContext,
    ) -> SearchResponse:
        return await self._retrieval.search(request, policy)

    async def answer(
        self,
        request: AnswerRequest,
        policy: RetrievalPolicyContext,
    ) -> AnswerResponse:
        return await self._retrieval.answer(request, policy)


def search_request_from_http(request: HttpSearchRequest) -> SearchRequest:
    """Map the Pydantic browser DTO into a framework-free application request."""
    return SearchRequest(
        query=request.query,
        answer_mode=AnswerMode(request.answer_mode.value),
        source_families=tuple(SourceFamily(item.value) for item in request.sources or ()),
        resource_refs=tuple(_resource_from_http(item) for item in request.resource_refs or ()),
        requested_environment=request.requested_environment,
        requested_corpus_version=request.requested_corpus_version,
        top_k=request.top_k,
    )


def answer_request_from_http(request: HttpAnswerRequest) -> AnswerRequest:
    mapped = search_request_from_http(
        HttpSearchRequest.model_validate(request.model_dump(by_alias=True))
    )
    return AnswerRequest(
        query=mapped.query,
        answer_mode=mapped.answer_mode,
        source_families=mapped.source_families,
        resource_refs=mapped.resource_refs,
        requested_environment=mapped.requested_environment,
        requested_corpus_version=mapped.requested_corpus_version,
        top_k=mapped.top_k,
    )


def search_response_to_http(response: SearchResponse) -> HttpSearchResponse:
    """Map authorized evidence without internal physical-index provenance."""
    return HttpSearchResponse(
        trace_id=response.trace_id,
        query_plan_id=response.query_plan_id,
        context_snapshot_id=response.context_snapshot_id,
        corpus_version=response.corpus_version,
        retrieval_profile_id=response.retrieval_profile_id.value,
        degraded_mode=response.degraded_mode,
        degradation_reasons=list(response.degradation_reasons) or None,
        hits=[_evidence_to_http(item) for item in response.evidence],
    )


def answer_response_to_http(response: AnswerResponse) -> HttpAnswerResponse:
    return HttpAnswerResponse(
        trace_id=response.trace_id,
        query_plan_id=response.query_plan_id,
        context_snapshot_id=response.context_snapshot_id,
        corpus_version=response.corpus_version,
        retrieval_profile_id=response.retrieval_profile_id.value,
        degraded_mode=response.degraded_mode,
        degradation_reasons=list(response.degradation_reasons) or None,
        answer=response.answer,
        abstained=response.abstained,
        abstention_reason=(
            HttpAbstentionReason(response.abstention_reason.value)
            if response.abstention_reason is not None
            else None
        ),
        claims=[
            HttpClaim(
                claim_id=item.claim_id,
                text=item.text,
                citation_ids=list(item.citation_ids),
            )
            for item in response.claims
        ],
        citations=[_citation_to_http(item) for item in response.citations],
    )


def _resource_from_http(resource: HttpResourceRef) -> ResourceRef:
    return ResourceRef(
        family=SourceFamily(resource.family.value),
        source_id=resource.source_id,
        mode=ResourceMode(resource.mode.value),
        requested_revision=resource.requested_revision,
        anchor=(_anchor_from_http(resource.anchor.root) if resource.anchor is not None else None),
    )


def _anchor_from_http(
    anchor: HttpDocumentAnchor
    | HttpCodeAnchor
    | HttpBddAnchor
    | HttpOpenApiAnchor
    | HttpFailureAnchor,
) -> StructuralAnchor:
    if isinstance(anchor, HttpDocumentAnchor):
        return DocumentAnchor(
            heading_path=tuple(anchor.heading_path or ()),
            page=anchor.page,
            bbox=tuple(anchor.bbox or ()),
            start_offset=anchor.start_offset,
            end_offset=anchor.end_offset,
        )
    if isinstance(anchor, HttpCodeAnchor):
        return CodeAnchor(
            repo=anchor.repo,
            path=anchor.path,
            symbol=anchor.symbol,
            line_start=anchor.line_start,
            line_end=anchor.line_end,
        )
    if isinstance(anchor, HttpBddAnchor):
        return BddAnchor(
            feature_id=anchor.feature_id,
            scenario_id=anchor.scenario_id,
            step_id=anchor.step_id,
        )
    if isinstance(anchor, HttpOpenApiAnchor):
        return OpenApiAnchor(
            method=anchor.method,
            path=anchor.path,
            json_pointer=anchor.json_pointer,
        )
    return FailureAnchor(
        incident_id=anchor.incident_id,
        run_id=anchor.run_id,
        time_start=anchor.time_start,
        time_end=anchor.time_end,
    )


def _source_to_http(source: SourceRevisionRef) -> HttpSourceRevision:
    anchor: dict[str, object]
    if isinstance(source.anchor, DocumentAnchor):
        anchor = {
            "type": "document",
            "headingPath": list(source.anchor.heading_path) or None,
            "page": source.anchor.page,
            "bbox": list(source.anchor.bbox) or None,
            "startOffset": source.anchor.start_offset,
            "endOffset": source.anchor.end_offset,
        }
    elif isinstance(source.anchor, CodeAnchor):
        anchor = {
            "type": "code",
            "repo": source.anchor.repo,
            "path": source.anchor.path,
            "symbol": source.anchor.symbol,
            "lineStart": source.anchor.line_start,
            "lineEnd": source.anchor.line_end,
        }
    elif isinstance(source.anchor, BddAnchor):
        anchor = {
            "type": "bdd",
            "featureId": source.anchor.feature_id,
            "scenarioId": source.anchor.scenario_id,
            "stepId": source.anchor.step_id,
        }
    elif isinstance(source.anchor, OpenApiAnchor):
        anchor = {
            "type": "openapi",
            "method": source.anchor.method,
            "path": source.anchor.path,
            "jsonPointer": source.anchor.json_pointer,
        }
    else:
        anchor = {
            "type": "failure",
            "incidentId": source.anchor.incident_id,
            "runId": source.anchor.run_id,
            "timeStart": source.anchor.time_start,
            "timeEnd": source.anchor.time_end,
        }
    return HttpSourceRevision.model_validate(
        {
            "sourceId": source.source_id,
            "sourceType": source.source_type,
            "revisionKind": source.revision_kind.value,
            "revision": source.revision,
            "sourceContentHash": source.source_content_hash,
            "anchor": anchor,
        }
    )


def _evidence_to_http(evidence: Evidence) -> HttpHit:
    return HttpHit(
        index_family=HttpSourceFamily(evidence.family.value),
        chunk_id=evidence.chunk_id,
        logical_chunk_id=evidence.logical_chunk_id,
        title=evidence.title,
        content=evidence.content,
        source=_source_to_http(evidence.source),
        chunk_content_hash=evidence.chunk_content_hash,
        content_role=HttpContentRole(evidence.content_role.value),
        citation_id=evidence.citation_id,
        evidence_label=evidence.evidence_label,
        scores=HttpRetrievalScores(rrf=evidence.score),
        acl_decision_id=evidence.acl_decision_id,
        schema_version=evidence.index_revision.schema_version,
        embedding_model_version=evidence.embedding_model_version,
    )


def _citation_to_http(citation: Citation) -> HttpCitation:
    return HttpCitation(
        citation_id=citation.citation_id,
        evidence_label=citation.evidence_label,
        chunk_id=citation.chunk_id,
        logical_chunk_id=citation.logical_chunk_id,
        source=_source_to_http(citation.source),
        chunk_content_hash=citation.chunk_content_hash,
        content_role=HttpContentRole(citation.content_role.value),
        derived_from_chunk_ids=list(citation.derived_from_chunk_ids) or None,
    )
