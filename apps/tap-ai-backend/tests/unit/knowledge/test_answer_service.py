from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from tap.modules.access.domain.policy import ResourceGrant, RetrievalPolicyContext
from tap.modules.knowledge.application.answer_templates import get_template
from tap.modules.knowledge.application.answers import (
    AnswerSelectionRejected,
    AnswerService,
    AnswerSnapshot,
    AnswerSnapshotUnavailable,
    CitationSnapshot,
    DocumentStateChanged,
    ReadyDocumentRevision,
)
from tap.modules.knowledge.application.planned_answer import (
    AuthorizedAnswerExecution,
    AuthorizedAnswerQuery,
)
from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority
from tap.modules.knowledge.application.relation_analysis import (
    RelationContext,
    RelationContextStatus,
    RelationEvidence,
    RelationSupport,
    SeedNode,
)
from tap.modules.knowledge.application.retrieve import AuthorizedRetrieval
from tap.modules.knowledge.domain.documents import (
    DocumentId,
    RevisionId,
    chunk_id_for,
    logical_chunk_id_for,
    logical_chunk_projection_id,
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
    EdgeCitation,
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
from tap.modules.knowledge.domain.review import KnowledgePublication
from tap.modules.knowledge.domain.sources import legacy_source_id
from tap.modules.knowledge.ports.errors import ModelUnavailable, SearchUnavailable
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    GeneratedClaim,
    IndexRevision,
    SearchHit,
)
from tests.contract.test_knowledge_api import (
    CurrentPolicyVerifier,
    PassthroughRedactor,
    policy_context,
)

SOURCE_HASH = "sha256:" + "a" * 64
SECOND_HASH = "sha256:" + "b" * 64
CHUNK_HASH = "sha256:" + "c" * 64


def ready(
    document_id: str = "doc_a",
    revision_id: str = "rev_a",
    source_hash: str = SOURCE_HASH,
) -> ReadyDocumentRevision:
    return ReadyDocumentRevision(
        document_id, revision_id, source_hash, legacy_source_id("tapper-demo", document_id)
    )


