from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.adapters.fake_extraction import DeterministicGraphExtraction
from tap.modules.graph.application.jobs import InMemoryGraphJobStore
from tap.modules.graph.application.worker import GraphWorker
from tap.modules.graph.domain.jobs import (
    GraphBatchStatus,
    GraphFragmentBatch,
    GraphJobLeaseLost,
    GraphJobRequest,
    GraphJobStatus,
)
from tap.modules.graph.domain.models import (
    Evidence,
    GraphNode,
    GraphSearchQuery,
    GraphSnapshotDraft,
)
from tap.modules.knowledge.domain.documents import ChunkDraft

NOW = datetime(2026, 9, 13, 9, 0, 0)


class Artifacts:
    async def read_chunks(self, locator):
        assert locator == "art1.chunks"
        return (
            ChunkDraft(
                chunk_id="chunk-1",
                logical_chunk_id="logical-1",
                root_id="document-1",
                parent_id=None,
                content="Claims require evidence.",
                anchor_json='{"endOffset":24,"headingPath":[],"startOffset":0,"type":"document"}',
                source_content_hash="sha256:" + "b" * 64,
                chunk_content_hash="sha256:" + "a" * 64,
            ),
        )


class Extractor:
    def __init__(self, draft: GraphSnapshotDraft) -> None:
        self._draft = draft

    async def extract(self, request):
        return self._draft


def _request_chunks(chunks, *, revision_id: str) -> tuple[dict, ...]:
    return tuple(
        {
            "sourceRevisionId": revision_id,
            "documentRevisionId": revision_id,
            "chunkId": str(chunk.chunk_id),
            "content": chunk.content,
            "anchor": json.loads(chunk.anchor_json),
            "contentDigest": chunk.chunk_content_hash,
        }
        for chunk in chunks
    )


@pytest.mark.asyncio
async def test_worker_publishes_a_claimed_job_atomically(monkeypatch) -> None:
    from tap.modules.graph.adapters.fake_extraction import rule_based_draft

    jobs = InMemoryGraphJobStore()
    request = GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="revision-1",
        chunks_locator="art1.chunks",
        extraction_profile_digest="sha256:" + "1" * 64,
        model_alias="qwen-plus",
    )
    job = await jobs.request(VALIDATION_SCOPE, request, now=datetime(2026, 9, 13, 9, 0, 0))
    chunks = await Artifacts().read_chunks("art1.chunks")
    request_chunks = _request_chunks(chunks, revision_id="revision-1")
    draft = rule_based_draft(job.snapshot, request_chunks, filename="revision-1")
    worker = GraphWorker(
        jobs=jobs,
        artifacts=Artifacts(),
        extractor=Extractor(draft),
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
    )
    monkeypatch.setattr(worker, "_now", lambda: datetime(2026, 9, 13, 9, 0, 1))

    result = await worker.run_once(limit=1)

    assert result.claimed == 1
    assert result.ready == 1
    assert result.failed == 0
    assert (await jobs.get_job(VALIDATION_SCOPE, job.job_id)).status is GraphJobStatus.READY


def _batch_chunks(count: int) -> tuple[ChunkDraft, ...]:
    return tuple(
        ChunkDraft(
            chunk_id=f"chunk-{index}",
            logical_chunk_id=f"logical-{index}",
            root_id="document-1",
            parent_id=None,
            content=f"content {index}",
            anchor_json='{"endOffset":1,"headingPath":[],"startOffset":0,"type":"document"}',
            source_content_hash="sha256:" + "b" * 64,
            chunk_content_hash="sha256:" + "a" * 64,
        )
        for index in range(count)
    )


class BatchArtifacts:
    def __init__(self, *, locator: str = "art-batches.chunks", count: int = 25) -> None:
        self._locator = locator
        self._chunks = _batch_chunks(count)

    async def read_chunks(self, locator):
        assert locator == self._locator
        return self._chunks


