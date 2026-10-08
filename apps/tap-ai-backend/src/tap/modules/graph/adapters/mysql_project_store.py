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
        (that lives only in `GraphSnapshotDraft`/`load_fragment_draft`).

        Each `select` names only the columns the row mapping below actually
        reads — never the five scope columns every `graph_project_*` table
        carries (`enterprise_id`/`project_id`/`actor_id`/`identity_mode`/
        `identity_origin`), which `scope_predicates` already filters on and
        which the resulting domain objects never use — and results are
        fetched as plain `Row` tuples rather than `RowMapping`s, then
        unpacked positionally in the same order as the `select(...)` column
        list below. At 10k nodes / 50k edges this is purely a column-count
        and row-object-shape change: same tables, same `where`/`order_by`,
        same values read, same domain objects built."""

        scope = require_project_scope(scope)
        async with self._sessions() as session:
            version_row = (
                await session.execute(
                    select(
                        graph_project_version.c.status,
                        graph_project_version.c.fragment_digest,
                        graph_project_version.c.node_count,
                        graph_project_version.c.edge_count,
                        graph_project_version.c.merged_at,
                    )
                    .where(
                        *scope_predicates(graph_project_version, scope),
                        graph_project_version.c.version == version,
                    )
                    .order_by(graph_project_version.c.version)
                )
            ).one_or_none()
            if version_row is None:
                raise ProjectGraphNotReady(
                    f"project {scope.project_id!r} has no graph version {version}"
                )
            # Every query below orders by its table's primary-key columns (past
            # `version`, which the `where` already pins to one value) so row
            # order — and therefore `LoadedProjectGraph`'s adjacency/evidence
            # tuple order — is defined rather than left to MySQL's whim.
            node_rows = (
                await session.execute(
                    select(
                        graph_project_node.c.node_id,
                        graph_project_node.c.label,
                        graph_project_node.c.node_type,
                        graph_project_node.c.canonical_key,
                        graph_project_node.c.degree,
                        graph_project_node.c.community_id,
                        graph_project_node.c.aliases,
                    )
                    .where(
                        *scope_predicates(graph_project_node, scope),
                        graph_project_node.c.version == version,
                    )
                    .order_by(graph_project_node.c.node_id)
                )
            ).all()
            edge_rows = (
                await session.execute(
                    select(
                        graph_project_edge.c.edge_id,
                        graph_project_edge.c.source_node_id,
                        graph_project_edge.c.target_node_id,
                        graph_project_edge.c.relation_type,
                        graph_project_edge.c.relation_label,
                        graph_project_edge.c.origin,
                        graph_project_edge.c.confidence,
                    )
                    .where(
                        *scope_predicates(graph_project_edge, scope),
                        graph_project_edge.c.version == version,
                    )
                    .order_by(graph_project_edge.c.edge_id)
                )
            ).all()
            source_rows = (
                await session.execute(
                    select(
                        graph_project_node_source.c.node_id,
                        graph_project_node_source.c.source_revision_id,
                        graph_project_node_source.c.document_revision_id,
                        graph_project_node_source.c.chunk_id,
                        graph_project_node_source.c.anchor_json,
                        graph_project_node_source.c.fragment_snapshot_id,
                        graph_project_node_source.c.fragment_node_id,
                    )
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
            ).all()
            evidence_rows = (
                await session.execute(
                    select(
                        graph_project_edge_evidence.c.edge_id,
                        graph_project_edge_evidence.c.source_revision_id,
                        graph_project_edge_evidence.c.document_revision_id,
                        graph_project_edge_evidence.c.chunk_id,
                        graph_project_edge_evidence.c.anchor_json,
                        graph_project_edge_evidence.c.content_digest,
                        graph_project_edge_evidence.c.fragment_snapshot_id,
                        graph_project_edge_evidence.c.fragment_edge_id,
                    )
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
            ).all()
            alias_rows = (
                await session.execute(
                    select(
                        graph_project_alias.c.alias_norm,
                        graph_project_alias.c.node_id,
                        graph_project_alias.c.origin,
                    )
                    .where(
                        *scope_predicates(graph_project_alias, scope),
                        graph_project_alias.c.version == version,
                    )
                    .order_by(graph_project_alias.c.alias_norm, graph_project_alias.c.node_id)
                )
            ).all()
            community_rows = (
                await session.execute(
                    select(
                        graph_project_community.c.community_id,
                        graph_project_community.c.label,
                        graph_project_community.c.size,
                    )
                    .where(
                        *scope_predicates(graph_project_community, scope),
                        graph_project_community.c.version == version,
                    )
                    .order_by(graph_project_community.c.community_id)
                )
            ).all()

        project_version = ProjectGraphVersion(
            project_id=scope.project_id,
            version=version,
            status=version_row[0],
            fragment_digest=version_row[1],
            node_count=version_row[2],
            edge_count=version_row[3],
            merged_at=version_row[4],
        )
        nodes = tuple(
            ProjectNode(
                node_id=row[0],
                label=row[1],
                node_type=row[2],
                canonical_key=row[3],
                degree=row[4],
                community_id=row[5],
                aliases=tuple(row[6] or ()),
            )
            for row in node_rows
        )
        edges = tuple(
            ProjectEdge(
                edge_id=row[0],
                source_node_id=row[1],
                target_node_id=row[2],
                relation_type=row[3],
                relation_label=row[4],
                origin=RelationOrigin(row[5]),
                confidence=float(row[6]),
            )
            for row in edge_rows
        )
        node_sources = tuple(
            NodeSource(
                node_id=row[0],
                source_revision_id=row[1],
                document_revision_id=row[2],
                chunk_id=row[3],
                anchor=row[4],
                fragment_snapshot_id=row[5],
                fragment_node_id=row[6],
            )
            for row in source_rows
        )
        edge_evidence = tuple(
            EdgeEvidence(
                edge_id=row[0],
                source_revision_id=row[1],
                document_revision_id=row[2],
                chunk_id=row[3],
                anchor=row[4],
                content_digest=row[5],
                fragment_snapshot_id=row[6],
                fragment_edge_id=row[7],
            )
            for row in evidence_rows
        )
        aliases = tuple(
            Alias(alias_norm=row[0], node_id=row[1], origin=row[2]) for row in alias_rows
        )
        communities = tuple(
            Community(community_id=row[0], label=row[1], size=row[2])
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
