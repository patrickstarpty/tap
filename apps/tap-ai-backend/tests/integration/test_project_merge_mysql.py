from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.entrypoints import graph_operator, tapper_runtime
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.adapters.model_gateway_extraction import GRAPH_EXTRACTION_PROFILE_DIGEST
from tap.modules.graph.adapters.mysql import MysqlGraphStore, graph_fragment_batch, graph_node
from tap.modules.graph.adapters.mysql import graph_snapshot as graph_snapshot_table
from tap.modules.graph.adapters.mysql_jobs import MysqlGraphJobStore
from tap.modules.graph.adapters.mysql_merge import MysqlMergeInputs, MysqlProjectMergeQueue
from tap.modules.graph.adapters.mysql_project import (
    graph_merge_log,
    graph_project_alias,
    graph_project_edge,
    graph_project_merge_job,
    graph_project_node,
    graph_project_node_source,
    graph_project_version,
)
from tap.modules.graph.application.alignment import alignment_key
from tap.modules.graph.application.fragments import assemble_fragment
from tap.modules.graph.application.merge_jobs import ProjectMergeLeaseLost
from tap.modules.graph.application.merger import ProjectGraphMerger
from tap.modules.graph.domain.jobs import (
    GraphBatchStatus,
    GraphFragmentBatch,
    GraphJobRequest,
    GraphJobStatus,
)
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshotDraft,
    RelationOrigin,
)
from tap.modules.graph.domain.project import project_node_id
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_document,
    knowledge_document_revision,
    knowledge_source,
)
from tap.platform.db.project_scope import scope_predicates, scope_values
from tests.object_settings import S3_SETTINGS
from tests.owned_mysql import owned_project_database_url

NOW = datetime(2026, 10, 7, 9, 0, 0)
# MysqlMergeInputs only replays fragments whose extraction_profile_digest matches
# the current extraction profile, so fragments seeded for these tests must use
# the real digest -- not an arbitrary placeholder -- or the merge join finds
# nothing.
_PROFILE_DIGEST = GRAPH_EXTRACTION_PROFILE_DIGEST


class FakeCurrentRevisions:
    """A mutable set of currently-published revision ids, for tests to shrink
    (simulating a source delete) between merges."""

    def __init__(self, revision_ids: set[str]) -> None:
        self.revision_ids = revision_ids

    async def current_revision_ids(self, scope) -> frozenset[str]:
        del scope
        return frozenset(self.revision_ids)


@pytest_asyncio.fixture
async def sessions(owned_project_mysql):
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _request(revision_id: str) -> GraphJobRequest:
    return GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id=revision_id,
        chunks_locator=f"art-{revision_id}.chunks",
        extraction_profile_digest=_PROFILE_DIGEST,
        model_alias="qwen-plus",
    )


def _draft(
    request: GraphJobRequest,
    nodes: list[tuple[str, str, str, str]],
    edges: list[tuple[str, str, str, str, float, str]] | None = None,
) -> GraphSnapshotDraft:
    snapshot = request.snapshot
    evidence = []
    graph_nodes = []
    for node_id, label, node_type, canonical_key in nodes:
        evidence_id = f"ev-{snapshot.snapshot_id}-{node_id}"
        evidence.append(
            Evidence(
                evidence_id=evidence_id,
                snapshot_id=snapshot.snapshot_id,
                source_revision_id=snapshot.source_revision_ids[0],
                document_revision_id=snapshot.document_revision_ids[0],
                chunk_id=f"chunk-{snapshot.snapshot_id}-{node_id}",
                anchor={"kind": "text", "start": 0, "end": 4},
                content_digest="sha256:" + ("c" * 64),
            )
        )
        graph_nodes.append(
            GraphNode(
                node_id, snapshot.snapshot_id, label, node_type, canonical_key, (evidence_id,)
            )
        )
    graph_edges = []
    for edge_id, source, target, relation_type, confidence, relation_label in edges or []:
        evidence_id = f"ev-{snapshot.snapshot_id}-{edge_id}"
        evidence.append(
            Evidence(
                evidence_id=evidence_id,
                snapshot_id=snapshot.snapshot_id,
                source_revision_id=snapshot.source_revision_ids[0],
                document_revision_id=snapshot.document_revision_ids[0],
                chunk_id=f"chunk-{snapshot.snapshot_id}-{edge_id}",
                anchor={"kind": "text", "start": 0, "end": 4},
                content_digest="sha256:" + ("d" * 64),
            )
        )
        graph_edges.append(
            GraphEdge(
                edge_id=edge_id,
                snapshot_id=snapshot.snapshot_id,
                source_node_id=source,
                target_node_id=target,
                relation_type=relation_type,
                origin=RelationOrigin.EXTRACTED,
                confidence=confidence,
                evidence_ids=(evidence_id,),
                relation_label=relation_label,
            )
        )
    return GraphSnapshotDraft(snapshot, tuple(graph_nodes), tuple(graph_edges), tuple(evidence), ())