def _draft_for_batch(snapshot, batch_index: int, chunk_ids: tuple[str, ...]) -> GraphSnapshotDraft:
    evidence = tuple(
        Evidence(
            f"evidence-{batch_index}-{chunk_id}",
            snapshot.snapshot_id,
            "revision-1",
            "revision-1",
            chunk_id,
            {"kind": "text", "start": 0, "end": 5},
            "sha256:" + "a" * 64,
        )
        for chunk_id in chunk_ids
    )
    node = GraphNode(
        f"node-{batch_index}",
        snapshot.snapshot_id,
        f"Entity {batch_index}",
        "CONCEPT",
        f"entity-{batch_index}",
        tuple(item.evidence_id for item in evidence),
    )
    return GraphSnapshotDraft(snapshot, (node,), (), evidence, ())


class CountingExtractor:
    def __init__(self, *, fail_batch_indices: frozenset[int] = frozenset()) -> None:
        self.requests: list = []
        self._fail_batch_indices = fail_batch_indices

    async def extract(self, request):
        self.requests.append(request)
        if request.batch_index in self._fail_batch_indices:
            raise RuntimeError("synthetic batch failure")
        chunk_ids = tuple(str(item["chunkId"]) for item in request.chunks)
        return _draft_for_batch(request.snapshot, request.batch_index, chunk_ids)


async def _requested_job(jobs: InMemoryGraphJobStore, *, locator: str = "art-batches.chunks"):
    request = GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="revision-1",
        chunks_locator=locator,
        extraction_profile_digest="sha256:" + "1" * 64,
        model_alias="qwen-plus",
    )
    return await jobs.request(VALIDATION_SCOPE, request, now=NOW)


@pytest.mark.asyncio
async def test_worker_extracts_in_batches_and_publishes_ready(monkeypatch) -> None:
    jobs = InMemoryGraphJobStore()
    job = await _requested_job(jobs)
    extractor = CountingExtractor()
    worker = GraphWorker(
        jobs=jobs,
        artifacts=BatchArtifacts(),
        extractor=extractor,
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=10,
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=1))

    result = await worker.run_once(limit=1)

    assert [request.batch_index for request in extractor.requests] == [0, 1, 2]
    second_request = extractor.requests[1]
    assert second_request.idempotency_key.endswith(":batch:1")
    # Batch 0's raw "node-0" id is namespaced ("b0-node-0") before it is offered to
    # later batches as a known entity -- see test_batch_ids_are_namespaced_except_known_entities.
    assert any(entity["id"] == "b0-node-0" for entity in second_request.known_entities)
    assert result.partial == 0
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.READY
    assert updated.snapshot.status == "READY"


@pytest.mark.asyncio
async def test_failed_batch_retries_with_backoff_then_partial(monkeypatch) -> None:
    jobs = InMemoryGraphJobStore()
    job = await _requested_job(jobs)
    extractor = CountingExtractor(fail_batch_indices=frozenset({0}))
    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    worker = GraphWorker(
        jobs=jobs,
        artifacts=BatchArtifacts(),
        extractor=extractor,
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=10,
        batch_retries=3,
        sleep=_record_sleep,
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=1))

    result = await worker.run_once(limit=1)

    assert sleeps == [1.0, 2.0]
    assert result.partial == 1
    batches = await jobs.load_batches(VALIDATION_SCOPE, SimpleNamespace(snapshot=job.snapshot))
    batch0 = next(batch for batch in batches if batch.batch_index == 0)
    assert batch0.status is GraphBatchStatus.FAILED
    assert batch0.attempt == 3
    assert batch0.failure_code == "RuntimeError"
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.READY
    assert updated.snapshot.status == "PARTIAL"


