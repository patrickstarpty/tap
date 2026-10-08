from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.graph.application.project_queries import InMemoryProjectGraphStore
from tap.modules.graph.application.queries import InMemoryGraphStore
from tap.modules.graph.domain.models import (
    Evidence as GraphEvidence,
)
from tap.modules.graph.domain.models import (
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
)
from tap.modules.graph.domain.project import Alias, NodeSource, ProjectGraphDraft, ProjectNode
from tap.modules.graph.domain.vocabulary import normalize_key
from tap.modules.knowledge.api import KnowledgeAPI, answer_response_to_http
from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
from tap.modules.knowledge.application.graph_enrichment import (
    GraphAnswerEnricher,
    GraphContextStatus,
)
from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority
from tap.modules.knowledge.domain.models import (
    AnswerMode,
    AnswerRequest,
    ContentRole,
    DocumentAnchor,
    IndexRevision,
    ResourceMode,
    ResourceRef,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.domain.review import KnowledgePublication
from tap.modules.knowledge.ports.answers import ReadyDocumentRevision
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    GeneratedClaim,
    RedactionResult,
    SearchHit,
)

NOW = datetime(2026, 9, 23, 9, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


class PublicationRepository:
    def __init__(self, publication: KnowledgePublication | None) -> None:
        self.publication = publication

    async def current_publication(self) -> KnowledgePublication | None:
        return self.publication


def _publication(**changes) -> KnowledgePublication:
    values = {
        "publication_id": "publication-1",
        "project_id": "project-1",
        "review_id": "review-1",
        "review_version": 2,
        "approval_digest": DIGEST,
        "source_revision_ids": ("revision-1", "revision-2"),
        "approved_item_ids": ("item-1", "item-2"),
        "generation": "knowledge-project-1-v7",
        "published_by": "reviewer-1",
        "published_at": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(hours=1),
    }
    values.update(changes)
    return KnowledgePublication(**values)


def _hit(revision: str, item: str, *, generation: str = "knowledge-project-1-v7") -> SearchHit:
    content = f"approved content for {item}"
    return SearchHit(
        family=SourceFamily.DOC,
        chunk_id=f"chunk-{item}",
        logical_chunk_id=f"logical-{item}",
        title=None,
        content=content,
        source=SourceRevisionRef(
            source_id=f"source-{revision}",
            source_type="document",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision=revision,
            source_content_hash=DIGEST,
            anchor=DocumentAnchor(inventory_item_id=item, start_offset=0, end_offset=len(content)),
        ),
        chunk_content_hash="sha256:" + __import__("hashlib").sha256(content.encode()).hexdigest(),
        content_role=ContentRole.SOURCE,
        index_revision=IndexRevision(generation, "v1", "tapper-demo-v2"),
        embedding_model_version="embedding-v1",
        score=1.0,
    )


@pytest.mark.asyncio
async def test_current_publication_authorizes_only_its_full_approved_selection() -> None:
    repository = PublicationRepository(_publication())
    authority = PublishedKnowledgeAuthority(repository, now=lambda: NOW)

    binding = await authority.authorize_hits(
        "project-1",
        (_hit("revision-1", "item-1"), _hit("revision-2", "item-2")),
    )

    assert binding.publication_id == "publication-1"
    assert binding.source_revision_ids == ("revision-1", "revision-2")
    assert binding.approval_digest == DIGEST


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("publication", "project_id", "hit"),
    [
        (_publication(project_id="another-project"), "project-1", _hit("revision-1", "item-1")),
        (
            _publication(status="withdrawn", withdrawn_by="reviewer-2", withdrawn_at=NOW),
            "project-1",
            _hit("revision-1", "item-1"),
        ),
        (_publication(expires_at=NOW), "project-1", _hit("revision-1", "item-1")),
        (_publication(), "project-1", _hit("revision-3", "item-1")),
        (_publication(), "project-1", _hit("revision-1", "item-3")),
        (_publication(), "project-1", _hit("revision-1", "item-1", generation="stale-generation")),
    ],
)
async def test_cross_project_withdrawn_expired_or_unapproved_evidence_is_rejected(
    publication: KnowledgePublication,
    project_id: str,
    hit: SearchHit,
) -> None:
    authority = PublishedKnowledgeAuthority(PublicationRepository(publication), now=lambda: NOW)

    with pytest.raises(AuthorizationDenied):
        await authority.authorize_hits(project_id, (hit,))


