import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.graph_runs import GraphCheckpointUnavailable
from tap.modules.test_management.application.generation import TestDesignWorker as DesignWorker
from tap.modules.test_management.domain.models import (
    GenerationJobStatus,
)
from tap.modules.test_management.domain.models import (
    TestPlanGenerationJob as GenerationJob,
)
from tap.modules.test_management.ports.generation import (
    ClaimedTestDesignJob,
    GenerationResponseUnknown,
)
from tap.modules.test_management.ports.generation import (
    TestDesignContext as DesignContext,
)
from tests.integration.test_test_plan_publish import _draft
from tests.integration.test_test_plan_repository import _request


class FlakyCheckpointSaver(InMemorySaver):
    def __init__(self) -> None:
        super().__init__()
        self.fail_once = True

    async def aget_tuple(self, config):
        if self.fail_once:
            self.fail_once = False
            raise GraphCheckpointUnavailable("checkpoint database unavailable")
        return await super().aget_tuple(config)


class RestartableJobs:
    def __init__(self) -> None:
        request = _request()
        job = GenerationJob(
            request,
            GenerationJobStatus.RUNNING,
            datetime(2026, 9, 24, 9, 0),
            datetime(2026, 9, 24, 9, 0),
            attempt_count=1,
            lease_owner="worker",
            lease_token="lease-1",
            lease_expires_at=datetime(2026, 9, 24, 9, 1),
        )
        self.claim = ClaimedTestDesignJob(job, "lease-1")
        self.checkpointer = InMemorySaver()
        self.completed = []
        self.failed = []
        self.waiting = []
        self.admission_waiting_reason = None
        self.renewed = asyncio.Event()
        self.renew_count = 0
        self.expected_lease_duration = timedelta(seconds=60)
        self.checkpointer_claims = []
        self.cancel_complete_once = False
        self.fail_complete_once = False

    def graph_checkpointer(self, claim):
        self.checkpointer_claims.append(claim)
        return self.checkpointer

    async def claim_generation_jobs(self, scope, *, worker_id, now, lease_duration, limit):
        assert scope == VALIDATION_SCOPE
        assert worker_id == "worker"
        assert lease_duration == self.expected_lease_duration
        return (self.claim,) if not self.completed else ()

    async def renew_generation_job(self, scope, claim, *, now, lease_duration):
        assert scope == VALIDATION_SCOPE and claim == self.claim
        assert lease_duration == self.expected_lease_duration
        self.renew_count += 1
        self.renewed.set()

    async def generation_context(self, scope, claim):
        assert scope == VALIDATION_SCOPE and claim == self.claim
        return DesignContext(scope, claim.job.request, {}, {})

    async def generation_waiting_reason(self, scope, claim):
        assert scope == VALIDATION_SCOPE and claim == self.claim
        return self.admission_waiting_reason

    async def complete_generation(self, scope, claim, revision, *, now):
        if self.cancel_complete_once:
            self.cancel_complete_once = False
            raise asyncio.CancelledError
        if self.fail_complete_once:
            self.fail_complete_once = False
            raise RuntimeError("transient settlement failure")
        self.completed.append(revision)
        return revision

    async def fail_generation(self, scope, claim, *, failure_code, now):
        self.failed.append(failure_code)

    async def wait_generation(self, scope, claim, *, reason, now):
        self.waiting.append(reason)


class CancellableGenerator:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.draft = None

    async def generate(self, context):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await asyncio.Event().wait()
        request = context.request
        self.draft = replace(
            _draft(),
            test_plan_id=request.test_plan_id,
            revision_id=request.revision_id,
        ).with_recomputed_digest()
        return self.draft


class WaitForRenewalGenerator:
    def __init__(self, jobs: RestartableJobs) -> None:
        self.jobs = jobs

    async def generate(self, context):
        await self.jobs.renewed.wait()
        request = context.request
        return replace(
            _draft(),
            test_plan_id=request.test_plan_id,
            revision_id=request.revision_id,
        ).with_recomputed_digest()


class ImmediateGenerator:
    def __init__(self) -> None:
        self.calls = 0
        self.draft = None

    async def generate(self, context):
        self.calls += 1
        self.draft = replace(
            _draft(),
            test_plan_id=context.request.test_plan_id,
            revision_id=context.request.revision_id,
        ).with_recomputed_digest()
        return self.draft


class UnknownResponseGenerator:
    async def generate(self, _context):
        raise GenerationResponseUnknown("provider response requires reconciliation")


