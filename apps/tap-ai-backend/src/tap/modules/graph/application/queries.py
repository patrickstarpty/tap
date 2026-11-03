"""Reference in-memory graph store and bounded traversal semantics."""

from __future__ import annotations

from dataclasses import replace

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.domain.index import SnapshotIndex
from tap.modules.graph.domain.models import (
    Evidence,
    GraphFactNotFound,
    GraphSearchQuery,
    GraphSnapshot,
    GraphSnapshotDraft,
    GraphSubgraph,
    NeighborQuery,
    PathQuery,
    source_set_digest,
)
from tap.platform.db.project_scope import require_project_scope


class InMemoryGraphStore:
    def __init__(self) -> None:
        self._drafts: dict[tuple[str, str], GraphSnapshotDraft] = {}
        self._active: dict[tuple[str, str], str] = {}
        self._indexes: dict[tuple[str, str], SnapshotIndex] = {}

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
        self._indexes[key] = SnapshotIndex.build(persisted)
        self._active[(scope.project_id, ready.source_set_digest)] = ready.snapshot_id
        return ready

    async def active_snapshot(
        self, scope: ProjectScopeContext, source_ids: tuple[str, ...]
    ) -> GraphSnapshot | None:
        scope = require_project_scope(scope)
        identity = self._active.get((scope.project_id, source_set_digest(source_ids)))
        if identity is None:
            return None
        snapshot = self._drafts[(scope.project_id, identity)].snapshot
        return snapshot if snapshot.source_revision_ids == tuple(sorted(source_ids)) else None

    async def get_snapshot(
        self, scope: ProjectScopeContext, snapshot_id: str
    ) -> GraphSnapshot | None:
        scope = require_project_scope(scope)
        draft = self._drafts.get((scope.project_id, snapshot_id))
        return None if draft is None else draft.snapshot

    def _index(self, scope: ProjectScopeContext, snapshot_id: str) -> SnapshotIndex:
        scope = require_project_scope(scope)
        try:
            return self._indexes[(scope.project_id, snapshot_id)]
        except KeyError as error:
            raise GraphFactNotFound("graph snapshot not found") from error

    async def node_detail(
        self, scope: ProjectScopeContext, snapshot_id: str, node_id: str
    ) -> GraphSubgraph:
        return self._index(scope, snapshot_id).node_detail(node_id)

    async def evidence(
        self, scope: ProjectScopeContext, snapshot_id: str, evidence_id: str
    ) -> Evidence:
        return self._index(scope, snapshot_id).evidence_detail(evidence_id)

    async def search(self, scope: ProjectScopeContext, query: GraphSearchQuery) -> GraphSubgraph:
        return self._index(scope, query.snapshot_id).search(query)

    async def neighbors(self, scope: ProjectScopeContext, query: NeighborQuery) -> GraphSubgraph:
        return self._index(scope, query.snapshot_id).neighbors(query)

    async def bounded_path(self, scope: ProjectScopeContext, query: PathQuery) -> GraphSubgraph:
        return self._index(scope, query.snapshot_id).bounded_path(query)
