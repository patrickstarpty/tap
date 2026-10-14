"""`LangfuseSyncService.run_once`: scan cursors, push through a `LangfuseSink`, record
failures, advance cursors. Pure application logic — fakes stand in for MySQL/Langfuse."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tap.modules.ai.application.export_redaction import ExportRedactor
from tap.modules.ai.application.langfuse_mapping import feedback_score_id
from tap.modules.ai.application.langfuse_sync import LangfuseSyncService, SyncReport
from tap.modules.ai.domain.langfuse_sync import (
    FeedbackRecord,
    LangfuseScore,
    LangfuseTrace,
    SpanRecord,
    SyncKind,
    TraceBundle,
)

_NOW = datetime(2026, 10, 14, 12, 0, 0)
_T0 = _NOW - timedelta(minutes=5)
_T1 = _NOW - timedelta(minutes=4)


def _span(span_id: str, *, started_at: datetime = _T0) -> SpanRecord:
    return SpanRecord(
        span_id=span_id,
        parent_span_id=None,
        name="chat.turn",
        service_name="tap-ai-backend",
        status="ok",
        status_message=None,
        started_at=started_at,
        duration_ms=10,
        attributes={},
    )


def _bundle(trace_id: str) -> TraceBundle:
    return TraceBundle(
        trace_id=trace_id,
        project_id="proj-1",
        actor_id="actor-1",
        conversation_id="conv-1",
        turn_id="turn-1",
        job_id=None,
        spans=(_span(f"{trace_id}-span"),),
        calls=(),
    )


def _feedback(
    feedback_id: str,
    *,
    turn_id: str = "turn-1",
    actor_id: str = "actor-1",
    trace_id: str | None = "trace-1",
    rating: str | None = "up",
    comment: str | None = None,
    created_at: datetime = _T0,
    updated_at: datetime = _T0,
) -> FeedbackRecord:
    return FeedbackRecord(
        feedback_id=feedback_id,
        trace_id=trace_id,
        turn_id=turn_id,
        actor_id=actor_id,
        rating=rating,
        comment=comment,
        created_at=created_at,
        updated_at=updated_at,
    )


class FakeStore:
    def __init__(self) -> None:
        self.cursor: dict[SyncKind, datetime] = {}
        self._traces: dict[str, TraceBundle] = {}
        self._feedback: dict[str, FeedbackRecord] = {}
        self._trace_pages: list[tuple[list[str], datetime | None]] = [([], None)]
        self._feedback_pages: list[tuple[list[FeedbackRecord], datetime | None]] = [([], None)]
        self._failures: dict[SyncKind, dict[str, str]] = {"trace": {}, "feedback": {}}
        self.scan_traces_calls = 0
        self.scan_feedback_calls = 0

    def put_trace(self, bundle: TraceBundle) -> None:
        self._traces[bundle.trace_id] = bundle

    def remove_trace(self, trace_id: str) -> None:
        self._traces.pop(trace_id, None)

    def put_feedback(self, record: FeedbackRecord) -> None:
        self._feedback[record.feedback_id] = record

    def queue_trace_page(self, trace_ids: list[str], position: datetime | None) -> None:
        self._trace_pages.append((trace_ids, position))

    def queue_feedback_page(self, records: list[FeedbackRecord], position: datetime | None) -> None:
        self._feedback_pages.append((records, position))

    def failures_of(self, kind: SyncKind) -> set[str]:
        return set(self._failures[kind].keys())

    async def scan_traces(
        self, *, cutoff: datetime, limit: int
    ) -> tuple[list[str], datetime | None]:
        self.scan_traces_calls += 1
        if len(self._trace_pages) > 1:
            return self._trace_pages.pop(1)
        return self._trace_pages[0]

    async def scan_feedback(
        self, *, cutoff: datetime, limit: int
    ) -> tuple[list[FeedbackRecord], datetime | None]:
        self.scan_feedback_calls += 1
        if len(self._feedback_pages) > 1:
            return self._feedback_pages.pop(1)
        return self._feedback_pages[0]

    async def advance(self, name: SyncKind, position: datetime) -> None:
        self.cursor[name] = position

    async def load_trace(self, trace_id: str) -> TraceBundle | None:
        return self._traces.get(trace_id)

    async def load_feedback(self, feedback_id: str) -> FeedbackRecord | None:
        return self._feedback.get(feedback_id)

    async def failures(self, kind: SyncKind, limit: int) -> list[str]:
        return list(self._failures[kind].keys())[:limit]

    async def record_failure(self, kind: SyncKind, subject_id: str, error: str) -> None:
        self._failures[kind][subject_id] = error

    async def clear_failure(self, kind: SyncKind, subject_id: str) -> None:
        self._failures[kind].pop(subject_id, None)


class FakeSink:
    def __init__(self) -> None:
        self.fail = False
        self.pushed_ids: set[str] = set()
        self.upserted_scores: list[LangfuseScore] = []
        self.deleted_score_ids: list[str] = []

    async def push_trace(self, trace: LangfuseTrace) -> None:
        if self.fail:
            raise RuntimeError("sink outage")
        self.pushed_ids.add(trace.id)

    async def upsert_score(self, score: LangfuseScore) -> None:
        if self.fail:
            raise RuntimeError("sink outage")
        self.upserted_scores.append(score)

    async def delete_score(self, score_id: str) -> None:
        if self.fail:
            raise RuntimeError("sink outage")
        self.deleted_score_ids.append(score_id)


def _service(store: FakeStore, sink: FakeSink) -> LangfuseSyncService:
    return LangfuseSyncService(
        store,
        sink,
        ExportRedactor(),
        environment="local",
        settle_seconds=60,
        batch_size=200,
        clock=lambda: _NOW,
    )


@pytest.mark.asyncio
async def test_unsettled_traces_are_not_pushed():
    store, sink = FakeStore(), FakeSink()
    store.put_trace(_bundle("tr1"))
    # scan_traces is a fake double of the store's own cutoff filtering; an empty
    # page here stands in for "nothing settled yet", so nothing should be pushed.
    store.queue_trace_page([], None)

    report = await _service(store, sink).run_once()

    assert report.traces_ok == 0
    assert sink.pushed_ids == set()
    assert "trace" not in store.cursor


@pytest.mark.asyncio
async def test_trace_rescanned_after_new_span():
    store, sink = FakeStore(), FakeSink()
    store.put_trace(_bundle("tr1"))
    store.queue_trace_page(["tr1"], _T0)

    first = await _service(store, sink).run_once()
    assert first.traces_ok == 1
    assert sink.pushed_ids == {"tr1"}
    assert store.cursor["trace"] == _T0

    # A new span lands on the same trace later; the worker rescans it and the
    # sink's idempotent-by-id push just overwrites the Langfuse side.
    store.queue_trace_page(["tr1"], _T1)

    second = await _service(store, sink).run_once()
    assert second.traces_ok == 1
    assert store.cursor["trace"] == _T1


@pytest.mark.asyncio
async def test_sink_outage_then_recovery_loses_nothing():
    store, sink = FakeStore(), FakeSink()
    store.put_trace(_bundle("tr1"))
    store.put_trace(_bundle("tr2"))
    store.queue_trace_page(["tr1", "tr2"], _T1)

    sink.fail = True
    r1 = await _service(store, sink).run_once()
    assert r1.traces_failed == 2
    assert store.cursor["trace"] == _T1
    assert store.failures_of("trace") == {"tr1", "tr2"}

    sink.fail = False
    await _service(store, sink).run_once()
    assert sink.pushed_ids == {"tr1", "tr2"}
    assert store.failures_of("trace") == set()


@pytest.mark.asyncio
async def test_retried_trace_that_no_longer_exists_is_cleared_not_retried_forever():
    store, sink = FakeStore(), FakeSink()
    store.put_trace(_bundle("tr1"))
    store.queue_trace_page(["tr1"], _T0)
    sink.fail = True
    await _service(store, sink).run_once()
    assert store.failures_of("trace") == {"tr1"}

    sink.fail = False
    store.remove_trace("tr1")

    report = await _service(store, sink).run_once()
    assert report.traces_ok == 0
    assert report.traces_failed == 0
    assert store.failures_of("trace") == set()
    assert sink.pushed_ids == set()


@pytest.mark.asyncio
async def test_one_bad_trace_does_not_block_others():
    store, sink = FakeStore(), FakeSink()
    store.put_trace(_bundle("tr1"))
    # tr2 is never loaded into the store, so load_trace returns None for it.
    store.queue_trace_page(["tr1", "tr2"], _T1)

    report = await _service(store, sink).run_once()

    assert sink.pushed_ids == {"tr1"}
    assert report.traces_ok == 1
    # A missing trace is treated as already-deleted, not a failure to retry.
    assert report.traces_failed == 0
    assert store.failures_of("trace") == set()
    assert store.cursor["trace"] == _T1


@pytest.mark.asyncio
async def test_feedback_up_down_and_retract_map_to_scores():
    store, sink = FakeStore(), FakeSink()
    up = _feedback("fb-up", turn_id="t-up", actor_id="a-up", trace_id="trace-up", rating="up")
    down = _feedback(
        "fb-down",
        turn_id="t-down",
        actor_id="a-down",
        trace_id="trace-down",
        rating="down",
        comment="contact a@example.com",
    )
    retract = _feedback("fb-retract", turn_id="t-retract", actor_id="a-retract", rating=None)
    store.queue_feedback_page([up, down, retract], _T1)

    report = await _service(store, sink).run_once()

    assert report.feedback_ok == 3
    assert report.feedback_failed == 0
    scores_by_id = {score.id: score for score in sink.upserted_scores}

    up_score = scores_by_id[feedback_score_id("t-up", "a-up")]
    assert up_score.value == 1.0
    assert up_score.trace_id == "trace-up"
    assert up_score.name == "user_feedback"
    assert up_score.timestamp == up.created_at

    down_score = scores_by_id[feedback_score_id("t-down", "a-down")]
    assert down_score.value == 0.0
    assert down_score.comment == "contact [EMAIL]"

    assert sink.deleted_score_ids == [feedback_score_id("t-retract", "a-retract")]
    assert store.cursor["feedback"] == _T1


@pytest.mark.asyncio
async def test_feedback_without_trace_id_fails_and_retries():
    store, sink = FakeStore(), FakeSink()
    orphan = _feedback("fb-orphan", trace_id=None, rating="up")
    store.queue_feedback_page([orphan], _T0)

    report = await _service(store, sink).run_once()

    assert report.feedback_failed == 1
    assert sink.upserted_scores == []
    assert store.failures_of("feedback") == {"fb-orphan"}

    # Once the trace_id is backfilled, the retry succeeds.
    store.put_feedback(_feedback("fb-orphan", trace_id="trace-now", rating="up"))
    second = await _service(store, sink).run_once()
    assert second.feedback_ok == 1
    assert store.failures_of("feedback") == set()


@pytest.mark.asyncio
async def test_run_once_returns_sync_report():
    store, sink = FakeStore(), FakeSink()
    report = await _service(store, sink).run_once()
    assert report == SyncReport(traces_ok=0, traces_failed=0, feedback_ok=0, feedback_failed=0)
