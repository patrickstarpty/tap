"""Unit tests for the deterministic relation analysis pipeline (PR 3 task 2).

`FakeProjectGraphStore` implements only the five `ProjectGraphStorePort`
methods the pipeline calls (`nodes_for_chunks`, `nodes`, `match_aliases`,
`neighbors`, `path`), backed by plain in-memory dicts -- a fixture-only
stand-in, not the full reference store in
`tap.modules.graph.application.project_queries`.

Fixture graph: nodes A/B/C/D; edges A->B REQUIRES 0.9, B->C PRECEDES 0.8,
C->D USES 0.7, A->D RELATED_TO 0.95. A->B/B->C/C->D are evidenced in
`rev-1`; A->D is evidenced only in `rev-2` (used to exercise authorized-source
filtering). Chunk `chunk-1` supports A->B. Node E is aliased "投保审核" and
has a source only in `rev-2` (used to exercise node-level, not just
edge-level, source-visibility filtering for query seeds).
"""

from __future__ import annotations

from collections import deque

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.application.alias_index import AliasIndex
from tap.modules.graph.domain.models import RelationOrigin
from tap.modules.graph.domain.project import (
    Alias,
    EdgeEvidence,
    ProjectEdge,
    ProjectNode,
    ProjectSubgraph,
)
from tap.modules.knowledge.application.relation_analysis import (
    EvidenceRef,
    RelationContext,
    RelationContextStatus,
    RelationEvidence,
    RelationPath,
    RelationSupport,
    assemble,
    expand,
    expansion_depth,
    find_paths,
    rank_edges,
    seed_from_evidence,
    seed_from_query,
    snippet_chunk_refs,
)

_SCOPE = VALIDATION_SCOPE

_NODE_A = ProjectNode(node_id="A", label="节点 A", node_type="ENTITY", canonical_key="a")
_NODE_B = ProjectNode(
    node_id="B", label="节点 B", node_type="ENTITY", canonical_key="b", aliases=("核保",)
)
_NODE_C = ProjectNode(node_id="C", label="节点 C", node_type="ENTITY", canonical_key="c")
_NODE_D = ProjectNode(
    node_id="D", label="节点 D", node_type="ENTITY", canonical_key="d", aliases=("健康告知",)
)
_NODE_E = ProjectNode(
    node_id="E", label="节点 E", node_type="ENTITY", canonical_key="e", aliases=("投保审核",)
)

_EDGE_AB = ProjectEdge(
    edge_id="e-ab",
    source_node_id="A",
    target_node_id="B",
    relation_type="REQUIRES",
    relation_label="REQUIRES",
    origin=RelationOrigin.EXTRACTED,
    confidence=0.9,
)
_EDGE_BC = ProjectEdge(
    edge_id="e-bc",
    source_node_id="B",
    target_node_id="C",
    relation_type="PRECEDES",
    relation_label="PRECEDES",
    origin=RelationOrigin.EXTRACTED,
    confidence=0.8,
)
_EDGE_CD = ProjectEdge(
    edge_id="e-cd",
    source_node_id="C",
    target_node_id="D",
    relation_type="USES",
    relation_label="USES",
    origin=RelationOrigin.EXTRACTED,
    confidence=0.7,
)
_EDGE_AD = ProjectEdge(
    edge_id="e-ad",
    source_node_id="A",
    target_node_id="D",
    relation_type="RELATED_TO",
    relation_label="RELATED_TO",
    origin=RelationOrigin.EXTRACTED,
    confidence=0.95,
)


def _evidence(edge_id: str, chunk_id: str, source_revision_id: str) -> EdgeEvidence:
    return EdgeEvidence(
        edge_id=edge_id,
        source_revision_id=source_revision_id,
        document_revision_id="doc-1",
        chunk_id=chunk_id,
        anchor={"page": 1},
        content_digest=f"digest-{chunk_id}",
        fragment_snapshot_id="fragment-1",
        fragment_edge_id=f"fe-{edge_id}",
    )


