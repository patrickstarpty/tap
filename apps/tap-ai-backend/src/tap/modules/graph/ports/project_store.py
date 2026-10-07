"""Port for bounded, Project-scoped queries over the merged project graph.

Every read method accepts a keyword-only `version`: `None` means "the current
READY version"; an explicit version is served only if it is the current
version or still inside the backing `ProjectGraphCache`'s retention window
(`keep_per_project=2`), otherwise `ProjectGraphVersionMismatch` is raised. This
lets a caller (PR 3's relation analysis) pin one version for the duration of a
single answer even if a background merge publishes a newer version mid-turn.
"""

from __future__ import annotations

from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.domain.project import (
    AliasMatch,
    Community,
    ProjectGraphVersion,
    ProjectNode,
    ProjectNodeDetail,
    ProjectSubgraph,
)
from tap.modules.graph.ports.store import GraphFactNotFound

__all__ = [
    "GraphFactNotFound",
    "ProjectGraphNotReady",
    "ProjectGraphStorePort",
    "ProjectGraphVersionMismatch",
]


class ProjectGraphNotReady(LookupError):
    """The project has no READY merged-graph version yet."""


class ProjectGraphVersionMismatch(LookupError):
    """A pinned `version=` fell outside the cache's retention window.

    `current` is the project's current READY version; HTTP callers map this to
    409 (spec 3.1 requires an exact match against the current version there,
    a stricter check than this store-level retention window).
    """

    def __init__(self, current: int) -> None:
        super().__init__(
            f"requested graph version is outside the retained window; current version is {current}"
        )
        self.current = current


class ProjectGraphStorePort(Protocol):
    async def get_current(self, scope: ProjectScopeContext) -> ProjectGraphVersion | None: ...

    async def communities(self, scope: ProjectScopeContext) -> tuple[Community, ...]: ...

    async def overview(
        self,
        scope: ProjectScopeContext,
        *,
        source_revision_ids: tuple[str, ...] = (),
        community_ids: tuple[str, ...] = (),
        node_limit: int = 150,
        version: int | None = None,
    ) -> ProjectSubgraph: ...

    async def search(
        self,
        scope: ProjectScopeContext,
        text: str,
        *,
        source_revision_ids: tuple[str, ...] = (),
        node_limit: int = 50,
        version: int | None = None,
    ) -> ProjectSubgraph: ...

    async def neighbors(
        self,
        scope: ProjectScopeContext,
        node_id: str,
        *,
        depth: int = 1,
        node_limit: int = 50,
        source_revision_ids: tuple[str, ...] = (),
        version: int | None = None,
    ) -> ProjectSubgraph: ...

    async def path(
        self,
        scope: ProjectScopeContext,
        source_node_id: str,
        target_node_id: str,
        *,
        max_hops: int = 3,
        source_revision_ids: tuple[str, ...] = (),
        version: int | None = None,
    ) -> ProjectSubgraph: ...

    async def node_detail(
        self,
        scope: ProjectScopeContext,
        node_id: str,
        *,
        version: int | None = None,
    ) -> ProjectNodeDetail: ...

    async def highlight(
        self,
        scope: ProjectScopeContext,
        edge_ids: tuple[str, ...],
        *,
        version: int | None = None,
    ) -> ProjectSubgraph: ...

    async def nodes_for_chunks(
        self,
        scope: ProjectScopeContext,
        chunk_ids: tuple[str, ...],
        *,
        version: int | None = None,
    ) -> tuple[str, ...]: ...

    async def match_aliases(
        self,
        scope: ProjectScopeContext,
        text: str,
        *,
        version: int | None = None,
    ) -> tuple[AliasMatch, ...]: ...

    async def nodes(
        self,
        scope: ProjectScopeContext,
        node_ids: tuple[str, ...],
        *,
        version: int | None = None,
    ) -> tuple[ProjectNode, ...]: ...
