"""MySQL-backed `ProjectGraphStorePort`: load one version's rows off the
`graph_project_*` tables into a `LoadedProjectGraph` and delegate every query
to it; results are cached per (project, version) in a `ProjectGraphCache` so
repeated queries within a process do not re-read MySQL."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.adapters.mysql_project import (
    graph_project_alias,
    graph_project_community,
    graph_project_edge,
    graph_project_edge_evidence,
    graph_project_node,
    graph_project_node_source,
    graph_project_version,
)
from tap.modules.graph.application.project_queries import (
    LoadedProjectGraph,
    ProjectGraphCache,
    ProjectGraphQueryDelegate,
    resolve_version,
)
from tap.modules.graph.domain.models import RelationOrigin
from tap.modules.graph.domain.project import (
    Alias,
    Community,
    EdgeEvidence,
    NodeSource,
    ProjectEdge,
    ProjectGraphVersion,
    ProjectNode,
)
from tap.modules.graph.ports.project_store import ProjectGraphNotReady, ProjectGraphStorePort
from tap.platform.db.project_scope import require_project_scope, scope_predicates

__all__ = ["MysqlProjectGraphStore"]


def _version_from_row(scope: ProjectScopeContext, row: RowMapping) -> ProjectGraphVersion:
    return ProjectGraphVersion(
        project_id=scope.project_id,
        version=row["version"],
        status=row["status"],
        fragment_digest=row["fragment_digest"],
        node_count=row["node_count"],
        edge_count=row["edge_count"],
        merged_at=row["merged_at"],
    )


class MysqlProjectGraphStore(ProjectGraphQueryDelegate, ProjectGraphStorePort):
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        cache: ProjectGraphCache | None = None,
    ) -> None:
        self._sessions = sessions
        self._cache = cache if cache is not None else ProjectGraphCache()

    async def get_current(self, scope: ProjectScopeContext) -> ProjectGraphVersion | None:
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(graph_project_version)
                        .where(
                            *scope_predicates(graph_project_version, scope),
                            graph_project_version.c.status == "READY",
                        )
                        .order_by(graph_project_version.c.version.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _version_from_row(scope, row)

    async def _loaded(self, scope: ProjectScopeContext, version: int | None) -> LoadedProjectGraph:
        scope = require_project_scope(scope)
        current = await self.get_current(scope)
        if current is None:
            raise ProjectGraphNotReady(f"project {scope.project_id!r} has no READY graph version")
        target_version = resolve_version(self._cache, scope.project_id, current.version, version)
        loaded = self._cache.get(scope.project_id, target_version)
        if loaded is not None:
            return loaded
        loaded = await self._load(scope, target_version)
        self._cache.put(loaded)
        return loaded

    async def _load(self, scope: ProjectScopeContext, version: int) -> LoadedProjectGraph:
        """Read-only load of one published version: one `select` per table,
        filtered by `(project_id, version)`, with no PARTIAL/fragment concept
        (that lives only in `GraphSnapshotDraft`/`load_fragment_draft`)."""

        scope = require_project_scope(scope)
        async with self._sessions() as session:
            version_row = (
                (
                    await session.execute(
                        select(graph_project_version)
                        .where(
                            *scope_predicates(graph_project_version, scope),
                            graph_project_version.c.version == version,
                        )
                        .order_by(graph_project_version.c.version)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if version_row is None:
                raise ProjectGraphNotReady(
                    f"project {scope.project_id!r} has no graph version {version}"
                )
            # Every query below orders by its table's primary-key columns (past
            # `version`, which the `where` already pins to one value) so row
            # order — and therefore `LoadedProjectGraph`'s adjacency/evidence
            # tuple order — is defined rather than left to MySQL's whim.
            node_rows = (
                (
                    await session.execute(
                        select(graph_project_node)
                        .where(
                            *scope_predicates(graph_project_node, scope),
                            graph_project_node.c.version == version,
                        )
                        .order_by(graph_project_node.c.node_id)
                    )
                )
                .mappings()
                .all()
            )
            edge_rows = (
                (
                    await session.execute(
                        select(graph_project_edge)
                        .where(
                            *scope_predicates(graph_project_edge, scope),
                            graph_project_edge.c.version == version,
                        )
                        .order_by(graph_project_edge.c.edge_id)
                    )
                )
                .mappings()
                .all()
            )
            source_rows = (
                (
                    await session.execute(
                        select(graph_project_node_source)
                        .where(
                            *scope_predicates(graph_project_node_source, scope),
                            graph_project_node_source.c.version == version,
                        )
                        .order_by(
                            graph_project_node_source.c.node_id,
                            graph_project_node_source.c.source_revision_id,
                            graph_project_node_source.c.chunk_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
            evidence_rows = (
                (
                    await session.execute(
                        select(graph_project_edge_evidence)
                        .where(
                            *scope_predicates(graph_project_edge_evidence, scope),
                            graph_project_edge_evidence.c.version == version,
                        )
                        .order_by(
                            graph_project_edge_evidence.c.edge_id,
                            graph_project_edge_evidence.c.source_revision_id,
                            graph_project_edge_evidence.c.chunk_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
            alias_rows = (
                (
                    await session.execute(
                        select(graph_project_alias)
                        .where(
                            *scope_predicates(graph_project_alias, scope),
                            graph_project_alias.c.version == version,
                        )
                        .order_by(graph_project_alias.c.alias_norm, graph_project_alias.c.node_id)
                    )
                )
                .mappings()
                .all()
            )
            community_rows = (
                (
                    await session.execute(
                        select(graph_project_community)
                        .where(
                            *scope_predicates(graph_project_community, scope),
                            graph_project_community.c.version == version,
                        )
                        .order_by(graph_project_community.c.community_id)
                    )
                )
                .mappings()
                .all()
            )

        project_version = _version_from_row(scope, version_row)
        nodes = tuple(
            ProjectNode(
                node_id=row["node_id"],
                label=row["label"],
                node_type=row["node_type"],
                canonical_key=row["canonical_key"],
                degree=row["degree"],
                community_id=row["community_id"],
                aliases=tuple(row["aliases"] or ()),
            )
            for row in node_rows
        )
        edges = tuple(
            ProjectEdge(
                edge_id=row["edge_id"],
                source_node_id=row["source_node_id"],
                target_node_id=row["target_node_id"],
                relation_type=row["relation_type"],
                relation_label=row["relation_label"],
                origin=RelationOrigin(row["origin"]),
                confidence=float(row["confidence"]),
            )
            for row in edge_rows
        )
        node_sources = tuple(
            NodeSource(
                node_id=row["node_id"],
                source_revision_id=row["source_revision_id"],
                document_revision_id=row["document_revision_id"],
                chunk_id=row["chunk_id"],
                anchor=row["anchor_json"],
                fragment_snapshot_id=row["fragment_snapshot_id"],
                fragment_node_id=row["fragment_node_id"],
            )
            for row in source_rows
        )
        edge_evidence = tuple(
            EdgeEvidence(
                edge_id=row["edge_id"],
                source_revision_id=row["source_revision_id"],
                document_revision_id=row["document_revision_id"],
                chunk_id=row["chunk_id"],
                anchor=row["anchor_json"],
                content_digest=row["content_digest"],
                fragment_snapshot_id=row["fragment_snapshot_id"],
                fragment_edge_id=row["fragment_edge_id"],
            )
            for row in evidence_rows
        )
        aliases = tuple(
            Alias(alias_norm=row["alias_norm"], node_id=row["node_id"], origin=row["origin"])
            for row in alias_rows
        )
        communities = tuple(
            Community(community_id=row["community_id"], label=row["label"], size=row["size"])
            for row in community_rows
        )
        return LoadedProjectGraph.from_rows(
            project_version,
            nodes=nodes,
            edges=edges,
            node_sources=node_sources,
            edge_evidence=edge_evidence,
            aliases=aliases,
            communities=communities,
        )
