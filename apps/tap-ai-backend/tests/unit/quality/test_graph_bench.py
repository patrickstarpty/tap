"""Unit tests for the synthetic project graph benchmark generator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tap.quality.graph_bench import (
    BENCH_KINDS,
    LOAD_MS_LIMIT_MS,
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
    assert LOAD_MS_LIMIT_MS == 1000.0
    graph = synthesize_graph(seed=11, node_count=300, edge_count=900, community_count=10)
    assert len(graph.node_sources) == 300
    assert len(graph.edge_evidence) == 900
    assert len(graph.aliases) == 300 // 10
    assert graph.version == "bench-11"
    edge_keys = {
        (e["source_node_id"], e["target_node_id"], e["relation_type"]) for e in graph.edges
    }
    assert len(edge_keys) == len(graph.edges)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[5]


def _load_graph_bench_cli_module() -> ModuleType:
    path = _REPOSITORY_ROOT / "scripts" / "graph-bench.py"
    spec = importlib.util.spec_from_file_location("graph_bench_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(_REPOSITORY_ROOT))
    spec.loader.exec_module(module)
    return module


def test_cli_refuses_the_tapper_demo_validation_scope() -> None:
    """`scripts/graph-bench.py` must never be able to target `tapper-demo`:
    a bench graph published there would become the demo's current graph
    version, be picked up by a real project-merge job, and be pruned/left
    behind with nothing to clean it up."""
    module = _load_graph_bench_cli_module()
    assert module.BENCH_SCOPE.project_id != module.VALIDATION_SCOPE.project_id
    with pytest.raises(ValueError, match="Tapper Demo"):
        module._require_non_demo_scope(module.VALIDATION_SCOPE)