@pytest.mark.asyncio
async def test_resume_skips_ready_batches(monkeypatch) -> None:
    jobs = InMemoryGraphJobStore()
    job = await _requested_job(jobs)
    pre_claim = (
        await jobs.claim(
            VALIDATION_SCOPE,
            worker_id="seed-worker",
            now=NOW,
            lease_duration=timedelta(seconds=1),
            limit=1,
        )
    )[0]
    batch0_chunk_ids = tuple(f"chunk-{index}" for index in range(10))
    await jobs.record_batch(
        VALIDATION_SCOPE,
        pre_claim,
        GraphFragmentBatch(
            job.snapshot.snapshot_id,
            0,
            batch0_chunk_ids,
            GraphBatchStatus.READY,
            draft=_draft_for_batch(job.snapshot, 0, batch0_chunk_ids),
        ),
        now=NOW,
    )

    extractor = CountingExtractor()
    worker = GraphWorker(
        jobs=jobs,
        artifacts=BatchArtifacts(),
        extractor=extractor,
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=10,
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=5))

    result = await worker.run_once(limit=1)

    assert [request.batch_index for request in extractor.requests] == [1, 2]
    assert result.partial == 0
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.READY
    assert updated.snapshot.status == "READY"


@pytest.mark.asyncio
async def test_all_batches_failed_fails_the_job(monkeypatch) -> None:
    jobs = InMemoryGraphJobStore()
    job = await _requested_job(jobs)
    extractor = CountingExtractor(fail_batch_indices=frozenset({0, 1, 2}))
    worker = GraphWorker(
        jobs=jobs,
        artifacts=BatchArtifacts(),
        extractor=extractor,
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=10,
        batch_retries=1,
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=1))

    result = await worker.run_once(limit=1)

    assert result.failed == 1
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.FAILED
    assert updated.failure_code == "graph-extraction-failed"
    assert updated.snapshot.status == "FAILED"


class LeaseLostJobs(InMemoryGraphJobStore):
    async def renew(self, scope, claim, *, now, lease_duration):
        raise GraphJobLeaseLost(claim.job_id)


@pytest.mark.asyncio
async def test_lease_lost_between_batches_stops_without_publishing(monkeypatch) -> None:
    jobs = LeaseLostJobs()
    job = await _requested_job(jobs)
    extractor = CountingExtractor()
    worker = GraphWorker(
        jobs=jobs,
        artifacts=BatchArtifacts(),
        extractor=extractor,
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=10,
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=1))

    result = await worker.run_once(limit=1)

    assert result.lease_lost == 1
    assert len(extractor.requests) == 1
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.RUNNING


class BrokenArtifacts:
    async def read_chunks(self, locator):
        raise RuntimeError("blob store unavailable")


@pytest.mark.asyncio
async def test_read_chunks_error_fails_the_job_and_run_once_returns(monkeypatch) -> None:
    jobs = InMemoryGraphJobStore()
    job = await _requested_job(jobs, locator="art-broken.chunks")
    worker = GraphWorker(
        jobs=jobs,
        artifacts=BrokenArtifacts(),
        extractor=CountingExtractor(),
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=1))

    result = await worker.run_once(limit=1)

    assert result.claimed == 1
    assert result.failed == 1
    assert result.lease_lost == 0
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.FAILED
    assert updated.failure_code == "RuntimeError"
    assert updated.snapshot.status == "FAILED"


@pytest.mark.asyncio
async def test_stored_pending_attempt_at_the_limit_becomes_failed(monkeypatch) -> None:
    jobs = InMemoryGraphJobStore()
    job = await _requested_job(jobs)
    pre_claim = (
        await jobs.claim(
            VALIDATION_SCOPE,
            worker_id="seed-worker",
            now=NOW,
            lease_duration=timedelta(seconds=1),
            limit=1,
        )
    )[0]
    batch0_chunk_ids = tuple(f"chunk-{index}" for index in range(10))
    await jobs.record_batch(
        VALIDATION_SCOPE,
        pre_claim,
        GraphFragmentBatch(
            job.snapshot.snapshot_id,
            0,
            batch0_chunk_ids,
            GraphBatchStatus.PENDING,
            attempt=2,
            failure_code="RuntimeError",
        ),
        now=NOW,
    )

    extractor = CountingExtractor()
    worker = GraphWorker(
        jobs=jobs,
        artifacts=BatchArtifacts(),
        extractor=extractor,
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=10,
        batch_retries=2,  # lowered below the stored attempt count for batch 0
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=5))

    result = await worker.run_once(limit=1)

    # Batch 0 is never retried through the extractor; it is recorded FAILED outright.
    assert [request.batch_index for request in extractor.requests] == [1, 2]
    assert result.partial == 1
    batches = await jobs.load_batches(VALIDATION_SCOPE, SimpleNamespace(snapshot=job.snapshot))
    batch0 = next(batch for batch in batches if batch.batch_index == 0)
    assert batch0.status is GraphBatchStatus.FAILED
    assert batch0.attempt == 2
    assert batch0.failure_code == "graph-batch-retry-limit-exceeded"
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.READY
    assert updated.snapshot.status == "PARTIAL"


