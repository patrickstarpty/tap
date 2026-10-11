from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.application.backfill import (
    GraphRevisionJobState,
    backfill_graph_jobs,
)
from tap.modules.graph.domain.jobs import GraphJobStatus

NOW = datetime(2026, 10, 11, 9, 0, 0)
CURRENT = "sha256:" + "c" * 64
STALE = "sha256:" + "5" * 64


class FakeJobs:
    def __init__(self, states: tuple[GraphRevisionJobState, ...] = ()) -> None:
        self.states = states
        self.requested: list[str] = []
        self.reset: list[str] = []
        self.busy: set[str] = set()

    async def revision_job_states(self, scope):
        return self.states

    async def request(self, scope, request, *, now):
        assert request.extraction_profile_digest == CURRENT
        assert request.model_alias == "qwen-plus"
        self.requested.append(request.revision_id)

    async def reset_for_profile(
        self, scope, revision_id, *, extraction_profile_digest, model_alias, now
    ):
        assert extraction_profile_digest == CURRENT and model_alias == "qwen-plus"
        if revision_id in self.busy:
            raise ValueError("graph job is running")
        self.reset.append(revision_id)


class FakeMergeQueue:
    def __init__(self) -> None:
        self.reasons: list[str] = []

    async def request(self, scope, *, reason, now):
        self.reasons.append(reason)


def _state(revision_id: str, status: GraphJobStatus, digest: str = CURRENT, lease=None):
    return GraphRevisionJobState(revision_id, status, digest, lease)


async def _backfill(published, jobs, merge_queue):
    return await backfill_graph_jobs(
        scope=VALIDATION_SCOPE,
        published=published,
        jobs=jobs,
        merge_queue=merge_queue,
        extraction_profile_digest=CURRENT,
        model_alias="qwen-plus",
        now=NOW,
    )


@pytest.mark.asyncio
async def test_published_revision_without_a_job_is_enqueued() -> None:
    jobs = FakeJobs((_state("revision-b", GraphJobStatus.READY),))
    merge_queue = FakeMergeQueue()

    result = await _backfill(
        {"revision-a": "art-a.chunks", "revision-b": "art-b.chunks"}, jobs, merge_queue
    )

    assert jobs.requested == ["revision-a"] and jobs.reset == []
    assert (result.requested, result.requeued) == (1, 0)
    # A fragment is still extracting: its own completion requests the merge.
    assert merge_queue.reasons == [] and result.merge_requested is False


@pytest.mark.asyncio
async def test_fragment_from_an_older_extraction_profile_is_requeued() -> None:
    jobs = FakeJobs(
        (
            _state("revision-a", GraphJobStatus.READY, STALE),
            _state("revision-b", GraphJobStatus.FAILED, STALE),
            _state("revision-c", GraphJobStatus.READY),
        )
    )

    result = await _backfill(
        {"revision-a": "a", "revision-b": "b", "revision-c": "c"}, jobs, FakeMergeQueue()
    )

    assert jobs.reset == ["revision-a", "revision-b"] and jobs.requested == []
    assert result.requeued == 2


@pytest.mark.asyncio
async def test_a_live_stale_job_is_left_to_its_current_owner() -> None:
    lease = NOW + timedelta(minutes=1)
    jobs = FakeJobs((_state("revision-a", GraphJobStatus.RUNNING, STALE, lease),))

    result = await _backfill({"revision-a": "a"}, jobs, FakeMergeQueue())

    assert jobs.reset == [] and result.requeued == 0 and result.skipped == 1


@pytest.mark.asyncio
async def test_a_reset_refused_as_running_is_skipped_not_fatal() -> None:
    jobs = FakeJobs((_state("revision-a", GraphJobStatus.READY, STALE),))
    jobs.busy.add("revision-a")

    result = await _backfill({"revision-a": "a"}, jobs, FakeMergeQueue())

    assert (result.requeued, result.skipped) == (0, 1)


@pytest.mark.asyncio
async def test_current_fragments_with_nothing_to_extract_request_one_merge() -> None:
    # Upgrade case: fragments are current but no merge was ever requested, so
    # the project graph stays empty until something republishes.
    jobs = FakeJobs(
        (
            _state("revision-a", GraphJobStatus.READY),
            _state("revision-b", GraphJobStatus.FAILED),
        )
    )
    merge_queue = FakeMergeQueue()

    result = await _backfill({"revision-a": "a", "revision-b": "b"}, jobs, merge_queue)

    assert jobs.requested == [] and jobs.reset == []
    assert merge_queue.reasons == ["graph-backfill"] and result.merge_requested is True


@pytest.mark.asyncio
async def test_nothing_published_requests_nothing() -> None:
    jobs = FakeJobs((_state("revision-gone", GraphJobStatus.READY, STALE),))
    merge_queue = FakeMergeQueue()

    result = await _backfill({}, jobs, merge_queue)

    assert jobs.requested == jobs.reset == [] and merge_queue.reasons == []
    assert (result.requested, result.requeued, result.merge_requested) == (0, 0, False)


@pytest.mark.asyncio
async def test_backfill_is_idempotent_once_jobs_exist() -> None:
    jobs = FakeJobs()
    merge_queue = FakeMergeQueue()
    await _backfill({"revision-a": "a"}, jobs, merge_queue)
    jobs.states = (_state("revision-a", GraphJobStatus.PENDING),)

    second = await _backfill({"revision-a": "a"}, jobs, merge_queue)

    assert jobs.requested == ["revision-a"]
    assert (second.requested, second.requeued) == (0, 0)
    # Extraction is still pending, so no merge is requested yet.
    assert merge_queue.reasons == []
