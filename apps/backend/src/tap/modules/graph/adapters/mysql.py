"""SQLAlchemy metadata and transactional MySQL graph store."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import cast

from sqlalchemy import (
    Column,
    Float,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.application.queries import InMemoryGraphStore
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSearchQuery,
    GraphSnapshot,
    GraphSnapshotDraft,
    GraphSubgraph,
    InferenceProvenance,
    NeighborQuery,
    PathQuery,
    RelationOrigin,
    source_set_digest,
)
from tap.modules.graph.ports.store import GraphFactNotFound
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import metadata


def _scope_constraints(name: str):
    return (
        ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name=f"fk_{name}_scope_project",
        ),
        ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name=f"fk_{name}_scope_actor",
        ),
    )


def _scoped(name: str, *columns, uniques=(), parents=()):
    return Table(
        name,
        metadata,
        *columns,
        Column("enterprise_id", String(128), nullable=False),
        Column("project_id", String(128), primary_key=True),
        Column("actor_id", String(128), nullable=False),
        Column("identity_mode", String(16), nullable=False),
        Column("identity_origin", String(16), nullable=False),
        *(
            UniqueConstraint("project_id", *fields, name=constraint)
            for fields, constraint in uniques
        ),
        *(
            ForeignKeyConstraint(
                ["project_id", *source],
                [f"{target}.project_id", *[f"{target}.{item}" for item in destination]],
                name=constraint,
            )
            for source, target, destination, constraint in parents
        ),
        *_scope_constraints(name),
    )


graph_snapshot = _scoped(
    "graph_snapshot",
    Column("snapshot_id", String(64), primary_key=True),
    Column("source_set_digest", String(71), nullable=False),
    Column("source_revision_ids", JSON, nullable=False),
    Column("document_revision_ids", JSON, nullable=False),
    Column("status", String(16), nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    uniques=((("snapshot_id",), "uq_graph_snapshot_project_pk"),),
)
graph_snapshot_revision = _scoped(
    "graph_snapshot_revision",
    Column("revision_id", String(64), primary_key=True),
    Column("snapshot_id", String(64), nullable=False),
    Column("revision_number", Integer, nullable=False),
    Column("content_digest", String(71), nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    uniques=((("snapshot_id", "revision_number"), "uq_graph_snapshot_revision_number"),),
    parents=(
        (
            ("snapshot_id",),
            "graph_snapshot",
            ("snapshot_id",),
            "fk_graph_snapshot_revision_snapshot",
        ),
    ),
)
graph_active_snapshot = _scoped(
    "graph_active_snapshot",
    Column("source_set_digest", String(71), primary_key=True),
    Column("snapshot_id", String(64), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
    parents=(
        (("snapshot_id",), "graph_snapshot", ("snapshot_id",), "fk_graph_active_snapshot_snapshot"),
    ),
)
graph_snapshot_document_revision = _scoped(
    "graph_snapshot_document_revision",
    Column("snapshot_id", String(64), primary_key=True),
    Column("document_revision_id", String(128), primary_key=True),
    parents=(
        (
            ("snapshot_id",),
            "graph_snapshot",
            ("snapshot_id",),
            "fk_graph_snapshot_document_snapshot",
        ),
    ),
)
graph_node = _scoped(
    "graph_node",
    Column("snapshot_id", String(64), primary_key=True),
    Column("node_id", String(128), primary_key=True),
    Column("label", String(512), nullable=False),
    Column("node_type", String(32), nullable=False),
    Column("canonical_key", String(512), nullable=False),
    uniques=(
        (("snapshot_id", "node_id"), "uq_graph_node_project_pk"),
        (("snapshot_id", "canonical_key"), "uq_graph_node_canonical"),
    ),
    parents=((("snapshot_id",), "graph_snapshot", ("snapshot_id",), "fk_graph_node_snapshot"),),
)
graph_edge = _scoped(
    "graph_edge",
    Column("snapshot_id", String(64), primary_key=True),
    Column("edge_id", String(128), primary_key=True),
    Column("source_node_id", String(128), nullable=False),
    Column("target_node_id", String(128), nullable=False),
    Column("relation_type", String(64), nullable=False),
    Column("origin", String(16), nullable=False),
    Column("confidence", Float, nullable=False),
    uniques=((("snapshot_id", "edge_id"), "uq_graph_edge_project_pk"),),
    parents=(
        (("snapshot_id",), "graph_snapshot", ("snapshot_id",), "fk_graph_edge_snapshot"),
        (
            ("snapshot_id", "source_node_id"),
            "graph_node",
            ("snapshot_id", "node_id"),
            "fk_graph_edge_source",
        ),
        (
            ("snapshot_id", "target_node_id"),
            "graph_node",
            ("snapshot_id", "node_id"),
            "fk_graph_edge_target",
        ),
    ),
)


def _evidence_table(name: str, owner_column: str, owner_table: str):
    return _scoped(
        name,
        Column("snapshot_id", String(64), primary_key=True),
        Column("evidence_id", String(128), primary_key=True),
        Column(owner_column, String(128), primary_key=True),
        Column("source_revision_id", String(128), nullable=False),
        Column("document_revision_id", String(128), nullable=False),
        Column("chunk_id", String(128), nullable=False),
        Column("anchor_json", JSON, nullable=False),
        Column("content_digest", String(71), nullable=False),
        parents=(
            (
                ("snapshot_id", owner_column),
                owner_table,
                ("snapshot_id", owner_column.removesuffix("_id") + "_id"),
                f"fk_{name}_owner",
            ),
        ),
    )


graph_node_evidence = _evidence_table("graph_node_evidence", "node_id", "graph_node")
graph_edge_evidence = _evidence_table("graph_edge_evidence", "edge_id", "graph_edge")
graph_inference_provenance = _scoped(
    "graph_inference_provenance",
    Column("snapshot_id", String(64), primary_key=True),
    Column("provenance_id", String(128), primary_key=True),
    Column("edge_id", String(128), nullable=False),
    Column("input_fact_ids", JSON, nullable=False),
    Column("rule_digest", String(71), nullable=False),
    parents=(
        (
            ("snapshot_id", "edge_id"),
            "graph_edge",
            ("snapshot_id", "edge_id"),
            "fk_graph_inference_edge",
        ),
    ),
)
graph_extraction_job = _scoped(
    "graph_extraction_job",
    Column("job_id", String(64), primary_key=True),
    Column("snapshot_id", String(64), nullable=False),
    Column("revision_id", String(128), nullable=False),
    Column("chunks_locator", String(2048), nullable=False),
    Column("extraction_profile_digest", String(71), nullable=False),
    Column("model_alias", String(128), nullable=False),
    Column("request_digest", String(71), nullable=False),
    Column("status", String(16), nullable=False),
    Column("lease_owner", String(128)),
    Column("lease_token", String(64)),
    Column("lease_expires_at", DATETIME(fsp=6)),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("failure_code", String(64)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
    uniques=((("request_digest",), "uq_graph_extraction_request"),),
    parents=(
        (("snapshot_id",), "graph_snapshot", ("snapshot_id",), "fk_graph_extraction_snapshot"),
    ),
)

GRAPH_TABLES = (
    graph_snapshot,
    graph_snapshot_revision,
    graph_active_snapshot,
    graph_snapshot_document_revision,
    graph_node,
    graph_edge,
    graph_node_evidence,
    graph_edge_evidence,
    graph_inference_provenance,
    graph_extraction_job,
)


async def publish_graph_snapshot(
    session: AsyncSession,
    scope: ProjectScopeContext,
    draft: GraphSnapshotDraft,
    *,
    now: datetime,
) -> GraphSnapshot:
    """Publish all graph facts in the caller-owned transaction."""

    scope = require_project_scope(scope)
    if not session.in_transaction():
        raise ValueError("graph publication requires an active transaction")
    if draft.snapshot.project_id != scope.project_id:
        raise ValueError("graph snapshot is outside Project scope")
    ready = replace(draft.snapshot, status="READY")
    existing = (
        (
            await session.execute(
                select(graph_snapshot)
                .where(
                    *scope_predicates(graph_snapshot, scope),
                    graph_snapshot.c.snapshot_id == ready.snapshot_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    common = scope_values(scope)
    if existing is not None:
        loaded = _snapshot(existing)
        same_identity = replace(loaded, status="CANDIDATE") == replace(ready, status="CANDIDATE")
        if not same_identity or loaded.status == "FAILED":
            raise ValueError("immutable graph snapshot conflict")
        if loaded.status == "READY":
            return loaded
        await session.execute(
            update(graph_snapshot)
            .where(
                *scope_predicates(graph_snapshot, scope),
                graph_snapshot.c.snapshot_id == ready.snapshot_id,
                graph_snapshot.c.status == "CANDIDATE",
            )
            .values(status="READY")
        )
    else:
        await session.execute(
            insert(graph_snapshot).values(
                **common,
                snapshot_id=ready.snapshot_id,
                source_set_digest=ready.source_set_digest,
                source_revision_ids=list(ready.source_revision_ids),
                document_revision_ids=list(ready.document_revision_ids),
                status=ready.status,
                created_at=now,
            )
        )
    await session.execute(
        insert(graph_snapshot_revision).values(
            **common,
            revision_id="grv_" + hashlib.sha256(ready.snapshot_id.encode()).hexdigest()[:32],
            snapshot_id=ready.snapshot_id,
            revision_number=1,
            content_digest=_draft_digest(draft),
            created_at=now,
        )
    )
    for document_id in ready.document_revision_ids:
        await session.execute(
            insert(graph_snapshot_document_revision).values(
                **common, snapshot_id=ready.snapshot_id, document_revision_id=document_id
            )
        )
    for node in draft.nodes:
        await session.execute(
            insert(graph_node).values(
                **common,
                snapshot_id=node.snapshot_id,
                node_id=node.node_id,
                label=node.label,
                node_type=node.node_type,
                canonical_key=node.canonical_key,
            )
        )
    for edge in draft.edges:
        await session.execute(
            insert(graph_edge).values(
                **common,
                snapshot_id=edge.snapshot_id,
                edge_id=edge.edge_id,
                source_node_id=edge.source_node_id,
                target_node_id=edge.target_node_id,
                relation_type=edge.relation_type,
                origin=edge.origin.value,
                confidence=edge.confidence,
            )
        )
    by_evidence = {item.evidence_id: item for item in draft.evidence}
    for node in draft.nodes:
        for evidence_id in node.evidence_ids:
            evidence_item = by_evidence[evidence_id]
            await session.execute(
                insert(graph_node_evidence).values(
                    **common,
                    snapshot_id=evidence_item.snapshot_id,
                    evidence_id=evidence_item.evidence_id,
                    node_id=node.node_id,
                    source_revision_id=evidence_item.source_revision_id,
                    document_revision_id=evidence_item.document_revision_id,
                    chunk_id=evidence_item.chunk_id,
                    anchor_json=dict(evidence_item.anchor),
                    content_digest=evidence_item.content_digest,
                )
            )
    for edge in draft.edges:
        for evidence_id in edge.evidence_ids:
            item = by_evidence[evidence_id]
            await session.execute(
                insert(graph_edge_evidence).values(
                    **common,
                    snapshot_id=item.snapshot_id,
                    evidence_id=item.evidence_id,
                    edge_id=edge.edge_id,
                    source_revision_id=item.source_revision_id,
                    document_revision_id=item.document_revision_id,
                    chunk_id=item.chunk_id,
                    anchor_json=dict(item.anchor),
                    content_digest=item.content_digest,
                )
            )
    for provenance in draft.provenance:
        await session.execute(
            insert(graph_inference_provenance).values(
                **common,
                snapshot_id=provenance.snapshot_id,
                provenance_id=provenance.provenance_id,
                edge_id=provenance.edge_id,
                input_fact_ids=list(provenance.input_fact_ids),
                rule_digest=provenance.rule_digest,
            )
        )
    pointer = mysql_insert(graph_active_snapshot).values(
        **common,
        source_set_digest=ready.source_set_digest,
        snapshot_id=ready.snapshot_id,
        updated_at=now,
    )
    await session.execute(
        pointer.on_duplicate_key_update(
            snapshot_id=pointer.inserted.snapshot_id,
            updated_at=pointer.inserted.updated_at,
            actor_id=pointer.inserted.actor_id,
            identity_mode=pointer.inserted.identity_mode,
            identity_origin=pointer.inserted.identity_origin,
        )
    )
    return ready


class MysqlGraphStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def publish(self, scope: ProjectScopeContext, draft: GraphSnapshotDraft) -> GraphSnapshot:
        scope = require_project_scope(scope)
        async with self._sessions() as session, session.begin():
            return await publish_graph_snapshot(
                session,
                scope,
                draft,
                now=datetime.now(timezone.utc).replace(tzinfo=None),
            )

    async def active_snapshot(
        self, scope: ProjectScopeContext, source_ids: tuple[str, ...]
    ) -> GraphSnapshot | None:
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(graph_snapshot)
                        .join(
                            graph_active_snapshot,
                            (graph_active_snapshot.c.project_id == graph_snapshot.c.project_id)
                            & (graph_active_snapshot.c.snapshot_id == graph_snapshot.c.snapshot_id),
                        )
                        .where(
                            *scope_predicates(graph_snapshot, scope),
                            graph_active_snapshot.c.source_set_digest
                            == source_set_digest(source_ids),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _snapshot(row)

    async def get_snapshot(
        self, scope: ProjectScopeContext, snapshot_id: str
    ) -> GraphSnapshot | None:
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            row = (
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
        return None if row is None else _snapshot(row)

    async def _memory(self, scope: ProjectScopeContext, snapshot_id: str) -> InMemoryGraphStore:
        scope = require_project_scope(scope)
        async with self._sessions() as session:
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
        draft = GraphSnapshotDraft(
            _snapshot(snapshot_row),
            tuple(
                GraphNode(
                    row["node_id"],
                    snapshot_id,
                    row["label"],
                    row["node_type"],
                    row["canonical_key"],
                    tuple(by_node.get(row["node_id"], ())),
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
        memory = InMemoryGraphStore()
        await memory.publish(
            scope, replace(draft, snapshot=replace(draft.snapshot, status="CANDIDATE"))
        )
        return memory

    async def search(self, scope: ProjectScopeContext, query: GraphSearchQuery) -> GraphSubgraph:
        return await (await self._memory(scope, query.snapshot_id)).search(scope, query)

    async def node_detail(
        self, scope: ProjectScopeContext, snapshot_id: str, node_id: str
    ) -> GraphSubgraph:
        return await (await self._memory(scope, snapshot_id)).node_detail(
            scope, snapshot_id, node_id
        )

    async def evidence(
        self, scope: ProjectScopeContext, snapshot_id: str, evidence_id: str
    ) -> Evidence:
        return await (await self._memory(scope, snapshot_id)).evidence(
            scope, snapshot_id, evidence_id
        )

    async def neighbors(self, scope: ProjectScopeContext, query: NeighborQuery) -> GraphSubgraph:
        return await (await self._memory(scope, query.snapshot_id)).neighbors(scope, query)

    async def bounded_path(self, scope: ProjectScopeContext, query: PathQuery) -> GraphSubgraph:
        return await (await self._memory(scope, query.snapshot_id)).bounded_path(scope, query)


def _snapshot(row) -> GraphSnapshot:
    return GraphSnapshot(
        row["snapshot_id"],
        row["project_id"],
        tuple(row["source_revision_ids"]),
        tuple(row["document_revision_ids"]),
        row["source_set_digest"],
        row["status"],
    )


def _draft_digest(draft: GraphSnapshotDraft) -> str:
    material = {
        "snapshotId": draft.snapshot.snapshot_id,
        "nodes": [
            [item.node_id, item.label, item.node_type, item.canonical_key, item.evidence_ids]
            for item in draft.nodes
        ],
        "edges": [
            [
                item.edge_id,
                item.source_node_id,
                item.target_node_id,
                item.relation_type,
                item.origin.value,
                item.confidence,
                item.evidence_ids,
            ]
            for item in draft.edges
        ],
        "evidence": [
            [
                item.evidence_id,
                item.source_revision_id,
                item.document_revision_id,
                item.chunk_id,
                dict(item.anchor),
                item.content_digest,
            ]
            for item in draft.evidence
        ],
        "provenance": [
            [item.provenance_id, item.edge_id, item.input_fact_ids, item.rule_digest]
            for item in draft.provenance
        ],
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