class EmptyBatchExtractor:
    """Simulates a model that grounds nothing for one batch of boilerplate chunks."""

    def __init__(self, *, empty_batch_indices: frozenset[int]) -> None:
        self.requests: list = []
        self._empty_batch_indices = empty_batch_indices

    async def extract(self, request):
        self.requests.append(request)
        if request.batch_index in self._empty_batch_indices:
            return None
        chunk_ids = tuple(str(item["chunkId"]) for item in request.chunks)
        return _draft_for_batch(request.snapshot, request.batch_index, chunk_ids)


@pytest.mark.asyncio
async def test_empty_batch_succeeds_without_retries_and_job_stays_ready(monkeypatch) -> None:
    # F3: a batch that grounds nothing (all boilerplate chunks) must be recorded
    # READY with no draft and must not burn the retry budget or turn a healthy
    # document PARTIAL.
    jobs = InMemoryGraphJobStore()
    job = await _requested_job(jobs)
    extractor = EmptyBatchExtractor(empty_batch_indices=frozenset({1}))
    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    worker = GraphWorker(
        jobs=jobs,
        artifacts=BatchArtifacts(),
        extractor=extractor,
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=10,
        sleep=_record_sleep,
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=1))

    result = await worker.run_once(limit=1)

    assert sleeps == []
    assert result.ready == 1
    assert result.partial == 0
    assert result.failed == 0
    batches = await jobs.load_batches(VALIDATION_SCOPE, SimpleNamespace(snapshot=job.snapshot))
    batch1 = next(batch for batch in batches if batch.batch_index == 1)
    assert batch1.status is GraphBatchStatus.READY
    assert batch1.draft is None
    assert batch1.attempt == 1
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.READY
    assert updated.snapshot.status == "READY"


@pytest.mark.asyncio
async def test_resume_skips_a_stored_empty_ready_batch(monkeypatch) -> None:
    jobs = InMemoryGraphJobStore()
    job = await _requested_job(jobs)
    pre_claim = (
        await jobs.claim(
            VALIDATION_SCOPE,
            worker_id="seed-worker",
            now=NOW,
            lease_duration=timedelta(seconds=1),
            limit=1,
        )
    )[0]
    batch0_chunk_ids = tuple(f"chunk-{index}" for index in range(10))
    await jobs.record_batch(
        VALIDATION_SCOPE,
        pre_claim,
        GraphFragmentBatch(
            job.snapshot.snapshot_id, 0, batch0_chunk_ids, GraphBatchStatus.READY, draft=None
        ),
        now=NOW,
    )

    extractor = CountingExtractor()
    worker = GraphWorker(
        jobs=jobs,
        artifacts=BatchArtifacts(),
        extractor=extractor,
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=10,
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=5))

    result = await worker.run_once(limit=1)

    assert [request.batch_index for request in extractor.requests] == [1, 2]
    assert result.partial == 0
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.READY
    assert updated.snapshot.status == "READY"


