"""MySQL-integration coverage for fragment retry (review finding I1): a
FAILED batch must actually change the published graph once the worker
reruns, not just flip its own row back to PENDING."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.adapters.mysql import graph_node, graph_snapshot
from tap.modules.graph.adapters.mysql_jobs import MysqlGraphJobStore
from tap.modules.graph.adapters.mysql_merge import MysqlProjectMergeQueue
from tap.modules.graph.adapters.mysql_project import graph_project_merge_job
from tap.modules.graph.application.worker import GraphWorker
from tap.modules.graph.domain.jobs import GraphJobBusy, GraphJobRequest, GraphJobStatus
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshotDraft,
    RelationOrigin,
)
from tap.modules.graph.ports.store import GraphFactNotFound
from tap.modules.knowledge.domain.documents import ChunkDraft
from tap.platform.db.project_scope import scope_predicates

_LOCATOR = "art1.fragment-retry.chunks"


class _Artifacts:
    async def read_chunks(self, locator):
        assert str(locator) == _LOCATOR
        return (
            ChunkDraft(
                chunk_id="chunk-1",
                logical_chunk_id="logical-1",
                root_id="document-retry",
                parent_id=None,
                content="Chunk one text.",
                anchor_json='{"endOffset":16,"headingPath":[],"startOffset":0,"type":"document"}',
                source_content_hash="sha256:" + "a" * 64,
                chunk_content_hash="sha256:" + "a" * 64,
            ),
            ChunkDraft(
                chunk_id="chunk-2",
                logical_chunk_id="logical-2",
                root_id="document-retry",
                parent_id=None,
                content="Chunk two text.",
                anchor_json='{"endOffset":16,"headingPath":[],"startOffset":16,"type":"document"}',
                source_content_hash="sha256:" + "b" * 64,
                chunk_content_hash="sha256:" + "b" * 64,
            ),
        )


class _SwitchableExtractor:
    """Fails batch 1 until `fail_batch_one` is cleared, counting every call so
    the test can assert the second worker run calls the model only for the
    batch that was actually requeued."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail_batch_one = True

    async def extract(self, request):
        self.calls += 1
        index = request.batch_index
        node_id = f"node-batch-{index}"
        source_id = f"{node_id}-a"
        target_id = f"{node_id}-b"
        if index == 1 and self.fail_batch_one:
            raise RuntimeError("simulated extraction failure")
        evidence = Evidence(
            f"evidence-batch-{index}",
            request.snapshot.snapshot_id,
            request.snapshot.source_revision_ids[0],
            request.snapshot.document_revision_ids[0],
            f"chunk-{index + 1}",
            {"kind": "text", "start": 0, "end": 5},
            "sha256:" + ("c" if index == 0 else "d") * 64,
        )
        # A batch with zero edges collapses to one document fallback node once
        # merged (see assemble_fragment's docstring), so this needs a real edge
        # between two nodes to keep its own namespaced node ids intact.
        return GraphSnapshotDraft(
            request.snapshot,
            (
                GraphNode(
                    source_id,
                    request.snapshot.snapshot_id,
                    "Policy",
                    "ENTITY",
                    source_id,
                    (evidence.evidence_id,),
                ),
                GraphNode(
                    target_id,
                    request.snapshot.snapshot_id,
                    "Policy",
                    "ENTITY",
                    target_id,
                    (evidence.evidence_id,),
                ),
            ),
            (
                GraphEdge(
                    f"edge-batch-{index}",
                    request.snapshot.snapshot_id,
                    source_id,
                    target_id,
                    "RELATES_TO",
                    RelationOrigin.EXTRACTED,
                    0.9,
                    (evidence.evidence_id,),
                ),
            ),
            (evidence,),
            (),
        )


@pytest_asyncio.fixture
async def sessions(owned_project_mysql):
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _now() -> datetime:
    return datetime(2026, 9, 20, 9, 0, 0)