def request_for(*document_ids: str) -> AnswerRequest:
    return AnswerRequest(
        query="What is the rule?",
        resource_refs=tuple(
            ResourceRef(
                family=SourceFamily.DOC,
                source_id=legacy_source_id("tapper-demo", document_id),
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
            source_id=legacy_source_id("tapper-demo", document_id),
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
        corpus_version="tapper-demo-v1",
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
        embedding_provenance=ModelCallProvenance("text-embedding-v4", "embed-request"),
        answer_provenance=(
            None if abstained else ModelCallProvenance("qwen-plus", "answer-request")
        ),
    )


class MemoryAnswerRepository:
    def __init__(self, rows: tuple[ReadyDocumentRevision, ...]) -> None:
        self.rows = rows
        self.snapshots: list[AnswerSnapshot] = []
        self.fail_save = False
        self.change_before_save = False
        self.load_requests: list[tuple[str, ...]] = []

    async def load_source_revisions(
        self, document_ids: tuple[str, ...]
    ) -> tuple[ReadyDocumentRevision, ...]:
        self.load_requests.append(document_ids)
        wanted = set(document_ids)
        return tuple(row for row in self.rows if row.source_id in wanted)

    async def load_revision_selection(self, revision_ids):
        wanted = set(revision_ids)
        return tuple(row for row in self.rows if row.revision_id in wanted)

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


@pytest.mark.asyncio
async def test_conversation_revision_selection_resolves_current_rows_and_policy_or_fails_closed():
    answer_service, _, _ = service()
    rows, policy = await answer_service.resolve_conversation_selection(("rev_a",))
    assert rows == (ready(),)
    assert policy.project_id == "tapper-demo"
    assert policy.acl_digest.startswith("sha256:")
    with pytest.raises(DocumentStateChanged, match="current and ready"):
        await answer_service.resolve_conversation_selection(("missing",))


@pytest.mark.asyncio
async def test_frozen_conversation_answer_never_reloads_current_document_state():
    answer_service, repository, gateway = service()
    frozen = (ready(),)
    from tap.modules.knowledge.application.demo_policy import build_demo_policy_context

    async def reject_reload(*_args):
        raise AssertionError("accepted Turn must not reload current revisions")

    repository.load_source_revisions = reject_reload
    repository.load_revision_selection = reject_reload
    response = await answer_service.answer_frozen(
        request_for("doc_a"), frozen, build_demo_policy_context(frozen), governance=None
    )
    assert response == answer_response()
    assert gateway.requests[0].resource_refs[0].requested_revision == "rev_a"


@pytest.mark.asyncio
@pytest.mark.parametrize("corpus", ["tapper-demo-v1", "tapper-demo-v2"])
@pytest.mark.parametrize("abstained", [False, True])
async def test_answer_uses_the_selected_runtime_projection_corpus(corpus, abstained):
    repository = MemoryAnswerRepository((ready(),))
    response = replace(answer_response(abstained=abstained), corpus_version=corpus)
    gateway = Gateway(response)
    service = AnswerService(repository=repository, knowledge=gateway, corpus_version=corpus)
    assert await service.answer(request_for("doc_a")) == response
    assert gateway.policies[0].active_corpus_version == corpus
    assert len(repository.snapshots) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured,returned",
    [("tapper-demo-v1", "tapper-demo-v2"), ("tapper-demo-v2", "tapper-demo-v1")],
)
async def test_answer_refuses_response_policy_corpus_mismatch_before_persistence(
    configured, returned
):
    repository = MemoryAnswerRepository((ready(),))
    gateway = Gateway(replace(answer_response(), corpus_version=returned))
    service = AnswerService(repository=repository, knowledge=gateway, corpus_version=configured)
    with pytest.raises(AnswerSnapshotUnavailable):
        await service.answer(request_for("doc_a"))
    assert gateway.policies[0].active_corpus_version == configured
    assert repository.snapshots == []


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
                requested_corpus_version="tapper-demo-v1",
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

        assert repository.load_requests == [(legacy_source_id("tapper-demo", "doc_processing"),)]
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
        assert tuple(ref.source_id for ref in trusted.resource_refs) == tuple(
            legacy_source_id("tapper-demo", item) for item in ("doc_a", "doc_b")
        )
        assert all(ref.mode is ResourceMode.SCOPE for ref in trusted.resource_refs)
        assert gateway.policies[0].resource_grants[0].source_id == legacy_source_id(
            "tapper-demo", "doc_a"
        )
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
        assert repository.load_requests == [
            tuple(legacy_source_id("tapper-demo", item) for item in ("doc_b", "doc_a"))
        ]
        assert len(gateway.requests) == 1
        trusted = gateway.requests[0]
        assert trusted.source_families == (SourceFamily.DOC,)
        assert tuple(ref.source_id for ref in trusted.resource_refs) == tuple(
            legacy_source_id("tapper-demo", item) for item in ("doc_a", "doc_b")
        )
        assert all(ref.mode is ResourceMode.SCOPE for ref in trusted.resource_refs)
        assert gateway.policies[0].resource_grants[0].source_id == legacy_source_id(
            "tapper-demo", "doc_a"
        )
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


def test_published_image_region_citation_is_snapshotted_with_its_bound_region() -> None:
    """Flowchart answers cite approved image regions; the snapshot must keep, not refuse, them."""

    async def scenario() -> None:
        anchor = DocumentAnchor(
            heading_path=("Flowchart",),
            start_offset=0,
            end_offset=40,
            inventory_item_id="pi_edge",
            bbox=(10, 20, 300, 90),
        )
        anchor_json = json.dumps(
            {
                "bbox": [10, 20, 300, 90],
                "endOffset": 40,
                "headingPath": ["Flowchart"],
                "inventoryItemId": "pi_edge",
                "startOffset": 0,
                "type": "document",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        base = citation()
        image = replace(
            base,
            chunk_id=str(chunk_id_for(RevisionId("rev_a"), anchor_json, CHUNK_HASH)),
            logical_chunk_id="h_"
            + str(logical_chunk_id_for(DocumentId("doc_a"), anchor_json)).removeprefix("lc_"),
            source=replace(base.source, anchor=anchor),
        )
        answer_service, repository, _gateway = service(response=answer_response(citations=(image,)))

        await answer_service.answer(request_for("doc_a"))

        assert json.loads(repository.snapshots[0].citations[0].anchor_json)["bbox"] == [
            10,
            20,
            300,
            90,
        ]

        forged = replace(
            image, source=replace(image.source, anchor=replace(anchor, bbox=(0, 0, 1, 1)))
        )
        answer_service, repository, _gateway = service(
            response=answer_response(citations=(forged,))
        )
        with pytest.raises(AnswerSnapshotUnavailable):
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


def _edge_citation(
    *,
    citation_id: str,
    evidence_label: str,
    graph_version: str = "7",
) -> Citation:
    base = citation(citation_id=citation_id, evidence_label=evidence_label)
    return replace(
        base,
        kind="edge",
        edge=EdgeCitation(
            edge_id="e-1",
            graph_version=graph_version,
            subject_node_id="A",
            subject_label="节点 A",
            object_node_id="B",
            object_label="节点 B",
            relation_type="REQUIRES",
            relation_label="REQUIRES",
        ),
    )


def test_snapshot_accepts_edge_citations_after_chunk_citations_with_one_graph_version() -> None:
    s1 = citation(citation_id="citation-1", evidence_label="S1")
    s2 = citation(citation_id="citation-2", evidence_label="S2")
    r1 = _edge_citation(citation_id="citation-3", evidence_label="R1")

    def build(citations: tuple[Citation, ...]) -> AnswerResponse:
        answer = "The rule is grounded."
        return AnswerResponse(
            trace_id="trace-a",
            query_plan_id="plan-a",
            context_snapshot_id="context-a",
            corpus_version="tapper-demo-v1",
            retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
            answer=answer,
            abstained=False,
            abstention_reason=None,
            claims=(
                Claim(
                    claim_id="claim-a",
                    text=answer,
                    answer_start=0,
                    answer_end=len(answer),
                    citation_ids=tuple(item.citation_id for item in citations),
                ),
            ),
            citations=citations,
            embedding_provenance=ModelCallProvenance("text-embedding-v4", "embed-request"),
            answer_provenance=ModelCallProvenance("qwen-plus", "answer-request"),
        )

    ok = AnswerSnapshot.from_response(
        response=build((s1, s2, r1)),
        query="q",
        selected_revisions=(ready(),),
    )
    assert ok.citations[-1].citation_kind == "edge"
    assert ok.citations[-1].graph_version == "7"

    with pytest.raises(ValueError):
        AnswerSnapshot.from_response(
            response=build((r1, s1, s2)),
            query="q",
            selected_revisions=(ready(),),
        )

    r2 = _edge_citation(citation_id="citation-4", evidence_label="R2", graph_version="8")
    with pytest.raises(ValueError):
        AnswerSnapshot.from_response(
            response=build((s1, s2, r1, r2)),
            query="q",
            selected_revisions=(ready(),),
        )


def test_edge_citation_snapshot_requires_all_edge_fields() -> None:
    anchor_json = json.dumps(
        {"endOffset": 12, "headingPath": ["Policy"], "startOffset": 3, "type": "document"},
        separators=(",", ":"),
        sort_keys=True,
    )
    with pytest.raises(ValueError):
        CitationSnapshot(
            trace_id="trace-a",
            citation_id="citation-a",
            document_id="doc_a",
            revision_id="rev_a",
            chunk_id="chunk-a",
            source_content_hash=SOURCE_HASH,
            chunk_content_hash=CHUNK_HASH,
            anchor_json=anchor_json,
            citation_kind="edge",
            edge_id=None,
        )
    with pytest.raises(ValueError):
        CitationSnapshot(
            trace_id="trace-a",
            citation_id="citation-a",
            document_id="doc_a",
            revision_id="rev_a",
            chunk_id="chunk-a",
            source_content_hash=SOURCE_HASH,
            chunk_content_hash=CHUNK_HASH,
            anchor_json=anchor_json,
            citation_kind="chunk",
            edge_id="e-1",
        )


def test_edge_citation_snapshot_bounds_edge_field_widths() -> None:
    anchor_json = json.dumps(
        {"endOffset": 12, "headingPath": ["Policy"], "startOffset": 3, "type": "document"},
        separators=(",", ":"),
        sort_keys=True,
    )

    def _edge_snapshot(**overrides: str) -> CitationSnapshot:
        fields: dict[str, str] = {
            "graph_version": "7",
            "edge_id": "e-1",
            "subject_node_id": "A",
            "object_node_id": "B",
            "relation_type": "REQUIRES",
            "relation_label": "REQUIRES",
        }
        fields.update(overrides)
        return CitationSnapshot(
            trace_id="trace-a",
            citation_id="citation-a",
            document_id="doc_a",
            revision_id="rev_a",
            chunk_id="chunk-a",
            source_content_hash=SOURCE_HASH,
            chunk_content_hash=CHUNK_HASH,
            anchor_json=anchor_json,
            citation_kind="edge",
            **fields,
        )

    _edge_snapshot()  # widths at the limit must pass
    with pytest.raises(ValueError):
        _edge_snapshot(graph_version="v" * 65)
    with pytest.raises(ValueError):
        _edge_snapshot(edge_id="e" * 129)
    with pytest.raises(ValueError):
        _edge_snapshot(subject_node_id="n" * 129)
    with pytest.raises(ValueError):
        _edge_snapshot(object_node_id="n" * 129)
    with pytest.raises(ValueError):
        _edge_snapshot(relation_type="R" * 65)


@pytest.mark.asyncio
async def test_canonical_source_expands_documents_and_preserves_document_chunk_identity():
    source_id = "src_" + "a" * 32
    rows = (
        replace(ready(), source_id=source_id),
        replace(ready("doc_b", "rev_b", SECOND_HASH), source_id=source_id),
    )
    evidence = citation()
    response = answer_response(
        citations=(replace(evidence, source=replace(evidence.source, source_id=source_id)),)
    )
    repository = MemoryAnswerRepository(rows)

    async def load_sources(ids):
        assert ids == (source_id,)
        return rows

    repository.load_source_revisions = load_sources
    gateway = Gateway(response)
    answer_service = AnswerService(repository=repository, knowledge=gateway)
    request = AnswerRequest(
        query="What is the rule?",
        resource_refs=(ResourceRef(SourceFamily.DOC, source_id, ResourceMode.SCOPE),),
    )
    assert await answer_service.answer(request) is response
    assert [
        (ref.source_id, ref.requested_revision) for ref in gateway.requests[0].resource_refs
    ] == [(source_id, "rev_a"), (source_id, "rev_b")]
    assert repository.snapshots[0].citations[0].document_id == "doc_a"


@pytest.mark.asyncio
async def test_unpublished_flowchart_images_are_refused_while_text_sources_answer_directly():
    from test_flowchart_publication_gate import gate, publication

    text = ready()
    image = replace(
        ready("doc_b", "rev_b", SECOND_HASH), filename="flow.png", source_name="flow.png"
    )
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE

    repository = MemoryAnswerRepository((text, image))
    repository.scope = VALIDATION_SCOPE  # type: ignore[attr-defined]
    unpublished = AnswerService(
        repository=repository, knowledge=Gateway(None), flowchart_gate=gate()
    )

    rows, _policy = await unpublished.resolve_conversation_selection(("rev_a",))
    assert rows == (text,)
    with pytest.raises(DocumentStateChanged, match="published review"):
        await unpublished.resolve_conversation_selection(("rev_a", "rev_b"))
    with pytest.raises(DocumentStateChanged, match="published review"):
        await unpublished.authorize_frozen_selection((text, image))

    published = AnswerService(
        repository=repository,
        knowledge=Gateway(None),
        flowchart_gate=gate(publication("rev_b")),
    )
    rows, _policy = await published.resolve_conversation_selection(("rev_a", "rev_b"))
    assert {row.revision_id for row in rows} == {"rev_a", "rev_b"}
    await published.authorize_frozen_selection((text, image))


# ---------------------------------------------------------------------------
# PR 3 Task 5: relation analysis wired into `AuthorizedRetrieval.answer` --
# prompt records, retrieval-augmentation re-authorization, claim
# reconciliation and edge citations.
# ---------------------------------------------------------------------------

_PUB_HASH = "sha256:" + "d" * 64
_SRC_ID = "src_" + "d" * 32


def _relation_hit(
    chunk_id: str,
    revision: str,
    *,
    content: str = "relation evidence content",
    item_id: str,
    generation: str = "gen-1",
    local_rank: int = 1,
) -> SearchHit:
    content_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    return SearchHit(
        family=SourceFamily.DOC,
        chunk_id=chunk_id,
        logical_chunk_id="h_" + "2" * 64,
        title=None,
        content=content,
        source=SourceRevisionRef(
            source_id=_SRC_ID,
            source_type="doc",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision=revision,
            source_content_hash=_PUB_HASH,
            anchor=DocumentAnchor(
                inventory_item_id=item_id, start_offset=0, end_offset=len(content)
            ),
        ),
        chunk_content_hash=content_hash,
        content_role=ContentRole.SOURCE,
        index_revision=IndexRevision(generation, "search-schema-v1", "corpus-17"),
        embedding_model_version="tap-embedding-v1",
        score=0.9,
        local_rank=local_rank,
    )


_REAL_HIT_DOCUMENT_ID = "doc_demo"


def _real_formula_hit(index: int, *, revision: str = "rev-1") -> SearchHit:
    """Like `_relation_hit`, but with a formula-correct `chunk_id`/
    `logical_chunk_id` (matching `chunk_id_for`/`logical_chunk_id_for`) so the
    resulting evidence survives `AnswerSnapshot.from_response`'s strict
    recomputation check -- needed to reproduce the C1 snapshot-layer failure
    end-to-end, not just at the `AuthorizedRetrieval.answer` boundary."""
    anchor = DocumentAnchor(heading_path=(f"H{index}",), start_offset=0, end_offset=10)
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
    content = f"relation evidence content {index}"
    content_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    chunk_id = str(chunk_id_for(RevisionId(revision), anchor_json, content_hash))
    logical_chunk_id = "h_" + str(
        logical_chunk_id_for(DocumentId(_REAL_HIT_DOCUMENT_ID), anchor_json)
    ).removeprefix("lc_")
    return SearchHit(
        family=SourceFamily.DOC,
        chunk_id=chunk_id,
        logical_chunk_id=logical_chunk_id,
        title=None,
        content=content,
        source=SourceRevisionRef(
            source_id=_SRC_ID,
            source_type="doc",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision=revision,
            source_content_hash=_PUB_HASH,
            anchor=anchor,
        ),
        chunk_content_hash=content_hash,
        content_role=ContentRole.SOURCE,
        index_revision=IndexRevision("gen-1", "search-schema-v1", "corpus-17"),
        embedding_model_version="tap-embedding-v1",
        score=0.9,
        local_rank=index,
    )


class _SequencedSearchPort:
    """Returns one configured hit tuple per call, in order."""

    def __init__(self, *responses: tuple[SearchHit, ...]) -> None:
        self._responses = list(responses)
        self.executions: list[object] = []

    async def search(self, execution) -> tuple[SearchHit, ...]:
        self.executions.append(execution)
        if not self._responses:
            return ()
        return self._responses.pop(0)


class _RecordingModel:
    embedding_model_id = "tap-embedding-v1"
    embedding_dimension = 2

    def __init__(self, generations: list[AnswerGeneration]) -> None:
        self._generations = list(generations)
        self.calls: list[dict[str, object]] = []

    async def embed(self, query: str) -> Embedding:
        return Embedding(
            vector=(0.25, 0.5), model_id=self.embedding_model_id, provider_request_id="e-1"
        )

    async def answer(self, query, evidence, profile_id, **kwargs) -> AnswerGeneration:
        self.calls.append(
            {"query": query, "evidence": evidence, "profile_id": profile_id, **kwargs}
        )
        return self._generations.pop(0) if len(self._generations) > 1 else self._generations[0]


class _FakeRelationAnalysis:
    def __init__(self, context: RelationContext) -> None:
        self._context = context
        self.calls: list[dict[str, object]] = []

    async def analyse(self, query, evidence, source_revision_ids, *, turn_id=None):
        self.calls.append(
            {"query": query, "evidence": evidence, "source_revision_ids": source_revision_ids}
        )
        return self._context


def _resource_grant(revision: str) -> ResourceGrant:
    return ResourceGrant(
        family="doc",
        source_id=_SRC_ID,
        revision_kind="blob_version",
        revision=revision,
        source_content_hash=_PUB_HASH,
        allow_all_anchors=True,
    )


def _relation_retrieval(
    search,
    model,
    *,
    relation_analysis=None,
    retrieval_augment: bool = True,
    publication_authority=None,
):
    ids = iter(f"id-{index}" for index in range(10_000))
    return AuthorizedRetrieval(
        search=search,
        embeddings=model,
        answers=model,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=lambda: next(ids),
        publication_authority=publication_authority,
        relation_analysis=relation_analysis,
        retrieval_augment=retrieval_augment,
    )


def _single_revision_request() -> AnswerRequest:
    return AnswerRequest(
        query="核保流程与健康告知的关系是什么？",
        answer_mode=AnswerMode.QUICK,
        resource_refs=(
            ResourceRef(
                family=SourceFamily.DOC,
                source_id=_SRC_ID,
                mode=ResourceMode.SCOPE,
                requested_revision="rev-1",
            ),
        ),
    )


def _single_revision_policy() -> RetrievalPolicyContext:
    return policy_context(
        allowed_source_families=frozenset({"doc"}),
        resource_grants=(_resource_grant("rev-1"),),
    )


def test_empty_relation_context_yields_no_relation_claim_and_empty_event() -> None:
    async def scenario() -> None:
        search = _SequencedSearchPort((_relation_hit("chunk-s1", "rev-1", item_id="item-1"),))
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="没有依据的关系。\n\n已验证的事实。",
                    claims=(
                        GeneratedClaim(text="没有依据的关系。", evidence_labels=("R1",)),
                        GeneratedClaim(text="已验证的事实。", evidence_labels=("S1",)),
                    ),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        relation = _FakeRelationAnalysis(
            RelationContext(status=RelationContextStatus.EMPTY, graph_version="7")
        )
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        response = await knowledge.answer(_single_revision_request(), _single_revision_policy())

        assert not response.abstained
        assert response.answer == "已验证的事实。"
        assert response.relation is not None
        assert response.relation.status is RelationContextStatus.EMPTY
        assert all(citation.kind == "chunk" for citation in response.citations)
        assert "invalid-relation-citation" in response.degradation_reasons

    asyncio.run(scenario())


def test_empty_relation_context_abstention_preserves_relation_outcome() -> None:
    """When every claim cites only an invalid R label, reconciliation strips
    them all and the answer abstains -- but the EMPTY relation hint (e.g. "no
    direct relation found between A and B") must still reach the response,
    not be lost to the abstention (minor 4)."""

    async def scenario() -> None:
        search = _SequencedSearchPort((_relation_hit("chunk-s1", "rev-1", item_id="item-1"),))
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="没有依据的关系。",
                    claims=(GeneratedClaim(text="没有依据的关系。", evidence_labels=("R1",)),),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        relation = _FakeRelationAnalysis(
            RelationContext(status=RelationContextStatus.EMPTY, graph_version="7")
        )
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        response = await knowledge.answer(_single_revision_request(), _single_revision_policy())

        assert response.abstained
        assert response.relation is not None
        assert response.relation.status is RelationContextStatus.EMPTY

    asyncio.run(scenario())


def test_stale_context_answers_chunk_only() -> None:
    async def scenario() -> None:
        search = _SequencedSearchPort((_relation_hit("chunk-s1", "rev-1", item_id="item-1"),))
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="已验证的事实。",
                    claims=(GeneratedClaim(text="已验证的事实。", evidence_labels=("S1",)),),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        relation = _FakeRelationAnalysis(
            RelationContext(status=RelationContextStatus.STALE, graph_version="8")
        )
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        response = await knowledge.answer(_single_revision_request(), _single_revision_policy())

        assert not response.abstained
        assert response.relation is not None
        assert response.relation.status is RelationContextStatus.STALE
        assert all(citation.kind == "chunk" for citation in response.citations)

    asyncio.run(scenario())


def test_augmentation_disabled_by_flag() -> None:
    async def scenario() -> None:
        search = _SequencedSearchPort(
            (_relation_hit("chunk-s1", "rev-1", item_id="item-1"),),
            (_relation_hit("chunk-aug-1", "rev-1", item_id="item-aug1"),),
        )
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="已验证的事实。",
                    claims=(GeneratedClaim(text="已验证的事实。", evidence_labels=("S1",)),),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        support = RelationSupport(
            chunk_id="chunk-aug-1",
            source_revision_id="rev-1",
            document_revision_id="rev-1",
            content_digest="sha256:" + "e" * 64,
            anchor={"page": 1},
            snippet="candidate snippet",
        )
        relation_evidence = RelationEvidence(
            label="R1",
            edge_id="e-1",
            subject_node_id="A",
            subject_label="核保流程",
            subject_aliases=(),
            object_node_id="B",
            object_label="健康告知",
            object_aliases=(),
            relation_type="REQUIRES",
            relation_label="REQUIRES",
            origin="EXTRACTED",
            confidence=0.9,
            support=(
                RelationSupport(
                    chunk_id="chunk-s1",
                    source_revision_id="rev-1",
                    document_revision_id="rev-1",
                    content_digest="sha256:" + "f" * 64,
                    anchor={"page": 1},
                    evidence_label="S1",
                ),
            ),
            on_path=False,
        )
        context = RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            relations=(relation_evidence,),
            augment_chunks=(support,),
        )
        relation = _FakeRelationAnalysis(context)
        knowledge = _relation_retrieval(
            search, model, relation_analysis=relation, retrieval_augment=False
        )
        await knowledge.answer(_single_revision_request(), _single_revision_policy())

        assert len(search.executions) == 1  # no second retrieval was attempted

    asyncio.run(scenario())


def _preferred_revision_request(revision: str = "rev-1") -> AnswerRequest:
    """Like `_single_revision_request`, but `PREFERRED` (not `SCOPE`) -- it still
    resolves into `run.plan.resources` (so augmentation treats it as
    "selected"), but does not narrow which hits `_hit_is_in_execution` accepts,
    letting a test's fake search port return hits for several revisions in one
    call without being rejected as "outside bound execution"."""
    return AnswerRequest(
        query="核保流程与健康告知的关系是什么？",
        answer_mode=AnswerMode.QUICK,
        resource_refs=(
            ResourceRef(
                family=SourceFamily.DOC,
                source_id=_SRC_ID,
                mode=ResourceMode.PREFERRED,
                requested_revision=revision,
            ),
        ),
    )


def test_augmentation_never_adds_unauthorized_chunks() -> None:
    async def scenario() -> None:
        # Published at the repository level for all three revisions -- "published"
        # and "selected" are independent axes; what must gate chunk-aug-2/3 out is
        # the augmentation's own selection filter below, not the search/publication
        # layer happening to never return their hits.
        publication = KnowledgePublication(
            publication_id="publication-1",
            project_id="project-a",
            review_id="review-1",
            review_version=1,
            approval_digest="sha256:" + "a" * 64,
            source_revision_ids=("rev-1", "rev-2", "rev-3"),
            approved_item_ids=("item-1", "item-aug1", "item-aug2", "item-aug3"),
            generation="gen-1",
            published_by="reviewer-1",
            published_at=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        )

        class _Repository:
            async def current_publication(self):
                return publication

        authority = PublishedKnowledgeAuthority(_Repository())

        search = _SequencedSearchPort(
            (_relation_hit("chunk-s1", "rev-1", item_id="item-1"),),
            (
                _relation_hit("chunk-aug-1", "rev-1", item_id="item-aug1", local_rank=1),
                _relation_hit("chunk-aug-2", "rev-2", item_id="item-aug2", local_rank=2),
                _relation_hit("chunk-aug-3", "rev-3", item_id="item-aug3", local_rank=3),
            ),
        )
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="已验证的事实。",
                    claims=(GeneratedClaim(text="已验证的事实。", evidence_labels=("S1",)),),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        augment_candidates = (
            RelationSupport(
                chunk_id="chunk-aug-1",
                source_revision_id="rev-1",
                document_revision_id="rev-1",
                content_digest="sha256:" + "e" * 64,
                anchor={"page": 1},
                snippet="selected",
            ),
            RelationSupport(
                chunk_id="chunk-aug-2",
                source_revision_id="rev-2",
                document_revision_id="rev-2",
                content_digest="sha256:" + "e" * 64,
                anchor={"page": 1},
                snippet="unselected and ungranted revision",
            ),
            RelationSupport(
                chunk_id="chunk-aug-3",
                source_revision_id="rev-3",
                document_revision_id="rev-3",
                content_digest="sha256:" + "e" * 64,
                anchor={"page": 1},
                snippet="granted by policy but not selected for this query",
            ),
        )
        relation_evidence = RelationEvidence(
            label="R1",
            edge_id="e-1",
            subject_node_id="A",
            subject_label="核保流程",
            subject_aliases=(),
            object_node_id="B",
            object_label="健康告知",
            object_aliases=(),
            relation_type="REQUIRES",
            relation_label="REQUIRES",
            origin="EXTRACTED",
            confidence=0.9,
            support=(
                RelationSupport(
                    chunk_id="chunk-s1",
                    source_revision_id="rev-1",
                    document_revision_id="rev-1",
                    content_digest="sha256:" + "f" * 64,
                    anchor={"page": 1},
                    evidence_label="S1",
                ),
            ),
            on_path=False,
        )
        context = RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            seeds=(SeedNode(node_id="A", label="核保流程", origin="query"),),
            relations=(relation_evidence,),
            augment_chunks=augment_candidates,
        )
        relation = _FakeRelationAnalysis(context)
        # rev-3 has a policy grant (ACL-authorized in general) but is not part of
        # *this* request's resource_refs, so it is "granted but unselected" --
        # distinct from rev-2, which has no grant at all.
        policy = policy_context(
            allowed_source_families=frozenset({"doc"}),
            resource_grants=(_resource_grant("rev-1"), _resource_grant("rev-3")),
        )
        knowledge = _relation_retrieval(
            search, model, relation_analysis=relation, publication_authority=authority
        )
        response = await knowledge.answer(_preferred_revision_request(), policy)

        assert not response.abstained
        final_evidence = model.calls[-1]["evidence"]
        assert len(final_evidence) == 2
        assert {item.chunk_id for item in final_evidence} == {"chunk-s1", "chunk-aug-1"}
        merged_chunk = next(item for item in final_evidence if item.chunk_id == "chunk-aug-1")
        assert merged_chunk.evidence_label == "S2"
        assert len(final_evidence) <= 20

    asyncio.run(scenario())


def test_augmentation_merges_at_most_five_candidates_even_with_more_available() -> None:
    """`_augment_from_relations` searches wider for recall (I3) but must still
    only ever merge up to 5 augmented chunks into the response, even when
    more than 5 authorized, selected candidates come back."""

    async def scenario() -> None:
        candidate_count = 6
        search = _SequencedSearchPort(
            (_relation_hit("chunk-s1", "rev-1", item_id="item-1"),),
            tuple(
                _relation_hit(
                    f"chunk-aug-{index}", "rev-1", item_id=f"item-aug{index}", local_rank=index
                )
                for index in range(1, candidate_count + 1)
            ),
        )
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="已验证的事实。",
                    claims=(GeneratedClaim(text="已验证的事实。", evidence_labels=("S1",)),),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        augment_candidates = tuple(
            RelationSupport(
                chunk_id=f"chunk-aug-{index}",
                source_revision_id="rev-1",
                document_revision_id="rev-1",
                content_digest="sha256:" + "e" * 64,
                anchor={"page": 1},
                snippet=f"candidate snippet {index}",
            )
            for index in range(1, candidate_count + 1)
        )
        relation_evidence = RelationEvidence(
            label="R1",
            edge_id="e-1",
            subject_node_id="A",
            subject_label="核保流程",
            subject_aliases=(),
            object_node_id="B",
            object_label="健康告知",
            object_aliases=(),
            relation_type="REQUIRES",
            relation_label="REQUIRES",
            origin="EXTRACTED",
            confidence=0.9,
            support=(
                RelationSupport(
                    chunk_id="chunk-s1",
                    source_revision_id="rev-1",
                    document_revision_id="rev-1",
                    content_digest="sha256:" + "f" * 64,
                    anchor={"page": 1},
                    evidence_label="S1",
                ),
            ),
            on_path=False,
        )
        context = RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            relations=(relation_evidence,),
            augment_chunks=augment_candidates,
        )
        relation = _FakeRelationAnalysis(context)
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        response = await knowledge.answer(_preferred_revision_request(), _single_revision_policy())

        assert not response.abstained
        final_evidence = model.calls[-1]["evidence"]
        # chunk-s1 plus at most 5 of the 6 augmented candidates.
        assert len(final_evidence) == 6
        augmented_merged = {item.chunk_id for item in final_evidence} - {"chunk-s1"}
        assert len(augmented_merged) == 5
        assert augmented_merged <= {f"chunk-aug-{index}" for index in range(1, candidate_count + 1)}

    asyncio.run(scenario())