async def _seed_fragment(
    sessions,
    *,
    revision_id: str,
    nodes: list[tuple[str, str, str, str]],
    edges: list[tuple[str, str, str, str, float, str]] | None = None,
) -> GraphJobRequest:
    """Seed one READY fragment snapshot using `MysqlGraphStore.publish` for the
    graph facts, and `MysqlGraphJobStore.request` for the matching job row the
    merge worker's `MysqlMergeInputs` join needs."""

    request = _request(revision_id)
    draft = _draft(request, nodes, edges)
    await MysqlGraphJobStore(sessions).request(VALIDATION_SCOPE, request, now=NOW)
    await MysqlGraphStore(sessions).publish(VALIDATION_SCOPE, draft)
    return request


@pytest.mark.asyncio
async def test_full_replay_merges_cross_fragment_entities(sessions) -> None:
    rev1 = await _seed_fragment(
        sessions, revision_id="rev-1", nodes=[("a1", "健康告知", "ENTITY", "健康告知")]
    )
    rev2 = await _seed_fragment(
        sessions,
        revision_id="rev-2",
        nodes=[("b1", "健康告知", "ENTITY", "健康告知"), ("b2", "理赔", "PROCESS", "理赔")],
        edges=[("be1", "b1", "b2", "REQUIRES", 0.9, "需要")],
    )
    queue = MysqlProjectMergeQueue(sessions)
    inputs = MysqlMergeInputs(sessions, FakeCurrentRevisions({rev1.revision_id, rev2.revision_id}))
    await queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=NOW)
    claim = await queue.claim(
        VALIDATION_SCOPE, worker_id="worker-1", now=NOW, lease_duration=timedelta(seconds=300)
    )
    assert claim is not None
    fragments = await inputs.load_fragments(VALIDATION_SCOPE)
    assert {record.revision_id for record in fragments} == {"rev-1", "rev-2"}
    draft = ProjectGraphMerger().merge(VALIDATION_SCOPE, fragments)

    version = await queue.complete(VALIDATION_SCOPE, claim, draft, now=NOW)

    assert version is not None and version.version == 1 and version.status == "READY"
    async with sessions() as session:
        node_rows = (
            (
                await session.execute(
                    select(graph_project_node).where(graph_project_node.c.version == 1)
                )
            )
            .mappings()
            .all()
        )
        assert len(node_rows) == 2
        disclosure = next(row for row in node_rows if row["canonical_key"] == "健康告知")
        source_count = await session.scalar(
            select(func.count())
            .select_from(graph_project_node_source)
            .where(
                graph_project_node_source.c.version == 1,
                graph_project_node_source.c.node_id == disclosure["node_id"],
            )
        )
        assert source_count == 2
        merge_log_row = (
            (
                await session.execute(
                    select(graph_merge_log).where(
                        graph_merge_log.c.version == 1,
                        graph_merge_log.c.node_id == disclosure["node_id"],
                    )
                )
            )
            .mappings()
            .one()
        )
        assert merge_log_row["rule"] == "EXACT"