@pytest.mark.asyncio
async def test_retry_resets_a_partial_snapshot_so_the_rerun_actually_republishes(sessions) -> None:
    merge_queue = MysqlProjectMergeQueue(sessions)
    jobs = MysqlGraphJobStore(sessions, merge_queue=merge_queue)
    request = GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="revision-fragment-retry",
        chunks_locator=_LOCATOR,
        extraction_profile_digest="sha256:" + "e" * 64,
        model_alias="qwen-plus",
    )
    await jobs.request(VALIDATION_SCOPE, request, now=_now())

    extractor = _SwitchableExtractor()
    worker = GraphWorker(
        jobs=jobs,
        artifacts=_Artifacts(),
        extractor=extractor,
        scope=VALIDATION_SCOPE,
        worker_id="retry-worker-1",
        batch_size=1,
        batch_retries=1,
        lease_duration=timedelta(seconds=60),
    )

    first_run = await worker.run_once(limit=1)
    assert first_run.partial == 1
    assert extractor.calls == 2  # batch 0 succeeds, batch 1 fails once (batch_retries=1)

    async with sessions() as session:
        snapshot_row = (
            (
                await session.execute(
                    select(graph_snapshot.c.status).where(
                        *scope_predicates(graph_snapshot, VALIDATION_SCOPE),
                        graph_snapshot.c.snapshot_id == request.snapshot.snapshot_id,
                    )
                )
            )
            .mappings()
            .one()
        )
        assert snapshot_row["status"] == "PARTIAL"
        node_rows = (
            (
                await session.execute(
                    select(graph_node.c.node_id).where(
                        *scope_predicates(graph_node, VALIDATION_SCOPE),
                        graph_node.c.snapshot_id == request.snapshot.snapshot_id,
                    )
                )
            )
            .mappings()
            .all()
        )
        assert {row["node_id"] for row in node_rows} == {
            "b0-node-batch-0-a",
            "b0-node-batch-0-b",
        }

    # Retry requeues exactly the one FAILED batch and resets the snapshot.
    requeued, status = await jobs.retry_failed_batches(
        VALIDATION_SCOPE, "revision-fragment-retry", now=_now() + timedelta(minutes=1)
    )
    assert requeued == 1
    assert status is GraphJobStatus.PENDING

    async with sessions() as session:
        snapshot_row = (
            (
                await session.execute(
                    select(graph_snapshot.c.status).where(
                        *scope_predicates(graph_snapshot, VALIDATION_SCOPE),
                        graph_snapshot.c.snapshot_id == request.snapshot.snapshot_id,
                    )
                )
            )
            .mappings()
            .one()
        )
        assert snapshot_row["status"] == "CANDIDATE"
        node_rows = (
            (
                await session.execute(
                    select(graph_node.c.node_id).where(
                        *scope_predicates(graph_node, VALIDATION_SCOPE),
                        graph_node.c.snapshot_id == request.snapshot.snapshot_id,
                    )
                )
            )
            .mappings()
            .all()
        )
        assert node_rows == []  # published facts were dropped, not left stale

    # The worker reruns: batch 0 is still READY (reused, no model call) and
    # batch 1 is retried -- this time it succeeds.
    extractor.fail_batch_one = False
    second_run = await worker.run_once(limit=1)
    assert second_run.ready == 1
    assert extractor.calls == 3  # exactly one new call, for the requeued batch

    async with sessions() as session:
        snapshot_row = (
            (
                await session.execute(
                    select(graph_snapshot.c.status).where(
                        *scope_predicates(graph_snapshot, VALIDATION_SCOPE),
                        graph_snapshot.c.snapshot_id == request.snapshot.snapshot_id,
                    )
                )
            )
            .mappings()
            .one()
        )
        assert snapshot_row["status"] == "READY"
        node_rows = (
            (
                await session.execute(
                    select(graph_node.c.node_id).where(
                        *scope_predicates(graph_node, VALIDATION_SCOPE),
                        graph_node.c.snapshot_id == request.snapshot.snapshot_id,
                    )
                )
            )
            .mappings()
            .all()
        )
        assert {row["node_id"] for row in node_rows} == {
            "b0-node-batch-0-a",
            "b0-node-batch-0-b",
            "b1-node-batch-1-a",
            "b1-node-batch-1-b",
        }

        merge_row = (
            (
                await session.execute(
                    select(graph_project_merge_job.c.due_at).where(
                        *scope_predicates(graph_project_merge_job, VALIDATION_SCOPE)
                    )
                )
            )
            .mappings()
            .one()
        )
        assert merge_row["due_at"] is not None


