"""Deterministic synthetic project-graph generator and p95 helper for the
`graph-bench` performance gate (spec 4.4): a seeded generator writes
`node_count` nodes and `edge_count` edges (80% intra-community, 20%
cross-community, no self loops, deduplicated `(source, target,
relation_type)` triples) plus one `node_source` per node, one
`edge_evidence` per edge, one alias per 10 nodes, and up to `community_count`
communities; `p95_ms` is the >=200-sample order statistic `graph-bench run`
uses to gate neighbors/path/overview query latency against `P95_LIMIT_MS`.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from tap.modules.graph.domain.vocabulary import NODE_TYPES, RELATION_TYPES, normalize_key
from tap.quality.evidence import canonical_digest

__all__ = [
    "BENCH_KINDS",
    "LOAD_MS_LIMIT_MS",
    "NODE_TYPES",
    "P95_LIMIT_MS",
    "RELATION_TYPES",
    "BenchGraph",
    "digest",
    "p95_ms",
    "synthesize_graph",
]

BENCH_KINDS: tuple[str, ...] = ("neighbors", "path", "overview")
P95_LIMIT_MS = 300.0
# Spec 1.3: "1万节点、5万边装载一次应在1秒内完成" — a one-time cache-fill load, not a
# per-query budget (that's `P95_LIMIT_MS`).
LOAD_MS_LIMIT_MS = 1000.0

_NODE_TYPES_ORDERED: tuple[str, ...] = tuple(sorted(NODE_TYPES))
_RELATION_TYPES_ORDERED: tuple[str, ...] = tuple(sorted(RELATION_TYPES))


@dataclass(frozen=True)
class BenchGraph:
    version: str
    nodes: tuple[dict[str, object], ...]
    edges: tuple[dict[str, object], ...]
    node_sources: tuple[dict[str, object], ...]
    edge_evidence: tuple[dict[str, object], ...]
    aliases: tuple[dict[str, object], ...]
    communities: tuple[dict[str, object], ...]


def _community_id(index: int) -> str:
    return f"bc_{index:03d}"


def synthesize_graph(
    *,
    seed: int,
    node_count: int = 10_000,
    edge_count: int = 50_000,
    community_count: int = 50,
    source_count: int = 40,
) -> BenchGraph:
    """Deterministically synthesize one `BenchGraph` for `seed`.

    A rejected draw (a `(source, target, relation_type)` collision, a
    same-node self-loop, or too-small a community pool) still consumes `rng`
    like any other draw — only the relation-type assignment is exempt: it
    cycles by *accepted*-edge index, not by draw/attempt count, so it does
    not shift when a retry happens to occur. The resulting edge set is
    therefore reproducible for a given `seed` regardless of how many
    collisions were skipped along the way.
    """
    if node_count <= 0 or edge_count < 0 or community_count <= 0 or source_count <= 0:
        raise ValueError("graph_bench synthesize_graph requires positive sizes")

    rng = random.Random(seed)
    node_ids = [f"bn_{index:06d}" for index in range(node_count)]
    community_of = [index % community_count for index in range(node_count)]
    members_by_community: dict[int, list[int]] = {}
    for index, community in enumerate(community_of):
        members_by_community.setdefault(community, []).append(index)

    degree = {node_id: 0 for node_id in node_ids}
    edges: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    max_attempts = max(edge_count * 50, 10_000)
    attempts = 0
    while len(edges) < edge_count:
        attempts += 1
        if attempts > max_attempts:
            raise ValueError(
                "graph_bench synthesize_graph could not reach the requested edge_count"
            )
        if rng.random() < 0.8:
            pool = members_by_community.get(rng.randrange(community_count), [])
            if len(pool) < 2:
                continue
            source_index, target_index = rng.sample(pool, 2)
        else:
            source_index = rng.randrange(node_count)
            target_index = rng.randrange(node_count)
            if source_index == target_index:
                continue
        source_id = node_ids[source_index]
        target_id = node_ids[target_index]
        relation_type = _RELATION_TYPES_ORDERED[len(edges) % len(_RELATION_TYPES_ORDERED)]
        key = (source_id, target_id, relation_type)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            {
                "edge_id": f"be_{len(edges):06d}",
                "source_node_id": source_id,
                "target_node_id": target_id,
                "relation_type": relation_type,
                "relation_label": relation_type,
                "origin": "EXTRACTED",
                "confidence": 1.0,
            }
        )
        degree[source_id] += 1
        degree[target_id] += 1

    nodes: list[dict[str, object]] = []
    for index, node_id in enumerate(node_ids):
        label = f"实体{index}"
        nodes.append(
            {
                "node_id": node_id,
                "label": label,
                "node_type": _NODE_TYPES_ORDERED[index % len(_NODE_TYPES_ORDERED)],
                "canonical_key": normalize_key(label),
                "degree": degree[node_id],
                "community_id": _community_id(community_of[index]),
                "aliases": [],
            }
        )

    node_sources: list[dict[str, object]] = []
    for index, node_id in enumerate(node_ids):
        source_index = index % source_count
        node_sources.append(
            {
                "node_id": node_id,
                "source_revision_id": f"bench-source-{source_index:03d}",
                "document_revision_id": f"bench-doc-{source_index:03d}",
                "chunk_id": f"bench-chunk-{index}",
                "anchor_json": {"start": 0, "end": 0},
                "fragment_snapshot_id": f"bench-snapshot-{source_index:03d}",
                "fragment_node_id": f"bench-fnode-{index:06d}",
            }
        )

    edge_evidence: list[dict[str, object]] = []
    for index, edge in enumerate(edges):
        source_index = index % source_count
        edge_evidence.append(
            {
                "edge_id": edge["edge_id"],
                "source_revision_id": f"bench-source-{source_index:03d}",
                "document_revision_id": f"bench-doc-{source_index:03d}",
                "chunk_id": f"bench-chunk-{index}",
                "anchor_json": {"start": 0, "end": 0},
                "content_digest": canonical_digest(edge),
                "fragment_snapshot_id": f"bench-snapshot-{source_index:03d}",
                "fragment_edge_id": f"bench-fedge-{index:06d}",
            }
        )

    aliases: list[dict[str, object]] = []
    for index in range(0, node_count, 10):
        alias_label = f"别名{index}"
        aliases.append(
            {
                "alias_norm": normalize_key(alias_label),
                "node_id": node_ids[index],
                "origin": "MODEL",
            }
        )

    community_sizes: dict[int, int] = {}
    for community in community_of:
        community_sizes[community] = community_sizes.get(community, 0) + 1
    communities = [
        {
            "community_id": _community_id(community),
            "label": f"社区{community}",
            "size": community_sizes[community],
        }
        for community in sorted(community_sizes)
    ]

    return BenchGraph(
        version=f"bench-{seed}",
        nodes=tuple(nodes),
        edges=tuple(edges),
        node_sources=tuple(node_sources),
        edge_evidence=tuple(edge_evidence),
        aliases=tuple(aliases),
        communities=tuple(communities),
    )


def digest(graph: BenchGraph) -> str:
    """`canonical_digest` over every `BenchGraph` field, for the
    determinism check in `test_synthesize_is_deterministic_and_sized`."""
    return canonical_digest(
        {
            "version": graph.version,
            "nodes": list(graph.nodes),
            "edges": list(graph.edges),
            "node_sources": list(graph.node_sources),
            "edge_evidence": list(graph.edge_evidence),
            "aliases": list(graph.aliases),
            "communities": list(graph.communities),
        }
    )


def p95_ms(samples: Sequence[float]) -> float:
    """The p95 order statistic over `samples`: `sorted(samples)[ceil(0.95 *
    len(samples)) - 1]`. Requires at least 200 samples so a single slow
    outlier cannot be smoothed away by a small sample size."""
    if len(samples) < 200:
        raise ValueError("p95 requires at least 200 samples")
    ordered = sorted(samples)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]
