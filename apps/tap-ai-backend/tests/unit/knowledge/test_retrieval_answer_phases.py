"""`AuthorizedRetrieval.answer` is split into `prepare_answer` / `generate_answer`
/ `verify_answer` so later LangGraph nodes can checkpoint an `EvidenceBundle`
between them. These tests prove the split is a pure move: the phased calls
must produce exactly the same `AnswerResponse` as the single-shot `answer()`,
including after an `EvidenceBundle` round trip through its JSON codec."""

from __future__ import annotations

import dataclasses
import hashlib
import json

import pytest

from tap.modules.access.domain.policy import ResourceGrant
from tap.modules.ai.domain.persona import TapperPersona
from tap.modules.knowledge.application.evidence_bundle import (
    EvidenceBundle,
    decode_bundle,
    encode_bundle,
)
from tap.modules.knowledge.application.relation_analysis import (
    RelationContext,
    RelationContextStatus,
    RelationEvidence,
    RelationSupport,
)
from tap.modules.knowledge.application.retrieve import AuthorizedRetrieval
from tap.modules.knowledge.domain.models import (
    AbstentionReason,
    AnswerMode,
    AnswerRequest,
    AnswerResponse,
    ContentRole,
    ContextLayer,
    ContextLayerKind,
    ContextSnapshot,
    DocumentAnchor,
    Evidence,
    IndexRevision,
    ModelCallProvenance,
    QueryPlan,
    ResolvedResourceRef,
    ResourceMode,
    ResourceRef,
    RetrievalProfileId,
    RevisionKind,
    SearchResponse,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.ports.answers import DocumentStateChanged
from tap.modules.knowledge.ports.models import AnswerGeneration, SearchHit
from tests.contract.test_knowledge_api import (
    FakeModelPort,
    FakeSearchPort,
    PassthroughRedactor,
    policy_context,
    search_hit,
)

_POOL_SOURCE_HASH = "sha256:" + "c" * 64


def _pool_hit(
    index: int,
    *,
    source_id: str = "src-a",
    revision: str = "rev-1",
    score: float = 0.5,
) -> SearchHit:
    content = f"pool content {source_id} {revision} {index}"
    chunk_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    return SearchHit(
        family=SourceFamily.DOC,
        # Zero-padded so the ranking tie-break below (ascending `chunk_id`,
        # the fallback once `local_rank`/`preferred_resource_boost` tie --
        # every pool hit here shares `local_rank=1` and the one preferred
        # resource) sorts by numeric `index`, not lexical string order
        # (unpadded, "100" < "3"). Fix round 1 I-3's test relies on this to
        # make a low-index duplicate of an existing pool entry rank ahead of
        # higher-index new hits, the same way a real index ranks an
        # already-indexed/frequently-matched chunk first.
        chunk_id=f"chunk-{source_id}-{revision}-{index:04d}",
        logical_chunk_id=f"logical-{source_id}-{revision}-{index:04d}",
        title="pool.md",
        content=content,
        source=SourceRevisionRef(
            source_id=source_id,
            source_type="doc",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision=revision,
            source_content_hash=_POOL_SOURCE_HASH,
            anchor=DocumentAnchor(heading_path=()),
        ),
        chunk_content_hash=chunk_hash,
        content_role=ContentRole.SOURCE,
        index_revision=IndexRevision(
            physical_index="kb-doc-v1-20260101",
            schema_version="search-schema-v1",
            corpus_version="corpus-17",
        ),
        embedding_model_version="tap-embedding-v1",
        score=score,
    )


def _pool_evidence(
    index: int,
    *,
    label: str,
    source_id: str = "src-a",
    revision: str = "rev-1",
) -> Evidence:
    hit = _pool_hit(index, source_id=source_id, revision=revision)
    return Evidence(
        family=hit.family,
        chunk_id=hit.chunk_id,
        logical_chunk_id=hit.logical_chunk_id,
        title=hit.title,
        content=hit.content,
        source=hit.source,
        chunk_content_hash=hit.chunk_content_hash,
        content_role=hit.content_role,
        citation_id=f"citation-{source_id}-{revision}-{index}",
        evidence_label=label,
        index_revision=hit.index_revision,
        embedding_model_version=hit.embedding_model_version,
        acl_decision_id="decision-pool",
        score=hit.score,
    )


def _resolved_resource(source_id: str = "src-a", revision: str = "rev-1") -> ResolvedResourceRef:
    return ResolvedResourceRef(
        family=SourceFamily.DOC,
        source_id=source_id,
        mode=ResourceMode.PREFERRED,
        revision_kind=RevisionKind.BLOB_VERSION,
        revision=revision,
        source_content_hash=_POOL_SOURCE_HASH,
        anchor=None,
    )


def _pool_bundle(
    evidence: tuple[Evidence, ...],
    *,
    resources: tuple[ResolvedResourceRef, ...],
    resource_refs: tuple[ResourceRef, ...],
    query: str = "原始问题",
    relation: RelationContext | None = None,
) -> EvidenceBundle:
    plan = QueryPlan(
        query_plan_id="pool-plan-1",
        operation_id="pool-operation-1",
        tenant_id="tenant-a",
        project_id="project-a",
        policy_decision_id="decision-17",
        policy_version="policy-17",
        acl_digest="sha256:acl-17",
        answer_mode=AnswerMode.QUICK,
        retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
        source_families=(SourceFamily.DOC,),
        resources=resources,
        effective_environment="production",
        corpus_version="corpus-17",
        candidate_limit=20,
        raw_request_hash="sha256:" + "b" * 64,
        sanitized_query=query,
        sanitized_query_hash="sha256:" + "d" * 64,
        redaction_version="redaction-v1",
        embedding_model_id="tap-embedding-v1",
        embedding_dimension=2,
    )
    context_snapshot = ContextSnapshot(
        context_snapshot_id="pool-snapshot-1",
        operation_id="pool-operation-1",
        tenant_id="tenant-a",
        project_id="project-a",
        policy_decision_id="decision-17",
        policy_version="policy-17",
        acl_digest="sha256:acl-17",
        layers=(
            ContextLayer(
                kind=ContextLayerKind.CURRENT_TURN,
                ref_ids=(),
                content_hash="sha256:" + "d" * 64,
                token_count=4,
            ),
        ),
    )
    return EvidenceBundle(
        request=AnswerRequest(query=query, resource_refs=resource_refs),
        generation_query=query,
        response=SearchResponse(
            trace_id="pool-trace-1",
            query_plan_id="pool-plan-1",
            context_snapshot_id="pool-snapshot-1",
            corpus_version="corpus-17",
            retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
            evidence=evidence,
            embedding_provenance=ModelCallProvenance("tap-embedding-v1", "embed-pool"),
        ),
        plan=plan,
        context_snapshot=context_snapshot,
        publication=None,
        relation=relation,
        answer_input=None,
        evidence_map={},
        graph_context=(),
        flow_context={},
        missing=False,
        relation_first=False,
    )


def _id_factory():
    ids = iter(
        (
            "operation-1",
            "query-plan-1",
            "context-snapshot-1",
            "trace-1",
            "citation-1",
            "claim-1",
            *(f"generated-{index}" for index in range(100)),
        )
    )
    return lambda: next(ids)


class CurrentPolicyVerifier:
    async def verify_current(self, expected):
        return expected


class ToggledPolicyVerifier:
    """Succeeds like `CurrentPolicyVerifier` until `current` is flipped to
    `False`, simulating the selected revision/policy changing underneath an
    in-flight answer between the prepare and generate phases."""

    def __init__(self) -> None:
        self.current = True

    async def verify_current(self, expected):
        if not self.current:
            raise DocumentStateChanged("selected revision changed")
        return expected


def _retrieval(
    search: FakeSearchPort,
    model: FakeModelPort,
    *,
    policy_verifier=None,
) -> AuthorizedRetrieval:
    return AuthorizedRetrieval(
        search=search,
        embeddings=model,
        answers=model,
        policy_verifier=policy_verifier or CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=_id_factory(),
    )


@pytest.mark.asyncio
async def test_phased_answer_equals_single_shot_answer() -> None:
    request = AnswerRequest(query="Where is policy verified?")
    policy = policy_context()

    single_shot = AuthorizedRetrieval(
        search=FakeSearchPort((search_hit(),)),
        embeddings=(model_a := FakeModelPort()),
        answers=model_a,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=_id_factory(),
    )
    phased = AuthorizedRetrieval(
        search=FakeSearchPort((search_hit(),)),
        embeddings=(model_b := FakeModelPort()),
        answers=model_b,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=_id_factory(),
    )

    expected = await single_shot.answer(request, policy)

    prepared = await phased.prepare_answer(request, policy)
    assert isinstance(prepared, EvidenceBundle)
    round_tripped = decode_bundle(json.loads(json.dumps(encode_bundle(prepared))))
    generation = await phased.generate_answer(round_tripped, policy)
    actual = await phased.verify_answer(round_tripped, generation, policy)

    assert actual == expected
    assert expected.abstained is False


@pytest.mark.asyncio
async def test_prepare_returns_abstain_response_without_evidence() -> None:
    model = FakeModelPort()
    retrieval = _retrieval(FakeSearchPort(()), model)

    result = await retrieval.prepare_answer(AnswerRequest(query="Unknown fact"), policy_context())

    assert isinstance(result, AnswerResponse)
    assert result.abstained is True
    assert result.abstention_reason is AbstentionReason.INSUFFICIENT_EVIDENCE
    assert model.answer_evidence == []


@pytest.mark.asyncio
async def test_generate_rechecks_current_revision_before_model_call() -> None:
    model = FakeModelPort()
    verifier = ToggledPolicyVerifier()
    retrieval = _retrieval(FakeSearchPort((search_hit(),)), model, policy_verifier=verifier)
    request = AnswerRequest(query="Where is policy verified?")
    policy = policy_context()

    prepared = await retrieval.prepare_answer(request, policy)
    assert isinstance(prepared, EvidenceBundle)

    verifier.current = False
    with pytest.raises(DocumentStateChanged):
        await retrieval.generate_answer(prepared, policy)

    assert model.answer_evidence == []


@pytest.mark.asyncio
async def test_generate_answer_forwards_a_non_default_persona_to_the_model_gateway() -> None:
    """Task 1 fix round 1: `generate_answer`'s `persona` kwarg (threaded down
    from `KnowledgeAPI.generate_frozen` -> `AnswerService.generate_frozen` ->
    `KnowledgeHttpService.generate_conversation_answer`, the grounded
    conversation-turn path) must reach the model gateway's `.answer()` call --
    not silently default to `CURRENT_PERSONA` once a Turn names a different
    one."""

    model = FakeModelPort()
    retrieval = _retrieval(FakeSearchPort((search_hit(),)), model)
    request = AnswerRequest(query="Where is policy verified?")
    policy = policy_context()
    other_persona = TapperPersona("tapper-persona-test", "Alternate platform voice.", "Boundary.")

    prepared = await retrieval.prepare_answer(request, policy)
    assert isinstance(prepared, EvidenceBundle)

    await retrieval.generate_answer(prepared, policy, persona=other_persona)

    assert model.answer_kwargs[-1]["persona"] is other_persona


@pytest.mark.asyncio
async def test_verify_passes_clarification_without_claims() -> None:
    model = FakeModelPort()
    retrieval = _retrieval(FakeSearchPort((search_hit(),)), model)
    request = AnswerRequest(query="Where is policy verified?")
    policy = policy_context()

    prepared = await retrieval.prepare_answer(request, policy)
    assert isinstance(prepared, EvidenceBundle)

    generation = AnswerGeneration(
        text="你指的是哪个产品版本？",
        claims=(),
        model_id="tap-answer-v1",
        profile_id="grounded-answer-v1",
        provider_request_id="answer-request-1",
        answer_kind="clarification",
    )

    response = await retrieval.verify_answer(prepared, generation, policy)

    assert isinstance(response, AnswerResponse)
    assert response.abstained is False
    assert response.claims == ()
    assert response.citations == ()
    assert response.answer_kind == "clarification"
    assert response.answer == "你指的是哪个产品版本？"


@pytest.mark.asyncio
async def test_verify_forwards_degraded_mode_for_a_clarification() -> None:
    """I-1 (task reviewer, fix round 1): the clarification short-circuit must
    forward `bundle.response.degraded_mode`/`degradation_reasons` the same way
    the success (~L979) and abstain (`_abstain`) paths do, not silently default
    them to `False`/`()`."""
    model = FakeModelPort()
    retrieval = _retrieval(FakeSearchPort((search_hit(),)), model)
    request = AnswerRequest(query="Where is policy verified?")
    policy = policy_context()

    prepared = await retrieval.prepare_answer(request, policy)
    assert isinstance(prepared, EvidenceBundle)
    degraded = dataclasses.replace(
        prepared,
        response=dataclasses.replace(
            prepared.response,
            degraded_mode=True,
            degradation_reasons=("partial-evidence",),
        ),
    )

    generation = AnswerGeneration(
        text="你指的是哪个产品版本？",
        claims=(),
        model_id="tap-answer-v1",
        profile_id="grounded-answer-v1",
        provider_request_id="answer-request-1",
        answer_kind="clarification",
    )

    response = await retrieval.verify_answer(degraded, generation, policy)

    assert response.answer_kind == "clarification"
    assert response.degraded_mode is True
    assert response.degradation_reasons == ("partial-evidence",)


@pytest.mark.asyncio
async def test_verify_abstains_instead_of_completing_an_empty_clarification() -> None:
    """Ruling (task reviewer, fix round 1): a `CLARIFY:` prefix with no
    question carries nothing to show the user -- the turn must abstain, not
    complete, even if `generation.answer_kind == "clarification"` reaches
    `verify_answer` with blank text (defense in depth; the gateway itself no
    longer produces this shape)."""
    model = FakeModelPort()
    retrieval = _retrieval(FakeSearchPort((search_hit(),)), model)
    request = AnswerRequest(query="Where is policy verified?")
    policy = policy_context()

    prepared = await retrieval.prepare_answer(request, policy)
    assert isinstance(prepared, EvidenceBundle)

    generation = AnswerGeneration(
        text="",
        claims=(),
        model_id="tap-answer-v1",
        profile_id="grounded-answer-v1",
        provider_request_id="answer-request-1",
        answer_kind="clarification",
    )

    response = await retrieval.verify_answer(prepared, generation, policy)

    assert response.abstained is True
    assert response.answer_kind == "answer"
    assert response.abstention_reason is AbstentionReason.INSUFFICIENT_EVIDENCE


class FakeRelationAnalysis:
    """Stands in for the `"relation-analysis"` agent subgraph: returns a
    fixed `RelationContext`, counting how many times it was invoked."""

    def __init__(self, context: RelationContext | None = None) -> None:
        self.context = context
        self.calls = 0

    async def analyse(self, query, evidence, source_revision_ids, *, turn_id=None):
        self.calls += 1
        return self.context


def _relation_support(label: str) -> RelationSupport:
    return RelationSupport(
        chunk_id=f"chunk-{label}",
        source_revision_id="rev-1",
        document_revision_id="rev-1",
        content_digest="sha256:" + "e" * 64,
        anchor={"page": 1},
        evidence_label=label,
    )


def _relation(label: str, edge_id: str, *, support_label: str = "S1") -> RelationEvidence:
    return RelationEvidence(
        label=label,
        edge_id=edge_id,
        subject_node_id=f"{edge_id}-a",
        subject_label="节点A",
        subject_aliases=(),
        object_node_id=f"{edge_id}-b",
        object_label="节点B",
        object_aliases=(),
        relation_type="REQUIRES",
        relation_label="REQUIRES",
        origin="EXTRACTED",
        confidence=0.9,
        support=(_relation_support(support_label),),
        on_path=False,
    )


@pytest.mark.asyncio
async def test_prepare_without_relations_skips_relation_analysis() -> None:
    """Task 6: `prepare_answer(..., include_relations=False)` -- the graph
    path's `seed_search` node calling convention -- must never invoke the
    configured relation analysis port, and the resulting bundle must carry
    no `RelationContext`."""
    model = FakeModelPort()
    relation = FakeRelationAnalysis(
        RelationContext(status=RelationContextStatus.EMPTY, graph_version="1")
    )
    retrieval = AuthorizedRetrieval(
        search=FakeSearchPort((search_hit(),)),
        embeddings=model,
        answers=model,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=_id_factory(),
        relation_analysis=relation,
    )
    request = AnswerRequest(query="Where is policy verified?")
    policy = policy_context()

    prepared = await retrieval.prepare_answer(request, policy, include_relations=False)

    assert isinstance(prepared, EvidenceBundle)
    assert prepared.relation is None
    assert relation.calls == 0


def _single_source_policy() -> object:
    return policy_context(
        resource_grants=(
            ResourceGrant(
                family="doc",
                source_id="src-a",
                revision_kind="blob_version",
                revision="rev-1",
                source_content_hash=_POOL_SOURCE_HASH,
                allow_all_anchors=True,
            ),
        )
    )


@pytest.mark.asyncio
async def test_search_more_never_relabels_and_caps_pool() -> None:
    """Task 6, Review Focus 5: an evidence pool near `_MAX_EVIDENCE` only
    admits as many new hits as there is capacity for, deduped by chunk
    identity, continuing the S label sequence -- existing labels are never
    reassigned, and once the pool is full a further call adds nothing."""
    model = FakeModelPort()
    existing = tuple(_pool_evidence(index, label=f"S{index}") for index in range(1, 19))
    resources = (_resolved_resource(),)
    resource_refs = (
        ResourceRef(family=SourceFamily.DOC, source_id="src-a", mode=ResourceMode.PREFERRED),
    )
    bundle = _pool_bundle(existing, resources=resources, resource_refs=resource_refs)

    duplicate_of_s3 = _pool_hit(3, score=0.95)
    new_hits = (
        duplicate_of_s3,
        _pool_hit(100, score=0.5),
        _pool_hit(101, score=0.5),
        _pool_hit(102, score=0.5),
        _pool_hit(103, score=0.5),
    )
    search = FakeSearchPort(new_hits)
    retrieval = AuthorizedRetrieval(
        search=search,
        embeddings=model,
        answers=model,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=_id_factory(),
    )
    policy = _single_source_policy()

    new_bundle, added = await retrieval.search_more(bundle, "更多信息", (), policy)

    assert [item.evidence_label for item in added] == ["S19", "S20"]
    assert len(new_bundle.response.evidence) == 20
    assert new_bundle.response.evidence[:18] == existing

    exhausted_bundle, more_added = await retrieval.search_more(new_bundle, "更多信息", (), policy)
    assert more_added == ()
    assert exhausted_bundle is new_bundle


@pytest.mark.asyncio
async def test_search_more_restricts_to_requested_sources() -> None:
    """Task 6: `source_ids` restricts the retrieval to the intersection with
    the bundle's own already-selected resources -- the underlying SearchPort
    must only see the requested source."""
    model = FakeModelPort()
    existing = (_pool_evidence(1, label="S1"),)
    resources = (_resolved_resource("src-a"), _resolved_resource("src-b"))
    resource_refs = (
        ResourceRef(family=SourceFamily.DOC, source_id="src-a", mode=ResourceMode.PREFERRED),
        ResourceRef(family=SourceFamily.DOC, source_id="src-b", mode=ResourceMode.PREFERRED),
    )
    bundle = _pool_bundle(existing, resources=resources, resource_refs=resource_refs)
    search = FakeSearchPort((_pool_hit(50, source_id="src-b"),))
    retrieval = AuthorizedRetrieval(
        search=search,
        embeddings=model,
        answers=model,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=_id_factory(),
    )
    policy = policy_context(
        resource_grants=(
            ResourceGrant(
                family="doc",
                source_id="src-a",
                revision_kind="blob_version",
                revision="rev-1",
                source_content_hash=_POOL_SOURCE_HASH,
                allow_all_anchors=True,
            ),
            ResourceGrant(
                family="doc",
                source_id="src-b",
                revision_kind="blob_version",
                revision="rev-1",
                source_content_hash=_POOL_SOURCE_HASH,
                allow_all_anchors=True,
            ),
        )
    )

    await retrieval.search_more(bundle, "更多信息", ("src-b",), policy)

    execution = search.executions[-1]
    assert {resource.source_id for resource in execution.plan.resources} == {"src-b"}


@pytest.mark.asyncio
async def test_analyse_relations_merges_and_continues_r_labels() -> None:
    """Task 6: a second `graph.relations` call merges onto the pool's
    existing relation context -- an edge already cited keeps its label, and
    only genuinely new edges continue the R label sequence."""
    model = FakeModelPort()
    existing_evidence = (_pool_evidence(1, label="S1"),)
    resources = (_resolved_resource(),)
    resource_refs = (
        ResourceRef(family=SourceFamily.DOC, source_id="src-a", mode=ResourceMode.PREFERRED),
    )
    existing_relation = RelationContext(
        status=RelationContextStatus.APPLIED,
        graph_version="7",
        relations=(_relation("R1", "edge-1"), _relation("R2", "edge-2")),
    )
    bundle = _pool_bundle(
        existing_evidence,
        resources=resources,
        resource_refs=resource_refs,
        relation=existing_relation,
    )
    new_context = RelationContext(
        status=RelationContextStatus.APPLIED,
        graph_version="7",
        relations=(
            _relation("R1", "edge-2"),
            _relation("R1", "edge-3"),
            _relation("R1", "edge-4"),
        ),
    )
    relation_port = FakeRelationAnalysis(new_context)
    retrieval = AuthorizedRetrieval(
        search=FakeSearchPort(()),
        embeddings=model,
        answers=model,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=_id_factory(),
        relation_analysis=relation_port,
    )

    new_bundle, added = await retrieval.analyse_relations(bundle, "A与B的关系是什么")

    assert [relation.label for relation in new_bundle.relation.relations] == [
        "R1",
        "R2",
        "R3",
        "R4",
    ]
    assert new_bundle.relation.relations[0] == existing_relation.relations[0]
    assert new_bundle.relation.relations[1] == existing_relation.relations[1]
    assert [relation.label for relation in added] == ["R3", "R4"]
