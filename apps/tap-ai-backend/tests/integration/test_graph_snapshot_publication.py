from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.adapters.mysql import MysqlGraphStore
from tap.modules.graph.adapters.mysql_jobs import MysqlGraphJobStore
from tap.modules.graph.application.extraction import GraphExtractionService
from tap.modules.graph.application.queries import InMemoryGraphStore
from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.jobs import GraphBatchStatus, GraphFragmentBatch, GraphJobRequest
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


@pytest.mark.asyncio
async def test_active_snapshot_binds_the_complete_revision_selection_digest():
    store = InMemoryGraphStore()
    snapshot = GraphSnapshot.create(
        snapshot_id="snapshot-selection",
        project_id=VALIDATION_SCOPE.project_id,
        source_revision_ids=("source-revision-2", "source-revision-1"),
        document_revision_ids=("document-revision-2", "document-revision-1"),
    )
    await store.publish(
        VALIDATION_SCOPE,
        GraphSnapshotDraft(
            snapshot,
            (
                GraphNode(
                    "node-selection", "snapshot-selection", "Selection", "ENTITY", "selection"
                ),
            ),
            (),
            (),
            (),
        ),
    )

    active = await store.active_snapshot(
        VALIDATION_SCOPE,
        ("source-revision-1", "source-revision-2"),
    )

    assert active is not None
    assert active.source_revision_ids == ("source-revision-1", "source-revision-2")
    assert active.document_revision_ids == ("document-revision-1", "document-revision-2")
    assert await store.active_snapshot(VALIDATION_SCOPE, ("source-revision-1",)) is None


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
async def test_mysql_restart_reads_only_the_exact_multi_revision_snapshot(
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

        active = await restarted.active_snapshot(
            VALIDATION_SCOPE,
            ("source-revision-1", "source-revision-2"),
        )
        assert active is not None
        assert active.snapshot_id == snapshot.snapshot_id
        assert active.source_revision_ids == (
            "source-revision-1",
            "source-revision-2",
        )
        assert active.document_revision_ids == (
            "document-revision-1",
            "document-revision-2",
        )
        assert (
            await restarted.active_snapshot(
                VALIDATION_SCOPE,
                ("source-revision-1",),
            )
            is None
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_batches_survive_reclaim_and_partial_publish(sessions) -> None:
    store = MysqlGraphJobStore(sessions)
    claim = await _claim(store)
    draft = _one_batch_draft(claim.snapshot)

    await store.record_batch(
        VALIDATION_SCOPE,
        claim,
        GraphFragmentBatch(
            claim.snapshot.snapshot_id, 0, ("chunk-1",), GraphBatchStatus.READY, draft=draft
        ),
        now=_now(),
    )
    reclaimed = (
        await store.claim(
            VALIDATION_SCOPE,
            worker_id="w2",
            now=_now() + timedelta(minutes=2),
            lease_duration=timedelta(seconds=60),
            limit=1,
        )
    )[0]

    assert (await store.load_batches(VALIDATION_SCOPE, reclaimed))[0].draft == draft

    job = await store.complete(
        VALIDATION_SCOPE, reclaimed, draft, now=_now() + timedelta(minutes=2), status="PARTIAL"
    )
    assert job.snapshot.status == "PARTIAL"
    assert (
        await MysqlGraphStore(sessions).get_snapshot(VALIDATION_SCOPE, job.snapshot.snapshot_id)
    ).status == "PARTIAL"