class FakeProjectGraphStore:
    """Minimal `ProjectGraphStorePort` stand-in: five methods, in-memory dicts."""

    def __init__(
        self,
        *,
        nodes: tuple[ProjectNode, ...],
        edges: tuple[ProjectEdge, ...],
        edge_evidence: dict[str, tuple[EdgeEvidence, ...]],
        node_chunks: dict[str, tuple[str, ...]],
        node_sources: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._nodes_by_id = {node.node_id: node for node in nodes}
        self._edges = edges
        self._edge_by_id = {edge.edge_id: edge for edge in edges}
        self._edge_evidence = edge_evidence
        self._node_chunks = node_chunks
        self._node_sources = node_sources or {}
        aliases = tuple(
            Alias(alias_norm=alias, node_id=node.node_id, origin="LABEL")
            for node in nodes
            for alias in node.aliases
        )
        self._alias_index = AliasIndex.build(aliases)

    def _edges_authorized(self, source_revision_ids: tuple[str, ...]) -> set[str]:
        if not source_revision_ids:
            return set(self._edge_by_id)
        allowed = set(source_revision_ids)
        return {
            edge_id
            for edge_id, items in self._edge_evidence.items()
            if any(item.source_revision_id in allowed for item in items)
        }

    def _node_visible(self, node_id: str, source_revision_ids: tuple[str, ...]) -> bool:
        if node_id not in self._nodes_by_id:
            return False
        if not source_revision_ids:
            return True
        allowed = set(source_revision_ids)
        return any(revision in allowed for revision in self._node_sources.get(node_id, ()))

    async def nodes_for_chunks(
        self, scope, chunk_ids: tuple[str, ...], *, version: int | None = None
    ) -> tuple[str, ...]:
        result: list[str] = []
        for chunk_id in chunk_ids:
            result.extend(self._node_chunks.get(chunk_id, ()))
        return tuple(result)

    async def nodes(
        self, scope, node_ids: tuple[str, ...], *, version: int | None = None
    ) -> tuple[ProjectNode, ...]:
        return tuple(
            self._nodes_by_id[node_id] for node_id in node_ids if node_id in self._nodes_by_id
        )

    async def match_aliases(self, scope, text: str, *, version: int | None = None):
        return self._alias_index.match(text)

    async def neighbors(
        self,
        scope,
        node_id: str,
        *,
        depth: int = 1,
        node_limit: int = 50,
        source_revision_ids: tuple[str, ...] = (),
        version: int | None = None,
    ) -> ProjectSubgraph:
        if not self._node_visible(node_id, source_revision_ids):
            return ProjectSubgraph(
                version=version or 1, nodes=(), edges=(), sources=(), evidence=()
            )
        authorized_edge_ids = self._edges_authorized(source_revision_ids)
        visited = {node_id}
        frontier = {node_id}
        selected_edge_ids: set[str] = set()
        for _ in range(depth):
            new_frontier: set[str] = set()
            for edge in self._edges:
                if edge.edge_id not in authorized_edge_ids:
                    continue
                if edge.source_node_id in frontier or edge.target_node_id in frontier:
                    selected_edge_ids.add(edge.edge_id)
                    for candidate in (edge.source_node_id, edge.target_node_id):
                        if candidate not in visited:
                            new_frontier.add(candidate)
            visited |= new_frontier
            frontier = new_frontier
            if not frontier:
                break
        ordering = [node_id] + sorted(visited - {node_id})
        nodes = tuple(
            self._nodes_by_id[candidate] for candidate in ordering if candidate in self._nodes_by_id
        )[:node_limit]
        selected_node_ids = {node.node_id for node in nodes}
        edges = tuple(
            self._edge_by_id[edge_id]
            for edge_id in selected_edge_ids
            if self._edge_by_id[edge_id].source_node_id in selected_node_ids
            and self._edge_by_id[edge_id].target_node_id in selected_node_ids
        )
        evidence = tuple(
            item for edge in edges for item in self._edge_evidence.get(edge.edge_id, ())
        )
        return ProjectSubgraph(
            version=version or 1, nodes=nodes, edges=edges, sources=(), evidence=evidence
        )

    async def path(
        self,
        scope,
        source_node_id: str,
        target_node_id: str,
        *,
        max_hops: int = 3,
        source_revision_ids: tuple[str, ...] = (),
        version: int | None = None,
    ) -> ProjectSubgraph:
        authorized_edge_ids = self._edges_authorized(source_revision_ids)
        if source_node_id == target_node_id:
            node = self._nodes_by_id.get(source_node_id)
            nodes = (node,) if node is not None else ()
            return ProjectSubgraph(
                version=version or 1, nodes=nodes, edges=(), sources=(), evidence=()
            )
        adjacency: dict[str, list[tuple[str, str]]] = {}
        for edge in self._edges:
            if edge.edge_id not in authorized_edge_ids:
                continue
            adjacency.setdefault(edge.source_node_id, []).append(
                (edge.target_node_id, edge.edge_id)
            )
            adjacency.setdefault(edge.target_node_id, []).append(
                (edge.source_node_id, edge.edge_id)
            )
        visited = {source_node_id}
        queue: deque[tuple[str, tuple[str, ...], tuple[str, ...]]] = deque(
            [(source_node_id, (source_node_id,), ())]
        )
        while queue:
            current, node_path, edge_path = queue.popleft()
            if len(edge_path) >= max_hops:
                continue
            for neighbor, edge_id in adjacency.get(current, ()):
                if neighbor in visited:
                    continue
                new_node_path = node_path + (neighbor,)
                new_edge_path = edge_path + (edge_id,)
                if neighbor == target_node_id:
                    nodes = tuple(
                        self._nodes_by_id[nid] for nid in new_node_path if nid in self._nodes_by_id
                    )
                    edges = tuple(self._edge_by_id[eid] for eid in new_edge_path)
                    evidence = tuple(
                        item for eid in new_edge_path for item in self._edge_evidence.get(eid, ())
                    )
                    return ProjectSubgraph(
                        version=version or 1,
                        nodes=nodes,
                        edges=edges,
                        sources=(),
                        evidence=evidence,
                    )
                visited.add(neighbor)
                queue.append((neighbor, new_node_path, new_edge_path))
        return ProjectSubgraph(version=version or 1, nodes=(), edges=(), sources=(), evidence=())


def _build_store() -> FakeProjectGraphStore:
    return FakeProjectGraphStore(
        nodes=(_NODE_A, _NODE_B, _NODE_C, _NODE_D, _NODE_E),
        edges=(_EDGE_AB, _EDGE_BC, _EDGE_CD, _EDGE_AD),
        edge_evidence={
            "e-ab": (_evidence("e-ab", "chunk-1", "rev-1"),),
            "e-bc": (_evidence("e-bc", "chunk-2", "rev-1"),),
            "e-cd": (_evidence("e-cd", "chunk-3", "rev-1"),),
            "e-ad": (_evidence("e-ad", "chunk-4", "rev-2"),),
        },
        node_chunks={"chunk-1": ("A",)},
        node_sources={
            "A": ("rev-1",),
            "B": ("rev-1",),
            "C": ("rev-1",),
            "D": ("rev-1",),
            "E": ("rev-2",),
        },
    )


class _ExplodingStore:
    """A `ProjectGraphStorePort` stand-in whose every method fails the test if
    called -- used to prove an empty source selection short-circuits before
    any store round-trip."""

    async def nodes_for_chunks(self, *args: object, **kwargs: object) -> tuple[str, ...]:
        raise AssertionError("nodes_for_chunks should not be called for an empty selection")

    async def nodes(self, *args: object, **kwargs: object) -> tuple[ProjectNode, ...]:
        raise AssertionError("nodes should not be called for an empty selection")

    async def match_aliases(self, *args: object, **kwargs: object) -> tuple[object, ...]:
        raise AssertionError("match_aliases should not be called for an empty selection")

    async def neighbors(self, *args: object, **kwargs: object) -> ProjectSubgraph:
        raise AssertionError("neighbors should not be called for an empty selection")

    async def path(self, *args: object, **kwargs: object) -> ProjectSubgraph:
        raise AssertionError("path should not be called for an empty selection")


@pytest.mark.asyncio
async def test_expand_with_empty_selection_returns_empty_subgraph_without_store_calls() -> None:
    subgraph = await expand(
        _ExplodingStore(),
        _SCOPE,
        1,
        (_NODE_A,),
        allowed_source_revision_ids=frozenset(),
    )
    assert subgraph == ProjectSubgraph(version=1, nodes=(), edges=(), sources=(), evidence=())


@pytest.mark.asyncio
async def test_find_paths_with_empty_selection_returns_empty_without_store_calls() -> None:
    paths = await find_paths(
        _ExplodingStore(),
        _SCOPE,
        1,
        (_NODE_A, _NODE_C),
        (),
        allowed_source_revision_ids=frozenset(),
    )
    assert paths == ()


@pytest.mark.asyncio
async def test_seed_from_evidence_dedupes_and_seed_from_query_matches_aliases() -> None:
    store = _build_store()
    evidence = (
        EvidenceRef(
            label="S1", chunk_id="chunk-1", source_revision_id="rev-1", document_revision_id="doc-1"
        ),
        EvidenceRef(
            label="S1", chunk_id="chunk-1", source_revision_id="rev-1", document_revision_id="doc-1"
        ),
    )
    evidence_seeds = await seed_from_evidence(store, _SCOPE, 1, evidence)
    assert [node.node_id for node in evidence_seeds] == ["A"]

    query_seeds = await seed_from_query(
        store, _SCOPE, 1, "核保与健康告知", allowed_source_revision_ids=frozenset({"rev-1"})
    )
    assert [node.node_id for node in query_seeds] == ["B", "D"]


@pytest.mark.asyncio
async def test_seed_from_query_excludes_node_without_visible_source_in_selection() -> None:
    store = _build_store()
    seeds = await seed_from_query(
        store, _SCOPE, 1, "投保审核", allowed_source_revision_ids=frozenset({"rev-1"})
    )
    assert seeds == ()

    visible_seeds = await seed_from_query(
        store, _SCOPE, 1, "投保审核", allowed_source_revision_ids=frozenset({"rev-2"})
    )
    assert [node.node_id for node in visible_seeds] == ["E"]


@pytest.mark.asyncio
async def test_seed_from_query_with_empty_selection_returns_no_seeds_without_match_aliases() -> (
    None
):
    store = _ExplodingStore()
    seeds = await seed_from_query(store, _SCOPE, 1, "核保", allowed_source_revision_ids=frozenset())
    assert seeds == ()


def test_expansion_depth_is_two_below_three_seeds() -> None:
    assert expansion_depth(2) == 2
    assert expansion_depth(3) == 1


@pytest.mark.asyncio
async def test_expand_respects_limits_and_authorized_sources() -> None:
    store = _build_store()
    subgraph = await expand(
        store,
        _SCOPE,
        1,
        (_NODE_A,),
        allowed_source_revision_ids=frozenset({"rev-1"}),
        node_limit=2,
        edge_limit=200,
    )
    assert len(subgraph.nodes) <= 2
    node_ids = {node.node_id for node in subgraph.nodes}
    assert "A" in node_ids
    assert "D" not in node_ids
    assert "e-ad" not in {edge.edge_id for edge in subgraph.edges}


@pytest.mark.asyncio
async def test_expand_seed_order_is_stable_when_seeds_exceed_node_limit() -> None:
    store = _build_store()
    subgraph = await expand(
        store,
        _SCOPE,
        1,
        (_NODE_D, _NODE_C, _NODE_B, _NODE_A),
        allowed_source_revision_ids=frozenset({"rev-1"}),
        node_limit=2,
        edge_limit=200,
    )
    assert [node.node_id for node in subgraph.nodes] == ["D", "C"]


@pytest.mark.asyncio
async def test_find_paths_bounds_hops_and_count() -> None:
    store = _build_store()
    allowed = frozenset({"rev-1"})

    paths = await find_paths(
        store,
        _SCOPE,
        1,
        (_NODE_A, _NODE_C),
        (),
        allowed_source_revision_ids=allowed,
        max_hops=3,
        path_limit=10,
    )
    assert len(paths) == 1
    assert paths[0].node_ids == ("A", "B", "C")
    assert paths[0].edge_ids == ("e-ab", "e-bc")

    no_paths = await find_paths(
        store,
        _SCOPE,
        1,
        (_NODE_A, _NODE_C),
        (),
        allowed_source_revision_ids=allowed,
        max_hops=1,
        path_limit=10,
    )
    assert no_paths == ()

    limited_paths = await find_paths(
        store,
        _SCOPE,
        1,
        (_NODE_A, _NODE_C),
        (_NODE_D,),
        allowed_source_revision_ids=allowed,
        max_hops=3,
        path_limit=1,
    )
    assert len(limited_paths) == 1


@pytest.mark.asyncio
async def test_find_paths_dedupes_unordered_seed_pairs() -> None:
    # (A, C) is both a query-seed pair and a query x evidence-seed pair here
    # (C also appears as an "evidence" seed): it must only consume one
    # `path_limit` slot, not two.
    store = _build_store()
    paths = await find_paths(
        store,
        _SCOPE,
        1,
        (_NODE_A, _NODE_C),
        (_NODE_C,),
        allowed_source_revision_ids=frozenset({"rev-1"}),
        max_hops=3,
        path_limit=10,
    )
    assert len(paths) == 1


def test_rank_edges_prefers_path_edges_then_confidence_times_adjacency() -> None:
    subgraph = ProjectSubgraph(
        version=1,
        nodes=(_NODE_A, _NODE_B, _NODE_C, _NODE_D),
        edges=(_EDGE_AB, _EDGE_BC, _EDGE_CD, _EDGE_AD),
    )
    path = RelationPath(
        node_ids=("A", "B", "C"),
        node_labels=("节点 A", "节点 B", "节点 C"),
        edge_ids=("e-ab", "e-bc"),
    )
    ranked = rank_edges(subgraph, (path,), frozenset({"A", "C"}))
    assert [edge.edge_id for edge, _on_path in ranked] == ["e-ab", "e-bc", "e-ad", "e-cd"]
    assert [on_path for _edge, on_path in ranked] == [True, True, False, False]


def test_assemble_reuses_s_label_without_snippet() -> None:
    nodes_by_id = {"A": _NODE_A, "B": _NODE_B, "C": _NODE_C}
    ranked = (
        (_EDGE_AB, True, (_evidence("e-ab", "chunk-1", "rev-1"),)),
        (_EDGE_BC, True, (_evidence("e-bc", "chunk-9", "rev-1"),)),
    )
    evidence = (
        EvidenceRef(
            label="S2", chunk_id="chunk-1", source_revision_id="rev-1", document_revision_id="doc-1"
        ),
    )
    snippets = {"chunk-9": "chunk nine content"}
    relations = assemble(
        ranked, nodes_by_id, evidence, snippets, allowed_source_revision_ids=frozenset({"rev-1"})
    )
    assert relations[0].support[0].evidence_label == "S2"
    assert relations[0].support[0].snippet is None
    assert relations[1].support[0].snippet == "chunk nine content"[:300]
    assert relations[1].support[0].evidence_label is None


def test_assemble_drops_edges_without_resolvable_support_and_numbers_r_labels() -> None:
    nodes_by_id = {"A": _NODE_A, "B": _NODE_B, "C": _NODE_C, "D": _NODE_D}
    ranked = (
        (_EDGE_AB, True, (_evidence("e-ab", "chunk-unresolvable", "rev-1"),)),
        (_EDGE_BC, True, (_evidence("e-bc", "chunk-9", "rev-1"),)),
        (_EDGE_CD, False, (_evidence("e-cd", "chunk-1", "rev-1"),)),
    )
    evidence = (
        EvidenceRef(
            label="S2", chunk_id="chunk-1", source_revision_id="rev-1", document_revision_id="doc-1"
        ),
    )
    snippets = {"chunk-9": "chunk nine content"}
    relations = assemble(
        ranked, nodes_by_id, evidence, snippets, allowed_source_revision_ids=frozenset({"rev-1"})
    )
    assert [relation.edge_id for relation in relations] == ["e-bc", "e-cd"]
    assert [relation.label for relation in relations] == ["R1", "R2"]


def test_rebind_moves_augmented_chunks_into_s_labels() -> None:
    augmented_support = RelationSupport(
        chunk_id="chunk-9",
        source_revision_id="rev-1",
        document_revision_id="doc-1",
        content_digest="digest-chunk-9",
        anchor={"page": 2},
        snippet="chunk nine content",
    )
    other_support = RelationSupport(
        chunk_id="chunk-10",
        source_revision_id="rev-1",
        document_revision_id="doc-1",
        content_digest="digest-chunk-10",
        anchor={"page": 3},
        snippet="chunk ten content",
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
        support=(augmented_support,),
        on_path=True,
    )
    context = RelationContext(
        status=RelationContextStatus.APPLIED,
        graph_version="gpv_1",
        relations=(relation,),
        augment_chunks=(augmented_support, other_support),
    )

    rebound = context.rebind(
        (
            EvidenceRef(
                label="S5",
                chunk_id="chunk-9",
                source_revision_id="rev-1",
                document_revision_id="doc-1",
            ),
        )
    )

    assert rebound.relations[0].support[0].evidence_label == "S5"
    assert rebound.relations[0].support[0].snippet is None
    assert [chunk.chunk_id for chunk in rebound.augment_chunks] == ["chunk-10"]


def test_snippet_chunk_refs_excludes_already_cited_chunks() -> None:
    ranked = (
        (_EDGE_AB, True, (_evidence("e-ab", "chunk-1", "rev-1"),)),
        (_EDGE_BC, True, (_evidence("e-bc", "chunk-9", "rev-1"),)),
    )
    evidence = (
        EvidenceRef(
            label="S2", chunk_id="chunk-1", source_revision_id="rev-1", document_revision_id="doc-1"
        ),
    )
    refs = snippet_chunk_refs(ranked, evidence, allowed_source_revision_ids=frozenset({"rev-1"}))
    assert refs == (("doc-1", "chunk-9"),)


def test_snippet_chunk_refs_excludes_unselected_revisions() -> None:
    ranked = (
        (_EDGE_AD, False, (_evidence("e-ad", "chunk-4", "rev-2"),)),
        (_EDGE_BC, True, (_evidence("e-bc", "chunk-9", "rev-1"),)),
    )
    refs = snippet_chunk_refs(ranked, (), allowed_source_revision_ids=frozenset({"rev-1"}))
    assert refs == (("doc-1", "chunk-9"),)


def test_snippet_chunk_refs_with_empty_selection_returns_no_refs() -> None:
    ranked = ((_EDGE_BC, True, (_evidence("e-bc", "chunk-9", "rev-1"),)),)
    refs = snippet_chunk_refs(ranked, (), allowed_source_revision_ids=frozenset())
    assert refs == ()


def test_assemble_drops_support_from_unselected_revision() -> None:
    nodes_by_id = {"A": _NODE_A, "D": _NODE_D}
    ranked = ((_EDGE_AD, False, (_evidence("e-ad", "chunk-4", "rev-2"),)),)
    relations = assemble(
        ranked,
        nodes_by_id,
        (),
        {"chunk-4": "chunk four content"},
        allowed_source_revision_ids=frozenset({"rev-1"}),
    )
    assert relations == ()


def test_assemble_stops_at_twenty_relations_instead_of_raising() -> None:
    nodes_by_id = {"A": _NODE_A, "B": _NODE_B}
    ranked = []
    snippets = {}
    for index in range(21):
        edge = ProjectEdge(
            edge_id=f"e-many-{index}",
            source_node_id="A",
            target_node_id="B",
            relation_type="REQUIRES",
            relation_label="REQUIRES",
            origin=RelationOrigin.EXTRACTED,
            confidence=0.5,
        )
        chunk_id = f"chunk-many-{index}"
        snippets[chunk_id] = f"content {index}"
        ranked.append((edge, False, (_evidence(edge.edge_id, chunk_id, "rev-1"),)))

    relations = assemble(
        tuple(ranked),
        nodes_by_id,
        (),
        snippets,
        allowed_source_revision_ids=frozenset({"rev-1"}),
    )
    assert len(relations) == 20
    assert relations[-1].label == "R20"


def test_relation_support_rejects_empty_string_snippet_alongside_evidence_label() -> None:
    with pytest.raises(ValueError):
        RelationSupport(
            chunk_id="chunk-1",
            source_revision_id="rev-1",
            document_revision_id="doc-1",
            content_digest="d1",
            anchor={},
            evidence_label="S1",
            snippet="",
        )