@pytest.mark.asyncio
async def test_replay_after_source_delete_drops_orphan_nodes(sessions) -> None:
    rev1 = await _seed_fragment(
        sessions,
        revision_id="rev-1",
        nodes=[("a1", "A", "ENTITY", "A"), ("a2", "共享实体", "ENTITY", "共享实体")],
        edges=[("ae1", "a1", "a2", "RELATED_TO", 0.8, "关联")],
    )
    rev2 = await _seed_fragment(
        sessions,
        revision_id="rev-2",
        nodes=[("b1", "共享实体", "ENTITY", "共享实体"), ("b2", "Y", "ENTITY", "Y")],
        edges=[("be1", "b1", "b2", "RELATED_TO", 0.8, "关联")],
    )
    queue = MysqlProjectMergeQueue(sessions)
    current_revisions = FakeCurrentRevisions({rev1.revision_id, rev2.revision_id})
    inputs = MysqlMergeInputs(sessions, current_revisions)
    merger = ProjectGraphMerger()

    await queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=NOW)
    claim = await queue.claim(
        VALIDATION_SCOPE, worker_id="worker-1", now=NOW, lease_duration=timedelta(seconds=300)
    )
    assert claim is not None
    fragments = await inputs.load_fragments(VALIDATION_SCOPE)
    draft = merger.merge(VALIDATION_SCOPE, fragments)
    version1 = await queue.complete(VALIDATION_SCOPE, claim, draft, now=NOW)
    assert version1 is not None and version1.version == 1

    node_id_a = project_node_id("ENTITY", alignment_key("A"))
    node_id_shared = project_node_id("ENTITY", alignment_key("共享实体"))
    node_id_y = project_node_id("ENTITY", alignment_key("Y"))

    # Simulate a source delete dropping rev-1 from the currently-published set.
    current_revisions.revision_ids.discard(rev1.revision_id)
    await queue.request(VALIDATION_SCOPE, reason="source-deleted", now=NOW + timedelta(seconds=1))
    claim2 = await queue.claim(
        VALIDATION_SCOPE,
        worker_id="worker-1",
        now=NOW + timedelta(seconds=1),
        lease_duration=timedelta(seconds=300),
    )
    assert claim2 is not None
    fragments2 = await inputs.load_fragments(VALIDATION_SCOPE)
    assert {record.revision_id for record in fragments2} == {"rev-2"}
    draft2 = merger.merge(VALIDATION_SCOPE, fragments2)
    version2 = await queue.complete(
        VALIDATION_SCOPE, claim2, draft2, now=NOW + timedelta(seconds=2)
    )

    assert version2 is not None and version2.version == 2

    async with sessions() as session:
        v2_node_ids = {
            row["node_id"]
            for row in (
                (
                    await session.execute(
                        select(graph_project_node.c.node_id).where(
                            graph_project_node.c.version == 2
                        )
                    )
                )
                .mappings()
                .all()
            )
        }
        assert v2_node_ids == {node_id_shared, node_id_y}
        v2_alias_node_ids = {
            row["node_id"]
            for row in (
                (
                    await session.execute(
                        select(graph_project_alias.c.node_id).where(
                            graph_project_alias.c.version == 2
                        )
                    )
                )
                .mappings()
                .all()
            )
        }
        assert node_id_a not in v2_alias_node_ids
        v2_edge_endpoints = (
            (
                await session.execute(
                    select(
                        graph_project_edge.c.source_node_id, graph_project_edge.c.target_node_id
                    ).where(graph_project_edge.c.version == 2)
                )
            )
            .mappings()
            .all()
        )
        for row in v2_edge_endpoints:
            assert node_id_a not in (row["source_node_id"], row["target_node_id"])

        v1_node_ids = {
            row["node_id"]
            for row in (
                (
                    await session.execute(
                        select(graph_project_node.c.node_id).where(
                            graph_project_node.c.version == 1
                        )
                    )
                )
                .mappings()
                .all()
            )
        }
        assert v1_node_ids == {node_id_a, node_id_shared, node_id_y}
        for shared_node_id in (node_id_shared, node_id_y):
            assert shared_node_id in v1_node_ids and shared_node_id in v2_node_ids

        version0_exists = await session.scalar(
            select(func.count())
            .select_from(graph_project_version)
            .where(graph_project_version.c.version == 0)
        )
        assert version0_exists == 0


@pytest.mark.asyncio
async def test_lost_lease_cannot_publish_duplicate_version(sessions) -> None:
    rev1 = await _seed_fragment(
        sessions, revision_id="rev-1", nodes=[("a1", "健康告知", "ENTITY", "健康告知")]
    )
    queue = MysqlProjectMergeQueue(sessions)
    inputs = MysqlMergeInputs(sessions, FakeCurrentRevisions({rev1.revision_id}))
    merger = ProjectGraphMerger()

    await queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=NOW)
    claim1 = await queue.claim(
        VALIDATION_SCOPE, worker_id="worker-1", now=NOW, lease_duration=timedelta(seconds=300)
    )
    assert claim1 is not None

    # worker-1's lease has already expired by the time it tries to complete.
    async with sessions() as session, session.begin():
        await session.execute(
            update(graph_project_merge_job)
            .where(*scope_predicates(graph_project_merge_job, VALIDATION_SCOPE))
            .values(lease_expires_at=NOW - timedelta(seconds=1))
        )

    claim2 = await queue.claim(
        VALIDATION_SCOPE,
        worker_id="worker-2",
        now=NOW + timedelta(seconds=1),
        lease_duration=timedelta(seconds=300),
    )
    assert claim2 is not None
    fragments = await inputs.load_fragments(VALIDATION_SCOPE)
    draft = merger.merge(VALIDATION_SCOPE, fragments)

    version = await queue.complete(VALIDATION_SCOPE, claim2, draft, now=NOW + timedelta(seconds=2))
    assert version is not None and version.version == 1

    with pytest.raises(ProjectMergeLeaseLost):
        await queue.complete(VALIDATION_SCOPE, claim1, draft, now=NOW + timedelta(seconds=3))

    async with sessions() as session:
        ready_count = await session.scalar(
            select(func.count())
            .select_from(graph_project_version)
            .where(
                graph_project_version.c.status == "READY",
                graph_project_version.c.fragment_digest == draft.fragment_digest,
            )
        )
    assert ready_count == 1