def test_edge_citation_reuses_s_chunk_fields_and_gets_r_label() -> None:
    async def scenario() -> None:
        search = _SequencedSearchPort(
            (
                _relation_hit("chunk-s1", "rev-1", item_id="item-1"),
                _relation_hit("chunk-s2", "rev-1", item_id="item-2", local_rank=2),
            ),
        )
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="核保流程需要健康告知。",
                    claims=(
                        GeneratedClaim(text="核保流程需要健康告知。", evidence_labels=("R1",)),
                    ),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        relation_evidence = RelationEvidence(
            label="R1",
            edge_id="e-1",
            subject_node_id="A",
            subject_label="核保流程",
            subject_aliases=(),
            object_node_id="B",
            object_label="健康告知",
            object_aliases=(),
            relation_type="REQUIRES",
            relation_label="REQUIRES",
            origin="EXTRACTED",
            confidence=0.9,
            support=(
                RelationSupport(
                    chunk_id="chunk-s2",
                    source_revision_id="rev-1",
                    document_revision_id="rev-1",
                    content_digest="sha256:" + "f" * 64,
                    anchor={"page": 1},
                    evidence_label="S2",
                ),
            ),
            on_path=False,
        )
        context = RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            relations=(relation_evidence,),
        )
        relation = _FakeRelationAnalysis(context)
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        response = await knowledge.answer(_single_revision_request(), _single_revision_policy())

        assert not response.abstained
        edge_citations = [citation for citation in response.citations if citation.kind == "edge"]
        assert len(edge_citations) == 1
        edge_citation = edge_citations[0]
        s2_citation = next(
            citation for citation in response.citations if citation.evidence_label == "S2"
        )
        assert edge_citation.chunk_id == s2_citation.chunk_id
        assert edge_citation.evidence_label == "R1"
        assert edge_citation.edge is not None
        assert edge_citation.edge.relation_type == "REQUIRES"

    asyncio.run(scenario())


