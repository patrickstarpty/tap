"""Regression lock for the three known-gap items confirmed closed by the
2026-10-14 LangGraph orchestration design spec, section 6.3: flowchart image
regions still require a published, approved review at both the retrieval and
relation-analysis layers, and a completed answer's citations are rechecked
for current authority before the Turn is allowed to finish as `completed`.

These behaviours already exist; these tests exist only to lock them so a
later PR in the LangGraph migration cannot silently regress them. They are
expected to pass unmodified -- a failure here means the spec 6.3 conclusion
no longer matches the code and must be reported, not patched here.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from test_answer_service import (
    _PUB_HASH,
    _SRC_ID,
    _RecordingModel,
    _relation_hit,
    _SequencedSearchPort,
    _single_revision_policy,
    _single_revision_request,
)
from test_flowchart_publication_gate import gate, publication

from tap.interfaces.http.knowledge_service import KnowledgeHttpService
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.ai.application.agents.relation_analysis import RelationAnalysisAgent
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    CitationEvidence,
    FrozenResource,
    GraphContextStatus,
    RetrievalSummary,
    TurnInput,
    TurnInputSnapshot,
    content_digest,
)
from tap.modules.knowledge.application.publication import (
    FlowchartPublicationGate,
    PublishedKnowledgeAuthority,
)
from tap.modules.knowledge.application.relation_analysis import RelationContextStatus
from tap.modules.knowledge.application.retrieve import AuthorizedRetrieval, EvidenceBundle
from tap.modules.knowledge.domain.models import (
    ContentRole,
    DocumentAnchor,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.ports.models import IndexRevision, SearchHit
from tests.contract.test_knowledge_api import CurrentPolicyVerifier, PassthroughRedactor
from tests.unit.ai.test_relation_analysis_agent import (
    _NOW,
    FakeSnippets,
    _context,
    _input,
    _publication,
    _PublicationRepository,
    _single_edge_store,
)


def _bbox_hit(item_id: str) -> SearchHit:
    """A flowchart image-region hit: its `DocumentAnchor` carries a `bbox`,
    so retrieval only trusts it as evidence through a published review --
    see `_flowchart_hit_is_approved` in `retrieve.py`."""
    content = "候选流程图片段"
    content_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    return SearchHit(
        family=SourceFamily.DOC,
        chunk_id="chunk-bbox-1",
        logical_chunk_id="h_" + "3" * 64,
        title=None,
        content=content,
        source=SourceRevisionRef(
            source_id=_SRC_ID,
            source_type="doc",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision="rev-1",
            source_content_hash=_PUB_HASH,
            anchor=DocumentAnchor(
                start_offset=0,
                end_offset=len(content),
                inventory_item_id=item_id,
                bbox=(1, 2, 3, 4),
            ),
        ),
        chunk_content_hash=content_hash,
        content_role=ContentRole.SOURCE,
        index_revision=IndexRevision("gen-1", "search-schema-v1", "corpus-17"),
        embedding_model_version="tap-embedding-v1",
        score=0.8,
        local_rank=2,
    )


@pytest.mark.asyncio
async def test_unapproved_flowchart_region_is_not_retrieval_evidence() -> None:
    """A flowchart image-region hit whose `inventory_item_id` is not among the
    current publication's approved items must never reach `prepare_answer`'s
    evidence pool, even though a plain text hit on the same revision answers
    directly -- locks `_flowchart_hit_is_approved`."""
    text_hit = _relation_hit("chunk-s1", "rev-1", item_id="item-1")
    image_hit = _bbox_hit("item-blocked")
    search = _SequencedSearchPort((text_hit, image_hit))
    model = _RecordingModel([])
    # `policy_context()` fixes `project_id="project-a"`; the publication
    # fixture otherwise defaults to `VALIDATION_SCOPE.project_id`.
    flowchart_gate = gate(
        replace(publication("rev-1", approved=("item-1",)), project_id="project-a")
    )
    knowledge = AuthorizedRetrieval(
        search=search,
        embeddings=model,
        answers=model,
        policy_verifier=CurrentPolicyVerifier(),
        redactor=PassthroughRedactor(),
        id_factory=lambda: "id-1",
        flowchart_gate=flowchart_gate,
    )

    prepared = await knowledge.prepare_answer(_single_revision_request(), _single_revision_policy())

    assert isinstance(prepared, EvidenceBundle)
    chunk_ids = {item.chunk_id for item in prepared.response.evidence}
    assert chunk_ids == {"chunk-s1"}


@pytest.mark.asyncio
async def test_unapproved_flowchart_region_cannot_support_a_relation() -> None:
    """The same approval gate applies to R-label relation support: an edge
    whose only evidence anchor carries a `bbox` for an unapproved item loses
    that support, and with no remaining support the relation is dropped
    entirely -- locks `_flowchart_support_allowed` in the relation-rank
    step (`modules/ai/application/agents/relation_analysis.py`)."""
    store = _single_edge_store(anchor={"bbox": [1, 2, 3, 4], "inventoryItemId": "item-blocked"})
    flowchart_gate = FlowchartPublicationGate(
        PublishedKnowledgeAuthority(_PublicationRepository(_publication()), now=lambda: _NOW)
    )
    agent = RelationAnalysisAgent(
        store, FakeSnippets({"chunk-1": "x 到 y 的片段"}), flowchart_gate=flowchart_gate
    )

    result = await agent.run(_context(), _input(query="x", evidence=()))

    assert result.status is RelationContextStatus.EMPTY
    assert result.relations == ()


class _ScopeDouble:
    scope = VALIDATION_SCOPE


class _FrozenAnswers(_ScopeDouble):
    """Confirms the frozen resource selection has not changed and hands back
    the policy facts needed to confirm the ACL/retrieval-policy digests."""

    def __init__(self, current: tuple[FrozenResource, ...], policy: object) -> None:
        self._current = current
        self._policy = policy

    async def resolve_conversation_selection(self, revision_ids: tuple[str, ...]):
        return self._current, self._policy

    async def authorize_frozen_selection(self, revisions) -> None:
        return None


class _RecheckingCitations(_ScopeDouble):
    """Denies re-authorization of one named citation -- the current revision
    has moved on since the answer was generated."""

    def __init__(self, deny_citation_id: str) -> None:
        self._deny_citation_id = deny_citation_id
        self.calls: list[str] = []

    async def authorize_current(self, citation_id: str) -> None:
        self.calls.append(citation_id)
        if citation_id == self._deny_citation_id:
            raise AuthorizationDenied("citation revision is no longer current")


class _Policy:
    acl_digest = "sha256:" + "1" * 64
    decision_id = "decision-1"
    policy_version = "policy-1"
    active_corpus_version = "tapper-demo-v1"


@pytest.mark.asyncio
async def test_completed_answer_rechecks_each_citation() -> None:
    """`authorize_completed_answer` must recheck *every* cited revision's
    current authority before letting a checkpointed answer finish as
    `completed` -- one stale citation among several must still raise, so the
    Turn cannot complete with evidence that is no longer current."""
    resource = FrozenResource(
        source_id="src_" + "a" * 32,
        document_id="doc_a",
        revision_id="rev_a",
        source_content_hash="sha256:" + "a" * 64,
    )
    policy = _Policy()
    turn_input = TurnInput(
        message="核保流程是什么？",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        resolved_resources=(resource,),
        acl_digest=policy.acl_digest,
        retrieval_policy_digest=content_digest(
            {
                "decisionId": policy.decision_id,
                "policyVersion": policy.policy_version,
                "corpusVersion": policy.active_corpus_version,
            }
        ),
    )
    snapshot = TurnInputSnapshot.create(
        snapshot_id="tis_1",
        project_id=VALIDATION_SCOPE.project_id,
        turn_id="turn_1",
        value=turn_input,
        now=datetime.now(UTC),
    )
    evidence = AnswerEvidence(
        answer="已验证的事实。",
        outcome="completed",
        retrieval_summary=RetrievalSummary(status="completed", authorized_hit_count=2),
        graph_context_status=GraphContextStatus.NOT_SELECTED,
        citations=(
            CitationEvidence(citation_snapshot_id="cs_1", citation_digest="sha256:" + "1" * 64),
            CitationEvidence(citation_snapshot_id="cs_2", citation_digest="sha256:" + "2" * 64),
        ),
    )
    citations = _RecheckingCitations(deny_citation_id="cs_2")
    service = KnowledgeHttpService(
        documents=_ScopeDouble(),
        answers=_FrozenAnswers((resource,), policy),
        citations=citations,
    )

    with pytest.raises(AuthorizationDenied, match="checkpoint citation authorization changed"):
        await service.authorize_completed_answer(snapshot, evidence)

    assert citations.calls == ["cs_1", "cs_2"]