@pytest.mark.asyncio
async def test_fragment_completion_enqueues_merge_in_the_same_transaction(sessions) -> None:
    queue = MysqlProjectMergeQueue(sessions)
    jobs = MysqlGraphJobStore(sessions, merge_queue=queue)
    request = _request("rev-1")
    await jobs.request(VALIDATION_SCOPE, request, now=NOW)
    claim = (
        await jobs.claim(
            VALIDATION_SCOPE,
            worker_id="worker-1",
            now=NOW,
            lease_duration=timedelta(seconds=60),
            limit=1,
        )
    )[0]
    draft = _draft(request, [("a1", "健康告知", "ENTITY", "健康告知")])

    await jobs.complete(VALIDATION_SCOPE, claim, draft, now=NOW)

    async with sessions() as session:
        row = (
            (
                await session.execute(
                    select(graph_project_merge_job).where(
                        *scope_predicates(graph_project_merge_job, VALIDATION_SCOPE)
                    )
                )
            )
            .mappings()
            .one()
        )
    assert row["due_at"] is not None
    assert row["last_reason"] == "fragment-ready"


@pytest.mark.asyncio
async def test_reset_for_profile_clears_fragment_and_requeues_job(sessions) -> None:
    jobs = MysqlGraphJobStore(sessions)
    request = _request("rev-1")
    await jobs.request(VALIDATION_SCOPE, request, now=NOW)
    claim = (
        await jobs.claim(
            VALIDATION_SCOPE,
            worker_id="worker-1",
            now=NOW,
            lease_duration=timedelta(seconds=60),
            limit=1,
        )
    )[0]
    draft0 = _draft(request, [("n0", "Entity 0", "CONCEPT", "entity-0")])
    draft1 = _draft(request, [("n1", "Entity 1", "CONCEPT", "entity-1")])
    await jobs.record_batch(
        VALIDATION_SCOPE,
        claim,
        GraphFragmentBatch(
            claim.snapshot.snapshot_id, 0, ("chunk-0",), GraphBatchStatus.READY, draft=draft0
        ),
        now=NOW,
    )
    await jobs.record_batch(
        VALIDATION_SCOPE,
        claim,
        GraphFragmentBatch(
            claim.snapshot.snapshot_id, 1, ("chunk-1",), GraphBatchStatus.READY, draft=draft1
        ),
        now=NOW,
    )
    merged = assemble_fragment(claim.snapshot, [draft0, draft1])
    completed = await jobs.complete(VALIDATION_SCOPE, claim, merged, now=NOW)
    assert completed.status == GraphJobStatus.READY
    assert completed.snapshot.status == "READY"

    new_digest = "sha256:" + "b" * 64
    job = await jobs.reset_for_profile(
        VALIDATION_SCOPE,
        request.revision_id,
        extraction_profile_digest=new_digest,
        model_alias="qwen-max",
        now=NOW + timedelta(seconds=10),
    )

    assert job.status == GraphJobStatus.PENDING
    assert job.request_digest != request.request_digest
    assert job.extraction_profile_digest == new_digest
    assert job.model_alias == "qwen-max"

    async with sessions() as session:
        node_count = await session.scalar(
            select(func.count())
            .select_from(graph_node)
            .where(graph_node.c.snapshot_id == claim.snapshot.snapshot_id)
        )
        batch_count = await session.scalar(
            select(func.count())
            .select_from(graph_fragment_batch)
            .where(graph_fragment_batch.c.snapshot_id == claim.snapshot.snapshot_id)
        )
        snapshot_status = await session.scalar(
            select(graph_snapshot_table.c.status).where(
                graph_snapshot_table.c.snapshot_id == claim.snapshot.snapshot_id
            )
        )
    assert node_count == 0
    assert batch_count == 0
    assert snapshot_status == "CANDIDATE"

    # The worker must be able to run this job back to READY after the reset:
    # if `graph_snapshot_document_revision` wasn't cleared alongside the other
    # fact tables, re-publishing the same snapshot_id/document_revision_id pair
    # collides on its primary key instead of reaching READY.
    reclaim = (
        await jobs.claim(
            VALIDATION_SCOPE,
            worker_id="worker-2",
            now=NOW + timedelta(seconds=11),
            lease_duration=timedelta(seconds=60),
            limit=1,
        )
    )[0]
    draft2 = _draft(request, [("n2", "Entity 2", "CONCEPT", "entity-2")])
    await jobs.record_batch(
        VALIDATION_SCOPE,
        reclaim,
        GraphFragmentBatch(
            reclaim.snapshot.snapshot_id, 0, ("chunk-0",), GraphBatchStatus.READY, draft=draft2
        ),
        now=NOW + timedelta(seconds=12),
    )
    remerged = assemble_fragment(reclaim.snapshot, [draft2])
    recompleted = await jobs.complete(
        VALIDATION_SCOPE, reclaim, remerged, now=NOW + timedelta(seconds=13)
    )
    assert recompleted.status == GraphJobStatus.READY
    assert recompleted.snapshot.status == "READY"