def _deep_revision_request() -> AnswerRequest:
    return AnswerRequest(
        query="核保流程与健康告知的关系是什么？",
        answer_mode=AnswerMode.DEEP,
        resource_refs=(
            ResourceRef(
                family=SourceFamily.DOC,
                source_id=_SRC_ID,
                mode=ResourceMode.SCOPE,
                requested_revision="rev-1",
            ),
        ),
    )


def _relation_evidence_for(
    label: str, *, support_label: str, support_chunk_id: str = "chunk-1"
) -> RelationEvidence:
    return RelationEvidence(
        label=label,
        edge_id=f"e-{label}",
        subject_node_id="A",
        subject_label="核保流程",
        subject_aliases=(),
        object_node_id="B",
        object_label="健康告知",
        object_aliases=(),
        relation_type="REQUIRES",
        relation_label="REQUIRES",
        origin="EXTRACTED",
        confidence=0.9,
        support=(
            RelationSupport(
                chunk_id=support_chunk_id,
                source_revision_id="rev-1",
                document_revision_id="rev-1",
                content_digest="sha256:" + "f" * 64,
                anchor={"page": 1},
                evidence_label=support_label,
            ),
        ),
        on_path=False,
    )


def test_citations_are_capped_at_twenty_dropping_uncited_s_first() -> None:
    """C1: 20 S citations (none cited by any claim) plus 1 cited R overflow the
    closed 20-citation bound; the lowest-ranked *uncited* S (S20) is dropped
    first, with no claim/degradation impact."""

    async def scenario() -> None:
        hits = tuple(
            _relation_hit(f"chunk-{index}", "rev-1", item_id=f"item-{index}")
            for index in range(1, 21)
        )
        search = _SequencedSearchPort(hits)
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="核保流程需要健康告知。",
                    claims=(
                        GeneratedClaim(text="核保流程需要健康告知。", evidence_labels=("R1",)),
                    ),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        context = RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            relations=(_relation_evidence_for("R1", support_label="S1"),),
        )
        relation = _FakeRelationAnalysis(context)
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        response = await knowledge.answer(_deep_revision_request(), _single_revision_policy())

        assert not response.abstained
        assert len(response.citations) == 20
        labels = {citation.evidence_label for citation in response.citations}
        assert "S20" not in labels
        assert {f"S{n}" for n in range(1, 20)} <= labels
        assert "R1" in labels
        assert response.degraded_mode is False
        assert "invalid-relation-citation" not in response.degradation_reasons

    asyncio.run(scenario())