@pytest.mark.asyncio
async def test_publication_is_rechecked_before_answer_delivery() -> None:
    repository = PublicationRepository(_publication())
    authority = PublishedKnowledgeAuthority(repository, now=lambda: NOW)
    binding = await authority.authorize_hits("project-1", (_hit("revision-1", "item-1"),))

    repository.publication = None

    with pytest.raises(AuthorizationDenied):
        await authority.revalidate(binding)


@pytest.mark.asyncio
async def test_historical_view_uses_current_authority_not_the_old_revision_authority() -> None:
    repository = PublicationRepository(_publication())
    authority = PublishedKnowledgeAuthority(repository, now=lambda: NOW)

    binding = await authority.authorize_historical_access("project-1", "revision-1")

    assert "historical-revision" not in binding.source_revision_ids
    repository.publication = None
    with pytest.raises(AuthorizationDenied):
        await authority.authorize_historical_access("project-1", "revision-1")


@pytest.mark.asyncio
async def test_historical_access_rejects_withdrawn_source_when_another_remains_published() -> None:
    class IndependentPublications:
        def __init__(self) -> None:
            self.publications = (
                _publication(publication_id="publication-1", source_revision_ids=("revision-1",)),
                _publication(publication_id="publication-2", source_revision_ids=("revision-2",)),
            )

        async def current_publications(self) -> tuple[KnowledgePublication, ...]:
            return self.publications

    repository = IndependentPublications()
    authority = PublishedKnowledgeAuthority(repository, now=lambda: NOW)
    assert (
        await authority.authorize_historical_access("project-1", "revision-1")
    ).publication_id == "publication-1"

    repository.publications = (repository.publications[1],)

    with pytest.raises(AuthorizationDenied):
        await authority.authorize_historical_access("project-1", "revision-1")
    assert (
        await authority.authorize_historical_access("project-1", "revision-2")
    ).publication_id == "publication-2"


class Search:
    def __init__(self, hits: tuple[SearchHit, ...]) -> None:
        self.hits = hits
        self.executions = []

    async def search(self, execution):  # type: ignore[no-untyped-def]
        self.executions.append(execution)
        return self.hits


class Models:
    embedding_model_id = "embedding-v1"
    embedding_dimension = 2

    def __init__(self, repository: PublicationRepository) -> None:
        self.repository = repository
        self.withdraw_during_answer = False

    async def embed(self, query: str) -> Embedding:
        return Embedding((0.1, 0.2), self.embedding_model_id, "embedding-request")

    async def answer(self, query, evidence, profile_id):  # type: ignore[no-untyped-def]
        if self.withdraw_during_answer:
            self.repository.publication = None
        return AnswerGeneration(
            "Published fact.",
            (GeneratedClaim("Published fact.", ("S1",)),),
            "answer-v1",
            profile_id,
            "answer-request",
        )


class CurrentPolicy:
    async def verify_current(self, expected):  # type: ignore[no-untyped-def]
        return expected


class Redactor:
    async def redact(self, text: str) -> RedactionResult:
        return RedactionResult(text, "redaction-v1")


@pytest.mark.asyncio
async def test_two_revision_answer_carries_publication_provenance_and_rechecks_delivery() -> None:
    rows = (
        ReadyDocumentRevision(
            "document-1", "revision-1", DIGEST, "src_11111111111111111111111111111111"
        ),
        ReadyDocumentRevision(
            "document-2", "revision-2", DIGEST, "src_22222222222222222222222222222222"
        ),
    )
    publication = _publication(project_id="tapper-demo", generation="knowledge-project-1-v7")
    repository = PublicationRepository(publication)
    authority = PublishedKnowledgeAuthority(repository, now=lambda: NOW)
    models = Models(repository)
    hit = _hit("revision-1", "item-1")
    hit = replace(
        hit,
        index_revision=IndexRevision("knowledge-project-1-v7", "v1", "tapper-demo-v1"),
        source=SourceRevisionRef(
            source_id=rows[0].source_id or "",
            source_type="doc",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision="revision-1",
            source_content_hash=DIGEST,
            anchor=DocumentAnchor(
                heading_path=("Approved",),
                inventory_item_id="item-1",
                start_offset=0,
                end_offset=len(hit.content),
            ),
        ),
    )
    ids = iter(f"id-{index}" for index in range(20))
    search = Search((hit,))
    knowledge = KnowledgeAPI(
        search=search,
        embeddings=models,
        answers=models,
        policy_verifier=CurrentPolicy(),
        redactor=Redactor(),
        id_factory=lambda: next(ids),
        publication_authority=authority,
    )
    request = AnswerRequest(
        query="What is published?",
        answer_mode=AnswerMode.QUICK,
        source_families=(SourceFamily.DOC,),
        resource_refs=tuple(
            ResourceRef(
                family=SourceFamily.DOC,
                source_id=row.source_id or "",
                mode=ResourceMode.SCOPE,
                requested_revision=row.revision_id,
            )
            for row in rows
        ),
    )
    policy = build_demo_policy_context(rows)

    response = await knowledge.answer(request, policy)

    assert search.executions[0].approved_item_scope == (
        ("revision-1", ("item-1", "item-2")),
        ("revision-2", ("item-1", "item-2")),
    )
    assert response.citations[0].publication_id == "publication-1"
    assert response.citations[0].approval_digest == DIGEST
    assert response.citations[0].approved_item_id == "item-1"
    delivered = answer_response_to_http(response).model_dump(by_alias=True)
    assert delivered["citations"][0]["publicationId"] == "publication-1"
    assert delivered["citations"][0]["approvedItemId"] == "item-1"
    assert delivered["citations"][0]["source"]["anchor"]["inventoryItemId"] == "item-1"

    models.withdraw_during_answer = True
    with pytest.raises(AuthorizationDenied):
        await knowledge.answer(request, policy)


