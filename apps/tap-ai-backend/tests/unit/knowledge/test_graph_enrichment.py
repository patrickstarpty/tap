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
_QUERY = "投保时健康告知书需要什么材料？"


def _draft() -> ProjectGraphDraft:
    """Two aliased nodes ("健康告知书"/"健康告知") plus a third node reachable
    only via a question chunk; `node-long`'s only evidence is `rev-1`, so it
    drops out entirely once a selection excludes that revision."""

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
                degree=2,
                aliases=("健康告知",),
            ),
            ProjectNode(
                node_id=_NODE_C,
                label="受益人",
                node_type="ENTITY",
                canonical_key="beneficiary",
                degree=1,
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
                _NODE_C,
                "rev-1",
                "doc-rev-1",
                "chunk-9",
                anchor,
                "frag-1",
                "fnode-c",
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


@pytest.mark.asyncio
async def test_evidence_outside_selection_is_filtered_or_failed():
    store = await _published_store()
    result = await GraphAnswerEnricher(store).enrich(VALIDATION_SCOPE, ("rev-2",), _QUERY)
    assert result.status is GraphContextStatus.NOT_READY
    assert result.snapshot_id is None
    assert result.facts == ()


@pytest.mark.asyncio
async def test_no_ready_version_is_not_ready_and_errors_are_fail_soft():
    empty_result = await GraphAnswerEnricher(InMemoryProjectGraphStore()).enrich(
        VALIDATION_SCOPE, ("rev-1",), _QUERY
    )
    assert empty_result.status is GraphContextStatus.NOT_READY

    class _BrokenStore(InMemoryProjectGraphStore):
        async def get_current(self, scope):
            raise RuntimeError("boom")

    broken_result = await GraphAnswerEnricher(_BrokenStore()).enrich(
        VALIDATION_SCOPE, ("rev-1",), _QUERY
    )
    assert broken_result.status is GraphContextStatus.UNAVAILABLE


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


def test_graph_answer_context_requires_graph_version_with_applied():
    with pytest.raises(ValueError):
        GraphAnswerContext(GraphContextStatus.APPLIED, "gpv_1", (), None, ())
