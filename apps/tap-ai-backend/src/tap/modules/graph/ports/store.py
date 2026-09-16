"""Port for bounded, Project-scoped graph persistence."""

from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.domain.models import (
    Evidence,
    GraphSearchQuery,
    GraphSnapshot,
    GraphSnapshotDraft,
    GraphSubgraph,
    NeighborQuery,
    PathQuery,
)


class GraphFactNotFound(LookupError):
    pass


class GraphStorePort(Protocol):
    async def publish(
        self, scope: ProjectScopeContext, draft: GraphSnapshotDraft
    ) -> GraphSnapshot: ...
    async def active_snapshot(
        self, scope: ProjectScopeContext, source_ids: tuple[str, ...]
    ) -> GraphSnapshot | None: ...
    async def get_snapshot(
        self, scope: ProjectScopeContext, snapshot_id: str
    ) -> GraphSnapshot | None: ...
    async def node_detail(
        self, scope: ProjectScopeContext, snapshot_id: str, node_id: str
    ) -> GraphSubgraph: ...
    async def evidence(
        self, scope: ProjectScopeContext, snapshot_id: str, evidence_id: str
    ) -> Evidence: ...
    async def search(
        self, scope: ProjectScopeContext, query: GraphSearchQuery
    ) -> GraphSubgraph: ...
    async def neighbors(
        self, scope: ProjectScopeContext, query: NeighborQuery
    ) -> GraphSubgraph: ...
    async def bounded_path(self, scope: ProjectScopeContext, query: PathQuery) -> GraphSubgraph: ...