def test_citations_cap_renumbers_surviving_s_citations_contiguously() -> None:
    """C1 (reproduced): dropping a *middle* uncited S citation (S19, while S20
    is cited) must not leave a gap -- `ports/answers.py` requires chunk
    citations to be exactly S1..Sn. The survivors are renumbered contiguously;
    the claim's own citation (originally S20) still resolves to its original
    chunk by `citation_id`, independent of the display label."""

    async def scenario() -> None:
        hits = tuple(_real_formula_hit(index) for index in range(1, 21))
        search = _SequencedSearchPort(hits)
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="已验证的事实。\n\n核保流程需要健康告知。",
                    claims=(
                        GeneratedClaim(text="已验证的事实。", evidence_labels=("S20",)),
                        GeneratedClaim(text="核保流程需要健康告知。", evidence_labels=("R1",)),
                    ),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        context = RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            relations=(
                _relation_evidence_for("R1", support_label="S1", support_chunk_id=hits[0].chunk_id),
            ),
        )
        relation = _FakeRelationAnalysis(context)
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        response = await knowledge.answer(_deep_revision_request(), _single_revision_policy())

        assert not response.abstained
        chunk_citations = [c for c in response.citations if c.kind == "chunk"]
        edge_citations = [c for c in response.citations if c.kind == "edge"]
        assert len(response.citations) == 20
        assert len(chunk_citations) == 19
        assert len(edge_citations) == 1
        # Contiguous S1..S19, no gap left by dropping the old S19.
        assert {c.evidence_label for c in chunk_citations} == {f"S{n}" for n in range(1, 20)}
        # The chunk originally labelled S20 (chunk 20's hit) survived, renumbered.
        renumbered = next(c for c in chunk_citations if c.chunk_id == hits[19].chunk_id)
        assert renumbered.evidence_label == "S19"
        # The claim that cited the original "S20" still resolves to chunk-20's
        # citation by `citation_id`, regardless of the new display label.
        s_claim = next(claim for claim in response.claims if claim.text == "已验证的事实。")
        assert s_claim.citation_ids == (renumbered.citation_id,)

    asyncio.run(scenario())


