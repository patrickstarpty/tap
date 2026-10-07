from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.application.merge_jobs import (
    InMemoryProjectMergeQueue,
    ProjectMergeLeaseLost,
)
from tap.modules.graph.application.merge_worker import ProjectGraphMergeWorker
from tap.modules.graph.application.merger import ProjectGraphMerger
from tap.modules.graph.domain.models import Evidence, GraphNode, GraphSnapshot, GraphSnapshotDraft
from tap.modules.graph.domain.project import FragmentRecord

NOW = datetime(2026, 10, 7, 9, 0, 0)


def _digest(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def _snapshot(snapshot_id: str) -> GraphSnapshot:
    return GraphSnapshot.create(
        snapshot_id=snapshot_id,
        project_id=VALIDATION_SCOPE.project_id,
        source_revision_ids=(f"{snapshot_id}-source",),
        document_revision_ids=(f"{snapshot_id}-document",),
        status="READY",
    )


def _fragment(snapshot_id: str, *, label: str = "保单") -> FragmentRecord:
    snapshot = _snapshot(snapshot_id)
    evidence_id = f"ev-{snapshot_id}"
    evidence = Evidence(
        evidence_id=evidence_id,
        snapshot_id=snapshot_id,
        source_revision_id=f"{snapshot_id}-source",
        document_revision_id=f"{snapshot_id}-document",
        chunk_id=f"chunk-{snapshot_id}",
        anchor={"kind": "text", "start": 0, "end": 4},
        content_digest=_digest(evidence_id),
    )
    node = GraphNode(f"{snapshot_id}-n1", snapshot_id, label, "ENTITY", label, (evidence_id,))
    draft = GraphSnapshotDraft(snapshot, (node,), (), (evidence,), ())
    return FragmentRecord(
        snapshot_id=snapshot_id,
        revision_id=f"{snapshot_id}-revision",
        status="READY",
        content_digest=_digest(snapshot_id),
        draft=draft,
    )


class FakeInputs:
    def __init__(self, fragments: tuple[FragmentRecord, ...]) -> None:
        self._fragments = fragments
        self.calls = 0

    async def load_fragments(self, scope):
        del scope
        self.calls += 1
        return self._fragments


class RequestingInputs:
    """Simulates a fragment turning READY while a merge is already in flight:
    the first `load_fragments` call re-requests the project before returning."""

    def __init__(self, fragments: tuple[FragmentRecord, ...], queue, *, request_at: datetime):
        self._fragments = fragments
        self._queue = queue
        self._request_at = request_at
        self.calls = 0

    async def load_fragments(self, scope):
        self.calls += 1
        if self.calls == 1:
            await self._queue.request(scope, reason="fragment-ready", now=self._request_at)
        return self._fragments


class FailingInputs:
    async def load_fragments(self, scope):
        del scope
        raise RuntimeError("synthetic merge input failure")


class LeaseLostQueue(InMemoryProjectMergeQueue):
    async def renew(self, scope, claim, *, now, lease_duration):
        del claim, now, lease_duration
        raise ProjectMergeLeaseLost(scope.project_id)


class RenewCountingQueue(InMemoryProjectMergeQueue):
    """Loses the lease on the Nth `renew` call (1-indexed); counts every call."""

    def __init__(self, *, lose_on_call: int | None = None) -> None:
        super().__init__()
        self.renew_calls = 0
        self._lose_on_call = lose_on_call

    async def renew(self, scope, claim, *, now, lease_duration):
        self.renew_calls += 1
        if self._lose_on_call is not None and self.renew_calls == self._lose_on_call:
            raise ProjectMergeLeaseLost(scope.project_id)
        return await super().renew(scope, claim, now=now, lease_duration=lease_duration)


class FailLeaseLostQueue(InMemoryProjectMergeQueue):
    """Simulates another worker having already claimed the row by the time
    this worker tries to record its own failure."""

    async def fail(self, scope, claim, *, failure_code, now):
        del claim, failure_code, now
        raise ProjectMergeLeaseLost(scope.project_id)


class RecordingEmbeddings:
    """Fake embeddings callable: records every batch it was called with and
    returns one deterministic vector per input text."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, texts):
        batch = tuple(texts)
        self.calls.append(batch)
        return tuple((float(len(text)), 0.0) for text in batch)


def _worker(
    *,
    queue,
    inputs,
    monkeypatch,
    now: datetime = NOW,
    embeddings=None,
    embed_chunk_size: int = 64,
) -> ProjectGraphMergeWorker:
    worker = ProjectGraphMergeWorker(
        queue=queue,
        inputs=inputs,
        merger=ProjectGraphMerger(),
        scope=VALIDATION_SCOPE,
        worker_id="merge-worker-1",
        embeddings=embeddings,
        embed_chunk_size=embed_chunk_size,
    )
    monkeypatch.setattr(worker, "_now", lambda: now)
    return worker


@pytest.mark.asyncio
async def test_merge_worker_publishes_a_version_and_clears_due(monkeypatch) -> None:
    queue = InMemoryProjectMergeQueue()
    fragments = (_fragment("frag-a"), _fragment("frag-b", label="理赔"))
    inputs = FakeInputs(fragments)
    worker = _worker(queue=queue, inputs=inputs, monkeypatch=monkeypatch)
    await queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=NOW)

    result = await worker.run_once(limit=10)

    assert result == 1
    current = await queue.store.get_current(VALIDATION_SCOPE)
    assert current is not None and current.version == 1

    again = await worker.run_once(limit=10)
    assert again == 0


@pytest.mark.asyncio
async def test_unchanged_digest_skips_publication(monkeypatch) -> None:
    queue = InMemoryProjectMergeQueue()
    fragments = (_fragment("frag-a"),)
    inputs = FakeInputs(fragments)
    worker = _worker(queue=queue, inputs=inputs, monkeypatch=monkeypatch)
    await queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=NOW)
    assert await worker.run_once(limit=10) == 1
    first = await queue.store.get_current(VALIDATION_SCOPE)
    assert first is not None and first.version == 1

    await queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=NOW)
    second_result = await worker.run_once(limit=10)

    assert second_result == 1
    second = await queue.store.get_current(VALIDATION_SCOPE)
    assert second is not None and second.version == 1


@pytest.mark.asyncio
async def test_request_during_merge_keeps_queue_due(monkeypatch) -> None:
    queue = InMemoryProjectMergeQueue()
    fragments = (_fragment("frag-a"),)
    inputs = RequestingInputs(fragments, queue, request_at=NOW + timedelta(seconds=1))
    worker = _worker(queue=queue, inputs=inputs, monkeypatch=monkeypatch)
    await queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=NOW)

    result = await worker.run_once(limit=10)
    assert result == 1

    claim = await queue.claim(
        VALIDATION_SCOPE,
        worker_id="merge-worker-2",
        now=NOW + timedelta(seconds=2),
        lease_duration=timedelta(seconds=300),
    )
    assert claim is not None


@pytest.mark.asyncio
async def test_failure_backs_off_and_records_code(monkeypatch) -> None:
    queue = InMemoryProjectMergeQueue()
    worker = _worker(queue=queue, inputs=FailingInputs(), monkeypatch=monkeypatch)
    await queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=NOW)

    result = await worker.run_once(limit=10)

    assert result == 1
    row = queue._rows[VALIDATION_SCOPE.project_id]
    assert row.failure_code == "RuntimeError"
    assert row.due_at == NOW + timedelta(seconds=60)


@pytest.mark.asyncio
async def test_lease_lost_is_not_published(monkeypatch) -> None:
    queue = LeaseLostQueue()
    fragments = (_fragment("frag-a"),)
    inputs = FakeInputs(fragments)
    worker = _worker(queue=queue, inputs=inputs, monkeypatch=monkeypatch)
    await queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=NOW)

    result = await worker.run_once(limit=10)

    assert result == 1
    assert await queue.store.get_current(VALIDATION_SCOPE) is None


@pytest.mark.asyncio
async def test_embedding_dedupes_labels_before_embedding(monkeypatch) -> None:
    # Three nodes across two fragments share the label "保单"; a fourth is
    # distinct. The embedder must see each unique label exactly once, not
    # once per node.
    fragments = (
        _fragment("frag-a", label="保单"),
        _fragment("frag-b", label="保单"),
        _fragment("frag-c", label="理赔"),
    )
    queue = InMemoryProjectMergeQueue()
    embeddings = RecordingEmbeddings()
    worker = _worker(
        queue=queue,
        inputs=FakeInputs(fragments),
        monkeypatch=monkeypatch,
        embeddings=embeddings,
    )
    await queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=NOW)

    result = await worker.run_once(limit=10)

    assert result == 1
    embedded_texts = [text for batch in embeddings.calls for text in batch]
    assert sorted(embedded_texts) == ["保单", "理赔"]
    current = await queue.store.get_current(VALIDATION_SCOPE)
    assert current is not None and current.version == 1


@pytest.mark.asyncio
async def test_embedding_renews_the_lease_between_chunks(monkeypatch) -> None:
    fragments = (
        _fragment("frag-a", label="A"),
        _fragment("frag-b", label="B"),
        _fragment("frag-c", label="C"),
    )
    queue = RenewCountingQueue()
    embeddings = RecordingEmbeddings()
    worker = _worker(
        queue=queue,
        inputs=FakeInputs(fragments),
        monkeypatch=monkeypatch,
        embeddings=embeddings,
        embed_chunk_size=1,
    )
    await queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=NOW)

    result = await worker.run_once(limit=10)

    assert result == 1
    # One renew before embedding starts, plus one renew per one-label chunk.
    assert queue.renew_calls == 1 + 3
    assert len(embeddings.calls) == 3
    current = await queue.store.get_current(VALIDATION_SCOPE)
    assert current is not None and current.version == 1


@pytest.mark.asyncio
async def test_lease_lost_mid_embedding_stops_without_publishing(monkeypatch) -> None:
    fragments = (
        _fragment("frag-a", label="A"),
        _fragment("frag-b", label="B"),
    )
    # The pre-embedding renew (call 1) succeeds; the renew after the first
    # one-label chunk (call 2) loses the lease.
    queue = RenewCountingQueue(lose_on_call=2)
    embeddings = RecordingEmbeddings()
    worker = _worker(
        queue=queue,
        inputs=FakeInputs(fragments),
        monkeypatch=monkeypatch,
        embeddings=embeddings,
        embed_chunk_size=1,
    )
    await queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=NOW)

    result = await worker.run_once(limit=10)

    assert result == 1
    assert await queue.store.get_current(VALIDATION_SCOPE) is None
    # Embedding stopped after the chunk whose renew lost the lease; it never
    # reached the second label's chunk.
    assert len(embeddings.calls) == 1


@pytest.mark.asyncio
async def test_fail_swallows_a_lease_already_lost_to_another_worker(monkeypatch) -> None:
    queue = FailLeaseLostQueue()
    worker = _worker(queue=queue, inputs=FailingInputs(), monkeypatch=monkeypatch)
    await queue.request(VALIDATION_SCOPE, reason="fragment-ready", now=NOW)

    # Must not raise ProjectMergeLeaseLost out of run_once: the pending_work
    # loop in tapper_ingestion_worker has no guard around it, so an
    # unswallowed exception here would kill the whole graph worker process.
    result = await worker.run_once(limit=10)

    assert result == 1