@pytest.mark.asyncio
async def test_resume_recomputes_a_batch_whose_chunk_ids_changed(monkeypatch) -> None:
    # T6: a stored batch is only reused if its chunk_ids still match this run's
    # split -- otherwise (e.g. batch_size changed between runs) it is recomputed
    # rather than silently reused for the wrong chunks.
    jobs = InMemoryGraphJobStore()
    job = await _requested_job(jobs)
    pre_claim = (
        await jobs.claim(
            VALIDATION_SCOPE,
            worker_id="seed-worker",
            now=NOW,
            lease_duration=timedelta(seconds=1),
            limit=1,
        )
    )[0]
    stale_chunk_ids = ("chunk-0",)  # not this run's batch 0 (chunk-0..chunk-9)
    await jobs.record_batch(
        VALIDATION_SCOPE,
        pre_claim,
        GraphFragmentBatch(
            job.snapshot.snapshot_id,
            0,
            stale_chunk_ids,
            GraphBatchStatus.READY,
            draft=_draft_for_batch(job.snapshot, 0, stale_chunk_ids),
        ),
        now=NOW,
    )

    extractor = CountingExtractor()
    worker = GraphWorker(
        jobs=jobs,
        artifacts=BatchArtifacts(),
        extractor=extractor,
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=10,
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=5))

    result = await worker.run_once(limit=1)

    # Batch 0 is recomputed through the extractor (its stale chunk_ids did not
    # match this run's split), unlike test_resume_skips_ready_batches.
    assert [request.batch_index for request in extractor.requests] == [0, 1, 2]
    assert result.partial == 0
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.READY
    assert updated.snapshot.status == "READY"


class FailOnFailJobs(InMemoryGraphJobStore):
    async def fail(self, scope, claim, *, failure_code, now):
        raise RuntimeError("database unavailable")


@pytest.mark.asyncio
async def test_fail_itself_raising_counts_as_lease_lost_instead_of_crashing(monkeypatch) -> None:
    # F4 regression: a non-GraphJobLeaseLost exception from jobs.fail() (e.g. a
    # database error while recording the failure) must not escape run_once and
    # crash the worker process.
    jobs = FailOnFailJobs()
    job = await _requested_job(jobs, locator="art-broken.chunks")
    worker = GraphWorker(
        jobs=jobs,
        artifacts=BrokenArtifacts(),
        extractor=CountingExtractor(),
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=1))

    result = await worker.run_once(limit=1)

    assert result.lease_lost == 1
    assert result.failed == 0
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.RUNNING


class RecordingSleepExtractor:
    def __init__(self) -> None:
        self.requests: list = []

    async def extract(self, request):
        self.requests.append(request)
        raise RuntimeError("synthetic failure")


@pytest.mark.asyncio
async def test_backoff_sleep_never_exceeds_the_lease_duration(monkeypatch) -> None:
    # M6: with a large configured backoff, the sleep before the next attempt must
    # stay below the lease duration that was just renewed, instead of risking the
    # lease expiring while still asleep.
    jobs = InMemoryGraphJobStore()
    await _requested_job(jobs)
    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    worker = GraphWorker(
        jobs=jobs,
        artifacts=BatchArtifacts(),
        extractor=RecordingSleepExtractor(),
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=10,
        batch_retries=3,
        backoff_seconds=1000.0,
        lease_duration=timedelta(seconds=60),
        sleep=_record_sleep,
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=1))

    await worker.run_once(limit=1)

    assert sleeps
    assert all(seconds < 60.0 for seconds in sleeps)


class EventOrderJobs(InMemoryGraphJobStore):
    """Records "renew" each time the lease is renewed, for ordering assertions."""

    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events

    async def renew(self, scope, claim, *, now, lease_duration):
        self._events.append("renew")
        return await super().renew(scope, claim, now=now, lease_duration=lease_duration)