def test_answer_snapshot_accepts_contiguous_s_labels_after_a_cap_drop() -> None:
    """C1 (reproduced): `AnswerSnapshot.from_response` requires chunk citations
    to be exactly S1..Sn (`ports/answers.py`). Before this fix, dropping an
    uncited S citation from the middle (e.g. an augmented chunk at the tail)
    left a gap (...S18, S20, R1) and the whole answer was lost as
    `AnswerSnapshotUnavailable`; this directly exercises the retrieve.py
    renumbering fix's output shape against that same strict check, using the
    only profile the local snapshot gateway accepts (QUICK_HYBRID_V1)."""
    citations = tuple(
        citation(citation_id=f"citation-s{n}", evidence_label=f"S{n}") for n in range(1, 20)
    ) + (
        replace(
            citation(citation_id="citation-r1", evidence_label="R1"),
            kind="edge",
            edge=EdgeCitation(
                edge_id="e-1",
                graph_version="7",
                subject_node_id="A",
                subject_label="核保流程",
                object_node_id="B",
                object_label="健康告知",
                relation_type="REQUIRES",
                relation_label="REQUIRES",
            ),
        ),
    )
    answer_text = "核保流程需要健康告知。"
    response = AnswerResponse(
        trace_id="trace-a",
        query_plan_id="plan-a",
        context_snapshot_id="context-a",
        corpus_version="tapper-demo-v1",
        retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
        answer=answer_text,
        abstained=False,
        claims=(
            Claim(
                claim_id="claim-1",
                text=answer_text,
                answer_start=0,
                answer_end=len(answer_text),
                citation_ids=("citation-r1",),
            ),
        ),
        citations=citations,
        embedding_provenance=ModelCallProvenance("text-embedding-v4", "embed-a"),
        answer_provenance=ModelCallProvenance("qwen-plus", "answer-a"),
    )

    snapshot = AnswerSnapshot.from_response(
        response=response, query="q", selected_revisions=(ready(),)
    )
    assert len(snapshot.citations) == 20
    assert snapshot.citations[-1].citation_kind == "edge"


def test_citations_cap_drops_lowest_ranked_r_when_no_s_is_droppable() -> None:
    """C1: 15 S citations (all cited) plus 6 cited R overflow by one; every S is
    used so none can be dropped, so the lowest-ranked cited R (R6) is dropped
    instead, stripped from its claim and counted as `invalid-relation-citation`
    / degraded. Quick profile: 10 base + 5 augmented S."""

    async def scenario() -> None:
        base_hits = tuple(
            _relation_hit(f"chunk-base-{index}", "rev-1", item_id=f"item-base-{index}")
            for index in range(1, 11)
        )
        augment_hits = tuple(
            _relation_hit(f"chunk-aug-{index}", "rev-1", item_id=f"item-aug-{index}")
            for index in range(1, 6)
        )
        search = _SequencedSearchPort(base_hits, augment_hits)
        all_s_claim_text = "全部切片均已验证。"
        r_claim_texts = [f"核保流程需要健康告知（关系{n}）。" for n in range(1, 7)]
        generation_text = "\n\n".join([all_s_claim_text, *r_claim_texts])
        claims = (
            GeneratedClaim(
                text=all_s_claim_text,
                evidence_labels=tuple(f"S{n}" for n in range(1, 16)),
            ),
            *(
                GeneratedClaim(text=r_claim_texts[n - 1], evidence_labels=(f"R{n}",))
                for n in range(1, 7)
            ),
        )
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text=generation_text,
                    claims=claims,
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        augment_candidates = tuple(
            RelationSupport(
                chunk_id=f"chunk-aug-{index}",
                source_revision_id="rev-1",
                document_revision_id="rev-1",
                content_digest="sha256:" + "e" * 64,
                anchor={"page": 1},
                snippet=f"augment candidate {index}",
            )
            for index in range(1, 6)
        )
        relations = tuple(
            _relation_evidence_for(
                f"R{n}", support_label=f"S{n}", support_chunk_id=f"chunk-base-{n}"
            )
            for n in range(1, 7)
        )
        context = RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            relations=relations,
            augment_chunks=augment_candidates,
        )
        relation = _FakeRelationAnalysis(context)
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        response = await knowledge.answer(_single_revision_request(), _single_revision_policy())

        assert not response.abstained
        assert len(response.citations) == 20
        s_labels = {c.evidence_label for c in response.citations if c.kind == "chunk"}
        r_labels = {c.evidence_label for c in response.citations if c.kind == "edge"}
        assert len(s_labels) == 15
        assert len(r_labels) == 5
        assert response.degraded_mode is True
        assert "invalid-relation-citation" in response.degradation_reasons
        # R6 (the worst-ranked cited relation) was dropped; its claim is gone too.
        assert not any("关系6" in claim.text for claim in response.claims)

    asyncio.run(scenario())