@pytest.mark.asyncio
async def test_worker_resumes_cancelled_graph_and_commits_one_draft() -> None:
    jobs = RestartableJobs()
    generator = CancellableGenerator()
    worker = DesignWorker(
        jobs=jobs,
        generator=generator,
        scope=VALIDATION_SCOPE,
        worker_id="worker",
    )

    interrupted = asyncio.create_task(worker.run_once(limit=1))
    await generator.started.wait()
    interrupted.cancel()
    with pytest.raises(asyncio.CancelledError):
        await interrupted
    checkpoint = await jobs.checkpointer.aget_tuple(
        {"configurable": {"thread_id": jobs.claim.job.request.job_id}}
    )
    assert checkpoint is not None
    assert checkpoint.checkpoint["channel_values"]["admitted"] is True
    result = await worker.run_once(limit=1)

    assert result.ready == 1
    assert generator.calls == 2
    assert len(jobs.completed) == 1
    assert jobs.completed[0] == generator.draft
    assert jobs.failed == []
    assert jobs.checkpointer_claims == [jobs.claim, jobs.claim]


@pytest.mark.asyncio
async def test_worker_renews_lease_while_graph_node_is_running() -> None:
    jobs = RestartableJobs()
    jobs.expected_lease_duration = timedelta(milliseconds=100)
    worker = DesignWorker(
        jobs=jobs,
        generator=WaitForRenewalGenerator(jobs),
        scope=VALIDATION_SCOPE,
        worker_id="worker",
        lease_duration=jobs.expected_lease_duration,
        renew_interval_seconds=0.01,
    )

    result = await worker.run_once(limit=1)

    assert result.ready == 1
    assert jobs.renew_count >= 1
    assert len(jobs.completed) == 1


@pytest.mark.asyncio
async def test_completed_graph_replays_exact_draft_without_calling_model_again() -> None:
    jobs = RestartableJobs()
    jobs.cancel_complete_once = True
    generator = ImmediateGenerator()
    worker = DesignWorker(
        jobs=jobs,
        generator=generator,
        scope=VALIDATION_SCOPE,
        worker_id="worker",
    )

    with pytest.raises(asyncio.CancelledError):
        await worker.run_once(limit=1)
    result = await worker.run_once(limit=1)

    assert result.ready == 1
    assert generator.calls == 1
    assert jobs.completed == [generator.draft]


@pytest.mark.asyncio
async def test_transient_settlement_failure_keeps_completed_graph_retryable() -> None:
    jobs = RestartableJobs()
    jobs.fail_complete_once = True
    generator = ImmediateGenerator()
    worker = DesignWorker(
        jobs=jobs,
        generator=generator,
        scope=VALIDATION_SCOPE,
        worker_id="worker",
    )

    interrupted = await worker.run_once(limit=1)
    resumed = await worker.run_once(limit=1)

    assert interrupted.lease_lost == 1
    assert interrupted.failed == 0
    assert resumed.ready == 1
    assert generator.calls == 1
    assert jobs.failed == []
    assert jobs.completed == [generator.draft]


@pytest.mark.asyncio
async def test_transient_checkpoint_failure_keeps_job_retryable() -> None:
    jobs = RestartableJobs()
    jobs.checkpointer = FlakyCheckpointSaver()
    generator = ImmediateGenerator()
    worker = DesignWorker(
        jobs=jobs,
        generator=generator,
        scope=VALIDATION_SCOPE,
        worker_id="worker",
    )

    interrupted = await worker.run_once(limit=1)
    resumed = await worker.run_once(limit=1)

    assert interrupted.lease_lost == 1
    assert interrupted.failed == 0
    assert resumed.ready == 1
    assert jobs.failed == []
    assert generator.calls == 1


@pytest.mark.asyncio
async def test_unknown_provider_response_releases_worker_into_waiting() -> None:
    jobs = RestartableJobs()
    worker = DesignWorker(
        jobs=jobs,
        generator=UnknownResponseGenerator(),
        scope=VALIDATION_SCOPE,
        worker_id="worker",
    )

    result = await worker.run_once(limit=1)

    assert result.waiting == 1
    assert result.failed == 0
    assert result.lease_lost == 0
    assert jobs.waiting == ["provider-response-unknown"]


@pytest.mark.asyncio
async def test_admission_wait_releases_worker_without_invoking_generator() -> None:
    jobs = RestartableJobs()
    jobs.admission_waiting_reason = "human-confirmation"
    generator = ImmediateGenerator()
    worker = DesignWorker(
        jobs=jobs,
        generator=generator,
        scope=VALIDATION_SCOPE,
        worker_id="worker",
    )

    result = await worker.run_once(limit=1)

    assert result.waiting == 1
    assert result.failed == 0
    assert generator.calls == 0
    assert jobs.waiting == ["human-confirmation"]
