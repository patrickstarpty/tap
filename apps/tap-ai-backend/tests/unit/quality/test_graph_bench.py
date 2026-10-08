"""Unit tests for the synthetic project graph benchmark generator."""

from __future__ import annotations

import pytest

from tap.quality.graph_bench import (
    BENCH_KINDS,
    P95_LIMIT_MS,
    RELATION_TYPES,
    digest,
    p95_ms,
    synthesize_graph,
)


def test_synthesize_is_deterministic_and_sized() -> None:
    a, b = (
        synthesize_graph(seed=7, node_count=500, edge_count=2000),
        synthesize_graph(seed=7, node_count=500, edge_count=2000),
    )
    assert digest(a) == digest(b) and digest(a) != digest(
        synthesize_graph(seed=8, node_count=500, edge_count=2000)
    )
    assert len(a.nodes) == 500 and len(a.edges) == 2000 and len(a.communities) <= 50
    ids = {n["node_id"] for n in a.nodes}
    assert all(
        e["source_node_id"] in ids
        and e["target_node_id"] in ids
        and e["source_node_id"] != e["target_node_id"]
        for e in a.edges
    )
    assert {e["relation_type"] for e in a.edges} <= RELATION_TYPES


def test_p95_uses_order_statistic_and_requires_200_samples() -> None:
    samples = [1.0] * 199 + [1000.0]
    assert p95_ms(samples) == 1.0  # mean would be ~6.0; a mean-based implementation fails this
    with pytest.raises(ValueError, match="200"):
        p95_ms(samples[:199])


def test_bench_constants_and_row_cardinality() -> None:
    assert BENCH_KINDS == ("neighbors", "path", "overview")
    assert P95_LIMIT_MS == 300.0
    graph = synthesize_graph(seed=11, node_count=300, edge_count=900, community_count=10)
    assert len(graph.node_sources) == 300
    assert len(graph.edge_evidence) == 900
    assert len(graph.aliases) == 300 // 10
    assert graph.version == "bench-11"
    edge_keys = {
        (e["source_node_id"], e["target_node_id"], e["relation_type"]) for e in graph.edges
    }
    assert len(edge_keys) == len(graph.edges)
