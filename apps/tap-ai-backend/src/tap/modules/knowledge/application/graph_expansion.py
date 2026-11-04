"""Deterministic retrieval-seeded expansion of an immutable authorized snapshot."""

from tap.modules.graph.domain.index import SnapshotIndex
from tap.modules.graph.domain.models import GraphSearchQuery, GraphSubgraph
from tap.platform.telemetry import span


class GraphContextExpander:
    def expand(
        self, index: SnapshotIndex, chunk_ids: tuple[str, ...], query: str, *, hops: int = 1
    ) -> GraphSubgraph:
        if hops not in (1, 2):
            raise ValueError("graph expansion requires one or two hops")
        seeds = {
            node.node_id
            for node in index.search(GraphSearchQuery(index.snapshot.snapshot_id, query, 5)).nodes
        }
        for chunk in chunk_ids:
            for identity in index.chunk_facts.get(chunk, ()):
                if identity in index.nodes:
                    seeds.add(identity)
                elif identity in index.edges:
                    edge = index.edges[identity]
                    seeds.update((edge.source_node_id, edge.target_node_id))

        def order(identity):
            return (index.degree[identity], identity)

        nodes = set(sorted(seeds, key=order)[:40])
        frontier = set(nodes)
        candidates: set[str] = set()
        truncated = len(seeds) > 40
        for _ in range(hops):
            following: set[str] = set()
            for identity in sorted(frontier, key=order):
                for edge_id in sorted(
                    index.adjacency[identity], key=lambda key: (-index.edges[key].confidence, key)
                ):
                    edge = index.edges[edge_id]
                    other = (
                        edge.target_node_id
                        if edge.source_node_id == identity
                        else edge.source_node_id
                    )
                    if other not in nodes:
                        if len(nodes) >= 40:
                            truncated = True
                            continue
                        nodes.add(other)
                        following.add(other)
                    candidates.add(edge_id)
            frontier = following
        edge_ids = sorted(candidates, key=lambda key: (-index.edges[key].confidence, key))
        truncated |= len(edge_ids) > 80
        graph = index._subgraph(nodes, set(edge_ids[:80]))
        graph = GraphSubgraph(
            graph.snapshot_id,
            graph.nodes,
            tuple(index.edges[key] for key in edge_ids[:80]),
            graph.evidence,
        )
        with span("graph.expand") as current:
            for name, value in {
                "seed_count": len(seeds),
                "hops": hops,
                "node_count": len(graph.nodes),
                "edge_count": len(graph.edges),
                "truncated": truncated,
            }.items():
                current.set_attribute(f"tap.graph.{name}", value)
        return graph
