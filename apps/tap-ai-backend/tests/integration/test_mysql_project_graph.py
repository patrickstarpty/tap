from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
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