_SNIPPET_ANCHOR: dict[str, object] = {
    "type": "document",
    "startOffset": 0,
    "endOffset": 10,
    "headingPath": ["H"],
}
_SNIPPET_CONTENT_DIGEST = "sha256:" + "f" * 64


def _snippet_anchor_json(anchor: dict[str, object]) -> str:
    value = dict(anchor)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _expected_snippet_chunk_id(
    *,
    revision: str = "rev-1",
    anchor: dict[str, object] | None = None,
    content_digest: str | None = None,
) -> str:
    return str(
        chunk_id_for(
            RevisionId(revision),
            _snippet_anchor_json(anchor if anchor is not None else _SNIPPET_ANCHOR),
            content_digest if content_digest is not None else _SNIPPET_CONTENT_DIGEST,
        )
    )


def _expected_snippet_logical_chunk_id(
    *, document_id: str, anchor: dict[str, object] | None = None
) -> str:
    return logical_chunk_projection_id(
        logical_chunk_id_for(
            DocumentId(document_id),
            _snippet_anchor_json(anchor if anchor is not None else _SNIPPET_ANCHOR),
        )
    )


def _snippet_only_relation_evidence(
    *,
    document_id: str | None,
    anchor: dict[str, object] | None = None,
    chunk_id: str | None = None,
    label: str = "R1",
) -> RelationEvidence:
    resolved_anchor = anchor if anchor is not None else _SNIPPET_ANCHOR
    return RelationEvidence(
        label=label,
        edge_id="e-1",
        subject_node_id="A",
        subject_label="核保流程",
        subject_aliases=(),
        object_node_id="B",
        object_label="健康告知",
        object_aliases=(),
        relation_type="REQUIRES",
        relation_label="REQUIRES",
        origin="EXTRACTED",
        confidence=0.9,
        support=(
            RelationSupport(
                chunk_id=(
                    chunk_id
                    if chunk_id is not None
                    else _expected_snippet_chunk_id(anchor=resolved_anchor)
                ),
                source_revision_id="rev-1",
                document_revision_id="rev-1",
                content_digest=_SNIPPET_CONTENT_DIGEST,
                anchor=resolved_anchor,
                snippet="核保流程与健康告知相关的片段。",
                document_id=document_id,
            ),
        ),
        on_path=False,
    )


def test_snippet_only_support_is_resolved_into_a_citable_edge_citation() -> None:
    """I2: a relation support that never became an `S` label (snippet-only) is
    still reconstructed into a citable edge citation from its own fields plus
    the matching selected revision."""

    async def scenario() -> None:
        search = _SequencedSearchPort((_relation_hit("chunk-s1", "rev-1", item_id="item-1"),))
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="核保流程需要健康告知。",
                    claims=(
                        GeneratedClaim(text="核保流程需要健康告知。", evidence_labels=("R1",)),
                    ),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        context = RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            relations=(_snippet_only_relation_evidence(document_id="doc-xyz"),),
        )
        relation = _FakeRelationAnalysis(context)
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        response = await knowledge.answer(_single_revision_request(), _single_revision_policy())

        assert not response.abstained
        edge_citations = [c for c in response.citations if c.kind == "edge"]
        assert len(edge_citations) == 1
        assert edge_citations[0].evidence_label == "R1"
        assert edge_citations[0].chunk_id not in {
            c.chunk_id for c in response.citations if c.kind == "chunk"
        }
        assert edge_citations[0].chunk_id == _expected_snippet_chunk_id()
        assert edge_citations[0].logical_chunk_id == _expected_snippet_logical_chunk_id(
            document_id="doc-xyz"
        )
        assert response.degraded_mode is False
        assert response.relation is not None
        assert response.relation.relation_count == 1

    asyncio.run(scenario())


def test_snippet_only_support_with_anchor_drift_is_unresolvable_and_degraded() -> None:
    """I2 follow-up: the model echoes `anchorJson` and extraction never
    validates it, so the support's own `chunk_id` can drift from what the
    anchor+digest actually recompute. Require the recomputed `chunk_id` to
    match `support.chunk_id`, or treat the support as unresolvable rather than
    minting a citation for a chunk that was never indexed."""

    async def scenario() -> None:
        search = _SequencedSearchPort((_relation_hit("chunk-s1", "rev-1", item_id="item-1"),))
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="已验证的事实。\n\n核保流程需要健康告知。",
                    claims=(
                        GeneratedClaim(text="已验证的事实。", evidence_labels=("S1",)),
                        GeneratedClaim(text="核保流程需要健康告知。", evidence_labels=("R1",)),
                    ),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        context = RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            relations=(
                _snippet_only_relation_evidence(
                    document_id="doc-xyz", chunk_id="chunk-drifted-does-not-match-formula"
                ),
            ),
        )
        relation = _FakeRelationAnalysis(context)
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        response = await knowledge.answer(_single_revision_request(), _single_revision_policy())

        assert not response.abstained
        assert response.answer == "已验证的事实。"
        assert all(c.kind == "chunk" for c in response.citations)
        assert response.degraded_mode is True
        assert response.relation is not None
        assert response.relation.relation_count == 0
        assert response.relation.diagnostics.get("relation-support-unresolvable") == 1

    asyncio.run(scenario())


def test_unresolvable_snippet_only_support_degrades_and_excludes_relation_count() -> None:
    """I2: when a snippet-only support's document_id cannot be resolved (never
    carried alongside the snippet), the R citation cannot be built; the claim's
    R label is stripped, `degraded_mode` is set, and the relation does not
    count toward `graphContext.relationCount`."""

    async def scenario() -> None:
        search = _SequencedSearchPort((_relation_hit("chunk-s1", "rev-1", item_id="item-1"),))
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="已验证的事实。\n\n核保流程需要健康告知。",
                    claims=(
                        GeneratedClaim(text="已验证的事实。", evidence_labels=("S1",)),
                        GeneratedClaim(text="核保流程需要健康告知。", evidence_labels=("R1",)),
                    ),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        context = RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            relations=(_snippet_only_relation_evidence(document_id=None),),
        )
        relation = _FakeRelationAnalysis(context)
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        response = await knowledge.answer(_single_revision_request(), _single_revision_policy())

        assert not response.abstained
        assert response.answer == "已验证的事实。"
        assert all(c.kind == "chunk" for c in response.citations)
        assert response.degraded_mode is True
        assert response.relation is not None
        assert response.relation.relation_count == 0
        assert response.relation.diagnostics.get("relation-support-unresolvable") == 1

    asyncio.run(scenario())


def test_uncited_unresolvable_relation_does_not_degrade() -> None:
    """Task 5 review: an unresolvable relation that no claim cited never
    reached the reader, so it must not flip `degraded_mode` or add
    `relation-support-unresolvable` to `degradation_reasons` -- only the
    diagnostics total (and `relation_count`, which already excludes every
    unresolvable relation) still reflect it."""

    async def scenario() -> None:
        search = _SequencedSearchPort((_relation_hit("chunk-s1", "rev-1", item_id="item-1"),))
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="核保流程需要健康告知。",
                    claims=(
                        GeneratedClaim(text="核保流程需要健康告知。", evidence_labels=("R1",)),
                    ),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        context = RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            relations=(
                _relation_evidence_for("R1", support_label="S1", support_chunk_id="chunk-s1"),
                _snippet_only_relation_evidence(document_id=None, label="R2"),
            ),
        )
        relation = _FakeRelationAnalysis(context)
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        response = await knowledge.answer(_single_revision_request(), _single_revision_policy())

        assert not response.abstained
        assert response.degraded_mode is False
        assert "relation-support-unresolvable" not in response.degradation_reasons
        assert response.relation is not None
        assert response.relation.relation_count == 1
        assert response.relation.diagnostics.get("relation-support-unresolvable") == 1

    asyncio.run(scenario())


