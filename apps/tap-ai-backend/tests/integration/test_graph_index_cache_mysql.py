from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.graph.adapters.mysql import MysqlGraphStore, graph_snapshot
from tap.modules.graph.application.cache import CachedSnapshotIndexes
from tap.modules.graph.domain.models import GraphSearchQuery, NeighborQuery
from tap.modules.graph.ports.store import GraphFactNotFound
from tests.unit.graph.test_snapshot_index import handcrafted_draft


def _draft(snapshot_id: str):
    draft = handcrafted_draft()
    snapshot = replace(draft.snapshot, snapshot_id=snapshot_id)
    return replace(
        draft,
        snapshot=snapshot,
        nodes=tuple(replace(node, snapshot_id=snapshot_id) for node in draft.nodes),
        edges=tuple(replace(edge, snapshot_id=snapshot_id) for edge in draft.edges),
        evidence=tuple(replace(item, snapshot_id=snapshot_id) for item in draft.evidence),
        provenance=tuple(replace(item, snapshot_id=snapshot_id) for item in draft.provenance),
    )


@pytest.mark.asyncio
async def test_mysql_graph_queries_load_each_ready_snapshot_index_once(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    node_reads = 0

    def count(_conn, _cursor, statement, *_args) -> None:
        nonlocal node_reads
        if "FROM graph_node " in statement or statement.rstrip().endswith("FROM graph_node"):
            node_reads += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        indexes = CachedSnapshotIndexes()
        store = MysqlGraphStore(async_sessionmaker(engine, expire_on_commit=False), indexes=indexes)
        await store.publish(VALIDATION_SCOPE, _draft("snapshot-cache-1"))

        first = await store.search(VALIDATION_SCOPE, GraphSearchQuery("snapshot-cache-1", "login"))
        second = await store.search(VALIDATION_SCOPE, GraphSearchQuery("snapshot-cache-1", "login"))
        neighbors = await store.neighbors(
            VALIDATION_SCOPE, NeighborQuery("snapshot-cache-1", "n-07", depth=2, node_limit=50)
        )

        assert first == second
        assert [node.node_id for node in first.nodes] == ["n-07", "n-09"]
        assert len(neighbors.nodes) == 9
        assert node_reads == 1
        assert indexes.stats()["misses"] == 1
        assert indexes.stats()["hits"] == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cached_index_never_bypasses_project_scope(owned_project_mysql) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        indexes = CachedSnapshotIndexes()
        store = MysqlGraphStore(async_sessionmaker(engine, expire_on_commit=False), indexes=indexes)
        await store.publish(VALIDATION_SCOPE, _draft("snapshot-cache-1"))
        await store.search(VALIDATION_SCOPE, GraphSearchQuery("snapshot-cache-1", "*"))
        other = ProjectScopeContext(
            enterprise_id=VALIDATION_SCOPE.enterprise_id,
            project_id="other-project",
            actor_id=VALIDATION_SCOPE.actor_id,
            identity_mode=IdentityMode.VALIDATION,
        )
        with pytest.raises(GraphFactNotFound, match="not found"):
            await store.search(other, GraphSearchQuery("snapshot-cache-1", "*"))
        with pytest.raises(GraphFactNotFound, match="not found"):
            await store.evidence(other, "snapshot-cache-1", "ev-1")
        assert indexes.stats()["misses"] == 1
        assert indexes.stats()["hits"] == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["CANDIDATE", "FAILED"])
async def test_non_ready_snapshots_are_served_but_never_cached(
    owned_project_mysql, status: str
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        indexes = CachedSnapshotIndexes()
        store = MysqlGraphStore(sessions, indexes=indexes)
        await store.publish(VALIDATION_SCOPE, _draft("snapshot-cache-1"))
        async with sessions() as session, session.begin():
            await session.execute(
                update(graph_snapshot)
                .where(graph_snapshot.c.snapshot_id == "snapshot-cache-1")
                .values(status=status)
            )

        for _ in range(2):
            result = await store.search(VALIDATION_SCOPE, GraphSearchQuery("snapshot-cache-1", "*"))
            assert len(result.nodes) == 12

        assert indexes.stats() == {"hits": 0, "misses": 0, "entries": 0, "evictions": 0}
    finally:
        await engine.dispose()
