"""Per-version adjacency cache and bounded traversal over the merged project
graph; `LoadedProjectGraph` holds one immutable version in memory, indexed
for O(1) node/edge lookup and O(degree) neighbor expansion, and `InMemory
ProjectGraphStore` is the reference port implementation used by contract
tests (the MySQL-backed store lives in `adapters/mysql_project_store.py` and
delegates to the same `LoadedProjectGraph` query methods)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.application.alias_index import AliasIndex, AliasMatch
from tap.modules.graph.domain.project import (
    Alias,
    Community,
    EdgeEvidence,
    NodeSource,
    ProjectEdge,
    ProjectGraphDraft,
    ProjectGraphVersion,
    ProjectNode,
    ProjectNodeDetail,
    ProjectSubgraph,
)
from tap.modules.graph.domain.vocabulary import normalize_key
from tap.modules.graph.ports.project_store import (
    ProjectGraphNotReady,
    ProjectGraphStorePort,
    ProjectGraphVersionMismatch,
)
from tap.modules.graph.ports.store import GraphFactNotFound
from tap.platform.db.project_scope import require_project_scope

# MySQL's `graph_project_alias.alias_norm` is `String(255)`; a normalized
# alias longer than that was never persisted, so loading must skip it rather
# than build an index entry nothing in storage can ever match.
_ALIAS_NORM_MAX = 255

__all__ = [
    "LoadedProjectGraph",
    "ProjectGraphCache",
    "ProjectGraphQueryDelegate",
    "InMemoryProjectGraphStore",
    "resolve_version",
]


@dataclass(frozen=True, slots=True)
class LoadedProjectGraph:
    version: ProjectGraphVersion
    nodes: Mapping[str, ProjectNode]
    edges: Mapping[str, ProjectEdge]
    adjacency: Mapping[str, tuple[tuple[str, str], ...]]
    node_sources: Mapping[str, tuple[NodeSource, ...]]
    edge_evidence: Mapping[str, tuple[EdgeEvidence, ...]]
    chunk_nodes: Mapping[str, tuple[str, ...]]
    communities: tuple[Community, ...]
    alias_index: AliasIndex

    @classmethod
    def from_draft(
        cls, version: ProjectGraphVersion, draft: ProjectGraphDraft
    ) -> LoadedProjectGraph:
        return cls.from_rows(
            version,
            nodes=draft.nodes,
            edges=draft.edges,
            node_sources=draft.node_sources,
            edge_evidence=draft.edge_evidence,
            aliases=draft.aliases,
            communities=draft.communities,
        )

    @classmethod
    def from_rows(
        cls,
        version: ProjectGraphVersion,
        *,
        nodes: tuple[ProjectNode, ...],
        edges: tuple[ProjectEdge, ...],
        node_sources: tuple[NodeSource, ...],
        edge_evidence: tuple[EdgeEvidence, ...],
        aliases: tuple[Alias, ...],
        communities: tuple[Community, ...],
    ) -> LoadedProjectGraph:
        """Build the adjacency cache from already-materialized domain tuples
        (either a `ProjectGraphDraft`'s fields via `from_draft`, or rows read
        straight off the eight MySQL tables) without going through the
        fragment object graph."""

        nodes_by_id = {node.node_id: node for node in nodes}
        edges_by_id = {edge.edge_id: edge for edge in edges}

        adjacency: dict[str, list[tuple[str, str]]] = {node_id: [] for node_id in nodes_by_id}
        for edge in edges:
            adjacency.setdefault(edge.source_node_id, []).append(
                (edge.target_node_id, edge.edge_id)
            )
            adjacency.setdefault(edge.target_node_id, []).append(
                (edge.source_node_id, edge.edge_id)
            )

        sources_by_node: dict[str, list[NodeSource]] = {}
        for source in node_sources:
            sources_by_node.setdefault(source.node_id, []).append(source)

        evidence_by_edge: dict[str, list[EdgeEvidence]] = {}
        for evidence in edge_evidence:
            evidence_by_edge.setdefault(evidence.edge_id, []).append(evidence)

        chunk_nodes: dict[str, list[str]] = {}
        for source in node_sources:
            bucket = chunk_nodes.setdefault(source.chunk_id, [])
            if source.node_id not in bucket:
                bucket.append(source.node_id)
        for evidence in edge_evidence:
            evidenced_edge = edges_by_id.get(evidence.edge_id)
            if evidenced_edge is None:
                continue
            bucket = chunk_nodes.setdefault(evidence.chunk_id, [])
            for node_id in (evidenced_edge.source_node_id, evidenced_edge.target_node_id):
                if node_id not in bucket:
                    bucket.append(node_id)

        # Skip aliases too long to ever have been stored (see `_ALIAS_NORM_MAX`).
        alias_index = AliasIndex.build(
            alias for alias in aliases if len(alias.alias_norm) <= _ALIAS_NORM_MAX
        )

        return cls(
            version=version,
            nodes=nodes_by_id,
            edges=edges_by_id,
            adjacency={node_id: tuple(pairs) for node_id, pairs in adjacency.items()},
            node_sources={node_id: tuple(items) for node_id, items in sources_by_node.items()},
            edge_evidence={edge_id: tuple(items) for edge_id, items in evidence_by_edge.items()},
            chunk_nodes={chunk_id: tuple(items) for chunk_id, items in chunk_nodes.items()},
            communities=communities,
            alias_index=alias_index,
        )

    # -- source-filter helpers -------------------------------------------------

    def _node_visible(self, node_id: str, source_revision_ids: tuple[str, ...]) -> bool:
        if node_id not in self.nodes:
            return False
        if not source_revision_ids:
            return True
        return any(
            source.source_revision_id in source_revision_ids
            for source in self.node_sources.get(node_id, ())
        )

    def _edge_visible(
        self,
        edge: ProjectEdge,
        visible_node_ids: set[str],
        source_revision_ids: tuple[str, ...],
    ) -> bool:
        if (
            edge.source_node_id not in visible_node_ids
            or edge.target_node_id not in visible_node_ids
        ):
            return False
        if not source_revision_ids:
            return True
        return any(
            evidence.source_revision_id in source_revision_ids
            for evidence in self.edge_evidence.get(edge.edge_id, ())
        )

    def _sources_for(
        self, node_ids: Iterable[str], source_revision_ids: tuple[str, ...]
    ) -> tuple[NodeSource, ...]:
        result: list[NodeSource] = []
        for node_id in node_ids:
            for source in self.node_sources.get(node_id, ()):
                if not source_revision_ids or source.source_revision_id in source_revision_ids:
                    result.append(source)
        return tuple(result)

    def _evidence_for(
        self, edge_ids: Iterable[str], source_revision_ids: tuple[str, ...]
    ) -> tuple[EdgeEvidence, ...]:
        result: list[EdgeEvidence] = []
        for edge_id in edge_ids:
            for evidence in self.edge_evidence.get(edge_id, ()):
                if not source_revision_ids or evidence.source_revision_id in source_revision_ids:
                    result.append(evidence)
        return tuple(result)

    # -- queries ----------------------------------------------------------------

    def overview(
        self,
        *,
        source_revision_ids: tuple[str, ...] = (),
        community_ids: tuple[str, ...] = (),
        node_limit: int = 150,
    ) -> ProjectSubgraph:
        if node_limit < 1:
            raise ValueError("node limit must be positive")
        candidates = [
            node
            for node in self.nodes.values()
            if self._node_visible(node.node_id, source_revision_ids)
        ]
        if community_ids:
            allowed = set(community_ids)
            candidates = [node for node in candidates if node.community_id in allowed]

        buckets: dict[str | None, list[ProjectNode]] = {}
        for node in candidates:
            buckets.setdefault(node.community_id, []).append(node)
        for bucket in buckets.values():
            bucket.sort(key=lambda node: (-node.degree, node.node_id))

        known_order = [
            community.community_id
            for community in self.communities
            if community.community_id in buckets
        ]
        leftover = sorted(
            (key for key in buckets if key not in known_order),
            key=lambda key: (key is None, key or ""),
        )
        rotation = known_order + leftover

        cursors = dict.fromkeys(rotation, 0)
        selected: list[ProjectNode] = []
        while len(selected) < node_limit and rotation:
            progressed = False
            for key in rotation:
                if len(selected) >= node_limit:
                    break
                bucket = buckets[key]
                cursor = cursors[key]
                if cursor < len(bucket):
                    selected.append(bucket[cursor])
                    cursors[key] = cursor + 1
                    progressed = True
            if not progressed:
                break

        node_ids = {node.node_id for node in selected}
        edges = tuple(
            edge
            for edge in self.edges.values()
            if self._edge_visible(edge, node_ids, source_revision_ids)
        )
        edge_ids = {edge.edge_id for edge in edges}
        return ProjectSubgraph(
            self.version.version,
            tuple(selected),
            edges,
            self._sources_for(node_ids, source_revision_ids),
            self._evidence_for(edge_ids, source_revision_ids),
        )

    def search(
        self,
        text: str,
        *,
        source_revision_ids: tuple[str, ...] = (),
        node_limit: int = 50,
    ) -> ProjectSubgraph:
        if node_limit < 1:
            raise ValueError("node limit must be positive")
        if text == "*":
            visible = [
                node
                for node in self.nodes.values()
                if self._node_visible(node.node_id, source_revision_ids)
            ]
            visible.sort(key=lambda node: (-node.degree, node.node_id))
            selected = visible[:node_limit]
        else:
            needle = normalize_key(text)
            prefix_matches: dict[str, ProjectNode] = {}
            contains_matches: dict[str, ProjectNode] = {}
            for alias in self.alias_index.entries:
                node = self.nodes.get(alias.node_id)
                if node is None or not self._node_visible(node.node_id, source_revision_ids):
                    continue
                if alias.alias_norm.startswith(needle):
                    prefix_matches.setdefault(node.node_id, node)
                elif needle in alias.alias_norm:
                    contains_matches.setdefault(node.node_id, node)
            for node_id in prefix_matches:
                contains_matches.pop(node_id, None)
            ranked = sorted(prefix_matches.values(), key=lambda node: (-node.degree, node.node_id))
            ranked += sorted(
                contains_matches.values(), key=lambda node: (-node.degree, node.node_id)
            )
            selected = ranked[:node_limit]

        node_ids = {node.node_id for node in selected}
        edges = tuple(
            edge
            for edge in self.edges.values()
            if self._edge_visible(edge, node_ids, source_revision_ids)
        )
        edge_ids = {edge.edge_id for edge in edges}
        return ProjectSubgraph(
            self.version.version,
            tuple(selected),
            edges,
            self._sources_for(node_ids, source_revision_ids),
            self._evidence_for(edge_ids, source_revision_ids),
        )

    def neighbors(
        self,
        node_id: str,
        *,
        depth: int = 1,
        node_limit: int = 50,
        source_revision_ids: tuple[str, ...] = (),
    ) -> ProjectSubgraph:
        if depth not in (1, 2):
            raise ValueError("depth must be between one and two")
        if node_limit < 1:
            raise ValueError("node limit must be positive")

        visible_node_ids = {
            node.node_id
            for node in self.nodes.values()
            if self._node_visible(node.node_id, source_revision_ids)
        }
        if node_id not in visible_node_ids:
            return ProjectSubgraph(self.version.version, (), (), (), ())

        seen = {node_id}
        frontier = {node_id}
        for _ in range(depth):
            adjacent: set[str] = set()
            for current in frontier:
                for neighbor_id, edge_id in self.adjacency.get(current, ()):
                    edge = self.edges[edge_id]
                    if neighbor_id in visible_node_ids and self._edge_visible(
                        edge, visible_node_ids, source_revision_ids
                    ):
                        adjacent.add(neighbor_id)
            frontier = adjacent - seen
            for identity in sorted(frontier):
                if len(seen) >= node_limit:
                    break
                seen.add(identity)

        nodes = tuple(node for node in self.nodes.values() if node.node_id in seen)
        edges = tuple(
            edge
            for edge in self.edges.values()
            if self._edge_visible(edge, seen, source_revision_ids)
        )
        edge_ids = {edge.edge_id for edge in edges}
        return ProjectSubgraph(
            self.version.version,
            nodes,
            edges,
            self._sources_for(seen, source_revision_ids),
            self._evidence_for(edge_ids, source_revision_ids),
        )

    def path(
        self,
        source_node_id: str,
        target_node_id: str,
        *,
        max_hops: int = 3,
        source_revision_ids: tuple[str, ...] = (),
    ) -> ProjectSubgraph:
        empty = ProjectSubgraph(self.version.version, (), (), (), ())
        visible_node_ids = {
            node.node_id
            for node in self.nodes.values()
            if self._node_visible(node.node_id, source_revision_ids)
        }
        if source_node_id not in visible_node_ids or target_node_id not in visible_node_ids:
            return empty
        if source_node_id == target_node_id:
            node = self.nodes[source_node_id]
            return ProjectSubgraph(
                self.version.version,
                (node,),
                (),
                self._sources_for((source_node_id,), source_revision_ids),
                (),
            )

        previous: dict[str, tuple[str, str]] = {}
        visited = {source_node_id}
        frontier = [source_node_id]
        hops = 0
        found = False
        while frontier and hops < max_hops and not found:
            hops += 1
            next_frontier: list[str] = []
            for current in frontier:
                if found:
                    break
                for neighbor_id, edge_id in self.adjacency.get(current, ()):
                    if neighbor_id in visited:
                        continue
                    edge = self.edges[edge_id]
                    if neighbor_id not in visible_node_ids or not self._edge_visible(
                        edge, visible_node_ids, source_revision_ids
                    ):
                        continue
                    visited.add(neighbor_id)
                    previous[neighbor_id] = (current, edge_id)
                    if neighbor_id == target_node_id:
                        found = True
                        break
                    next_frontier.append(neighbor_id)
            frontier = next_frontier

        if not found:
            return empty

        node_ids = {target_node_id}
        edge_ids: set[str] = set()
        current = target_node_id
        while current != source_node_id:
            parent, edge_id = previous[current]
            node_ids.add(parent)
            edge_ids.add(edge_id)
            current = parent

        nodes = tuple(node for node in self.nodes.values() if node.node_id in node_ids)
        edges = tuple(edge for edge in self.edges.values() if edge.edge_id in edge_ids)
        return ProjectSubgraph(
            self.version.version,
            nodes,
            edges,
            self._sources_for(node_ids, source_revision_ids),
            self._evidence_for(edge_ids, source_revision_ids),
        )

    def node_detail(self, node_id: str) -> ProjectNodeDetail:
        node = self.nodes.get(node_id)
        if node is None:
            raise GraphFactNotFound(f"project node {node_id!r} not found")
        community = next((c for c in self.communities if c.community_id == node.community_id), None)
        sources = self.node_sources.get(node_id, ())

        seen_edge_ids: set[str] = set()
        edges: list[ProjectEdge] = []
        seen_neighbor_ids: set[str] = set()
        neighbors: list[ProjectNode] = []
        for neighbor_id, edge_id in self.adjacency.get(node_id, ()):
            if edge_id not in seen_edge_ids:
                seen_edge_ids.add(edge_id)
                edges.append(self.edges[edge_id])
            if neighbor_id not in seen_neighbor_ids and neighbor_id in self.nodes:
                seen_neighbor_ids.add(neighbor_id)
                neighbors.append(self.nodes[neighbor_id])

        evidence = tuple(
            item for edge in edges for item in self.edge_evidence.get(edge.edge_id, ())
        )
        return ProjectNodeDetail(
            self.version.version, node, community, sources, tuple(edges), tuple(neighbors), evidence
        )

    def highlight(self, edge_ids: tuple[str, ...], *, limit: int = 200) -> ProjectSubgraph:
        # Deterministic by construction: requested edges are added in request
        # order (capped at `limit`), then their endpoints are walked in the
        # order those edges introduced them (never a `set`, whose iteration
        # order is not meaningful). An edge dropped because the cap was
        # already full never contributes to `endpoint_order`, so its unique
        # endpoint cannot leak into the result through a later union.
        collected: dict[str, ProjectEdge] = {}
        endpoint_order: list[str] = []
        seen_endpoints: set[str] = set()

        for edge_id in edge_ids:
            if len(collected) >= limit:
                break
            if edge_id in collected:
                continue
            edge = self.edges.get(edge_id)
            if edge is None:
                continue
            collected[edge_id] = edge
            for node_id in (edge.source_node_id, edge.target_node_id):
                if node_id not in seen_endpoints:
                    seen_endpoints.add(node_id)
                    endpoint_order.append(node_id)

        for endpoint in endpoint_order:
            if len(collected) >= limit:
                break
            for _, adjacent_edge_id in self.adjacency.get(endpoint, ()):
                if len(collected) >= limit:
                    break
                collected.setdefault(adjacent_edge_id, self.edges[adjacent_edge_id])

        edges = tuple(collected.values())
        node_ids = {
            node_id for edge in edges for node_id in (edge.source_node_id, edge.target_node_id)
        }
        nodes = tuple(node for node in self.nodes.values() if node.node_id in node_ids)
        edge_ids_final = {edge.edge_id for edge in edges}
        return ProjectSubgraph(
            self.version.version,
            nodes,
            edges,
            self._sources_for(node_ids, ()),
            self._evidence_for(edge_ids_final, ()),
        )

    def nodes_for_chunks(self, chunk_ids: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for chunk_id in chunk_ids:
            for node_id in self.chunk_nodes.get(chunk_id, ()):
                if node_id not in seen:
                    seen.add(node_id)
                    result.append(node_id)
        return tuple(result)

    def match_aliases(self, text: str) -> tuple[AliasMatch, ...]:
        return self.alias_index.match(text)


class ProjectGraphCache:
    """Per-project LRU-by-insertion window of the last `keep_per_project`
    loaded versions; `publish`/`_load` call `put` after materializing a
    `LoadedProjectGraph`, and query methods call `get` to avoid re-loading."""

    def __init__(self, *, keep_per_project: int = 2) -> None:
        self._keep_per_project = keep_per_project
        self._by_project: dict[str, dict[int, LoadedProjectGraph]] = {}
        self._order: dict[str, list[int]] = {}

    def get(self, project_id: str, version: int) -> LoadedProjectGraph | None:
        return self._by_project.get(project_id, {}).get(version)

    def put(self, graph: LoadedProjectGraph) -> None:
        project_id = graph.version.project_id
        version = graph.version.version
        versions = self._by_project.setdefault(project_id, {})
        order = self._order.setdefault(project_id, [])
        if version in order:
            order.remove(version)
        versions[version] = graph
        order.append(version)
        while len(order) > self._keep_per_project:
            oldest = order.pop(0)
            versions.pop(oldest, None)

    def invalidate(self, project_id: str) -> None:
        self._by_project.pop(project_id, None)
        self._order.pop(project_id, None)

    def loaded_versions(self, project_id: str) -> tuple[int, ...]:
        return tuple(sorted(self._order.get(project_id, ())))


def resolve_version(
    cache: ProjectGraphCache, project_id: str, current_version: int, version: int | None
) -> int:
    """Resolve a caller-requested `version` against the current version and
    the cache's retention window; raises `ProjectGraphVersionMismatch` when
    neither applies."""

    if version is None or version == current_version:
        return current_version
    if version in cache.loaded_versions(project_id):
        return version
    raise ProjectGraphVersionMismatch(current_version)


class ProjectGraphQueryDelegate:
    """Shared query delegation for any store that can resolve a pinned-or-
    current `LoadedProjectGraph`; concrete stores provide `_loaded` (and their
    own `get_current`), and this mixin forwards every read method to it so the
    traversal logic lives exactly once, in `LoadedProjectGraph`."""

    async def _loaded(self, scope: ProjectScopeContext, version: int | None) -> LoadedProjectGraph:
        raise NotImplementedError

    async def communities(self, scope: ProjectScopeContext) -> tuple[Community, ...]:
        loaded = await self._loaded(scope, None)
        return loaded.communities

    async def overview(
        self,
        scope: ProjectScopeContext,
        *,
        source_revision_ids: tuple[str, ...] = (),
        community_ids: tuple[str, ...] = (),
        node_limit: int = 150,
        version: int | None = None,
    ) -> ProjectSubgraph:
        loaded = await self._loaded(scope, version)
        return loaded.overview(
            source_revision_ids=source_revision_ids,
            community_ids=community_ids,
            node_limit=node_limit,
        )

    async def search(
        self,
        scope: ProjectScopeContext,
        text: str,
        *,
        source_revision_ids: tuple[str, ...] = (),
        node_limit: int = 50,
        version: int | None = None,
    ) -> ProjectSubgraph:
        loaded = await self._loaded(scope, version)
        return loaded.search(text, source_revision_ids=source_revision_ids, node_limit=node_limit)

    async def neighbors(
        self,
        scope: ProjectScopeContext,
        node_id: str,
        *,
        depth: int = 1,
        node_limit: int = 50,
        source_revision_ids: tuple[str, ...] = (),
        version: int | None = None,
    ) -> ProjectSubgraph:
        loaded = await self._loaded(scope, version)
        return loaded.neighbors(
            node_id, depth=depth, node_limit=node_limit, source_revision_ids=source_revision_ids
        )

    async def path(
        self,
        scope: ProjectScopeContext,
        source_node_id: str,
        target_node_id: str,
        *,
        max_hops: int = 3,
        source_revision_ids: tuple[str, ...] = (),
        version: int | None = None,
    ) -> ProjectSubgraph:
        loaded = await self._loaded(scope, version)
        return loaded.path(
            source_node_id,
            target_node_id,
            max_hops=max_hops,
            source_revision_ids=source_revision_ids,
        )

    async def node_detail(
        self, scope: ProjectScopeContext, node_id: str, *, version: int | None = None
    ) -> ProjectNodeDetail:
        loaded = await self._loaded(scope, version)
        return loaded.node_detail(node_id)

    async def highlight(
        self,
        scope: ProjectScopeContext,
        edge_ids: tuple[str, ...],
        *,
        version: int | None = None,
    ) -> ProjectSubgraph:
        loaded = await self._loaded(scope, version)
        return loaded.highlight(edge_ids)

    async def nodes_for_chunks(
        self,
        scope: ProjectScopeContext,
        chunk_ids: tuple[str, ...],
        *,
        version: int | None = None,
    ) -> tuple[str, ...]:
        loaded = await self._loaded(scope, version)
        return loaded.nodes_for_chunks(chunk_ids)

    async def match_aliases(
        self, scope: ProjectScopeContext, text: str, *, version: int | None = None
    ) -> tuple[AliasMatch, ...]:
        loaded = await self._loaded(scope, version)
        return loaded.match_aliases(text)

    async def nodes(
        self,
        scope: ProjectScopeContext,
        node_ids: tuple[str, ...],
        *,
        version: int | None = None,
    ) -> tuple[ProjectNode, ...]:
        loaded = await self._loaded(scope, version)
        # `nodes` is the field name on `LoadedProjectGraph` for the node-id ->
        # `ProjectNode` mapping, so this filters it directly rather than
        # calling a same-named method (there isn't one).
        return tuple(loaded.nodes[node_id] for node_id in node_ids if node_id in loaded.nodes)


class InMemoryProjectGraphStore(ProjectGraphQueryDelegate, ProjectGraphStorePort):
    """Reference `ProjectGraphStorePort` implementation: versions and their
    loaded graphs live only in process memory, keyed by project."""

    def __init__(self, *, cache: ProjectGraphCache | None = None) -> None:
        self._cache = cache if cache is not None else ProjectGraphCache()
        self._versions: dict[str, list[ProjectGraphVersion]] = {}
        self._merging: set[str] = set()

    async def publish(
        self, scope: ProjectScopeContext, draft: ProjectGraphDraft, *, now: datetime
    ) -> ProjectGraphVersion:
        scope = require_project_scope(scope)
        project_id = scope.project_id
        versions = self._versions.setdefault(project_id, [])

        # Dedup only against the *current* version's digest, not every past
        # digest ever published: if the source set changes and later reverts
        # to a prior state (A -> B -> A), that is a new version (e.g. v3),
        # not a silent return of the stale v1 that the cache/DB no longer
        # serves as current.
        if versions and versions[-1].fragment_digest == draft.fragment_digest:
            return versions[-1]

        next_version = versions[-1].version + 1 if versions else 1
        published = ProjectGraphVersion(
            project_id=project_id,
            version=next_version,
            status="READY",
            fragment_digest=draft.fragment_digest,
            node_count=len(draft.nodes),
            edge_count=len(draft.edges),
            merged_at=now,
        )
        versions.append(published)
        self._merging.discard(project_id)
        self._cache.put(LoadedProjectGraph.from_draft(published, draft))
        return published

    async def mark_merging(self, scope: ProjectScopeContext) -> None:
        scope = require_project_scope(scope)
        self._merging.add(scope.project_id)

    def is_merging(self, scope: ProjectScopeContext) -> bool:
        """Test/HTTP-layer support for `mark_merging`: whether this project has
        an in-flight merge that has not yet published a new version."""
        scope = require_project_scope(scope)
        return scope.project_id in self._merging

    async def get_current(self, scope: ProjectScopeContext) -> ProjectGraphVersion | None:
        scope = require_project_scope(scope)
        versions = self._versions.get(scope.project_id, [])
        return versions[-1] if versions else None

    async def _loaded(self, scope: ProjectScopeContext, version: int | None) -> LoadedProjectGraph:
        scope = require_project_scope(scope)
        current = await self.get_current(scope)
        if current is None:
            raise ProjectGraphNotReady(f"project {scope.project_id!r} has no READY graph version")
        target_version = resolve_version(self._cache, scope.project_id, current.version, version)
        loaded = self._cache.get(scope.project_id, target_version)
        if loaded is None:
            # `publish` always caches the version it just created and the
            # cache keeps at least one slot per project, so the current
            # version is always present; `resolve_version` only returns an
            # older version when it is still in `loaded_versions`, i.e. also
            # still retrievable here. Unreachable in practice.
            raise ProjectGraphNotReady(
                f"project {scope.project_id!r} version {target_version} is not loaded"
            )
        return loaded
