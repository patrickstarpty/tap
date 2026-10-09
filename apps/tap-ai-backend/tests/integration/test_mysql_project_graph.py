from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.adapters.mysql_project import (
    graph_merge_log,
    graph_project_alias,
    graph_project_community,
    graph_project_edge_evidence,
    graph_project_node_source,
    graph_project_version,
    prune_project_versions,
    publish_project_version,
)
from tap.modules.graph.adapters.mysql_project_store import MysqlProjectGraphStore
from tap.modules.graph.domain.models import RelationOrigin
from tap.modules.graph.domain.project import (
    Alias,
    Community,
    EdgeEvidence,
    MergeLogEntry,
    NodeSource,
    ProjectEdge,
    ProjectGraphDraft,
    ProjectNode,
)

_ANCHOR = {"kind": "text", "start": 0, "end": 5}


def _draft(fragment_digest: str = "sha256:" + "a" * 64) -> ProjectGraphDraft:
    anchor = {"kind": "text", "start": 0, "end": 5}
    return ProjectGraphDraft(
        fragment_digest=fragment_digest,
        nodes=(
            ProjectNode(
                node_id="node-1",
                label="Policy",
                node_type="ENTITY",
                canonical_key="policy",
                degree=1,
                community_id="community-1",
                aliases=("policy-alias",),
            ),
            ProjectNode(
                node_id="node-2",
                label="Claim",
                node_type="ENTITY",
                canonical_key="claim",
                degree=1,
            ),
        ),
        edges=(
            ProjectEdge(
                edge_id="edge-1",
                source_node_id="node-1",
                target_node_id="node-2",
                relation_type="GOVERNS",
                relation_label="需要",
                origin=RelationOrigin.EXTRACTED,
                confidence=1.0,
            ),
        ),
        node_sources=(
            NodeSource(
                node_id="node-1",
                source_revision_id="source-revision-1",
                document_revision_id="document-revision-1",
                chunk_id="chunk-1",
                anchor=anchor,
                fragment_snapshot_id="fragment-snapshot-1",
                fragment_node_id="fragment-node-1",
            ),
        ),
        edge_evidence=(
            EdgeEvidence(
                edge_id="edge-1",
                source_revision_id="source-revision-1",
                document_revision_id="document-revision-1",
                chunk_id="chunk-1",
                anchor=anchor,
                content_digest="sha256:" + "b" * 64,
                fragment_snapshot_id="fragment-snapshot-1",
                fragment_edge_id="fragment-edge-1",
            ),
        ),
        aliases=(Alias(alias_norm="policy-alias", node_id="node-1", origin="LABEL"),),
        communities=(Community(community_id="community-1", label="Policy", size=2),),
        merge_log=(
            MergeLogEntry(
                node_id="node-1",
                merged_from=(("fragment-snapshot-1", "fragment-node-1"),),
                rule="EXACT",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_publish_version_writes_all_tables_and_prunes_old_versions(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        draft = _draft()
        now = datetime(2026, 10, 7, 9, 0, 0)
        for version in (1, 2, 3):
            async with sessions() as session, session.begin():
                published = await publish_project_version(
                    session, VALIDATION_SCOPE, draft, version=version, now=now
                )
            assert published.status == "READY"
            assert published.merged_at == now
            assert published.node_count == 2
            assert published.edge_count == 1

        async with sessions() as session, session.begin():
            removed = await prune_project_versions(session, VALIDATION_SCOPE, keep_latest=2)
        assert removed == 1

        async with sessions() as session:
            remaining = (
                (await session.execute(select(graph_project_version.c.version))).scalars().all()
            )
            assert sorted(remaining) == [2, 3]

            latest = (
                (
                    await session.execute(
                        select(graph_project_version).where(graph_project_version.c.version == 3)
                    )
                )
                .mappings()
                .one()
            )
            assert latest["status"] == "READY"
            assert latest["merged_at"] is not None

            for table in (
                graph_project_node_source,
                graph_project_edge_evidence,
                graph_project_alias,
                graph_project_community,
                graph_merge_log,
            ):
                count = await session.scalar(
                    select(func.count()).select_from(table).where(table.c.version == 3)
                )
                assert count == 1, table.name
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_publish_version_rejects_duplicate_version_instead_of_overwriting(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        draft = _draft()
        now = datetime(2026, 10, 7, 9, 0, 0)
        async with sessions() as session, session.begin():
            await publish_project_version(session, VALIDATION_SCOPE, draft, version=1, now=now)

        with pytest.raises(ValueError, match="already exists"):
            async with sessions() as session, session.begin():
                await publish_project_version(session, VALIDATION_SCOPE, draft, version=1, now=now)
    finally:
        await engine.dispose()


def _draft_with_extra_node(fragment_digest: str) -> ProjectGraphDraft:
    base = _draft()
    extra = ProjectNode(
        node_id="node-3", label="Payment", node_type="ENTITY", canonical_key="payment", degree=0
    )
    return replace(base, fragment_digest=fragment_digest, nodes=base.nodes + (extra,))


def _source_filter_draft() -> ProjectGraphDraft:
    """Node A's only evidence is in rev-1; B and C's is in rev-2. Edge A-B's
    evidence is rev-1, edge B-C's is rev-2 — filtering on rev-2 should drop A
    and edge A-B but keep B, C and edge B-C."""

    nodes = (
        ProjectNode(node_id="node-a", label="A", node_type="ENTITY", canonical_key="a", degree=1),
        ProjectNode(node_id="node-b", label="B", node_type="ENTITY", canonical_key="b", degree=2),
        ProjectNode(node_id="node-c", label="C", node_type="ENTITY", canonical_key="c", degree=1),
    )
    edges = (
        ProjectEdge(
            "edge-ab", "node-a", "node-b", "RELATED_TO", "relates", RelationOrigin.EXTRACTED, 1.0
        ),
        ProjectEdge(
            "edge-bc", "node-b", "node-c", "RELATED_TO", "relates", RelationOrigin.EXTRACTED, 1.0
        ),
    )
    node_sources = (
        NodeSource(
            "node-a", "rev-1", "document-1", "chunk-a", _ANCHOR, "fragment-snapshot-1", "fn-a"
        ),
        NodeSource(
            "node-b", "rev-2", "document-2", "chunk-b", _ANCHOR, "fragment-snapshot-1", "fn-b"
        ),
        NodeSource(
            "node-c", "rev-2", "document-2", "chunk-c", _ANCHOR, "fragment-snapshot-1", "fn-c"
        ),
    )
    edge_evidence = (
        EdgeEvidence(
            "edge-ab",
            "rev-1",
            "document-1",
            "chunk-a",
            _ANCHOR,
            "sha256:" + "c" * 64,
            "fragment-snapshot-1",
            "fe-ab",
        ),
        EdgeEvidence(
            "edge-bc",
            "rev-2",
            "document-2",
            "chunk-b",
            _ANCHOR,
            "sha256:" + "d" * 64,
            "fragment-snapshot-1",
            "fe-bc",
        ),
    )
    return ProjectGraphDraft(
        fragment_digest="sha256:" + "f" * 64,
        nodes=nodes,
        edges=edges,
        node_sources=node_sources,
        edge_evidence=edge_evidence,
        aliases=(),
        communities=(),
        merge_log=(),
    )


@pytest.mark.asyncio
async def test_version_switch_invalidates_cache_and_serves_new_version(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime(2026, 10, 7, 9, 0, 0)
        async with sessions() as session, session.begin():
            await publish_project_version(
                session,
                VALIDATION_SCOPE,
                _draft(fragment_digest="sha256:" + "1" * 64),
                version=1,
                now=now,
            )

        store = MysqlProjectGraphStore(sessions)
        current = await store.get_current(VALIDATION_SCOPE)
        assert current is not None and current.version == 1
        overview_v1 = await store.overview(VALIDATION_SCOPE)
        assert {node.node_id for node in overview_v1.nodes} == {"node-1", "node-2"}

        async with sessions() as session, session.begin():
            await publish_project_version(
                session,
                VALIDATION_SCOPE,
                _draft_with_extra_node(fragment_digest="sha256:" + "2" * 64),
                version=2,
                now=now,
            )

        current_v2 = await store.get_current(VALIDATION_SCOPE)
        assert current_v2 is not None and current_v2.version == 2
        overview_v2 = await store.overview(VALIDATION_SCOPE)
        assert {node.node_id for node in overview_v2.nodes} == {"node-1", "node-2", "node-3"}
        assert store._cache.loaded_versions(VALIDATION_SCOPE.project_id) == (1, 2)

        # v1 is no longer current but is still inside the 2-version retention
        # window, so a pinned read for it must keep serving v1's rows even
        # after the switch to v2.
        pinned_v1 = await store.overview(VALIDATION_SCOPE, version=1)
        assert pinned_v1.version == 1
        assert {node.node_id for node in pinned_v1.nodes} == {"node-1", "node-2"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_overview_source_filter_drops_nodes_without_in_filter_evidence(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime(2026, 10, 7, 9, 0, 0)
        async with sessions() as session, session.begin():
            await publish_project_version(
                session, VALIDATION_SCOPE, _source_filter_draft(), version=1, now=now
            )

        store = MysqlProjectGraphStore(sessions)
        overview = await store.overview(VALIDATION_SCOPE, source_revision_ids=("rev-2",))
        assert {node.node_id for node in overview.nodes} == {"node-b", "node-c"}
        assert {edge.edge_id for edge in overview.edges} == {"edge-bc"}
        assert overview.evidence
        assert all(evidence.source_revision_id == "rev-2" for evidence in overview.evidence)
        assert overview.sources
        assert all(source.source_revision_id == "rev-2" for source in overview.sources)

        neighbors = await store.neighbors(
            VALIDATION_SCOPE, "node-b", source_revision_ids=("rev-2",)
        )
        assert {node.node_id for node in neighbors.nodes} == {"node-b", "node-c"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_queries_are_project_scoped(owned_project_mysql) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime(2026, 10, 7, 9, 0, 0)
        async with sessions() as session, session.begin():
            await publish_project_version(session, VALIDATION_SCOPE, _draft(), version=1, now=now)

        store = MysqlProjectGraphStore(sessions)
        other = ProjectScopeContext(
            enterprise_id=VALIDATION_SCOPE.enterprise_id,
            project_id="other-project",
            actor_id=VALIDATION_SCOPE.actor_id,
            identity_mode=VALIDATION_SCOPE.identity_mode,
        )
        assert await store.get_current(other) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pinned_cached_version_reads_skip_get_current(owned_project_mysql) -> None:
    """Once a pinned, explicit
    `version` is already cached, `_loaded` must serve it straight from
    `ProjectGraphCache` without a `get_current` MySQL round-trip first -- a
    relation-analysis run that pins one version for its whole run (seed,
    expand, path, rank -- several store calls, all with the same explicit
    `version=`) must not issue one `get_current` SELECT per call."""
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime(2026, 10, 7, 9, 0, 0)
        async with sessions() as session, session.begin():
            await publish_project_version(
                session,
                VALIDATION_SCOPE,
                _draft(fragment_digest="sha256:" + "4" * 64),
                version=1,
                now=now,
            )

        store = MysqlProjectGraphStore(sessions)
        current = await store.get_current(VALIDATION_SCOPE)
        assert current is not None and current.version == 1
        # Warm the cache for the pinned version (mirrors the agent's own
        # initial `get_current`, before it starts pinning `version=1`).
        await store.overview(VALIDATION_SCOPE, version=1)

        calls = 0
        original_get_current = store.get_current

        async def counting_get_current(scope: ProjectScopeContext):
            nonlocal calls
            calls += 1
            return await original_get_current(scope)

        store.get_current = counting_get_current  # type: ignore[method-assign]

        await store.overview(VALIDATION_SCOPE, version=1)
        await store.neighbors(VALIDATION_SCOPE, "node-1", version=1)
        await store.nodes(VALIDATION_SCOPE, ("node-1",), version=1)
        await store.path(VALIDATION_SCOPE, "node-1", "node-2", version=1)

        assert calls == 0
    finally:
        await engine.dispose()
