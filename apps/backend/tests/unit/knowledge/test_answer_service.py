from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from tap.modules.access.domain.policy import RetrievalPolicyContext
from tap.modules.knowledge.application.answers import (
    AnswerSelectionRejected,
    AnswerService,
    AnswerSnapshot,
    AnswerSnapshotUnavailable,
    CitationSnapshot,
    DocumentStateChanged,
    ReadyDocumentRevision,
)
from tap.modules.knowledge.domain.documents import (
    DocumentId,
    RevisionId,
    chunk_id_for,
    logical_chunk_id_for,
)
from tap.modules.knowledge.domain.models import (
    AbstentionReason,
    AnswerMode,
    AnswerRequest,
    AnswerResponse,
    Citation,
    Claim,
    ContentRole,
    DocumentAnchor,
    ModelCallProvenance,
    ResourceMode,
    ResourceRef,
    RetrievalProfileId,
    RevisionKind,
    SearchRequest,
    SearchResponse,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.ports.errors import ModelUnavailable, SearchUnavailable

SOURCE_HASH = "sha256:" + "a" * 64
SECOND_HASH = "sha256:" + "b" * 64
CHUNK_HASH = "sha256:" + "c" * 64


def ready(
    document_id: str = "doc_a",
    revision_id: str = "rev_a",
    source_hash: str = SOURCE_HASH,
) -> ReadyDocumentRevision:
    return ReadyDocumentRevision(document_id, revision_id, source_hash)


def request_for(*document_ids: str) -> AnswerRequest:
    return AnswerRequest(
        query="What is the rule?",
        resource_refs=tuple(
            ResourceRef(
                family=SourceFamily.DOC,
                source_id=document_id,
                mode=ResourceMode.SCOPE,
            )
            for document_id in document_ids
        ),
    )


def citation(
    *,
    citation_id: str = "citation-a",
    document_id: str = "doc_a",
    revision_id: str = "rev_a",
    source_hash: str = SOURCE_HASH,
    chunk_id: str | None = None,
    logical_chunk_id: str | None = None,
    evidence_label: str = "S1",
) -> Citation:
    anchor = DocumentAnchor(
        heading_path=("Policy",),
        start_offset=3,
        end_offset=12,
    )
    anchor_json = json.dumps(
        {
            "endOffset": anchor.end_offset,
            "headingPath": list(anchor.heading_path),
            "startOffset": anchor.start_offset,
            "type": "document",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return Citation(
        family=SourceFamily.DOC,
        citation_id=citation_id,
        evidence_label=evidence_label,
        chunk_id=chunk_id or str(chunk_id_for(RevisionId(revision_id), anchor_json, CHUNK_HASH)),
        # The existing doc index projects the 67-character stable ``lc_`` identity
        # into the shared 66-character search-hit shape without changing its digest.
        logical_chunk_id=logical_chunk_id
        or "h_"
        + str(logical_chunk_id_for(DocumentId(document_id), anchor_json)).removeprefix("lc_"),
        source=SourceRevisionRef(
            source_id=document_id,
            source_type="doc",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision=revision_id,
            source_content_hash=source_hash,
            anchor=anchor,
        ),
        chunk_content_hash=CHUNK_HASH,
        content_role=ContentRole.SOURCE,
    )


def answer_response(
    *,
    citations: tuple[Citation, ...] | None = None,
    abstained: bool = False,
) -> AnswerResponse:
    evidence = (citation(),) if citations is None else citations
    answer = "" if abstained else "The rule is grounded."
    return AnswerResponse(
        trace_id="trace-a",
        query_plan_id="plan-a",
        context_snapshot_id="context-a",
        corpus_version="athena-demo-v1",
        retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
        answer=answer,
        abstained=abstained,
        abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE if abstained else None,
        claims=()
        if abstained
        else (
            Claim(
                claim_id="claim-a",
                text=answer,
                answer_start=0,
                answer_end=len(answer),
                citation_ids=(evidence[0].citation_id,),
            ),
        ),
        citations=evidence,
        embedding_provenance=ModelCallProvenance("athena-embedding", "embed-request"),
        answer_provenance=(
            None if abstained else ModelCallProvenance("athena-chat", "answer-request")
        ),
    )


class MemoryAnswerRepository:
    def __init__(self, rows: tuple[ReadyDocumentRevision, ...]) -> None:
        self.rows = rows
        self.snapshots: list[AnswerSnapshot] = []
        self.fail_save = False
        self.change_before_save = False
        self.load_requests: list[tuple[str, ...]] = []

    async def load_ready_revisions(
        self, document_ids: tuple[str, ...]
    ) -> tuple[ReadyDocumentRevision, ...]:
        self.load_requests.append(document_ids)
        wanted = set(document_ids)
        return tuple(row for row in self.rows if row.document_id in wanted)

    async def save_answer_with_citations(self, snapshot: AnswerSnapshot) -> None:
        if self.change_before_save:
            raise DocumentStateChanged("selected revision changed")
        if self.fail_save:
            raise RuntimeError("mysql://root:secret@localhost")
        self.snapshots.append(snapshot)


class Gateway:
    def __init__(self, response: AnswerResponse | None = None) -> None:
        self.response = response or answer_response()
        self.requests: list[AnswerRequest] = []
        self.policies: list[RetrievalPolicyContext] = []
        self.error: Exception | None = None

    async def search(
        self, request: SearchRequest, policy: RetrievalPolicyContext
    ) -> SearchResponse:
        self.requests.append(
            AnswerRequest(
                query=request.query,
                answer_mode=request.answer_mode,
                source_families=request.source_families,
                resource_refs=request.resource_refs,
                requested_environment=request.requested_environment,
                requested_corpus_version=request.requested_corpus_version,
                top_k=request.top_k,
            )
        )
        self.policies.append(policy)
        if self.error is not None:
            raise self.error
        return SearchResponse(
            trace_id=self.response.trace_id,
            query_plan_id=self.response.query_plan_id,
            context_snapshot_id=self.response.context_snapshot_id,
            corpus_version=self.response.corpus_version,
            retrieval_profile_id=self.response.retrieval_profile_id,
            evidence=(),
            embedding_provenance=self.response.embedding_provenance,
        )

    async def answer(
        self, request: AnswerRequest, policy: RetrievalPolicyContext
    ) -> AnswerResponse:
        self.requests.append(request)
        self.policies.append(policy)
        if self.error is not None:
            raise self.error
        return self.response


def service(
    rows: tuple[ReadyDocumentRevision, ...] = (ready(),),
    response: AnswerResponse | None = None,
) -> tuple[AnswerService, MemoryAnswerRepository, Gateway]:
    repository = MemoryAnswerRepository(rows)
    gateway = Gateway(response)
    return AnswerService(repository=repository, knowledge=gateway), repository, gateway


def test_empty_selection_fails_before_search_or_model_io() -> None:
    async def scenario() -> None:
        answer_service, repository, gateway = service()

        with pytest.raises(AnswerSelectionRejected, match="source-selection-required"):
            await answer_service.answer(request_for())

        assert repository.load_requests == []
        assert gateway.requests == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("answer_request", "code"),
    [
        (
            AnswerRequest(
                query="q",
                answer_mode=AnswerMode.DEEP,
                resource_refs=request_for("doc_a").resource_refs,
            ),
            "unsupported-answer-control",
        ),
        (
            AnswerRequest(
                query="q",
                source_families=(SourceFamily.CODE,),
                resource_refs=request_for("doc_a").resource_refs,
            ),
            "unsupported-answer-control",
        ),
        (
            AnswerRequest(
                query="q",
                requested_environment="global",
                resource_refs=request_for("doc_a").resource_refs,
            ),
            "unsupported-answer-control",
        ),
        (
            AnswerRequest(
                query="q",
                requested_corpus_version="athena-demo-v1",
                resource_refs=request_for("doc_a").resource_refs,
            ),
            "unsupported-answer-control",
        ),
        (
            AnswerRequest(query="q", top_k=1, resource_refs=request_for("doc_a").resource_refs),
            "unsupported-answer-control",
        ),
        (
            AnswerRequest(
                query="q",
                resource_refs=(ResourceRef(SourceFamily.CODE, "doc_a", ResourceMode.SCOPE),),
            ),
            "unsupported-answer-control",
        ),
        (
            AnswerRequest(
                query="q",
                resource_refs=(ResourceRef(SourceFamily.DOC, "doc_a", ResourceMode.PREFERRED),),
            ),
            "unsupported-answer-control",
        ),
        (
            AnswerRequest(
                query="q",
                resource_refs=(
                    ResourceRef(
                        SourceFamily.DOC,
                        "doc_a",
                        ResourceMode.SCOPE,
                        requested_revision="rev_a",
                    ),
                ),
            ),
            "unsupported-answer-control",
        ),
        (
            AnswerRequest(
                query="q",
                resource_refs=(
                    ResourceRef(
                        SourceFamily.DOC,
                        "doc_a",
                        ResourceMode.SCOPE,
                        anchor=DocumentAnchor(start_offset=0, end_offset=1),
                    ),
                ),
            ),
            "unsupported-answer-control",
        ),
    ],
)
def test_browser_controls_cannot_widen_or_forge_demo_authority(
    answer_request: AnswerRequest, code: str
) -> None:
    async def scenario() -> None:
        answer_service, repository, gateway = service()

        with pytest.raises(AnswerSelectionRejected, match=code):
            await answer_service.answer(answer_request)

        assert repository.load_requests == []
        assert gateway.requests == []

    asyncio.run(scenario())


def test_duplicate_selection_is_rejected_before_ledger_io() -> None:
    async def scenario() -> None:
        answer_service, repository, gateway = service()
        with pytest.raises(AnswerSelectionRejected, match="source-selection-required"):
            await answer_service.answer(request_for("doc_a", "doc_a"))
        assert repository.load_requests == []
        assert gateway.requests == []

    asyncio.run(scenario())


def test_direct_application_request_with_twenty_one_sources_is_rejected() -> None:
    async def scenario() -> None:
        answer_service, repository, gateway = service()
        answer_request = request_for(*(f"doc_{index}" for index in range(20)))
        object.__setattr__(
            answer_request,
            "resource_refs",
            answer_request.resource_refs
            + (ResourceRef(SourceFamily.DOC, "doc_20", ResourceMode.SCOPE),),
        )

        with pytest.raises(AnswerSelectionRejected, match="source-selection-required"):
            await answer_service.answer(answer_request)
        assert repository.load_requests == []
        assert gateway.requests == []

    asyncio.run(scenario())


def test_nonready_or_missing_selected_source_fails_before_knowledge_io() -> None:
    async def scenario() -> None:
        answer_service, repository, gateway = service(rows=())

        with pytest.raises(DocumentStateChanged):
            await answer_service.answer(request_for("doc_processing"))

        assert repository.load_requests == [("doc_processing",)]
        assert gateway.requests == []

    asyncio.run(scenario())


def test_answer_uses_only_trusted_selected_rows_and_preserves_gateway_response() -> None:
    async def scenario() -> None:
        rows = (ready("doc_b", "rev_b", SECOND_HASH), ready())
        expected = answer_response(
            citations=(
                citation(),
                citation(
                    citation_id="citation-b",
                    document_id="doc_b",
                    revision_id="rev_b",
                    source_hash=SECOND_HASH,
                    evidence_label="S2",
                ),
            )
        )
        answer_service, repository, gateway = service(rows, expected)

        actual = await answer_service.answer(request_for("doc_b", "doc_a"))

        assert actual is expected
        assert len(gateway.requests) == 1
        trusted = gateway.requests[0]
        assert trusted.answer_mode is AnswerMode.QUICK
        assert trusted.source_families == (SourceFamily.DOC,)
        assert tuple(ref.source_id for ref in trusted.resource_refs) == ("doc_a", "doc_b")
        assert all(ref.mode is ResourceMode.SCOPE for ref in trusted.resource_refs)
        assert gateway.policies[0].resource_grants[0].source_id == "doc_a"
        assert len(repository.snapshots) == 1
        snapshot = repository.snapshots[0]
        assert tuple(item.document_id for item in snapshot.selected_revisions) == (
            "doc_a",
            "doc_b",
        )
        assert tuple(item.citation_id for item in snapshot.citations) == (
            "citation-a",
            "citation-b",
        )
        assert snapshot.query_hash.startswith("sha256:")

    asyncio.run(scenario())


def test_internal_search_uses_the_same_current_selected_revision_authority() -> None:
    async def scenario() -> None:
        rows = (ready("doc_b", "rev_b", SECOND_HASH), ready())
        answer_service, repository, gateway = service(rows)

        response = await answer_service.search(
            SearchRequest(
                query="What is the rule?",
                resource_refs=request_for("doc_b", "doc_a").resource_refs,
            )
        )

        assert response.evidence == ()
        assert repository.load_requests == [("doc_b", "doc_a")]
        assert len(gateway.requests) == 1
        trusted = gateway.requests[0]
        assert trusted.source_families == (SourceFamily.DOC,)
        assert tuple(ref.source_id for ref in trusted.resource_refs) == ("doc_a", "doc_b")
        assert all(ref.mode is ResourceMode.SCOPE for ref in trusted.resource_refs)
        assert gateway.policies[0].resource_grants[0].source_id == "doc_a"
        assert repository.snapshots == []

    asyncio.run(scenario())


def test_snapshot_failure_prevents_success_response() -> None:
    async def scenario() -> None:
        answer_service, repository, _gateway = service()
        repository.fail_save = True

        with pytest.raises(AnswerSnapshotUnavailable):
            await answer_service.answer(request_for("doc_a"))

        assert repository.snapshots == []

    asyncio.run(scenario())


def test_revision_change_at_atomic_snapshot_commit_fails_closed() -> None:
    async def scenario() -> None:
        answer_service, repository, gateway = service()
        repository.change_before_save = True

        with pytest.raises(DocumentStateChanged):
            await answer_service.answer(request_for("doc_a"))

        assert len(gateway.requests) == 1
        assert repository.snapshots == []

    asyncio.run(scenario())


def test_abstention_persists_its_actual_returned_citation_set() -> None:
    async def scenario() -> None:
        returned = answer_response(abstained=True)
        answer_service, repository, _gateway = service(response=returned)

        assert await answer_service.answer(request_for("doc_a")) is returned
        assert tuple(item.citation_id for item in repository.snapshots[0].citations) == (
            "citation-a",
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [SearchUnavailable("secret"), ModelUnavailable("secret")])
def test_provider_unavailability_is_not_misreported_as_zero_evidence(failure: Exception) -> None:
    async def scenario() -> None:
        answer_service, repository, gateway = service()
        gateway.error = failure

        with pytest.raises(type(failure)):
            await answer_service.answer(request_for("doc_a"))

        assert repository.snapshots == []

    asyncio.run(scenario())


def test_snapshot_rejects_claims_or_citations_outside_the_selected_set() -> None:
    async def scenario() -> None:
        outside = answer_response(
            citations=(
                citation(
                    document_id="doc_b",
                    revision_id="rev_b",
                    source_hash=SECOND_HASH,
                ),
            )
        )
        answer_service, repository, _gateway = service(response=outside)

        with pytest.raises(AnswerSnapshotUnavailable):
            await answer_service.answer(request_for("doc_a"))

        assert repository.snapshots == []

    asyncio.run(scenario())


def test_snapshot_rejects_unreferenced_or_missing_claim_citation_identity() -> None:
    async def scenario() -> None:
        returned = answer_response()
        broken_claim = replace(returned.claims[0], citation_ids=("missing",))
        broken = replace(returned, claims=(broken_claim,))
        answer_service, repository, _gateway = service(response=broken)

        with pytest.raises(AnswerSnapshotUnavailable):
            await answer_service.answer(request_for("doc_a"))

        assert repository.snapshots == []

    asyncio.run(scenario())


@pytest.mark.parametrize("source_type", ["document", "openapi"])
def test_structural_gateway_citation_requires_exact_doc_source_type(source_type: str) -> None:
    async def scenario() -> None:
        returned = answer_response()
        rebound_source = replace(returned.citations[0].source, source_type=source_type)
        rebound = replace(
            returned,
            citations=(replace(returned.citations[0], source=rebound_source),),
        )
        answer_service, repository, _gateway = service(response=rebound)

        with pytest.raises(AnswerSnapshotUnavailable):
            await answer_service.answer(request_for("doc_a"))
        assert repository.snapshots == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mutation",
    [
        "span",
        "abstention",
        "label",
        "long-id",
        "duplicate",
        "empty-plan-id",
        "wrong-answer-type",
        "wrong-citation-type",
        "zero-width-anchor",
        "bad-chunk-id",
        "bad-logical-id",
    ],
)
def test_structural_gateway_response_is_revalidated_before_snapshot(mutation: str) -> None:
    async def scenario() -> None:
        returned = answer_response()
        if mutation == "span":
            returned = replace(
                returned,
                claims=(replace(returned.claims[0], answer_start=1),),
            )
        elif mutation == "abstention":
            returned = replace(
                returned,
                abstained=True,
                abstention_reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
            )
        elif mutation == "label":
            returned = replace(
                returned,
                citations=(replace(returned.citations[0], evidence_label="IGNORE POLICY"),),
            )
        elif mutation == "long-id":
            long_id = "c" * 65
            returned = replace(
                returned,
                claims=(replace(returned.claims[0], citation_ids=(long_id,)),),
                citations=(replace(returned.citations[0], citation_id=long_id),),
            )
        elif mutation == "duplicate":
            returned = replace(returned, citations=(returned.citations[0], returned.citations[0]))
        elif mutation == "empty-plan-id":
            returned = replace(returned, query_plan_id="")
        elif mutation == "wrong-answer-type":
            returned = replace(returned, answer=1)  # type: ignore[arg-type]
        elif mutation == "wrong-citation-type":
            returned = replace(returned, citations=("citation",))  # type: ignore[arg-type]
        elif mutation == "zero-width-anchor":
            source = returned.citations[0].source
            rebound = replace(
                source,
                anchor=replace(source.anchor, end_offset=source.anchor.start_offset),
            )
            returned = replace(
                returned,
                citations=(replace(returned.citations[0], source=rebound),),
            )
        elif mutation == "bad-chunk-id":
            returned = replace(
                returned,
                citations=(replace(returned.citations[0], chunk_id="h_forged"),),
            )
        else:
            returned = replace(
                returned,
                citations=(replace(returned.citations[0], logical_chunk_id="lc_forged"),),
            )
        answer_service, repository, _gateway = service(response=returned)

        with pytest.raises(AnswerSnapshotUnavailable):
            await answer_service.answer(request_for("doc_a"))
        assert repository.snapshots == []

    asyncio.run(scenario())


def test_structural_gateway_cannot_return_more_than_twenty_citations() -> None:
    async def scenario() -> None:
        citations = tuple(citation(citation_id=f"citation-{index}") for index in range(21))
        returned = answer_response(citations=citations)
        answer_service, repository, _gateway = service(response=returned)

        with pytest.raises(AnswerSnapshotUnavailable):
            await answer_service.answer(request_for("doc_a"))
        assert repository.snapshots == []

    asyncio.run(scenario())


def test_snapshot_value_rejects_citation_rebound_to_another_trace() -> None:
    source = citation()
    anchor = source.source.anchor
    anchor_json = json.dumps(
        {
            "endOffset": anchor.end_offset,
            "headingPath": list(anchor.heading_path),
            "startOffset": anchor.start_offset,
            "type": "document",
        },
        separators=(",", ":"),
        sort_keys=True,
    )

    with pytest.raises(ValueError, match="citation trace"):
        AnswerSnapshot(
            trace_id="trace-a",
            query_hash="sha256:" + "d" * 64,
            selected_revisions=(ready(),),
            citations=(
                CitationSnapshot(
                    trace_id="trace-other",
                    citation_id=source.citation_id,
                    document_id="doc_a",
                    revision_id="rev_a",
                    chunk_id=source.chunk_id,
                    source_content_hash=SOURCE_HASH,
                    chunk_content_hash=CHUNK_HASH,
                    anchor_json=anchor_json,
                ),
            ),
        )