@pytest.mark.asyncio
async def test_graph_evidence_outside_the_approved_slice_fails_closed() -> None:
    """`GraphAnswerEnricher` reads the merged project graph, not the legacy
    fragment snapshot store (which has no `get_current` and would raise,
    landing in the UNAVAILABLE branch rather than FAILED). A node whose only
    evidence cites an inventory item the current publication never approved
    must fail closed with FAILED, the same as a source-revision mismatch."""
    publication = _publication(project_id=VALIDATION_SCOPE.project_id)
    authority = PublishedKnowledgeAuthority(PublicationRepository(publication), now=lambda: NOW)
    store = InMemoryProjectGraphStore()
    node_id = "gpn_unapproved_fact"
    node = ProjectNode(
        node_id=node_id,
        label="Unapproved fact",
        node_type="ENTITY",
        canonical_key=normalize_key("Unapproved fact"),
        degree=0,
        community_id="community-1",
        aliases=(),
    )
    node_source = NodeSource(
        node_id=node_id,
        source_revision_id="revision-1",
        document_revision_id="revision-1",
        chunk_id="chunk-unapproved",
        anchor={
            "type": "document",
            "headingPath": ["Unapproved"],
            "startOffset": 0,
            "endOffset": 10,
            "inventoryItemId": "item-3",
        },
        fragment_snapshot_id="snapshot-publication",
        fragment_node_id="node-unapproved",
    )
    draft = ProjectGraphDraft(
        fragment_digest="sha256:" + "f" * 64,
        nodes=(node,),
        edges=(),
        node_sources=(node_source,),
        edge_evidence=(),
        aliases=(Alias(alias_norm=normalize_key("Unapproved fact"), node_id=node_id, origin="LABEL"),),
        communities=(),
        merge_log=(),
    )
    await store.publish(VALIDATION_SCOPE, draft, now=NOW)

    result = await GraphAnswerEnricher(store, publication_authority=authority).enrich(
        VALIDATION_SCOPE,
        ("revision-1", "revision-2"),
        "Unapproved fact",
    )

    assert result.status is GraphContextStatus.FAILED


@pytest.mark.asyncio
async def test_independent_publications_bind_each_hit_to_its_own_approval_and_generation():
    first = _publication(source_revision_ids=("revision-1",), approved_item_ids=("item-1",))
    second = _publication(
        publication_id="publication-2",
        review_id="review-2",
        source_revision_ids=("revision-2",),
        approved_item_ids=("item-2",),
        generation="second-generation",
    )

    class Repository(PublicationRepository):
        publications = (first, second)

        async def current_publications(self):
            return self.publications

    repository = Repository(first)
    authority = PublishedKnowledgeAuthority(repository, now=lambda: NOW)
    binding = await authority.authorize_hits(
        "project-1",
        (
            _hit("revision-1", "item-1"),
            _hit("revision-2", "item-2", generation="second-generation"),
        ),
    )
    assert binding.for_revision("revision-1").publication_id == "publication-1"
    assert binding.for_revision("revision-2").publication_id == "publication-2"
    with pytest.raises(AuthorizationDenied):
        await authority.authorize_hits("project-1", (_hit("revision-1", "item-2"),))
    with pytest.raises(AuthorizationDenied):
        await authority.authorize_hits("project-1", (_hit("revision-2", "item-2"),))
    selected_first = await authority.authorize_selection("project-1", ("revision-1",))
    repository.publications = (first,)
    await authority.revalidate(selected_first)
    with pytest.raises(AuthorizationDenied):
        await authority.revalidate(binding)


