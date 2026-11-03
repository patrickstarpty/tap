"""Graph query latency benchmark: 10,000 nodes, 50,000 edges (run via `make graph-bench`)."""

from __future__ import annotations

import random
import statistics
import time
from collections.abc import Awaitable, Callable

import pytest

from tap.modules.graph.application.cache import CachedSnapshotIndexes
from tap.modules.graph.domain.index import SnapshotIndex
from tap.modules.graph.domain.models import (
    GraphSearchQuery,
    GraphSubgraph,
    NeighborQuery,
    PathQuery,
)
from tests.perf.graph_fixture import PROJECT_ID, SNAPSHOT_ID, synthetic_draft

pytestmark = pytest.mark.perf

RUNS = 200
P95_BUDGET_SECONDS = 0.3


def _percentiles(samples: list[float]) -> tuple[float, float]:
    cuts = statistics.quantiles(samples, n=100, method="inclusive")
    return cuts[49], cuts[94]


async def _measure(
    name: str, queries: list[Callable[[], Awaitable[GraphSubgraph]]]
) -> tuple[float, float]:
    samples = []
    for query in queries:
        started = time.perf_counter()
        await query()
        samples.append(time.perf_counter() - started)
    p50, p95 = _percentiles(samples)
    print(
        f"graph-bench {name:<10} runs={len(samples)} p50={p50 * 1000:.2f}ms p95={p95 * 1000:.2f}ms"
    )
    return p50, p95


@pytest.mark.asyncio
async def test_graph_queries_meet_p95_budget_at_ten_thousand_nodes() -> None:
    draft = synthetic_draft()
    started = time.perf_counter()
    built = SnapshotIndex.build(draft)
    print(
        f"\ngraph-bench build      nodes={len(built.nodes)} edges={len(built.edges)} "
        f"entries={built.size} seconds={time.perf_counter() - started:.3f}"
    )

    cache = CachedSnapshotIndexes()
    key = (PROJECT_ID, SNAPSHOT_ID)

    async def loader() -> SnapshotIndex:
        return built

    await cache.get_or_load(key, loader)

    async def index() -> SnapshotIndex:
        return await cache.get_or_load(key, loader)

    rng = random.Random(42)
    node_ids = list(built.nodes)
    labels = [node.label for node in built.nodes.values()]

    def fragment() -> str:
        label = rng.choice(labels)
        start = rng.randint(0, max(0, len(label) - 3))
        return label[start : start + rng.randint(3, 10)].strip() or label

    async def search(text: str) -> GraphSubgraph:
        return (await index()).search(GraphSearchQuery(SNAPSHOT_ID, text, node_limit=50))

    async def neighbors(node_id: str) -> GraphSubgraph:
        return (await index()).neighbors(
            NeighborQuery(SNAPSHOT_ID, node_id, depth=2, node_limit=200)
        )

    found = 0

    async def path(source: str, target: str) -> GraphSubgraph:
        nonlocal found
        result = (await index()).bounded_path(
            PathQuery(SNAPSHOT_ID, source, target, node_limit=500)
        )
        found += bool(result.nodes)
        return result

    searches = [fragment() for _ in range(RUNS)]
    starts = [rng.choice(node_ids) for _ in range(RUNS)]

    def walk(source: str) -> str:
        # PathQuery bounds by node budget, not hops; targets 1-4 hops away keep pairs reachable.
        current = source
        for _ in range(rng.randint(1, 4)):
            edge = built.edges[rng.choice(built.adjacency[current])]
            current = edge.target_node_id if edge.source_node_id == current else edge.source_node_id
        return current

    connected = [node for node in node_ids if built.degree[node]]
    sources = [rng.choice(connected) for _ in range(RUNS)]
    pairs = [
        (source, walk(source) if index % 2 == 0 else rng.choice(node_ids))
        for index, source in enumerate(sources)
    ]
    results = {
        "search": await _measure("search", [lambda text=text: search(text) for text in searches]),
        "neighbors": await _measure(
            "neighbors", [lambda node=node: neighbors(node) for node in starts]
        ),
        "path": await _measure("path", [lambda pair=pair: path(*pair) for pair in pairs]),
    }
    print(f"graph-bench path       reachable={found}/{RUNS}")
    stats = cache.stats()
    assert stats["misses"] == 1 and stats["hits"] == 3 * RUNS
    over_budget = {name: p95 for name, (_, p95) in results.items() if p95 >= P95_BUDGET_SECONDS}
    assert not over_budget, over_budget
