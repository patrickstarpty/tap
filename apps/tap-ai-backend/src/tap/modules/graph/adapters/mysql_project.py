"""SQLAlchemy metadata and write/load functions for the Project-scoped merged
knowledge graph: an immutable version lineage of nodes, edges, provenance,
aliases, communities and the merge audit log."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import (
    Column,
    Float,
    Index,
    Integer,
    String,
    Table,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.ext.asyncio import AsyncSession

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.adapters.mysql import (
    _scoped,
    graph_edge,
    graph_edge_evidence,
    graph_inference_provenance,
    graph_node,
    graph_node_evidence,
    graph_snapshot,
)
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    InferenceProvenance,
    RelationOrigin,
)
from tap.modules.graph.domain.project import ProjectGraphDraft, ProjectGraphVersion
from tap.modules.graph.ports.store import GraphFactNotFound
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values

_BATCH_SIZE = 500

graph_project_version = _scoped(
    "graph_project_version",
    Column("version", Integer, primary_key=True),
    Column("status", String(16), nullable=False),
    Column("fragment_digest", String(71), nullable=False),
    Column("node_count", Integer, nullable=False),
    Column("edge_count", Integer, nullable=False),
    Column("merged_at", DATETIME(fsp=6), nullable=True),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    uniques=((("version",), "uq_graph_project_version_project_pk"),),
)

graph_project_node = _scoped(
    "graph_project_node",
    Column("version", Integer, primary_key=True),
    Column("node_id", String(128), primary_key=True),
    Column("label", String(512), nullable=False),
    Column("node_type", String(32), nullable=False),
    Column("canonical_key", String(512), nullable=False),
    Column("degree", Integer, nullable=False),
    Column("community_id", String(64), nullable=True),
    Column("aliases", JSON, nullable=False),
    uniques=((("version", "node_id"), "uq_graph_project_node_project_pk"),),
    parents=(
        (("version",), "graph_project_version", ("version",), "fk_graph_project_node_version"),
    ),
)
Index(
    "ix_graph_project_node_key",
    graph_project_node.c.project_id,
    graph_project_node.c.version,
    graph_project_node.c.canonical_key,
)
Index(
    "ix_graph_project_node_community",
    graph_project_node.c.project_id,
    graph_project_node.c.version,
    graph_project_node.c.community_id,
)

graph_project_edge = _scoped(
    "graph_project_edge",
    Column("version", Integer, primary_key=True),
    Column("edge_id", String(128), primary_key=True),
    Column("source_node_id", String(128), nullable=False),
    Column("target_node_id", String(128), nullable=False),
    Column("relation_type", String(64), nullable=False),
    Column("relation_label", String(64), nullable=False),
    Column("origin", String(16), nullable=False),
    Column("confidence", Float, nullable=False),
    uniques=((("version", "edge_id"), "uq_graph_project_edge_project_pk"),),
    parents=(
        (("version",), "graph_project_version", ("version",), "fk_graph_project_edge_version"),
    ),
)
Index(
    "ix_graph_project_edge_source",
    graph_project_edge.c.project_id,
    graph_project_edge.c.version,
    graph_project_edge.c.source_node_id,
)
Index(
    "ix_graph_project_edge_target",
    graph_project_edge.c.project_id,
    graph_project_edge.c.version,
    graph_project_edge.c.target_node_id,
)

graph_project_node_source = _scoped(
    "graph_project_node_source",
    Column("version", Integer, primary_key=True),
    Column("node_id", String(128), primary_key=True),
    Column("source_revision_id", String(128), primary_key=True),
    Column("chunk_id", String(128), primary_key=True),
    Column("document_revision_id", String(128), nullable=False),
    Column("anchor_json", JSON, nullable=False),
    Column("fragment_snapshot_id", String(64), nullable=False),
    Column("fragment_node_id", String(128), nullable=False),
    uniques=(
        (
            ("version", "node_id", "source_revision_id", "chunk_id"),
            "uq_graph_project_node_source_project_pk",
        ),
    ),
    parents=(
        (
            ("version", "node_id"),
            "graph_project_node",
            ("version", "node_id"),
            "fk_graph_project_node_source_node",
        ),
    ),
)
Index(
    "ix_graph_project_node_source_chunk",
    graph_project_node_source.c.project_id,
    graph_project_node_source.c.version,
    graph_project_node_source.c.chunk_id,
)

graph_project_edge_evidence = _scoped(
    "graph_project_edge_evidence",
    Column("version", Integer, primary_key=True),
    Column("edge_id", String(128), primary_key=True),
    Column("source_revision_id", String(128), primary_key=True),
    Column("chunk_id", String(128), primary_key=True),
    Column("document_revision_id", String(128), nullable=False),
    Column("anchor_json", JSON, nullable=False),
    Column("content_digest", String(71), nullable=False),
    Column("fragment_snapshot_id", String(64), nullable=False),
    Column("fragment_edge_id", String(128), nullable=False),
    uniques=(
        (
            ("version", "edge_id", "source_revision_id", "chunk_id"),
            "uq_graph_project_edge_evidence_project_pk",
        ),
    ),
    parents=(
        (
            ("version", "edge_id"),
            "graph_project_edge",
            ("version", "edge_id"),
            "fk_graph_project_edge_evidence_edge",
        ),
    ),
)
Index(
    "ix_graph_project_edge_evidence_chunk",
    graph_project_edge_evidence.c.project_id,
    graph_project_edge_evidence.c.version,
    graph_project_edge_evidence.c.chunk_id,
)

graph_project_alias = _scoped(
    "graph_project_alias",
    Column("version", Integer, primary_key=True),
    # 255, not the 512 used for other normalized-text columns in this module:
    # under the server's utf8mb4 default, (version, alias_norm, node_id,
    # project_id) at 512 chars overflows MySQL's 3072-byte composite index
    # limit by 4 bytes (confirmed against a live MySQL 8.0 instance); 255
    # keeps a comfortable margin for a normalized alias term.
    Column("alias_norm", String(255), primary_key=True),
    Column("node_id", String(128), primary_key=True),
    Column("origin", String(8), nullable=False),
    uniques=((("version", "alias_norm", "node_id"), "uq_graph_project_alias_project_pk"),),
    parents=(
        (
            ("version", "node_id"),
            "graph_project_node",
            ("version", "node_id"),
            "fk_graph_project_alias_node",
        ),
    ),
)

graph_project_community = _scoped(
    "graph_project_community",
    Column("version", Integer, primary_key=True),
    Column("community_id", String(64), primary_key=True),
    Column("label", String(512), nullable=False),
    Column("size", Integer, nullable=False),
    uniques=((("version", "community_id"), "uq_graph_project_community_project_pk"),),
    parents=(
        (
            ("version",),
            "graph_project_version",
            ("version",),
            "fk_graph_project_community_version",
        ),
    ),
)

graph_merge_log = _scoped(
    "graph_merge_log",
    Column("version", Integer, primary_key=True),
    Column("node_id", String(128), primary_key=True),
    Column("merged_from", JSON, nullable=False),
    Column("rule", String(16), nullable=False),
    uniques=((("version", "node_id"), "uq_graph_merge_log_project_pk"),),
    parents=(
        (
            ("version", "node_id"),
            "graph_project_node",
            ("version", "node_id"),
            "fk_graph_merge_log_node",
        ),
    ),
)

graph_project_merge_job = _scoped(
    "graph_project_merge_job",
    Column("due_at", DATETIME(fsp=6), nullable=True),
    Column("last_reason", String(32), nullable=True),
    Column("claimed_due_at", DATETIME(fsp=6), nullable=True),
    Column("lease_owner", String(128), nullable=True),
    Column("lease_token", String(64), nullable=True),
    Column("lease_expires_at", DATETIME(fsp=6), nullable=True),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("failure_code", String(64), nullable=True),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
    uniques=(((), "uq_graph_project_merge_job_project_pk"),),
)

PROJECT_GRAPH_TABLES: tuple[Table, ...] = (
    graph_project_version,
    graph_project_node,
    graph_project_edge,
    graph_project_node_source,
    graph_project_edge_evidence,
    graph_project_alias,
    graph_project_community,
    graph_merge_log,
    graph_project_merge_job,
)


async def _insert_batches(
    session: AsyncSession, table: Table, rows: list[dict[str, object]]
) -> None:
    buffer: list[dict[str, object]] = []
    for row in rows:
        buffer.append(row)
        if len(buffer) >= _BATCH_SIZE:
            await session.execute(insert(table).values(buffer))
            buffer = []
    if buffer:
        await session.execute(insert(table).values(buffer))


async def publish_project_version(
    session: AsyncSession,
    scope: ProjectScopeContext,
    draft: ProjectGraphDraft,
    *,
    version: int,
    now: datetime,
) -> ProjectGraphVersion:
    """Publish one immutable merged-graph version in the caller-owned transaction.

    Idempotency against a re-run with the same `version` is enforced by raising
    a clear `ValueError` when a version row already exists; the caller (a
    lease-guarded merge job) guarantees version uniqueness and must not retry
    silently over an existing version.
    """

    scope = require_project_scope(scope)
    if not session.in_transaction():
        raise ValueError("project graph publication requires an active transaction")
    common = scope_values(scope)
    existing = (
        await session.execute(
            select(graph_project_version.c.version)
            .where(
                *scope_predicates(graph_project_version, scope),
                graph_project_version.c.version == version,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"project graph version {version} already exists")
    await session.execute(
        insert(graph_project_version).values(
            **common,
            version=version,
            status="MERGING",
            fragment_digest=draft.fragment_digest,
            node_count=len(draft.nodes),
            edge_count=len(draft.edges),
            merged_at=None,
            created_at=now,
        )
    )
    await _insert_batches(
        session,
        graph_project_node,
        [
            {
                **common,
                "version": version,
                "node_id": node.node_id,
                "label": node.label,
                "node_type": node.node_type,
                "canonical_key": node.canonical_key,
                "degree": node.degree,
                "community_id": node.community_id,
                "aliases": list(node.aliases),
            }
            for node in draft.nodes
        ],
    )
    await _insert_batches(
        session,
        graph_project_edge,
        [
            {
                **common,
                "version": version,
                "edge_id": edge.edge_id,
                "source_node_id": edge.source_node_id,
                "target_node_id": edge.target_node_id,
                "relation_type": edge.relation_type,
                "relation_label": edge.relation_label,
                "origin": edge.origin.value,
                "confidence": edge.confidence,
            }
            for edge in draft.edges
        ],
    )
    await _insert_batches(
        session,
        graph_project_node_source,
        [
            {
                **common,
                "version": version,
                "node_id": source.node_id,
                "source_revision_id": source.source_revision_id,
                "chunk_id": source.chunk_id,
                "document_revision_id": source.document_revision_id,
                "anchor_json": dict(source.anchor),
                "fragment_snapshot_id": source.fragment_snapshot_id,
                "fragment_node_id": source.fragment_node_id,
            }
            for source in draft.node_sources
        ],
    )
    await _insert_batches(
        session,
        graph_project_edge_evidence,
        [
            {
                **common,
                "version": version,
                "edge_id": evidence.edge_id,
                "source_revision_id": evidence.source_revision_id,
                "chunk_id": evidence.chunk_id,
                "document_revision_id": evidence.document_revision_id,
                "anchor_json": dict(evidence.anchor),
                "content_digest": evidence.content_digest,
                "fragment_snapshot_id": evidence.fragment_snapshot_id,
                "fragment_edge_id": evidence.fragment_edge_id,
            }
            for evidence in draft.edge_evidence
        ],
    )
    await _insert_batches(
        session,
        graph_project_alias,
        [
            {
                **common,
                "version": version,
                "alias_norm": alias.alias_norm,
                "node_id": alias.node_id,
                "origin": alias.origin,
            }
            for alias in draft.aliases
        ],
    )
    await _insert_batches(
        session,
        graph_project_community,
        [
            {
                **common,
                "version": version,
                "community_id": community.community_id,
                "label": community.label,
                "size": community.size,
            }
            for community in draft.communities
        ],
    )
    await _insert_batches(
        session,
        graph_merge_log,
        [
            {
                **common,
                "version": version,
                "node_id": entry.node_id,
                "merged_from": [list(pair) for pair in entry.merged_from],
                "rule": entry.rule,
            }
            for entry in draft.merge_log
        ],
    )
    await session.execute(
        update(graph_project_version)
        .where(
            *scope_predicates(graph_project_version, scope),
            graph_project_version.c.version == version,
        )
        .values(status="READY", merged_at=now)
    )
    return ProjectGraphVersion(
        project_id=scope.project_id,
        version=version,
        status="READY",
        fragment_digest=draft.fragment_digest,
        node_count=len(draft.nodes),
        edge_count=len(draft.edges),
        merged_at=now,
    )


async def prune_project_versions(
    session: AsyncSession, scope: ProjectScopeContext, *, keep_latest: int = 2
) -> int:
    """Delete every version older than the `keep_latest` newest ones; returns
    the number of versions removed."""

    scope = require_project_scope(scope)
    versions = (
        (
            await session.execute(
                select(graph_project_version.c.version)
                .where(*scope_predicates(graph_project_version, scope))
                .order_by(graph_project_version.c.version.desc())
            )
        )
        .scalars()
        .all()
    )
    stale = versions[keep_latest:]
    if not stale:
        return 0
    for table in (
        graph_merge_log,
        graph_project_community,
        graph_project_alias,
        graph_project_edge_evidence,
        graph_project_node_source,
        graph_project_edge,
        graph_project_node,
        graph_project_version,
    ):
        await session.execute(
            delete(table).where(
                *scope_predicates(table, scope),
                table.c.version.in_(stale),
            )
        )
    return len(stale)


async def load_fragment_draft(
    session: AsyncSession, scope: ProjectScopeContext, snapshot_id: str
) -> GraphSnapshotDraft:
    """Read-only load of one graph fragment snapshot (nodes/edges/evidence/
    provenance including aliases and relation labels); extracted from
    `MysqlGraphStore._memory` so both it and future merge-job readers share
    one implementation."""

    scope = require_project_scope(scope)
    snapshot_row = (
        (
            await session.execute(
                select(graph_snapshot).where(
                    *scope_predicates(graph_snapshot, scope),
                    graph_snapshot.c.snapshot_id == snapshot_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if snapshot_row is None:
        raise GraphFactNotFound("graph snapshot not found")
    node_rows = (
        (
            await session.execute(
                select(graph_node).where(
                    *scope_predicates(graph_node, scope),
                    graph_node.c.snapshot_id == snapshot_id,
                )
            )
        )
        .mappings()
        .all()
    )
    edge_rows = (
        (
            await session.execute(
                select(graph_edge).where(
                    *scope_predicates(graph_edge, scope),
                    graph_edge.c.snapshot_id == snapshot_id,
                )
            )
        )
        .mappings()
        .all()
    )
    evidence_rows = (
        (
            await session.execute(
                select(graph_edge_evidence).where(
                    *scope_predicates(graph_edge_evidence, scope),
                    graph_edge_evidence.c.snapshot_id == snapshot_id,
                )
            )
        )
        .mappings()
        .all()
    )
    node_evidence_rows = (
        (
            await session.execute(
                select(graph_node_evidence).where(
                    *scope_predicates(graph_node_evidence, scope),
                    graph_node_evidence.c.snapshot_id == snapshot_id,
                )
            )
        )
        .mappings()
        .all()
    )
    provenance_rows = (
        (
            await session.execute(
                select(graph_inference_provenance).where(
                    *scope_predicates(graph_inference_provenance, scope),
                    graph_inference_provenance.c.snapshot_id == snapshot_id,
                )
            )
        )
        .mappings()
        .all()
    )
    all_evidence_rows = (*evidence_rows, *node_evidence_rows)
    unique_evidence_rows = {cast(str, row["evidence_id"]): row for row in all_evidence_rows}
    evidence = tuple(
        Evidence(
            row["evidence_id"],
            snapshot_id,
            row["source_revision_id"],
            row["document_revision_id"],
            row["chunk_id"],
            row["anchor_json"],
            row["content_digest"],
        )
        for row in unique_evidence_rows.values()
    )
    by_edge: dict[str, list[str]] = {}
    for row in evidence_rows:
        by_edge.setdefault(cast(str, row["edge_id"]), []).append(cast(str, row["evidence_id"]))
    by_node: dict[str, list[str]] = {}
    for row in node_evidence_rows:
        by_node.setdefault(cast(str, row["node_id"]), []).append(cast(str, row["evidence_id"]))
    return GraphSnapshotDraft(
        GraphSnapshot(
            snapshot_row["snapshot_id"],
            snapshot_row["project_id"],
            tuple(snapshot_row["source_revision_ids"]),
            tuple(snapshot_row["document_revision_ids"]),
            snapshot_row["source_set_digest"],
            snapshot_row["status"],
        ),
        tuple(
            GraphNode(
                row["node_id"],
                snapshot_id,
                row["label"],
                row["node_type"],
                row["canonical_key"],
                tuple(by_node.get(row["node_id"], ())),
                tuple(row["aliases"] or ()),
            )
            for row in node_rows
        ),
        tuple(
            GraphEdge(
                row["edge_id"],
                snapshot_id,
                row["source_node_id"],
                row["target_node_id"],
                row["relation_type"],
                RelationOrigin(row["origin"]),
                float(row["confidence"]),
                tuple(by_edge.get(row["edge_id"], ())),
                row["relation_label"] or "",
            )
            for row in edge_rows
        ),
        evidence,
        tuple(
            InferenceProvenance(
                row["provenance_id"],
                snapshot_id,
                row["edge_id"],
                tuple(row["input_fact_ids"]),
                row["rule_digest"],
            )
            for row in provenance_rows
        ),
    )


__all__ = [
    "PROJECT_GRAPH_TABLES",
    "graph_project_version",
    "graph_project_node",
    "graph_project_edge",
    "graph_project_node_source",
    "graph_project_edge_evidence",
    "graph_project_alias",
    "graph_project_community",
    "graph_merge_log",
    "graph_project_merge_job",
    "publish_project_version",
    "prune_project_versions",
    "load_fragment_draft",
]
