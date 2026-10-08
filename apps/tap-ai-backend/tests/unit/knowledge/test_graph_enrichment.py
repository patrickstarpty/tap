from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.graph.application.project_queries import InMemoryProjectGraphStore
from tap.modules.graph.domain.models import RelationOrigin
from tap.modules.graph.domain.project import (
    Alias,
    EdgeEvidence,
    NodeSource,
    ProjectEdge,
    ProjectGraphDraft,
    ProjectNode,
)
from tap.modules.graph.domain.vocabulary import normalize_key
from tap.modules.knowledge.application.graph_enrichment import (
    GraphAnswerContext,
    GraphAnswerEnricher,
    GraphContextStatus,
)

_NODE_LONG = "node-long"
_NODE_SHORT = "node-short"
_NODE_C = "node-c"
_NODE_D = "node-d"
_NODE_E = "node-e"
_QUERY = "投保时健康告知书需要什么材料？"


def _draft() -> ProjectGraphDraft:
    """Five nodes exercising the revision-visibility edges of seeding and
    expansion:

    - `node-long`: aliased "健康告知书"; evidence only in `rev-1`, so it drops
      out entirely once a selection excludes that revision.
    - `node-short`: aliased "健康告知"; has *mixed* `rev-1`/`rev-2` evidence,
      so a `rev-1`-only selection must keep the node but hide its `rev-2`
      evidence row from the facts.
    - `node-c`: reachable only via question chunk `chunk-9`; `rev-1` only.
    - `node-d`: reachable only through `node-short`, two hops from
      `node-long`; evidence only in `rev-2` — proves a `rev-1`+`rev-2`
      selection reads one merged project graph, not just the `rev-1` half.
    - `node-e`: reachable only via question chunk `chunk-20`; evidence only
      in `rev-2` — a seed that a `rev-1`-only selection must filter out of
      `seed_node_ids` without failing the whole selection, since `node-long`
      is also a seed and stays visible.
    """

    anchor = {"kind": "text", "start": 0, "end": 5}
    return ProjectGraphDraft(
        fragment_digest="sha256:" + "d" * 64,
        nodes=(
            ProjectNode(
                node_id=_NODE_LONG,
                label="健康告知书",
                node_type="REQUIREMENT",
                canonical_key="health_declaration_form",
                degree=1,
                aliases=("健康告知书",),
            ),
            ProjectNode(
                node_id=_NODE_SHORT,
                label="健康告知",
                node_type="REQUIREMENT",
                canonical_key="health_declaration",
                degree=3,
                aliases=("健康告知",),
            ),
            ProjectNode(
                node_id=_NODE_C,
                label="受益人",
                node_type="ENTITY",
                canonical_key="beneficiary",
                degree=1,
            ),
            ProjectNode(
                node_id=_NODE_D,
                label="犹豫期",
                node_type="CONCEPT",
                canonical_key="cooling_off_period",
                degree=1,
            ),
            ProjectNode(
                node_id=_NODE_E,
                label="受益人变更",
                node_type="ENTITY",
                canonical_key="beneficiary_change",
                degree=0,
            ),
        ),
        edges=(
            ProjectEdge(
                "edge-1",
                _NODE_LONG,
                _NODE_SHORT,
                "REQUIRES",
                "亦称",
                RelationOrigin.EXTRACTED,
                0.9,
            ),
            ProjectEdge(
                "edge-2",
                _NODE_SHORT,
                _NODE_C,
                "RELATED_TO",
                "关联",
                RelationOrigin.EXTRACTED,
                0.8,
            ),
            ProjectEdge(
                "edge-3",
                _NODE_SHORT,
                _NODE_D,
                "APPLIES_TO",
                "适用",
                RelationOrigin.EXTRACTED,
                0.7,
            ),
        ),
        node_sources=(
            NodeSource(
                _NODE_LONG,
                "rev-1",
                "doc-rev-1",
                "chunk-1",
                anchor,
                "frag-1",
                "fnode-long",
            ),
            NodeSource(
                _NODE_SHORT,
                "rev-1",
                "doc-rev-1",
                "chunk-2",
                anchor,
                "frag-1",
                "fnode-short",
            ),
            NodeSource(
                _NODE_SHORT,
                "rev-2",
                "doc-rev-2",
                "chunk-5",
                anchor,
                "frag-1",
                "fnode-short-2",
            ),
            NodeSource(
                _NODE_C,
                "rev-1",
                "doc-rev-1",
                "chunk-9",
                anchor,
                "frag-1",
                "fnode-c",
            ),
            NodeSource(
                _NODE_D,
                "rev-2",
                "doc-rev-2",
                "chunk-6",
                anchor,
                "frag-1",
                "fnode-d",
            ),
            NodeSource(
                _NODE_E,
                "rev-2",
                "doc-rev-2",
                "chunk-20",
                anchor,
                "frag-1",
                "fnode-e",
            ),
        ),
        edge_evidence=(
            EdgeEvidence(
                "edge-1",
                "rev-1",
                "doc-rev-1",
                "chunk-1",
                anchor,
                "sha256:" + "b" * 64,
                "frag-1",
                "fedge-1",
            ),
            EdgeEvidence(
                "edge-2",
                "rev-1",
                "doc-rev-1",
                "chunk-2",
                anchor,
                "sha256:" + "c" * 64,
                "frag-1",
                "fedge-2",
            ),
            EdgeEvidence(
                "edge-3",
                "rev-2",
                "doc-rev-2",
                "chunk-6",
                anchor,
                "sha256:" + "e" * 64,
                "frag-1",
                "fedge-3",
            ),
        ),
        aliases=(
            Alias(normalize_key("健康告知书"), _NODE_LONG, "LABEL"),
            Alias(normalize_key("健康告知"), _NODE_SHORT, "LABEL"),
        ),
        communities=(),
        merge_log=(),
    )