@pytest.mark.asyncio
async def test_retry_raises_graph_fact_not_found_for_an_unknown_revision(sessions) -> None:
    jobs = MysqlGraphJobStore(sessions)
    with pytest.raises(GraphFactNotFound):
        await jobs.retry_failed_batches(VALIDATION_SCOPE, "no-such-revision", now=_now())


@pytest.mark.asyncio
async def test_retry_is_busy_while_the_job_is_running_under_a_live_lease(sessions) -> None:
    jobs = MysqlGraphJobStore(sessions)
    request = GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="revision-fragment-retry-busy",
        chunks_locator=_LOCATOR,
        extraction_profile_digest="sha256:" + "f" * 64,
        model_alias="qwen-plus",
    )
    await jobs.request(VALIDATION_SCOPE, request, now=_now())
    await jobs.claim(
        VALIDATION_SCOPE,
        worker_id="busy-worker-1",
        now=_now(),
        lease_duration=timedelta(minutes=5),
        limit=1,
    )
    with pytest.raises(GraphJobBusy):
        await jobs.retry_failed_batches(
            VALIDATION_SCOPE, "revision-fragment-retry-busy", now=_now() + timedelta(seconds=1)
        )


@pytest.mark.asyncio
async def test_retry_with_no_failed_batches_is_a_no_op(sessions) -> None:
    jobs = MysqlGraphJobStore(sessions)
    request = GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="revision-fragment-retry-noop",
        chunks_locator=_LOCATOR,
        extraction_profile_digest="sha256:" + "0" * 64,
        model_alias="qwen-plus",
    )
    await jobs.request(VALIDATION_SCOPE, request, now=_now())
    requeued, status = await jobs.retry_failed_batches(
        VALIDATION_SCOPE, "revision-fragment-retry-noop", now=_now() + timedelta(seconds=1)
    )
    assert requeued == 0
    assert status is GraphJobStatus.PENDING


@pytest.mark.asyncio
async def test_list_fragment_states_reports_job_and_snapshot_status(sessions) -> None:
    jobs = MysqlGraphJobStore(sessions)
    request = GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="revision-fragment-states",
        chunks_locator=_LOCATOR,
        extraction_profile_digest="sha256:" + "1" * 64,
        model_alias="qwen-plus",
    )
    await jobs.request(VALIDATION_SCOPE, request, now=_now())
    states = await jobs.list_fragment_states(VALIDATION_SCOPE)
    matching = [state for state in states if state[0] == "revision-fragment-states"]
    assert matching == [("revision-fragment-states", "PENDING", "CANDIDATE")]


@pytest.mark.asyncio
async def test_merge_state_reflects_the_project_merge_queue(sessions) -> None:
    merge_queue = MysqlProjectMergeQueue(sessions)
    jobs = MysqlGraphJobStore(sessions, merge_queue=merge_queue)
    assert await jobs.merge_state(VALIDATION_SCOPE, now=_now()) == "IDLE"

    await merge_queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=_now())
    assert await jobs.merge_state(VALIDATION_SCOPE, now=_now()) == "PENDING"

    claim = await merge_queue.claim(
        VALIDATION_SCOPE,
        worker_id="merge-worker-1",
        now=_now() + timedelta(seconds=1),
        lease_duration=timedelta(seconds=60),
    )
    assert claim is not None
    assert await jobs.merge_state(VALIDATION_SCOPE, now=_now() + timedelta(seconds=2)) == "RUNNING"

    await merge_queue.fail(
        VALIDATION_SCOPE,
        claim,
        failure_code="boom",
        now=_now() + timedelta(seconds=3),
    )
    assert await jobs.merge_state(VALIDATION_SCOPE, now=_now() + timedelta(seconds=4)) == "FAILED"
