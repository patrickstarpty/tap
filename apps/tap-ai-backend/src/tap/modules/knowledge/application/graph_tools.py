"""Read-only graph tools bound to the immutable snapshot authorized for one Turn."""

import asyncio
import json
from collections import deque

from tap.modules.ai.application.tool_registry import graph_builtin_tools
from tap.modules.ai.domain.tools import ToolResult
from tap.modules.graph.domain.models import NeighborQuery
from tap.modules.knowledge.application.graph_enrichment import _evidence_locator

RELATION_TOOLS = frozenset({"graph.neighbors", "graph.path", "knowledge.search"})


class GraphTurnTools:
    def __init__(self, scope, index, facts, *, search=None, authorize=None, authorize_graph=None):
        self.scope = scope
        self.index = index
        self.facts = facts
        self.search = search
        self.authorize = authorize
        self.authorize_graph = authorize_graph
        self.lock = asyncio.Lock()

    def builtins(self):
        return {
            identity: (descriptor, _GraphTool(self, identity))
            for identity, (descriptor, _) in graph_builtin_tools().items()
        }

    def node_id(self, value):
        if value in self.index.nodes:
            return value
        matches = [
            node.node_id
            for node in self.index.nodes.values()
            if node.label.casefold() == value.casefold()
        ]
        if len(matches) != 1:
            raise ValueError("node label must identify one node")
        return matches[0]

    def path(self, source, target, hops):
        queue = deque([(source, ())])
        seen = {source}
        while queue and len(seen) <= 500:
            current, path = queue.popleft()
            if current == target:
                return path
            if len(path) >= hops:
                continue
            for edge_id in sorted(self.index.adjacency[current]):
                edge = self.index.edges[edge_id]
                other = (
                    edge.target_node_id if edge.source_node_id == current else edge.source_node_id
                )
                if other not in seen:
                    seen.add(other)
                    queue.append((other, (*path, edge_id)))
        return ()

    async def resolve(self, edge_ids):
        known = {fact["id"]: fact for fact in self.facts if fact.get("kind") == "edge"}
        records = []
        additions = []
        evidence = []
        for edge_id in edge_ids:
            edge = self.index.edges[edge_id]
            if edge_id not in known:
                record = {
                    "kind": "edge",
                    "id": edge_id,
                    "snapshotId": self.index.snapshot.snapshot_id,
                    "relationLabel": f"R{len(known) + 1}",
                    "sourceNodeId": edge.source_node_id,
                    "targetNodeId": edge.target_node_id,
                    "sourceLabel": self.index.nodes[edge.source_node_id].label,
                    "targetLabel": self.index.nodes[edge.target_node_id].label,
                    "relationType": edge.relation_type,
                    "origin": edge.origin.value,
                    "confidence": edge.confidence,
                    "evidence": [
                        _evidence_locator(self.index.evidence[key]) for key in edge.evidence_ids
                    ],
                    "evidenceLabels": [],
                }
                additions.append(record)
                known[edge_id] = record
            records.append(known[edge_id])
        if self.search is not None and records:
            # Reuse authorized retrieval, never hydrate graph locators directly into citations.
            chunks = {item["chunkId"] for record in records for item in record["evidence"]}
            query = " ".join(
                dict.fromkeys(
                    str(record[key]) for record in records for key in ("sourceLabel", "targetLabel")
                )
            )[:500]
            evidence = [item for item in await self.search(query, 8) if item.chunk_id in chunks]
        if self.authorize_graph is not None:
            await self.authorize_graph(tuple(edge_ids))
        self.facts.extend(additions)
        return ToolResult(
            json.dumps({"relations": records}, ensure_ascii=False), evidence=tuple(evidence)
        )


class _GraphTool:
    def __init__(self, owner, identity):
        self.owner = owner
        self.identity = identity

    async def invoke(self, scope, arguments):
        from tap.modules.ai.application.tool_loop import validate_tool_arguments

        owner = self.owner
        if scope != owner.scope:
            return ToolResult("tool-scope-mismatch", is_error=True)
        descriptor = owner.builtins()[self.identity][0]
        if not validate_tool_arguments(descriptor.input_schema, arguments):
            return ToolResult("tool-arguments-invalid", is_error=True)
        async with owner.lock:
            if owner.authorize is not None:
                await owner.authorize()
            try:
                if self.identity == "graph.path":
                    source, target = (
                        owner.node_id(arguments["from"]),
                        owner.node_id(arguments["to"]),
                    )
                    edges = owner.path(source, target, arguments.get("maxHops", 4))
                else:
                    value = arguments.get("nodeId", arguments.get("nodeLabel"))
                    if not isinstance(value, str) or (
                        "nodeId" in arguments and "nodeLabel" in arguments
                    ):
                        raise ValueError("one node reference required")
                    node = owner.node_id(value)
                    graph = owner.index.neighbors(
                        NeighborQuery(
                            owner.index.snapshot.snapshot_id, node, arguments.get("depth", 1), 40
                        )
                    )
                    edges = tuple(item.edge_id for item in graph.edges[:80])
                if owner.authorize_graph is not None:
                    await owner.authorize_graph(tuple(edges))
                result = await owner.resolve(edges)
                if owner.authorize is not None:
                    await owner.authorize()
                return result
            except Exception:
                return ToolResult("graph-fact-unavailable", is_error=True)