async def _published_store() -> InMemoryProjectGraphStore:
    store = InMemoryProjectGraphStore()
    await store.publish(VALIDATION_SCOPE, _draft(), now=datetime.now(UTC))
    return store


@pytest.mark.asyncio
async def test_question_seeds_come_from_alias_longest_match_not_substring():
    store = await _published_store()
    result = await GraphAnswerEnricher(store).enrich(VALIDATION_SCOPE, ("rev-1",), _QUERY)
    assert result.status is GraphContextStatus.APPLIED
    assert result.graph_version == 1
    assert result.seed_node_ids == (_NODE_LONG,)
    assert result.snapshot_id == "gpv_1"
    assert any(f["kind"] == "edge" and f["relationLabel"] for f in result.facts)


@pytest.mark.asyncio
async def test_chunk_seeds_join_question_seeds():
    store = await _published_store()
    result = await GraphAnswerEnricher(store).enrich(
        VALIDATION_SCOPE, ("rev-1",), _QUERY, chunk_ids=("chunk-9",)
    )
    assert result.status is GraphContextStatus.APPLIED
    assert _NODE_C in result.seed_node_ids
    assert _NODE_LONG in result.seed_node_ids


@pytest.mark.asyncio
async def test_multi_source_selection_uses_one_project_graph():
    store = await _published_store()
    result = await GraphAnswerEnricher(store).enrich(VALIDATION_SCOPE, ("rev-1", "rev-2"), _QUERY)
    assert result.status is GraphContextStatus.APPLIED
    # `node-d` has evidence only in `rev-2`, two hops from the `rev-1`-only
    # alias seed `node-long`: only visible if both revisions' fragments were
    # actually merged into the one project graph this selection reads.
    assert _NODE_D in {f["id"] for f in result.facts if f["kind"] == "node"}


@pytest.mark.asyncio
async def test_evidence_outside_selection_is_filtered_or_failed():
    store = await _published_store()
    result = await GraphAnswerEnricher(store).enrich(VALIDATION_SCOPE, ("rev-2",), _QUERY)
    assert result.status is GraphContextStatus.NOT_READY
    assert result.snapshot_id is None
    assert result.facts == ()


@pytest.mark.asyncio
async def test_mixed_revision_node_evidence_is_filtered_to_selection():
    """`node-short` has one `rev-1` and one `rev-2` `NodeSource` row; a
    `rev-1`-only selection must keep the node (it has some evidence inside the
    selection) but must never let the `rev-2` evidence row reach the facts."""

    store = await _published_store()
    result = await GraphAnswerEnricher(store).enrich(VALIDATION_SCOPE, ("rev-1",), _QUERY)
    assert result.status is GraphContextStatus.APPLIED
    node_short_fact = next(
        f for f in result.facts if f["kind"] == "node" and f["id"] == _NODE_SHORT
    )
    assert node_short_fact["evidence"]
    assert all(item["sourceRevisionId"] == "rev-1" for item in node_short_fact["evidence"])
    assert all(item["chunkId"] != "chunk-5" for item in node_short_fact["evidence"])


