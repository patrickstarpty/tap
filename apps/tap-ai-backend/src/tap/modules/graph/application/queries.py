"""Reference in-memory graph store and bounded traversal semantics."""

from __future__ import annotations

from collections import deque
from dataclasses import replace

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.domain.models import (
    Evidence,
    GraphSearchQuery,
    GraphSnapshot,
    GraphSnapshotDraft,
    GraphSubgraph,
    NeighborQuery,
    PathQuery,
    source_set_digest,
)
from tap.modules.graph.ports.store import GraphFactNotFound
from tap.platform.db.project_scope import require_project_scope


class InMemoryGraphStore:
    def __init__(self) -> None:
        self._drafts: dict[tuple[str, str], GraphSnapshotDraft] = {}
        self._active: dict[tuple[str, str], str] = {}

    async def publish(self, scope: ProjectScopeContext, draft: GraphSnapshotDraft) -> GraphSnapshot:
        scope = require_project_scope(scope)
        if draft.snapshot.project_id != scope.project_id:
            raise ValueError("graph snapshot is outside Project scope")
        key = (scope.project_id, draft.snapshot.snapshot_id)
        if key in self._drafts:
            if self._drafts[key] != draft:
                raise ValueError("immutable graph snapshot conflict")
            return self._drafts[key].snapshot
        ready = replace(draft.snapshot, status="READY")
        persisted = replace(draft, snapshot=ready)
        self._drafts[key] = persisted
        self._active[(scope.project_id, ready.source_set_digest)] = ready.snapshot_id
        return ready

    async def active_snapshot(
        self, scope: ProjectScopeContext, source_ids: tuple[str, ...]
    ) -> GraphSnapshot | None:
        scope = require_project_scope(scope)
        identity = self._active.get((scope.project_id, source_set_digest(source_ids)))
        return None if identity is None else self._drafts[(scope.project_id, identity)].snapshot

    async def get_snapshot(
        self, scope: ProjectScopeContext, snapshot_id: str
    ) -> GraphSnapshot | None:
        scope = require_project_scope(scope)
        draft = self._drafts.get((scope.project_id, snapshot_id))
        return None if draft is None else draft.snapshot

    def _draft(self, scope: ProjectScopeContext, snapshot_id: str) -> GraphSnapshotDraft:
        scope = require_project_scope(scope)
        try:
            return self._drafts[(scope.project_id, snapshot_id)]
        except KeyError as error:
            raise GraphFactNotFound("graph snapshot not found") from error

    def _subgraph(
        self,
        draft: GraphSnapshotDraft,
        node_ids: set[str],
        edge_ids: set[str] | None = None,
    ) -> GraphSubgraph:
        nodes = tuple(node for node in draft.nodes if node.node_id in node_ids)
        edges = tuple(
            edge
            for edge in draft.edges
            if edge.source_node_id in node_ids
            and edge.target_node_id in node_ids
            and (edge_ids is None or edge.edge_id in edge_ids)
        )
        evidence_ids = {evidence_id for node in nodes for evidence_id in node.evidence_ids} | {
            evidence_id for edge in edges for evidence_id in edge.evidence_ids
        }
        evidence = tuple(item for item in draft.evidence if item.evidence_id in evidence_ids)
        return GraphSubgraph(draft.snapshot.snapshot_id, nodes, edges, evidence)

    async def node_detail(
        self, scope: ProjectScopeContext, snapshot_id: str, node_id: str
    ) -> GraphSubgraph:
        draft = self._draft(scope, snapshot_id)
        if node_id not in {node.node_id for node in draft.nodes}:
            raise GraphFactNotFound("graph node not found")
        return self._subgraph(draft, {node_id}, set())

    async def evidence(
        self, scope: ProjectScopeContext, snapshot_id: str, evidence_id: str
    ) -> Evidence:
        draft = self._draft(scope, snapshot_id)
        try:
            return next(item for item in draft.evidence if item.evidence_id == evidence_id)
        except StopIteration as error:
            raise GraphFactNotFound("graph evidence not found") from error

    async def search(self, scope: ProjectScopeContext, query: GraphSearchQuery) -> GraphSubgraph:
        draft = self._draft(scope, query.snapshot_id)
        needle = query.text.casefold()
        nodes = tuple(
            node
            for node in draft.nodes
            if query.text == "*"
            or needle in node.label.casefold()
            or needle in node.canonical_key.casefold()
        )[: query.node_limit]
        identities = {node.node_id for node in nodes}
        return self._subgraph(draft, identities)

    async def neighbors(self, scope: ProjectScopeContext, query: NeighborQuery) -> GraphSubgraph:
        draft = self._draft(scope, query.snapshot_id)
        seen = {query.node_id}
        frontier = {query.node_id}
        for _ in range(query.depth):
            adjacent: set[str] = set()
            for edge in draft.edges:
                if edge.source_node_id in frontier:
                    adjacent.add(edge.target_node_id)
                if edge.target_node_id in frontier:
                    adjacent.add(edge.source_node_id)
            frontier = adjacent - seen
            for identity in sorted(frontier):
                if len(seen) >= query.node_limit:
                    break
                seen.add(identity)
        return self._subgraph(draft, seen)

    async def bounded_path(self, scope: ProjectScopeContext, query: PathQuery) -> GraphSubgraph:
        draft = self._draft(scope, query.snapshot_id)
        previous: dict[str, tuple[str, str] | None] = {query.source_node_id: None}
        queue = deque([query.source_node_id])
        while queue and query.target_node_id not in previous and len(previous) < query.node_limit:
            current = queue.popleft()
            for edge in draft.edges:
                neighbor = None
                if edge.source_node_id == current:
                    neighbor = edge.target_node_id
                elif edge.target_node_id == current:
                    neighbor = edge.source_node_id
                if (
                    neighbor is not None
                    and neighbor not in previous
                    and len(previous) < query.node_limit
                ):
                    previous[neighbor] = (current, edge.edge_id)
                    queue.append(neighbor)
        if query.target_node_id not in previous:
            return GraphSubgraph(query.snapshot_id, (), ())
        node_ids = {query.target_node_id}
        edge_ids: set[str] = set()
        current = query.target_node_id
        while (parent := previous[current]) is not None:
            current, edge_id = parent
            node_ids.add(current)
            edge_ids.add(edge_id)
        return self._subgraph(draft, node_ids, edge_ids)