class EventExtractor:
    """Like CountingExtractor, but also logs "extract" into a shared events list."""

    def __init__(self, events: list[str], *, fail_batch_indices: frozenset[int]) -> None:
        self._events = events
        self._fail_batch_indices = fail_batch_indices

    async def extract(self, request):
        self._events.append("extract")
        if request.batch_index in self._fail_batch_indices:
            raise RuntimeError("synthetic batch failure")
        chunk_ids = tuple(str(item["chunkId"]) for item in request.chunks)
        return _draft_for_batch(request.snapshot, request.batch_index, chunk_ids)


@pytest.mark.asyncio
async def test_renews_the_lease_again_after_the_backoff_sleep(monkeypatch) -> None:
    # R4: capping the backoff sleep below the lease duration (M6) is not
    # enough on its own -- the sleep plus the next attempt's own extract() call
    # can still together outlive the lease that was renewed only *before* the
    # sleep. Renew again right after waking up too, so the event right after
    # each "sleep" is "renew", never the next attempt's "extract" landing
    # before a renew ever happens.
    events: list[str] = []
    jobs = EventOrderJobs(events)
    await _requested_job(jobs)
    extractor = EventExtractor(events, fail_batch_indices=frozenset({0}))

    async def _record_sleep(seconds: float) -> None:
        events.append("sleep")

    worker = GraphWorker(
        jobs=jobs,
        artifacts=BatchArtifacts(),
        extractor=extractor,
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=10,
        batch_retries=3,
        sleep=_record_sleep,
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=1))

    result = await worker.run_once(limit=1)

    assert result.partial == 1
    sleep_positions = [index for index, event in enumerate(events) if event == "sleep"]
    assert len(sleep_positions) == 2  # batch 0 fails twice before succeeding on attempt 3
    for position in sleep_positions:
        assert events[position + 1] == "renew"


@pytest.mark.asyncio
async def test_record_batch_rejects_a_mismatched_snapshot_id() -> None:
    # T3: record_batch must guard that the batch it is given belongs to the
    # claim's own snapshot, not some other job's.
    jobs = InMemoryGraphJobStore()
    job = await _requested_job(jobs)
    claim = (
        await jobs.claim(
            VALIDATION_SCOPE, worker_id="w1", now=NOW, lease_duration=timedelta(seconds=60), limit=1
        )
    )[0]
    assert job.snapshot.snapshot_id == claim.snapshot.snapshot_id

    with pytest.raises(ValueError, match="snapshot"):
        await jobs.record_batch(
            VALIDATION_SCOPE,
            claim,
            GraphFragmentBatch(
                "a-different-snapshot-id",
                0,
                ("chunk-0",),
                GraphBatchStatus.FAILED,
                attempt=1,
                failure_code="x",
            ),
            now=NOW,
        )


class ManyNodesExtractor:
    def __init__(self, *, nodes_per_batch: int) -> None:
        self._nodes_per_batch = nodes_per_batch
        self.requests: list = []

    async def extract(self, request):
        self.requests.append(request)
        chunk_id = str(request.chunks[0]["chunkId"])
        evidence = Evidence(
            "ev1",
            request.snapshot.snapshot_id,
            "revision-1",
            "revision-1",
            chunk_id,
            {"kind": "text", "start": 0, "end": 5},
            "sha256:" + "a" * 64,
        )
        # Ids are already batch-qualified at the raw level (unlike a real model's),
        # deliberately independent of namespacing, so this test exercises the
        # truncation fix in assemble_fragment in isolation from the namespacing fix.
        nodes = tuple(
            GraphNode(
                f"node-{request.batch_index}-{i}",
                request.snapshot.snapshot_id,
                f"Entity {i}",
                "CONCEPT",
                f"entity-{request.batch_index}-{i}",
                ("ev1",),
            )
            for i in range(self._nodes_per_batch)
        )
        return GraphSnapshotDraft(request.snapshot, nodes, (), (evidence,), ())


