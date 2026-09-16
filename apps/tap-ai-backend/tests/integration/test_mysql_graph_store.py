from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.governance.adapters.schema import project_audit
from tap.modules.graph.adapters.mysql import (
    MysqlGraphStore,
    graph_active_snapshot,
    graph_node_evidence,
    graph_snapshot_revision,
)
from tap.modules.graph.adapters.mysql_jobs import MysqlGraphJobStore
from tap.modules.graph.domain.jobs import GraphJobLeaseLost, GraphJobRequest, GraphJobStatus
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    NeighborQuery,
    RelationOrigin,
)
from tap.platform.db.schema import outbox


def _draft(snapshot_id: str) -> GraphSnapshotDraft:
    evidence = Evidence(
        "evidence-1",
        snapshot_id,
        "source-revision-1",
        "document-revision-1",
        "chunk-1",
        {"kind": "text", "start": 0, "end": 5},
        "sha256:" + "a" * 64,
    )
    return GraphSnapshotDraft(
        GraphSnapshot.create(
            snapshot_id=snapshot_id,
            project_id=VALIDATION_SCOPE.project_id,
            source_revision_ids=("source-revision-1",),
            document_revision_ids=("document-revision-1",),
        ),
        (
            GraphNode("node-1", snapshot_id, "Policy", "ENTITY", "policy", ("evidence-1",)),
            GraphNode("node-2", snapshot_id, "Claim", "ENTITY", "claim"),
        ),
        (
            GraphEdge(
                "edge-1",
                snapshot_id,
                "node-1",
                "node-2",
                "GOVERNS",
                RelationOrigin.EXTRACTED,
                1.0,
                ("evidence-1",),
            ),
        ),
        (evidence,),
        (),
    )


def _draft_for_snapshot(snapshot: GraphSnapshot) -> GraphSnapshotDraft:
    evidence = Evidence(
        "evidence-job-1",
        snapshot.snapshot_id,
        snapshot.source_revision_ids[0],
        snapshot.document_revision_ids[0],
        "chunk-job-1",
        {"kind": "text", "start": 0, "end": 5},
        "sha256:" + "b" * 64,
    )
    return GraphSnapshotDraft(
        snapshot,
        (
            GraphNode(
                "node-job-1",
                snapshot.snapshot_id,
                "Policy",
                "ENTITY",
                "policy",
                (evidence.evidence_id,),
            ),
        ),
        (),
        (evidence,),
        (),
    )


@pytest.mark.asyncio
async def test_mysql_graph_publish_preserves_history_and_atomically_moves_active_pointer(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        store = MysqlGraphStore(sessions)
        await store.publish(VALIDATION_SCOPE, _draft("snapshot-1"))
        await store.publish(VALIDATION_SCOPE, _draft("snapshot-2"))

        active = await store.active_snapshot(VALIDATION_SCOPE, ("source-revision-1",))
        historical = await store.get_snapshot(VALIDATION_SCOPE, "snapshot-1")
        graph = await store.neighbors(
            VALIDATION_SCOPE,
            NeighborQuery("snapshot-2", "node-1", depth=1, node_limit=10),
        )
        assert active is not None and active.snapshot_id == "snapshot-2"
        assert historical is not None and historical.snapshot_id == "snapshot-1"
        assert graph.nodes[0].evidence_ids == ("evidence-1",)
        assert graph.edges[0].evidence_ids == ("evidence-1",)

        async with sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(graph_active_snapshot)) == 1
            )
            assert (
                await session.scalar(select(func.count()).select_from(graph_snapshot_revision)) == 2
            )
            assert await session.scalar(select(func.count()).select_from(graph_node_evidence)) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_graph_store_fails_closed_for_cross_project_queries(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        store = MysqlGraphStore(async_sessionmaker(engine, expire_on_commit=False))
        await store.publish(VALIDATION_SCOPE, _draft("snapshot-1"))
        other = ProjectScopeContext(
            enterprise_id=VALIDATION_SCOPE.enterprise_id,
            project_id="other-project",
            actor_id=VALIDATION_SCOPE.actor_id,
            identity_mode=IdentityMode.VALIDATION,
        )
        assert await store.get_snapshot(other, "snapshot-1") is None
        with pytest.raises(LookupError, match="not found"):
            await store.neighbors(
                other,
                NeighborQuery("snapshot-1", "node-1", depth=1, node_limit=10),
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_graph_job_recovers_expired_lease_and_completes_all_facts_atomically(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    jobs = MysqlGraphJobStore(sessions)
    now = datetime(2026, 9, 13, 9, 0, 0)
    request = GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="source-revision-1",
        chunks_locator="art1.chunks",
        extraction_profile_digest="sha256:" + "1" * 64,
        model_alias="tapper-chat",
    )
    try:
        queued = await jobs.request(VALIDATION_SCOPE, request, now=now)
        replay = await jobs.request(
            VALIDATION_SCOPE,
            request,
            now=now + timedelta(seconds=1),
        )
        assert replay == queued
        first = (
            await jobs.claim(
                VALIDATION_SCOPE,
                worker_id="worker-1",
                now=now,
                lease_duration=timedelta(seconds=30),
                limit=1,
            )
        )[0]
        async with sessions() as session, session.begin():
            from tap.modules.graph.adapters.mysql import graph_extraction_job

            await session.execute(
                update(graph_extraction_job)
                .where(graph_extraction_job.c.job_id == first.job_id)
                .values(lease_expires_at=now - timedelta(seconds=1))
            )
        recovered = (
            await jobs.claim(
                VALIDATION_SCOPE,
                worker_id="worker-2",
                now=now + timedelta(seconds=31),
                lease_duration=timedelta(seconds=30),
                limit=1,
            )
        )[0]
        with pytest.raises(GraphJobLeaseLost):
            await jobs.complete(
                VALIDATION_SCOPE,
                first,
                _draft_for_snapshot(request.snapshot),
                now=now + timedelta(seconds=32),
            )
        ready = await jobs.complete(
            VALIDATION_SCOPE,
            recovered,
            _draft_for_snapshot(request.snapshot),
            now=now + timedelta(seconds=32),
        )
        assert ready.status is GraphJobStatus.READY

        async with sessions() as session:
            events = (
                (
                    await session.execute(
                        select(outbox.c.message_type).where(
                            outbox.c.project_id == VALIDATION_SCOPE.project_id,
                            outbox.c.aggregate_id == request.snapshot.snapshot_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            audits = (
                (
                    await session.execute(
                        select(project_audit.c.action).where(
                            project_audit.c.project_id == VALIDATION_SCOPE.project_id,
                            project_audit.c.resource_id == request.snapshot.snapshot_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert set(events) == {
                "knowledge.graph-snapshot.requested",
                "knowledge.graph-snapshot.ready",
            }
            assert set(audits) == {"graph-snapshot-requested", "graph-snapshot-ready"}
            assert (
                await session.scalar(select(func.count()).select_from(graph_snapshot_revision)) == 1
            )
    finally:
        await engine.dispose()