@pytest.mark.asyncio
async def test_hidden_seed_is_absent_from_seed_node_ids_and_facts():
    """Two seeds are matched: `node-e` via `chunk_ids` (evidence only in
    `rev-2`) and `node-long` via the question alias (evidence only in
    `rev-1`). A `rev-1`-only selection must drop `node-e` from both
    `seed_node_ids` and the facts while keeping the overall context `APPLIED`
    because `node-long` still survives."""

    store = await _published_store()
    result = await GraphAnswerEnricher(store).enrich(
        VALIDATION_SCOPE, ("rev-1",), _QUERY, chunk_ids=("chunk-20",)
    )
    assert result.status is GraphContextStatus.APPLIED
    assert _NODE_E not in result.seed_node_ids
    assert _NODE_LONG in result.seed_node_ids
    assert _NODE_E not in {f["id"] for f in result.facts if f["kind"] == "node"}


@pytest.mark.asyncio
async def test_no_ready_version_is_not_ready_and_errors_are_fail_soft(caplog):
    empty_result = await GraphAnswerEnricher(InMemoryProjectGraphStore()).enrich(
        VALIDATION_SCOPE, ("rev-1",), _QUERY
    )
    assert empty_result.status is GraphContextStatus.NOT_READY

    class _BrokenStore(InMemoryProjectGraphStore):
        async def get_current(self, scope):
            raise RuntimeError("boom")

    with caplog.at_level("WARNING"):
        broken_result = await GraphAnswerEnricher(_BrokenStore()).enrich(
            VALIDATION_SCOPE, ("rev-1",), _QUERY
        )
    assert broken_result.status is GraphContextStatus.UNAVAILABLE
    # The exception type must be logged rather than swallowed silently.
    assert any(
        "RuntimeError" in record.message and VALIDATION_SCOPE.project_id in record.message
        for record in caplog.records
    )


class _DenyingAuthority:
    async def authorize_selection(self, project_id, source_revision_ids):
        raise AuthorizationDenied("revision selection is outside the current publication")


@pytest.mark.asyncio
async def test_publication_authority_denial_is_failed():
    store = await _published_store()
    result = await GraphAnswerEnricher(store, publication_authority=_DenyingAuthority()).enrich(
        VALIDATION_SCOPE, ("rev-1",), _QUERY
    )
    assert result.status is GraphContextStatus.FAILED


class _FakeAuthority:
    """Stands in for `PublishedKnowledgeAuthority`: `authorize_selection`
    always succeeds; `authorize_evidence`/`revalidate` can be made to deny so
    tests can isolate which call fails the enrichment."""

    def __init__(self, *, deny_evidence: bool = False, deny_revalidate: bool = False) -> None:
        self._deny_evidence = deny_evidence
        self._deny_revalidate = deny_revalidate
        self.evidence_calls: list[tuple[str, str | None, str | None]] = []
        self.revalidated = False

    async def authorize_selection(self, project_id, source_revision_ids):
        return ("binding", project_id, source_revision_ids)

    async def authorize_evidence(
        self, project_id, *, source_revision_id, document_revision_id=None, approved_item_id=None
    ):
        self.evidence_calls.append((source_revision_id, document_revision_id, approved_item_id))
        if self._deny_evidence:
            raise AuthorizationDenied("evidence is outside the current publication")

    async def revalidate(self, expected):
        if self._deny_revalidate:
            raise AuthorizationDenied("knowledge publication changed during retrieval")
        self.revalidated = True
        return expected


@pytest.mark.asyncio
async def test_publication_authority_success_applies():
    store = await _published_store()
    authority = _FakeAuthority()
    result = await GraphAnswerEnricher(store, publication_authority=authority).enrich(
        VALIDATION_SCOPE, ("rev-1",), _QUERY
    )
    assert result.status is GraphContextStatus.APPLIED
    assert authority.evidence_calls
    assert authority.revalidated


@pytest.mark.asyncio
async def test_publication_authority_evidence_denial_is_failed():
    store = await _published_store()
    authority = _FakeAuthority(deny_evidence=True)
    result = await GraphAnswerEnricher(store, publication_authority=authority).enrich(
        VALIDATION_SCOPE, ("rev-1",), _QUERY
    )
    assert result.status is GraphContextStatus.FAILED


@pytest.mark.asyncio
async def test_publication_authority_revalidate_denial_is_failed():
    store = await _published_store()
    authority = _FakeAuthority(deny_revalidate=True)
    result = await GraphAnswerEnricher(store, publication_authority=authority).enrich(
        VALIDATION_SCOPE, ("rev-1",), _QUERY
    )
    assert result.status is GraphContextStatus.FAILED


def test_graph_answer_context_requires_graph_version_with_applied():
    with pytest.raises(ValueError):
        GraphAnswerContext(GraphContextStatus.APPLIED, "gpv_1", (), None, ())
