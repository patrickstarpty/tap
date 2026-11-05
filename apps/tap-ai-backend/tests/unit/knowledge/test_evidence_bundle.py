from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tap.modules.knowledge.application.answer_templates import (
    AssembledAnswer,
    assemble_answer,
    get_template,
)
from tap.modules.knowledge.application.evidence_bundle import (
    EVIDENCE_BUNDLE_MAX_BYTES,
    EvidenceBundle,
    EvidenceBundleTooLarge,
    decode_bundle,
    encode_bundle,
)
from tap.modules.knowledge.application.publication import PublicationBinding
from tap.modules.knowledge.application.relation_analysis import (
    RelationContext,
    RelationContextStatus,
    RelationEvidence,
    RelationSupport,
)
from tap.modules.knowledge.domain.documents import (
    DocumentId,
    RevisionId,
    chunk_id_for,
    logical_chunk_id_for,
)
from tap.modules.knowledge.domain.models import (
    AnswerMode,
    AnswerRequest,
    ContentRole,
    ContextLayer,
    ContextLayerKind,
    ContextSnapshot,
    DocumentAnchor,
    Evidence,
    IndexRevision,
    ModelCallProvenance,
    QueryPlan,
    RetrievalProfileId,
    RevisionKind,
    SearchResponse,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.domain.review import KnowledgePublication

SOURCE_HASH = "sha256:" + "a" * 64
CHUNK_HASH = "sha256:" + "c" * 64


def _query_plan() -> QueryPlan:
    return QueryPlan(
        query_plan_id="plan-v1",
        operation_id="operation-v1",
        tenant_id="tenant-a",
        project_id="project-a",
        policy_decision_id="decision-v1",
        policy_version="policy-v1",
        acl_digest=SOURCE_HASH,
        answer_mode=AnswerMode.QUICK,
        retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
        source_families=(SourceFamily.DOC,),
        resources=(),
        effective_environment="production",
        corpus_version="corpus-fixture-v1",
        candidate_limit=50,
        raw_request_hash="sha256:" + "b" * 64,
        sanitized_query="What is the rule?",
        sanitized_query_hash="sha256:" + "d" * 64,
        redaction_version="redaction-v1",
        embedding_model_id="research-embedding-v1",
        embedding_dimension=1536,
    )


def _context_snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        context_snapshot_id="snapshot-v1",
        operation_id="operation-v1",
        tenant_id="tenant-a",
        project_id="project-a",
        policy_decision_id="decision-v1",
        policy_version="policy-v1",
        acl_digest=SOURCE_HASH,
        layers=(
            ContextLayer(
                kind=ContextLayerKind.CURRENT_TURN,
                ref_ids=(),
                content_hash="sha256:" + "d" * 64,
                token_count=7,
            ),
        ),
    )


def _evidence(*, document_id: str = "doc_a", revision_id: str = "rev_a") -> Evidence:
    anchor = DocumentAnchor(heading_path=("Policy",), start_offset=0, end_offset=10)
    anchor_json = json.dumps(
        {"endOffset": 10, "headingPath": ["Policy"], "startOffset": 0, "type": "document"},
        separators=(",", ":"),
        sort_keys=True,
    )
    chunk_id = str(chunk_id_for(RevisionId(revision_id), anchor_json, CHUNK_HASH))
    logical_chunk_id = str(logical_chunk_id_for(DocumentId(document_id), anchor_json))
    return Evidence(
        family=SourceFamily.DOC,
        chunk_id=chunk_id,
        logical_chunk_id=logical_chunk_id,
        title="Policy",
        content="The rule is grounded.",
        source=SourceRevisionRef(
            source_id=f"doc:{document_id}",
            source_type="doc",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision=revision_id,
            source_content_hash=SOURCE_HASH,
            anchor=anchor,
        ),
        chunk_content_hash=CHUNK_HASH,
        content_role=ContentRole.SOURCE,
        citation_id="citation-a",
        evidence_label="S1",
        index_revision=IndexRevision(
            physical_index="doc-index-v1",
            schema_version="schema-v1",
            corpus_version="corpus-fixture-v1",
        ),
        embedding_model_version="text-embedding-v4",
        acl_decision_id="decision-v1",
        score=0.9,
    )


def _search_response(evidence: tuple[Evidence, ...]) -> SearchResponse:
    return SearchResponse(
        trace_id="trace-a",
        query_plan_id="plan-v1",
        context_snapshot_id="snapshot-v1",
        corpus_version="corpus-fixture-v1",
        retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
        evidence=evidence,
        embedding_provenance=ModelCallProvenance("text-embedding-v4", "embed-request"),
    )