@pytest.mark.asyncio
async def test_oversized_merged_draft_completes_instead_of_crashing(monkeypatch) -> None:
    # 300 distinct nodes per batch, two batches: each batch's own draft is within the
    # 500-node cap, but the merge (600 distinct nodes) is not.
    jobs = InMemoryGraphJobStore()
    job = await _requested_job(jobs, locator="art-oversized.chunks")
    extractor = ManyNodesExtractor(nodes_per_batch=300)
    worker = GraphWorker(
        jobs=jobs,
        artifacts=BatchArtifacts(locator="art-oversized.chunks", count=2),
        extractor=extractor,
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=1,
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=1))

    result = await worker.run_once(limit=1)

    # A crash (surfaced pre-fix as the job going FAILED, since assemble_fragment would
    # raise on construction) is what this test guards against; READY confirms the
    # merge was truncated to the 500-node cap instead of raising.
    assert result.ready == 1
    assert result.failed == 0
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.READY
    assert updated.snapshot.status == "READY"


class ModelStyleExtractor:
    """Simulates a model that restarts its own id counter (``n1``/``ev1``/...) every call."""

    def __init__(self) -> None:
        self.requests: list = []

    async def extract(self, request):
        self.requests.append(request)
        chunk_id = str(request.chunks[0]["chunkId"])
        evidence = Evidence(
            "ev1",
            request.snapshot.snapshot_id,
            "revision-1",
            "revision-1",
            chunk_id,
            {"kind": "text", "start": 0, "end": 5},
            "sha256:" + "a" * 64,
        )
        if request.batch_index in (0, 1):
            # Batches 0 and 1 each mint a brand-new, unrelated entity -- but both
            # calls restart the model's own id counter at "n1", purely coincidentally
            # colliding on the same raw id.
            label = "Policy" if request.batch_index == 0 else "Claim"
            node = GraphNode(
                "n1", request.snapshot.snapshot_id, label, "CONCEPT", label.lower(), ("ev1",)
            )
            return GraphSnapshotDraft(request.snapshot, (node,), (), (evidence,), ())
        # Batch 2: echo one of the known entities' own (already namespaced) id
        # verbatim, exactly as a well-behaved model reusing a real-world entity it
        # was told about would.
        reused_id = str(request.known_entities[0]["id"])
        node = GraphNode(
            reused_id, request.snapshot.snapshot_id, "Policy", "CONCEPT", "policy", ("ev1",)
        )
        return GraphSnapshotDraft(request.snapshot, (node,), (), (evidence,), ())


@pytest.mark.asyncio
async def test_batch_ids_are_namespaced_except_known_entities(monkeypatch) -> None:
    jobs = InMemoryGraphJobStore()
    job = await _requested_job(jobs, locator="art-three-batches.chunks")
    extractor = ModelStyleExtractor()
    worker = GraphWorker(
        jobs=jobs,
        artifacts=BatchArtifacts(locator="art-three-batches.chunks", count=3),
        extractor=extractor,
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=1,
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=1))

    result = await worker.run_once(limit=1)

    assert result.partial == 0
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.READY
    batches = await jobs.load_batches(VALIDATION_SCOPE, SimpleNamespace(snapshot=job.snapshot))
    node_id_by_batch = {
        batch.batch_index: next(iter(batch.draft.nodes)).node_id for batch in batches
    }

    # Batches 0 and 1 each minted the raw id "n1" for an unrelated entity; both got
    # namespaced, so they don't collide with each other or with the raw literal.
    assert node_id_by_batch[0] != "n1"
    assert node_id_by_batch[1] != "n1"
    assert node_id_by_batch[0] != node_id_by_batch[1]
    # Batch 2 echoed a known entity's exact (already namespaced) id and kept it as-is
    # -- an intentional reuse, not a fresh id, so it was not re-prefixed.
    assert node_id_by_batch[2] in {node_id_by_batch[0], node_id_by_batch[1]}


