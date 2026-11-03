from __future__ import annotations

import random
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.governance.adapters.schema import project_audit
from tap.modules.graph.adapters.mysql import graph_extraction_job, graph_snapshot
from tap.modules.graph.adapters.mysql_jobs import MysqlGraphJobStore
from tap.modules.graph.domain.jobs import GraphJobRequest, GraphJobStatus
from tap.platform.retry import RetryPolicy

NOW = datetime(2026, 11, 3, 9, 0, 0)


class FixedDelayPolicy(RetryPolicy):
    def next_delay(self, attempt: int, *, rng: random.Random | None = None) -> timedelta:
        del attempt, rng
        return timedelta(seconds=60)


@pytest_asyncio.fixture
async def sessions(owned_project_mysql) -> AsyncIterator[async_sessionmaker[AsyncSession]]:  # type: ignore[no-untyped-def]
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _request() -> GraphJobRequest:
    return GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="source-revision-1",
        chunks_locator="art1.chunks",
        extraction_profile_digest="sha256:" + "1" * 64,
        model_alias="qwen-plus",
    )


async def _claim(jobs: MysqlGraphJobStore, now: datetime):  # type: ignore[no-untyped-def]
    return await jobs.claim(
        VALIDATION_SCOPE,
        worker_id="graph-worker",
        now=now,
        lease_duration=timedelta(seconds=30),
        limit=1,
    )


async def _set(sessions: async_sessionmaker[AsyncSession], job_id: str, **values: object) -> None:
    async with sessions() as session, session.begin():
        await session.execute(
            update(graph_extraction_job)
            .where(graph_extraction_job.c.job_id == job_id)
            .values(**values)
        )


async def _job_row(sessions: async_sessionmaker[AsyncSession], job_id: str):  # type: ignore[no-untyped-def]
    async with sessions() as session:
        return (
            (
                await session.execute(
                    select(graph_extraction_job).where(graph_extraction_job.c.job_id == job_id)
                )
            )
            .mappings()
            .one()
        )


async def _snapshot_status(sessions: async_sessionmaker[AsyncSession], snapshot_id: str) -> str:
    async with sessions() as session:
        return await session.scalar(
            select(graph_snapshot.c.status).where(graph_snapshot.c.snapshot_id == snapshot_id)
        )


@pytest.mark.asyncio
async def test_expired_job_at_the_attempt_cap_fails_instead_of_being_claimed(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    jobs = MysqlGraphJobStore(sessions)
    request = _request()
    await jobs.request(VALIDATION_SCOPE, request, now=NOW)
    first = (await _claim(jobs, NOW))[0]
    await _set(sessions, first.job_id, attempt_count=5, lease_expires_at=NOW - timedelta(seconds=1))

    assert await _claim(jobs, NOW + timedelta(seconds=31)) == ()

    row = await _job_row(sessions, first.job_id)
    assert row["status"] == GraphJobStatus.FAILED.value
    assert row["failure_code"] == "attempts-exhausted"
    assert row["lease_token"] is None
    assert await _snapshot_status(sessions, request.snapshot.snapshot_id) == "FAILED"
    async with sessions() as session:
        audits = (
            await session.execute(
                select(project_audit.c.action).where(
                    project_audit.c.resource_id == request.snapshot.snapshot_id
                )
            )
        ).scalars()
        assert "graph-snapshot-failed" in set(audits)


@pytest.mark.asyncio
async def test_transient_failure_backs_off_before_the_job_is_claimable_again(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    jobs = MysqlGraphJobStore(sessions, retry_policy=FixedDelayPolicy())
    request = _request()
    await jobs.request(VALIDATION_SCOPE, request, now=NOW)
    claim = (await _claim(jobs, NOW))[0]

    pending = await jobs.fail(
        VALIDATION_SCOPE, claim, failure_code="model-unavailable", now=NOW + timedelta(seconds=1)
    )

    assert pending.status is GraphJobStatus.PENDING
    assert pending.failure_code == "model-unavailable"
    row = await _job_row(sessions, claim.job_id)
    assert row["status"] == GraphJobStatus.PENDING.value
    assert row["lease_token"] is None
    assert row["next_attempt_at"] == NOW + timedelta(seconds=61)
    assert await _snapshot_status(sessions, request.snapshot.snapshot_id) == "CANDIDATE"
    assert await _claim(jobs, NOW + timedelta(seconds=60)) == ()
    reclaimed = await _claim(jobs, NOW + timedelta(seconds=61))
    assert [job.job_id for job in reclaimed] == [claim.job_id]
    assert (await _job_row(sessions, claim.job_id))["attempt_count"] == 2


@pytest.mark.asyncio
async def test_default_transient_backoff_is_bounded_by_the_first_ceiling(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    jobs = MysqlGraphJobStore(sessions)
    await jobs.request(VALIDATION_SCOPE, _request(), now=NOW)
    claim = (await _claim(jobs, NOW))[0]

    await jobs.fail(VALIDATION_SCOPE, claim, failure_code="model-timeout", now=NOW)

    row = await _job_row(sessions, claim.job_id)
    assert row["status"] == GraphJobStatus.PENDING.value
    assert NOW <= row["next_attempt_at"] <= NOW + timedelta(seconds=2)


@pytest.mark.asyncio
async def test_transient_failure_on_the_last_attempt_is_terminal(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    jobs = MysqlGraphJobStore(sessions, retry_policy=FixedDelayPolicy())
    request = _request()
    await jobs.request(VALIDATION_SCOPE, request, now=NOW)
    claim = (await _claim(jobs, NOW))[0]
    await _set(sessions, claim.job_id, attempt_count=5)

    failed = await jobs.fail(
        VALIDATION_SCOPE, claim, failure_code="model-unavailable", now=NOW + timedelta(seconds=1)
    )

    assert failed.status is GraphJobStatus.FAILED
    assert failed.failure_code == "model-unavailable"
    assert await _snapshot_status(sessions, request.snapshot.snapshot_id) == "FAILED"


@pytest.mark.asyncio
async def test_non_transient_failure_stays_terminal(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    jobs = MysqlGraphJobStore(sessions)
    request = _request()
    await jobs.request(VALIDATION_SCOPE, request, now=NOW)
    claim = (await _claim(jobs, NOW))[0]

    failed = await jobs.fail(
        VALIDATION_SCOPE,
        claim,
        failure_code="graph-extraction-failed",
        now=NOW + timedelta(seconds=1),
    )

    assert failed.status is GraphJobStatus.FAILED
    row = await _job_row(sessions, claim.job_id)
    assert row["status"] == GraphJobStatus.FAILED.value
    assert row["next_attempt_at"] is None
    assert await _snapshot_status(sessions, request.snapshot.snapshot_id) == "FAILED"
    assert await _claim(jobs, NOW + timedelta(hours=1)) == ()
