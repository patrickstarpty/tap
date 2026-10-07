from __future__ import annotations

import time
from datetime import datetime

from tap.modules.graph.application.project_queries import LoadedProjectGraph, ProjectGraphCache
from tap.modules.graph.domain.models import RelationOrigin
from tap.modules.graph.domain.project import (
    Community,
    EdgeEvidence,
    NodeSource,
    ProjectEdge,
    ProjectGraphDraft,
    ProjectGraphVersion,
    ProjectNode,
)

_ANCHOR = {"kind": "text", "start": 0, "end": 1}


def _version(version: int, project_id: str = "tapper-demo") -> ProjectGraphVersion:
    return ProjectGraphVersion(
        project_id=project_id,
        version=version,
        status="READY",
        fragment_digest="sha256:" + "a" * 64,
        node_count=0,
        edge_count=0,
        merged_at=datetime(2026, 10, 7, 9, 0, 0),
    )


def _loaded(version: int, project_id: str = "tapper-demo") -> LoadedProjectGraph:
    return LoadedProjectGraph.from_rows(
        _version(version, project_id),
        nodes=(),
        edges=(),
        node_sources=(),
        edge_evidence=(),
        aliases=(),
        communities=(),
    )


def _chain(count: int) -> tuple[tuple[ProjectNode, ...], tuple[ProjectEdge, ...]]:
    nodes = tuple(
        ProjectNode(node_id=f"n{i}", label=f"n{i}", node_type="ENTITY", canonical_key=f"n{i}")
        for i in range(count)
    )
    edges = tuple(
        ProjectEdge(
            edge_id=f"e{i}",
            source_node_id=f"n{i}",
            target_node_id=f"n{i + 1}",
            relation_type="RELATED_TO",
            relation_label="relates",
            origin=RelationOrigin.EXTRACTED,
            confidence=1.0,
        )
        for i in range(count - 1)
    )
    return nodes, edges


def _synthetic_draft(*, nodes: int, edges: int) -> ProjectGraphDraft:
    node_objs = tuple(
        ProjectNode(node_id=f"n{i}", label=f"n{i}", node_type="ENTITY", canonical_key=f"n{i}")
        for i in range(nodes)
    )
    edge_objs = tuple(
        ProjectEdge(
            edge_id=f"e{i}",
            source_node_id=f"n{i % nodes}",
            target_node_id=f"n{(i * 7 + 1) % nodes}",
            relation_type="RELATED_TO",
            relation_label="relates",
            origin=RelationOrigin.INFERRED,
            confidence=1.0,
        )
        for i in range(edges)
    )
    return ProjectGraphDraft(
        fragment_digest="sha256:" + "a" * 64,
        nodes=node_objs,
        edges=edge_objs,
        node_sources=(),
        edge_evidence=(),
        aliases=(),
        communities=(),
        merge_log=(),
    )


def test_cache_keeps_two_versions_per_project_and_invalidates() -> None:
    cache = ProjectGraphCache(keep_per_project=2)
    for version in (1, 2, 3):
        cache.put(_loaded(version))
    assert cache.loaded_versions("tapper-demo") == (2, 3)
    assert cache.get("tapper-demo", 1) is None
    cache.invalidate("tapper-demo")
    assert cache.loaded_versions("tapper-demo") == ()


def test_overview_rotates_across_communities_by_degree() -> None:
    nodes = tuple(
        ProjectNode(
            node_id=f"c1-{i}",
            label=f"c1-{i}",
            node_type="ENTITY",
            canonical_key=f"c1-{i}",
            degree=i,
            community_id="community-1",
        )
        for i in range(5)
    ) + tuple(
        ProjectNode(
            node_id=f"c2-{i}",
            label=f"c2-{i}",
            node_type="ENTITY",
            canonical_key=f"c2-{i}",
            degree=i,
            community_id="community-2",
        )
        for i in range(5)
    )
    communities = (
        Community("community-1", "Community 1", 5),
        Community("community-2", "Community 2", 5),
    )
    loaded = LoadedProjectGraph.from_rows(
        _version(1),
        nodes=nodes,
        edges=(),
        node_sources=(),
        edge_evidence=(),
        aliases=(),
        communities=communities,
    )
    subgraph = loaded.overview(node_limit=4)
    by_community: dict[str | None, list[str]] = {}
    for node in subgraph.nodes:
        by_community.setdefault(node.community_id, []).append(node.node_id)
    assert {key: len(value) for key, value in by_community.items()} == {
        "community-1": 2,
        "community-2": 2,
    }
    assert set(by_community["community-1"]) == {"c1-4", "c1-3"}
    assert set(by_community["community-2"]) == {"c2-4", "c2-3"}