def test_s_label_support_chunk_id_mismatch_is_treated_as_unresolvable() -> None:
    """Task 5 review: a relation support that carries an `S` label must still
    have its own `chunk_id` verified against that label's actual chunk --
    same identity guard as the snippet-only path. A mismatch (stale or wrong
    label) is skipped in favour of the next support instead of silently
    minting a citation for the wrong chunk."""

    async def scenario() -> None:
        search = _SequencedSearchPort(
            (
                _relation_hit("chunk-s1", "rev-1", item_id="item-1"),
                _relation_hit("chunk-s2", "rev-1", item_id="item-2", local_rank=2),
            ),
        )
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="核保流程需要健康告知。",
                    claims=(
                        GeneratedClaim(text="核保流程需要健康告知。", evidence_labels=("R1",)),
                    ),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        relation_evidence = RelationEvidence(
            label="R1",
            edge_id="e-1",
            subject_node_id="A",
            subject_label="核保流程",
            subject_aliases=(),
            object_node_id="B",
            object_label="健康告知",
            object_aliases=(),
            relation_type="REQUIRES",
            relation_label="REQUIRES",
            origin="EXTRACTED",
            confidence=0.9,
            support=(
                RelationSupport(
                    chunk_id="chunk-stale-does-not-match-s1",
                    source_revision_id="rev-1",
                    document_revision_id="rev-1",
                    content_digest="sha256:" + "f" * 64,
                    anchor={"page": 1},
                    evidence_label="S1",
                ),
                RelationSupport(
                    chunk_id="chunk-s2",
                    source_revision_id="rev-1",
                    document_revision_id="rev-1",
                    content_digest="sha256:" + "f" * 64,
                    anchor={"page": 1},
                    evidence_label="S2",
                ),
            ),
            on_path=False,
        )
        context = RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            relations=(relation_evidence,),
        )
        relation = _FakeRelationAnalysis(context)
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        response = await knowledge.answer(_single_revision_request(), _single_revision_policy())

        assert not response.abstained
        edge_citations = [c for c in response.citations if c.kind == "edge"]
        assert len(edge_citations) == 1
        s2_citation = next(c for c in response.citations if c.evidence_label == "S2")
        assert edge_citations[0].chunk_id == s2_citation.chunk_id

    asyncio.run(scenario())


def test_citing_only_r2_renumbers_to_r1_with_the_right_edge() -> None:
    """M4: the model may cite any original R label; the surviving edge citation
    is always renumbered starting at R1 (ascending original-rank order), never
    keeping a gap from an unused earlier label."""

    async def scenario() -> None:
        search = _SequencedSearchPort(
            (
                _relation_hit("chunk-s1", "rev-1", item_id="item-1"),
                _relation_hit("chunk-s2", "rev-1", item_id="item-2", local_rank=2),
            ),
        )
        model = _RecordingModel(
            [
                AnswerGeneration(
                    text="核保流程需要健康告知。",
                    claims=(
                        GeneratedClaim(text="核保流程需要健康告知。", evidence_labels=("R2",)),
                    ),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        context = RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            relations=(
                _relation_evidence_for("R1", support_label="S1"),
                _relation_evidence_for("R2", support_label="S2", support_chunk_id="chunk-s2"),
            ),
        )
        relation = _FakeRelationAnalysis(context)
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        response = await knowledge.answer(_single_revision_request(), _single_revision_policy())

        assert not response.abstained
        edge_citations = [c for c in response.citations if c.kind == "edge"]
        assert len(edge_citations) == 1
        assert edge_citations[0].evidence_label == "R1"
        s2_citation = next(c for c in response.citations if c.evidence_label == "S2")
        assert edge_citations[0].chunk_id == s2_citation.chunk_id
        assert edge_citations[0].edge is not None
        assert edge_citations[0].edge.relation_type == "REQUIRES"

    asyncio.run(scenario())


def test_stale_context_omits_relation_prompt() -> None:
    """M5: a STALE (non-applied) relation context never triggers the relation
    prompt text unless `relation_first` is true."""

    async def scenario() -> None:
        search = _SequencedSearchPort((_relation_hit("chunk-s1", "rev-1", item_id="item-1"),))
        received_relation_first: list[bool] = []

        class _PromptRecordingModel(_RecordingModel):
            async def answer(self, query, evidence, profile_id, **kwargs):
                received_relation_first.append(bool(kwargs.get("relation_first")))
                return await super().answer(query, evidence, profile_id, **kwargs)

        model = _PromptRecordingModel(
            [
                AnswerGeneration(
                    text="已验证的事实。",
                    claims=(GeneratedClaim(text="已验证的事实。", evidence_labels=("S1",)),),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        relation = _FakeRelationAnalysis(
            RelationContext(status=RelationContextStatus.STALE, graph_version="8")
        )
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        await knowledge.answer(_single_revision_request(), _single_revision_policy())

        assert received_relation_first == [False]

    asyncio.run(scenario())


def test_intent_relation_execution_sets_relation_first_through_planned_retrieval() -> None:
    """M5: `AuthorizedAnswerExecution(intent="relation")`, constructed directly as
    the brief specifies, is exactly what makes `AuthorizedRetrieval.answer`'s
    `relation_first` true -- exercised through the real planned-retrieval path,
    not a standalone boolean check."""

    async def scenario() -> None:
        query = "核保流程与健康告知的关系是什么？"
        search = _SequencedSearchPort((_relation_hit("chunk-s1", "rev-1", item_id="item-1"),))
        received_relation_first: list[bool] = []

        class _PromptRecordingModel(_RecordingModel):
            async def answer(self, query, evidence, profile_id, **kwargs):
                received_relation_first.append(bool(kwargs.get("relation_first")))
                return await super().answer(query, evidence, profile_id, **kwargs)

        model = _PromptRecordingModel(
            [
                AnswerGeneration(
                    text="已验证的事实。",
                    claims=(GeneratedClaim(text="已验证的事实。", evidence_labels=("S1",)),),
                    model_id="m-1",
                    profile_id="grounded-answer-v1",
                    provider_request_id="req-1",
                )
            ]
        )
        relation = _FakeRelationAnalysis(
            RelationContext(status=RelationContextStatus.EMPTY, graph_version="7")
        )
        knowledge = _relation_retrieval(search, model, relation_analysis=relation)
        template = get_template("general", "1")
        answer_execution = AuthorizedAnswerExecution(
            plan_id="plan-a",
            project_id="project-a",
            acl_digest="sha256:acl-17",
            model_alias="model-a",
            original_question=query,
            standalone_question=query,
            queries=(
                AuthorizedAnswerQuery(
                    id="q1",
                    text=query,
                    depends_on=(),
                    source_ids=(_SRC_ID,),
                    evidence_goal="relation",
                ),
            ),
            template_id="general",
            template_version="1",
            template_digest=template.digest,
            candidate_limit=20,
            evidence_limit=10,
            remaining_seconds=30.0,
            intent="relation",
        )
        assert answer_execution.intent == "relation"

        request = replace(_single_revision_request(), query=query)
        await knowledge.answer(
            request, _single_revision_policy(), answer_execution=answer_execution
        )

        assert received_relation_first == [True]

    asyncio.run(scenario())
