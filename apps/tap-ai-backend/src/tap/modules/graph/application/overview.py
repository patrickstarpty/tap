"""Project domain overview merged from every published source's active graph snapshot."""

from __future__ import annotations

import asyncio
import hashlib
from collections import Counter, OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from types import MappingProxyType
from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.application.cache import CachedSnapshotIndexes
from tap.modules.graph.domain.index import SnapshotIndex
from tap.modules.graph.domain.models import GraphSnapshot
from tap.platform.db.project_scope import require_project_scope
from tap.platform.telemetry import span

MAX_OVERVIEW_NODES = 300
_RESULT_CACHE_SIZE = 64


@dataclass(frozen=True, slots=True)
class PublishedSource:
    source_id: str
    source_revision_id: str


class PublishedGraphSources(Protocol):
    async def published_source_revisions(
        self, scope: ProjectScopeContext
    ) -> tuple[PublishedSource, ...]: ...


class OverviewGraphStore(Protocol):
    async def active_snapshot(
        self, scope: ProjectScopeContext, source_ids: tuple[str, ...]
    ) -> GraphSnapshot | None: ...
    async def load_index(
        self, scope: ProjectScopeContext, snapshot: GraphSnapshot
    ) -> SnapshotIndex: ...


@dataclass(frozen=True, slots=True)
class OverviewMember:
    snapshot_id: str
    node_id: str
    source_id: str


@dataclass(frozen=True, slots=True)
class OverviewNode:
    overview_node_id: str
    label: str
    node_type: str
    degree: int
    members: tuple[OverviewMember, ...]


@dataclass(frozen=True, slots=True)
class OverviewEdge:
    overview_edge_id: str
    source: str
    target: str
    relation_type: str
    support: int


@dataclass(frozen=True, slots=True)
class GraphOverview:
    nodes: tuple[OverviewNode, ...]
    edges: tuple[OverviewEdge, ...]
    node_type_counts: Mapping[str, int]
    source_count: int
    snapshot_ids: tuple[str, ...]


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:32]


class GraphOverviewService:
    """Merge published snapshots by ``(node_type, canonical_key)``.

    Snapshot indexes come from the shared process cache; merged results are
    cached per snapshot set because published snapshots are immutable.
    """

    def __init__(
        self,
        store: OverviewGraphStore,
        sources: PublishedGraphSources,
        indexes: CachedSnapshotIndexes,
    ) -> None:
        self._store = store
        self._sources = sources
        self._indexes = indexes
        self._results: OrderedDict[tuple[str, tuple[str, ...], int], GraphOverview] = OrderedDict()

    async def overview(self, scope: ProjectScopeContext, *, node_limit: int = 150) -> GraphOverview:
        if not 1 <= node_limit <= MAX_OVERVIEW_NODES:
            raise ValueError("overview node limit must be between 1 and 300")
        scope = require_project_scope(scope)
        with span("graph.overview") as current:
            published = await self._sources.published_source_revisions(scope)
            snapshots = await asyncio.gather(
                *(
                    self._store.active_snapshot(scope, (item.source_revision_id,))
                    for item in published
                )
            )
            selected: dict[str, tuple[GraphSnapshot, str]] = {}
            for item, snapshot in zip(published, snapshots, strict=True):
                if snapshot is not None and snapshot.status == "READY":
                    selected.setdefault(snapshot.snapshot_id, (snapshot, item.source_id))
            key = (scope.project_id, tuple(sorted(selected)), node_limit)
            cached = self._results.get(key)
            current.set_attribute("tap.graph.overview_cache_hit", cached is not None)
            current.set_attribute("tap.graph.snapshot_count", len(selected))
            if cached is not None:
                self._results.move_to_end(key)
                return cached
            members: list[tuple[SnapshotIndex, str]] = []
            for snapshot_id in key[1]:
                snapshot, source_id = selected[snapshot_id]
                index = await self._indexes.get_or_load(
                    (scope.project_id, snapshot_id),
                    partial(self._store.load_index, scope, snapshot),
                )
                members.append((index, source_id))
            result = _merge(members, node_limit)
            self._results[key] = result
            while len(self._results) > _RESULT_CACHE_SIZE:
                self._results.popitem(last=False)
            return result


def _merge(indexes: list[tuple[SnapshotIndex, str]], node_limit: int) -> GraphOverview:
    merged_of: dict[tuple[str, str], str] = {}
    node_types: dict[str, str] = {}
    labels: dict[str, Counter[str]] = {}
    node_members: dict[str, list[OverviewMember]] = {}
    edge_support: Counter[tuple[str, str, str]] = Counter()
    for index, source_id in indexes:
        snapshot_id = index.snapshot.snapshot_id
        for node in index.nodes.values():
            identity = "overview:" + _digest(node.node_type, node.canonical_key)
            merged_of[(snapshot_id, node.node_id)] = identity
            node_types[identity] = node.node_type
            labels.setdefault(identity, Counter())[node.label] += 1
            node_members.setdefault(identity, []).append(
                OverviewMember(snapshot_id, node.node_id, source_id)
            )
        for edge in index.edges.values():
            source = merged_of[(snapshot_id, edge.source_node_id)]
            target = merged_of[(snapshot_id, edge.target_node_id)]
            if source != target:
                edge_support[(source, edge.relation_type, target)] += 1
    degree: Counter[str] = Counter()
    for source, _, target in edge_support:
        degree[source] += 1
        degree[target] += 1
    nodes = sorted(
        (
            OverviewNode(
                overview_node_id=identity,
                label=min(counts, key=lambda label: (-counts[label], label)),
                node_type=node_types[identity],
                degree=degree[identity],
                members=tuple(
                    sorted(
                        node_members[identity],
                        key=lambda item: (item.snapshot_id, item.node_id),
                    )
                ),
            )
            for identity, counts in labels.items()
        ),
        key=lambda node: (-node.degree, node.label, node.overview_node_id),
    )
    kept = nodes[:node_limit]
    kept_ids = {node.overview_node_id for node in kept}
    edges = tuple(
        OverviewEdge(
            overview_edge_id="overview-edge:" + _digest(source, relation, target),
            source=source,
            target=target,
            relation_type=relation,
            support=support,
        )
        for (source, relation, target), support in sorted(edge_support.items())
        if source in kept_ids and target in kept_ids
    )
    return GraphOverview(
        nodes=tuple(kept),
        edges=edges,
        node_type_counts=MappingProxyType(dict(sorted(Counter(node_types.values()).items()))),
        source_count=len({source_id for _, source_id in indexes}),
        snapshot_ids=tuple(index.snapshot.snapshot_id for index, _ in indexes),
    )