@pytest.mark.asyncio
@pytest.mark.parametrize("edge_count", (4, 16))
async def test_answer_traverses_published_retry_path_and_retrieves_rule_candidates(
    edge_count,
) -> None:
    """Exercise the real Knowledge answer boundary, including post-generation withdrawal."""
    contents = (
        "流程图连线 risk → pay：风险 → 支付；条件：高风险",
        "流程图连线 pay → retry：支付 → 重试；条件：失败",
        "流程图连线 retry → pay：重试 → 支付；条件：允许重试",
        "流程图连线 pay → done：支付 → 完成；条件：成功",
        *(f"流程图连线 x{i} → y{i}：额外节点 → 额外结束" for i in range(edge_count - 4)),
        "高风险业务规则：金额超过1000需人工审批。",
    )
    rows = tuple(
        ReadyDocumentRevision(
            f"document-{i}",
            f"revision-{i}",
            DIGEST,
            "src_" + str(i) * 32,
        )
        for i in (1, 2)
    )
    repository = PublicationRepository(
        _publication(
            project_id="tapper-demo",
            approved_item_ids=tuple(f"item-{i}" for i in range(edge_count + 1)),
        )
    )
    authority = PublishedKnowledgeAuthority(repository, now=lambda: NOW)
    hits = []
    for index, content in enumerate(contents):
        row = rows[0 if index < edge_count else 1]
        hit = _hit(row.revision_id, f"item-{index}")
        hits.append(
            replace(
                hit,
                content=content,
                local_rank=index + 1,
                chunk_content_hash="sha256:"
                + __import__("hashlib").sha256(content.encode()).hexdigest(),
                index_revision=IndexRevision("knowledge-project-1-v7", "v1", "tapper-demo-v1"),
                source=replace(
                    hit.source,
                    source_id=row.source_id,
                    anchor=DocumentAnchor(
                        bbox=(0, 0, 20, 20) if index < edge_count else (),
                        inventory_item_id=f"item-{index}",
                        start_offset=0,
                        end_offset=len(content),
                    ),
                ),
            )
        )

    class FlowModels(Models):
        contradict = False

        async def answer(self, query, evidence, profile_id, **kwargs):
            self.context = kwargs["graph_context"][-1]
            self.evidence = evidence
            if self.contradict:
                return AnswerGeneration(
                    "Unsupported combined path.",
                    (GeneratedClaim("Unsupported combined path.", ("S1", "S5")),),
                    "answer-v1",
                    profile_id,
                    "answer-request",
                )
            return await super().answer(query, evidence, profile_id)

    class RuleSearch(Search):
        async def search(self, execution):
            self.executions.append(execution)
            return tuple(
                hits[edge_count:]
                if "business rules thresholds" in execution.plan.sanitized_query
                else hits[:4]
            )

        async def flowchart_edges(self, execution):
            self.executions.append(execution)
            return tuple(hits[:edge_count])

    models = FlowModels(repository)
    search = RuleSearch(())
    ids = iter(f"flow-id-{i}" for i in range(200))
    knowledge = KnowledgeAPI(
        search=search,
        embeddings=models,
        answers=models,
        policy_verifier=CurrentPolicy(),
        redactor=Redactor(),
        id_factory=lambda: next(ids),
        publication_authority=authority,
    )
    request = AnswerRequest(
        query="高风险支付失败后重试成功经过什么路径？",
        answer_mode=AnswerMode.QUICK,
        source_families=(SourceFamily.DOC,),
        resource_refs=tuple(
            ResourceRef(
                SourceFamily.DOC,
                row.source_id,
                ResourceMode.SCOPE,
                requested_revision=row.revision_id,
            )
            for row in rows
        ),
    )
    response = await knowledge.answer(request, build_demo_policy_context(rows))
    assert len(search.executions) == 3
    assert models.context["coverage"] == "complete-published-approved-edge-set"
    assert models.context["businessRulesStatus"] == "unknown-from-image"
    assert models.context["businessRuleCandidateEvidenceLabels"] == [f"S{edge_count + 1}"]
    assert any(
        path["nodeIds"] == ["risk", "pay", "retry", "pay", "done"]
        for path in models.context["paths"]
    )
    assert len(response.citations) == edge_count + 1
    assert all(citation.publication_id == "publication-1" for citation in response.citations)
    if edge_count == 16:
        models.contradict = True
        rejected = await knowledge.answer(request, build_demo_policy_context(rows))
        assert rejected.abstained
        models.contradict = False
    models.withdraw_during_answer = True
    with pytest.raises(AuthorizationDenied):
        await knowledge.answer(request, build_demo_policy_context(rows))