class RepeatedContentArtifacts:
    """12 chunks, all with the same content, split into 2 batches of 10/2."""

    def __init__(self, *, locator: str, content: str) -> None:
        self._locator = locator
        self._content = content

    async def read_chunks(self, locator):
        assert locator == self._locator
        return tuple(
            ChunkDraft(
                chunk_id=f"chunk-{index}",
                logical_chunk_id=f"logical-{index}",
                root_id="document-1",
                parent_id=None,
                content=self._content,
                anchor_json=(
                    '{"endOffset":%d,"headingPath":[],"startOffset":0,"type":"document"}'
                    % len(self._content)
                ),
                source_content_hash="sha256:" + "b" * 64,
                chunk_content_hash="sha256:" + "a" * 64,
            )
            for index in range(12)
        )


@pytest.mark.asyncio
async def test_batches_a_related_document_through_the_real_rule_based_extractor(
    monkeypatch,
) -> None:
    # R3 repro #1: the real DeterministicGraphExtraction, batch_size 10, 12
    # chunks all containing the same relation sentence -- each batch
    # independently grounds the same two real-world entities under its own
    # batch-namespaced ids, exercising the real canonical-key merge (F1) end to
    # end rather than hand-built drafts.
    jobs = InMemoryGraphJobStore()
    job = await _requested_job(jobs, locator="art-related.chunks")
    worker = GraphWorker(
        jobs=jobs,
        artifacts=RepeatedContentArtifacts(
            locator="art-related.chunks", content="核保流程需要健康告知。"
        ),
        extractor=DeterministicGraphExtraction(),
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=10,
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=1))

    result = await worker.run_once(limit=1)

    assert result.ready == 1
    assert result.partial == 0
    assert result.failed == 0
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.READY
    assert updated.snapshot.status == "READY"
    published = await jobs._graph.search(
        VALIDATION_SCOPE, GraphSearchQuery(updated.snapshot.snapshot_id, "*", node_limit=500)
    )
    by_key = {node.canonical_key: node for node in published.nodes}
    assert set(by_key) == {"核保流程", "健康告知"}
    assert len(published.edges) == 1
    assert published.edges[0].source_node_id == by_key["核保流程"].node_id
    assert published.edges[0].target_node_id == by_key["健康告知"].node_id


@pytest.mark.asyncio
async def test_batches_a_relation_less_document_into_one_document_node(monkeypatch) -> None:
    # R3 repro #2: the real DeterministicGraphExtraction, batch_size 10, 12
    # relation-less chunks -- each batch independently mints its own
    # "document:<revision>" fallback node; the real canonical-key merge (F1)
    # must collapse them into exactly one, with zero edges, READY rather than
    # failing MySQL's uq_graph_node_canonical constraint.
    jobs = InMemoryGraphJobStore()
    job = await _requested_job(jobs, locator="art-relationless.chunks")
    worker = GraphWorker(
        jobs=jobs,
        artifacts=RepeatedContentArtifacts(
            locator="art-relationless.chunks", content="今天天气很好。"
        ),
        extractor=DeterministicGraphExtraction(),
        scope=VALIDATION_SCOPE,
        worker_id="graph-worker-1",
        batch_size=10,
    )
    monkeypatch.setattr(worker, "_now", lambda: NOW + timedelta(seconds=1))

    result = await worker.run_once(limit=1)

    assert result.ready == 1
    assert result.partial == 0
    assert result.failed == 0
    updated = await jobs.get_job(VALIDATION_SCOPE, job.job_id)
    assert updated.status is GraphJobStatus.READY
    assert updated.snapshot.status == "READY"
    published = await jobs._graph.search(
        VALIDATION_SCOPE, GraphSearchQuery(updated.snapshot.snapshot_id, "*", node_limit=500)
    )
    assert len(published.nodes) == 1
    assert published.nodes[0].canonical_key.startswith("document:")
    assert published.edges == ()
    # Grounding must be complete, not just nonempty: one evidence id per chunk
    # across both batches (10 + 2), not merely whichever batch happened to
    # survive the canonical-key merge.
    assert len(published.nodes[0].evidence_ids) == 12
    assert {item.chunk_id for item in published.evidence} == {
        f"chunk-{index}" for index in range(12)
    }