def test_path_respects_max_hops() -> None:
    nodes, edges = _chain(5)
    too_far = LoadedProjectGraph.from_rows(
        _version(1),
        nodes=nodes,
        edges=edges,
        node_sources=(),
        edge_evidence=(),
        aliases=(),
        communities=(),
    )
    assert too_far.path("n0", "n4", max_hops=3) == too_far.path("n0", "n4", max_hops=3)
    assert too_far.path("n0", "n4", max_hops=3).nodes == ()
    assert too_far.path("n0", "n4", max_hops=3).edges == ()

    nodes3, edges3 = _chain(4)
    reachable = LoadedProjectGraph.from_rows(
        _version(1),
        nodes=nodes3,
        edges=edges3,
        node_sources=(),
        edge_evidence=(),
        aliases=(),
        communities=(),
    )
    subgraph = reachable.path("n0", "n3", max_hops=3)
    assert [node.node_id for node in subgraph.nodes] == ["n0", "n1", "n2", "n3"]
    assert len(subgraph.edges) == 3


def test_highlight_returns_edges_endpoints_and_one_hop_context() -> None:
    nodes = tuple(
        ProjectNode(node_id=name, label=name, node_type="ENTITY", canonical_key=name)
        for name in ("A", "B", "C", "D")
    )
    edges = (
        ProjectEdge("e1", "A", "B", "RELATED_TO", "relates", RelationOrigin.EXTRACTED, 1.0),
        ProjectEdge("e2", "B", "C", "RELATED_TO", "relates", RelationOrigin.EXTRACTED, 1.0),
        ProjectEdge("e3", "A", "D", "RELATED_TO", "relates", RelationOrigin.EXTRACTED, 1.0),
    )
    loaded = LoadedProjectGraph.from_rows(
        _version(1),
        nodes=nodes,
        edges=edges,
        node_sources=(),
        edge_evidence=(),
        aliases=(),
        communities=(),
    )
    subgraph = loaded.highlight(("e1",))
    assert {edge.edge_id for edge in subgraph.edges} == {"e1", "e2", "e3"}
    assert {node.node_id for node in subgraph.nodes} == {"A", "B", "C", "D"}


def test_nodes_for_chunks_uses_node_sources_and_edge_evidence() -> None:
    nodes = tuple(
        ProjectNode(node_id=name, label=name, node_type="ENTITY", canonical_key=name)
        for name in ("A", "B", "C")
    )
    edges = (ProjectEdge("e1", "B", "C", "RELATED_TO", "relates", RelationOrigin.EXTRACTED, 1.0),)
    node_sources = (NodeSource("A", "rev-1", "doc-1", "chunk-1", _ANCHOR, "fs-1", "fn-1"),)
    edge_evidence = (
        EdgeEvidence(
            "e1", "rev-1", "doc-1", "chunk-2", _ANCHOR, "sha256:" + "b" * 64, "fs-1", "fe-1"
        ),
    )
    loaded = LoadedProjectGraph.from_rows(
        _version(1),
        nodes=nodes,
        edges=edges,
        node_sources=node_sources,
        edge_evidence=edge_evidence,
        aliases=(),
        communities=(),
    )
    assert loaded.nodes_for_chunks(("chunk-1", "chunk-2")) == ("A", "B", "C")


def test_loading_ten_thousand_nodes_takes_under_one_second() -> None:
    draft = _synthetic_draft(nodes=10_000, edges=50_000)
    started = time.perf_counter()
    LoadedProjectGraph.from_draft(_version(1), draft)
    assert time.perf_counter() - started < 1.0