def _publication_binding() -> PublicationBinding:
    published_at = datetime(2026, 1, 1, tzinfo=UTC)
    expires_at = datetime(2026, 12, 31, tzinfo=UTC)
    return PublicationBinding(
        publication_id="publication-a",
        project_id="project-a",
        approval_digest=SOURCE_HASH,
        source_revision_ids=("rev_a",),
        approved_item_ids=("item-a",),
        generation="generation-a",
        expires_at=expires_at,
        publications=(
            KnowledgePublication(
                publication_id="publication-a",
                project_id="project-a",
                review_id="review-a",
                review_version=1,
                approval_digest=SOURCE_HASH,
                source_revision_ids=("rev_a",),
                approved_item_ids=("item-a",),
                generation="generation-a",
                published_by="reviewer-a",
                published_at=published_at,
                expires_at=expires_at,
            ),
        ),
    )


def _relation_context() -> RelationContext:
    support = RelationSupport(
        chunk_id="chunk-9",
        source_revision_id="rev_a",
        document_revision_id="doc_a",
        content_digest="digest-chunk-9",
        anchor={"page": 2},
        evidence_label="S1",
    )
    relation = RelationEvidence(
        label="R1",
        edge_id="e-bc",
        subject_node_id="B",
        subject_label="节点 B",
        subject_aliases=(),
        object_node_id="C",
        object_label="节点 C",
        object_aliases=(),
        relation_type="PRECEDES",
        relation_label="PRECEDES",
        origin="EXTRACTED",
        confidence=0.8,
        support=(support,),
        on_path=True,
    )
    return RelationContext(
        status=RelationContextStatus.APPLIED,
        graph_version="gpv_1",
        relations=(relation,),
    )


def _assembled_answer() -> AssembledAnswer:
    return assemble_answer(
        template_id="general",
        template_version="2",
        template_digest=get_template("general", "2").digest,
        original_question="What is the rule?",
        standalone_question="What is the rule?",
        evidence_map={"S1": ("S1",)},
    )


def _bundle(*, generation_query: str = "What is the rule?") -> EvidenceBundle:
    evidence = (_evidence(),)
    return EvidenceBundle(
        request=AnswerRequest(query="What is the rule?"),
        generation_query=generation_query,
        response=_search_response(evidence),
        plan=_query_plan(),
        context_snapshot=_context_snapshot(),
        publication=_publication_binding(),
        relation=_relation_context(),
        answer_input=_assembled_answer(),
        evidence_map={"S1": ("S1",)},
        graph_context=(),
        flow_context={},
        missing=False,
        relation_first=False,
    )


def test_bundle_round_trip_preserves_retrieval_artifacts() -> None:
    bundle = _bundle()

    restored = decode_bundle(json.loads(json.dumps(encode_bundle(bundle))))

    assert restored == bundle


def test_bundle_limit_counts_utf8_bytes() -> None:
    # Each repeated character is 3 UTF-8 bytes; pick a length so the encoded
    # bundle crosses EVIDENCE_BUNDLE_MAX_BYTES in bytes while its Python
    # character count stays below that limit.
    padding = "中" * (EVIDENCE_BUNDLE_MAX_BYTES // 2)
    too_large = _bundle(generation_query=padding)
    assert len(too_large.generation_query) < EVIDENCE_BUNDLE_MAX_BYTES
    assert len(too_large.generation_query.encode("utf-8")) > EVIDENCE_BUNDLE_MAX_BYTES

    with pytest.raises(EvidenceBundleTooLarge):
        encode_bundle(too_large)

    # Measure the bundle's fixed overhead (everything except the padded
    # field) so the padded field can be sized to land exactly 1 KiB under
    # the limit, regardless of how large the rest of the bundle is.
    overhead_bytes = len(
        json.dumps(encode_bundle(_bundle(generation_query="")), ensure_ascii=False).encode("utf-8")
    )
    target_bytes = EVIDENCE_BUNDLE_MAX_BYTES - 1_024
    smaller_padding = "中" * max(0, (target_bytes - overhead_bytes) // 3)
    fits = _bundle(generation_query=smaller_padding)
    encoded_fits_size = len(json.dumps(encode_bundle(fits), ensure_ascii=False).encode("utf-8"))
    assert encoded_fits_size <= target_bytes
    encode_bundle(fits)
