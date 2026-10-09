from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.adapters.mysql import MysqlGraphStore
from tap.modules.graph.adapters.mysql_jobs import MysqlGraphJobStore
from tap.modules.graph.application.extraction import GraphExtractionService
from tap.modules.graph.application.fragments import assemble_fragment
from tap.modules.graph.application.queries import InMemoryGraphStore
from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.jobs import (
    GraphBatchStatus,
    GraphFragmentBatch,
    GraphJobLeaseLost,
    GraphJobRequest,
)
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)


def _now() -> datetime:
    return datetime(2026, 9, 13, 9, 0, 0)


def _one_batch_draft(snapshot: GraphSnapshot) -> GraphSnapshotDraft:
    evidence = Evidence(
        "evidence-batch-1",
        snapshot.snapshot_id,
        snapshot.source_revision_ids[0],
        snapshot.document_revision_ids[0],
        "chunk-1",
        {"kind": "text", "start": 0, "end": 5},
        "sha256:" + "c" * 64,
    )
    return GraphSnapshotDraft(
        snapshot,
        (
            GraphNode(
                "node-batch-1",
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


async def _claim(store: MysqlGraphJobStore):
    request = GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="source-revision-batch-1",
        chunks_locator="art-batch.chunks",
        extraction_profile_digest="sha256:" + "d" * 64,
        model_alias="qwen-plus",
    )
    await store.request(VALIDATION_SCOPE, request, now=_now())
    return (
        await store.claim(
            VALIDATION_SCOPE,
            worker_id="worker-1",
            now=_now(),
            lease_duration=timedelta(seconds=60),
            limit=1,
        )
    )[0]


@pytest_asyncio.fixture
async def sessions(owned_project_mysql):
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


class Extractor:
    def __init__(self, draft):
        self.draft = draft
        self.calls = 0

    async def extract(self, request):
        self.calls += 1
        return self.draft


@pytest.mark.asyncio
async def test_duplicate_worker_delivery_reuses_the_same_published_snapshot():
    store = InMemoryGraphStore()
    snapshot = GraphSnapshot.create(
        snapshot_id="snapshot-1",
        project_id=VALIDATION_SCOPE.project_id,
        source_revision_ids=("source-revision-1",),
        document_revision_ids=("document-revision-1",),
    )
    evidence = Evidence(
        "evidence-1",
        "snapshot-1",
        "source-revision-1",
        "document-revision-1",
        "chunk-1",
        {"kind": "text", "start": 0, "end": 5},
        "sha256:" + "a" * 64,
    )
    draft = GraphSnapshotDraft(
        snapshot,
        (
            GraphNode("node-1", "snapshot-1", "Policy", "ENTITY", "policy"),
            GraphNode("node-2", "snapshot-1", "Claim", "ENTITY", "claim"),
        ),
        (
            GraphEdge(
                "edge-1",
                "snapshot-1",
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
    extractor = Extractor(draft)
    service = GraphExtractionService(store=store, extractor=extractor)
    request = GraphExtractionRequest(
        scope=VALIDATION_SCOPE,
        snapshot=GraphSnapshot.create(
            snapshot_id="snapshot-1",
            project_id=VALIDATION_SCOPE.project_id,
            source_revision_ids=("source-revision-1",),
            document_revision_ids=("document-revision-1",),
        ),
        chunks=({},),
        model_alias="tapper-graph",
        idempotency_key="graph:snapshot-1",
    )
    first = await service.execute(request)
    replay = await service.execute(request)
    assert first == replay
    assert extractor.calls == 1


def test_graph_job_snapshot_is_created_for_the_complete_normalized_selection():
    request = GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="source-revision-2",
        source_revision_ids=("source-revision-2", "source-revision-1"),
        document_revision_ids=("document-revision-2", "document-revision-1"),
        chunks_locator="blob://chunks/revision-2.json",
        extraction_profile_digest="sha256:" + "b" * 64,
        model_alias="tapper-graph",
    )

    assert request.snapshot.source_revision_ids == (
        "source-revision-1",
        "source-revision-2",
    )
    assert request.snapshot.document_revision_ids == (
        "document-revision-1",
        "document-revision-2",
    )


@pytest.mark.asyncio
async def test_mysql_restart_reads_the_exact_multi_revision_snapshot_by_id(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    snapshot = GraphSnapshot.create(
        snapshot_id="snapshot-multi-revision",
        project_id=VALIDATION_SCOPE.project_id,
        source_revision_ids=("source-revision-2", "source-revision-1"),
        document_revision_ids=("document-revision-2", "document-revision-1"),
    )
    try:
        await MysqlGraphStore(sessions).publish(
            VALIDATION_SCOPE,
            GraphSnapshotDraft(
                snapshot,
                (
                    GraphNode(
                        "node-multi-revision",
                        snapshot.snapshot_id,
                        "Selection",
                        "ENTITY",
                        "selection",
                    ),
                ),
                (),
                (),
                (),
            ),
        )

        restarted = MysqlGraphStore(sessions)

        persisted = await restarted.get_snapshot(VALIDATION_SCOPE, snapshot.snapshot_id)
        assert persisted is not None
        assert persisted.source_revision_ids == (
            "source-revision-1",
            "source-revision-2",
        )
        assert persisted.document_revision_ids == (
            "document-revision-1",
            "document-revision-2",
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_batches_survive_reclaim_and_partial_publish(sessions) -> None:
    store = MysqlGraphJobStore(sessions)
    claim = await _claim(store)
    draft = _one_batch_draft(claim.snapshot)
    now = _now()

    # First write: no draft yet, just a pending marker for batch 0.
    await store.record_batch(
        VALIDATION_SCOPE,
        claim,
        GraphFragmentBatch(
            claim.snapshot.snapshot_id, 0, ("chunk-1",), GraphBatchStatus.PENDING, attempt=1
        ),
        now=now,
    )
    # Second write on the same (snapshot_id, batch_index): the upsert must overwrite,
    # not append, and the later status/attempt/draft must win.
    await store.record_batch(
        VALIDATION_SCOPE,
        claim,
        GraphFragmentBatch(
            claim.snapshot.snapshot_id,
            0,
            ("chunk-1",),
            GraphBatchStatus.READY,
            attempt=2,
            draft=draft,
        ),
        now=now + timedelta(seconds=1),
    )
    overwritten = await store.load_batches(VALIDATION_SCOPE, claim)
    assert len(overwritten) == 1
    assert overwritten[0].status is GraphBatchStatus.READY
    assert overwritten[0].attempt == 2
    assert overwritten[0].draft == draft

    # The live claim can still renew its lease before it expires.
    renewed = await store.renew(
        VALIDATION_SCOPE,
        claim,
        now=now + timedelta(seconds=2),
        lease_duration=timedelta(seconds=30),
    )
    assert renewed.lease_expires_at == now + timedelta(seconds=32)

    # Once the (renewed) lease has actually expired without a reclaim, renew is fenced too.
    with pytest.raises(GraphJobLeaseLost):
        await store.renew(
            VALIDATION_SCOPE,
            claim,
            now=now + timedelta(seconds=40),
            lease_duration=timedelta(seconds=10),
        )

    # A worker crash: w2 reclaims the now-expired job.
    reclaimed = (
        await store.claim(
            VALIDATION_SCOPE,
            worker_id="w2",
            now=now + timedelta(seconds=41),
            lease_duration=timedelta(seconds=60),
            limit=1,
        )
    )[0]
    assert reclaimed.lease_token != claim.lease_token

    # The original (stale) claim is fenced out of both mutating paths after reclaim.
    with pytest.raises(GraphJobLeaseLost):
        await store.record_batch(
            VALIDATION_SCOPE,
            claim,
            GraphFragmentBatch(
                claim.snapshot.snapshot_id, 1, ("chunk-2",), GraphBatchStatus.PENDING, attempt=1
            ),
            now=now + timedelta(seconds=42),
        )
    with pytest.raises(GraphJobLeaseLost):
        await store.renew(
            VALIDATION_SCOPE,
            claim,
            now=now + timedelta(seconds=42),
            lease_duration=timedelta(seconds=10),
        )

    assert (await store.load_batches(VALIDATION_SCOPE, reclaimed))[0].draft == draft

    job = await store.complete(
        VALIDATION_SCOPE, reclaimed, draft, now=now + timedelta(seconds=43), status="PARTIAL"
    )
    assert job.snapshot.status == "PARTIAL"
    assert (
        await MysqlGraphStore(sessions).get_snapshot(VALIDATION_SCOPE, job.snapshot.snapshot_id)
    ).status == "PARTIAL"


@pytest.mark.asyncio
async def test_mysql_publish_merges_a_shared_entity_across_batches(sessions) -> None:
    # F1: two batches independently ground the same real-world entity
    # ("Policy"/"policy") under different (worker-namespaced) node ids, the way
    # the rule-based extractor's raw ids collide across batches without a shared
    # known-entities cache. Publishing the merged fragment must not raise MySQL's
    # uq_graph_node_canonical(project_id, snapshot_id, canonical_key) IntegrityError.
    store = MysqlGraphJobStore(sessions)
    claim = await _claim(store)
    snapshot = claim.snapshot

    def _evidence(evidence_id: str, chunk_id: str) -> Evidence:
        return Evidence(
            evidence_id,
            snapshot.snapshot_id,
            snapshot.source_revision_ids[0],
            snapshot.document_revision_ids[0],
            chunk_id,
            {"kind": "text", "start": 0, "end": 5},
            "sha256:" + "a" * 64,
        )

    evidence_1 = _evidence("evidence-b0", "chunk-1")
    evidence_2 = _evidence("evidence-b1", "chunk-2")
    node_batch_0 = GraphNode(
        "b0-policy", snapshot.snapshot_id, "Policy", "ENTITY", "policy", ("evidence-b0",)
    )
    node_batch_1 = GraphNode(
        "b1-policy", snapshot.snapshot_id, "Policy", "ENTITY", "policy", ("evidence-b1",)
    )
    other_node_batch_0 = GraphNode(
        "b0-claim", snapshot.snapshot_id, "Claim", "ENTITY", "claim", ("evidence-b0",)
    )
    other_node_batch_1 = GraphNode(
        "b1-claim", snapshot.snapshot_id, "Claim", "ENTITY", "claim", ("evidence-b1",)
    )
    edge_batch_0 = GraphEdge(
        "b0-edge",
        snapshot.snapshot_id,
        "b0-policy",
        "b0-claim",
        "GOVERNS",
        RelationOrigin.EXTRACTED,
        1.0,
        ("evidence-b0",),
    )
    edge_batch_1 = GraphEdge(
        "b1-edge",
        snapshot.snapshot_id,
        "b1-policy",
        "b1-claim",
        "GOVERNS",
        RelationOrigin.EXTRACTED,
        1.0,
        ("evidence-b1",),
    )
    draft_0 = GraphSnapshotDraft(
        snapshot,
        (node_batch_0, other_node_batch_0),
        (edge_batch_0,),
        (evidence_1,),
        (),
    )
    draft_1 = GraphSnapshotDraft(
        snapshot,
        (node_batch_1, other_node_batch_1),
        (edge_batch_1,),
        (evidence_2,),
        (),
    )

    fragment = assemble_fragment(snapshot, (draft_0, draft_1))
    assert len(fragment.nodes) == 2  # merged, not four
    assert len(fragment.edges) == 1  # merged, not two

    job = await store.complete(VALIDATION_SCOPE, claim, fragment, now=_now(), status="READY")
    assert job.snapshot.status == "READY"
    published = await MysqlGraphStore(sessions).get_snapshot(
        VALIDATION_SCOPE, job.snapshot.snapshot_id
    )
    assert published.status == "READY"