@pytest.mark.asyncio
async def test_rebuild_cli_does_not_request_an_immediate_merge(owned_project_mysql, sessions):
    """`graph rebuild` must not request a merge itself: requesting one right
    after resetting every fragment to CANDIDATE would publish a near-empty
    graph before any fragment re-extracts. Fragments already request their
    own merge when they republish, so the queue must stay un-due here."""
    document_id = "doc_rebuild_cli"
    revision_id = "rev_rebuild_cli"
    async with sessions() as session, session.begin():
        await session.execute(
            insert(knowledge_source).values(
                **scope_values(VALIDATION_SCOPE),
                source_id="src_" + "9" * 32,
                name="rebuild-cli-source",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.execute(
            insert(knowledge_document).values(
                **scope_values(VALIDATION_SCOPE),
                document_id=document_id,
                source_id="src_" + "9" * 32,
                filename="rebuild-cli.md",
                media_type="text/markdown",
                current_revision_id=None,
                source_content_hash="sha256:" + "1" * 64,
                dedupe_key="sha256:" + "2" * 64,
                staging_blob_locator=None,
                promoted_blob_locator=None,
                reservation_owner_token=None,
                reservation_expires_at=None,
                reservation_parser_version="tapper-parser-v1",
                reservation_chunker_version="tapper-chunker-v1",
                reservation_pipeline_version="tapper-ingestion-v1",
                status="ready",
                stage="ready",
                chunk_count=1,
                error_code=None,
                error_summary=None,
                activated_at=NOW,
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.execute(
            insert(knowledge_document_revision).values(
                **scope_values(VALIDATION_SCOPE),
                revision_id=revision_id,
                source_id="src_" + "9" * 32,
                document_id=document_id,
                source_content_hash="sha256:" + "1" * 64,
                original_blob_locator="tapper-originals/rebuild-cli.md",
                normalized_blob_locator="tapper-artifacts/rebuild-cli.normalized",
                chunks_blob_locator="tapper-artifacts/rebuild-cli.chunks",
                embeddings_blob_locator=None,
                parser_version="tapper-parser-v1",
                chunker_version="tapper-chunker-v1",
                pipeline_version="tapper-ingestion-v1",
                parse_inventory_attempt=1,
                parser_config_digest="sha256:" + "3" * 64,
                parse_inventory_digest="sha256:" + "4" * 64,
                chunk_manifest_digest="sha256:" + "5" * 64,
                projection_digest="sha256:" + "6" * 64,
                created_at=NOW,
            )
        )
        await session.execute(
            update(knowledge_document)
            .where(
                *scope_predicates(knowledge_document, VALIDATION_SCOPE),
                knowledge_document.c.document_id == document_id,
            )
            .values(current_revision_id=revision_id)
        )
    jobs = MysqlGraphJobStore(sessions)
    await jobs.request(VALIDATION_SCOPE, _request(revision_id), now=NOW)

    url = owned_project_database_url(owned_project_mysql)
    settings = replace(tapper_runtime.TapperSettings.from_mapping(S3_SETTINGS), database_url=url)
    operation = graph_operator.GraphOperation(
        command="rebuild",
        project_id=VALIDATION_SCOPE.project_id,
        limit=100,
        interval_seconds=0.01,
    )

    result = await graph_operator.run(settings=settings, operation=operation)
    assert result["requeuedCount"] == 1

    async with sessions() as session:
        row = (
            (
                await session.execute(
                    select(graph_project_merge_job).where(
                        *scope_predicates(graph_project_merge_job, VALIDATION_SCOPE)
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
    assert row is None
