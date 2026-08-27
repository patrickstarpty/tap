from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from tap.entrypoints import athena_ingestion_worker
from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.knowledge.application.ingestion import WorkerRun
from tap.modules.knowledge.ports.documents import ArtifactLocator, ReserveUpload
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.messaging.redis_wakeup import RedisWakeupConsumer

DATABASE_URL = os.getenv(
    "TAP_DATABASE_URL",
    "mysql+asyncmy://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4",
)
KNOWLEDGE_TABLES = (
    "knowledge_citation_snapshot",
    "knowledge_answer_snapshot",
    "knowledge_chunk_manifest",
    "knowledge_ingestion_job",
    "knowledge_document_revision",
    "knowledge_document",
)


async def _clean_knowledge(engine) -> None:  # type: ignore[no-untyped-def]
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM outbox WHERE aggregate_type = 'knowledge_document'")
        )
        await connection.execute(text("UPDATE knowledge_document SET current_revision_id = NULL"))
        for table in KNOWLEDGE_TABLES:
            await connection.execute(text(f"DELETE FROM {table}"))


class ScanningWorker:
    def __init__(self) -> None:
        self.runs = 0

    async def run_once(self, limit: int) -> WorkerRun:
        self.runs += 1
        return WorkerRun(1, 1, 0, 0, 0)


class LostWakeups:
    def __init__(self) -> None:
        self.waits = 0
        self.acks = 0

    async def wait(self, *, max_wait_seconds: float):
        self.waits += 1
        return None

    async def ack(self, wakeup):  # type: ignore[no-untyped-def]
        self.acks += 1


class UnavailableWakeups(LostWakeups):
    async def wait(self, *, max_wait_seconds: float):
        del max_wait_seconds
        self.waits += 1
        raise ConnectionError("redis is unavailable")


class Closeable:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class MysqlClaimWorker:
    def __init__(self, repository: MysqlDocumentRepository) -> None:
        self.repository = repository
        self.claimed: tuple[str, ...] = ()

    async def run_once(self, limit: int) -> WorkerRun:
        jobs = await self.repository.claim_jobs(
            worker_id="redis-order-worker",
            now=datetime.now(),
            lease_duration=timedelta(seconds=30),
            limit=limit,
        )
        self.claimed = tuple(job.job_id for job in jobs)
        return WorkerRun(len(jobs), 0, 0, 0, 0)


@pytest.mark.asyncio
async def test_worker_scans_mysql_when_redis_wakeup_is_lost() -> None:
    """Making Redis authoritative would strand a durable pending MySQL job."""

    worker = ScanningWorker()
    wakeups = LostWakeups()
    settings = replace(
        athena_ingestion_worker.load_settings({}),
        poll_seconds=0.01,
        wakeup_seconds=0.01,
    )

    await athena_ingestion_worker.run_worker_loop(
        worker=worker,
        wakeups=wakeups,
        settings=settings,
        stop=asyncio.Event(),
        max_iterations=1,
    )

    assert worker.runs == wakeups.waits == 1
    assert wakeups.acks == 0


@pytest.mark.asyncio
async def test_worker_keeps_scanning_when_redis_is_unavailable() -> None:
    worker = ScanningWorker()
    wakeups = UnavailableWakeups()
    settings = replace(
        athena_ingestion_worker.load_settings({}),
        poll_seconds=0.01,
        wakeup_seconds=0.01,
    )

    await athena_ingestion_worker.run_worker_loop(
        worker=worker,
        wakeups=wakeups,
        settings=settings,
        stop=asyncio.Event(),
        max_iterations=1,
    )

    assert worker.runs == wakeups.waits == 1
    assert wakeups.acks == 0


@pytest.mark.asyncio
async def test_run_builds_signal_driven_runtime_and_closes_every_resource() -> None:
    """The process entrypoint must own stop handlers and finally-close its runtime."""

    worker = ScanningWorker()
    wakeups = LostWakeups()
    resource = Closeable()
    installed: list[asyncio.Event] = []

    async def factory(settings):  # type: ignore[no-untyped-def]
        assert settings.group_name == "athena-ingestion"
        return athena_ingestion_worker.WorkerRuntime(worker, wakeups, (resource,))

    await athena_ingestion_worker.run(
        runtime_factory=factory,
        environment={
            "TAP_ATHENA_POLL_SECONDS": "0.01",
            "TAP_ATHENA_WAKEUP_SECONDS": "0.01",
        },
        signal_installer=installed.append,
        max_iterations=1,
    )

    assert worker.runs == 1
    assert len(installed) == 1
    assert resource.closed is True


def test_main_fails_closed_when_runtime_factory_is_not_configured() -> None:
    """`python -m` must not silently exit when Task 5 composition is absent."""

    with pytest.raises(RuntimeError, match="TAP_ATHENA_RUNTIME_FACTORY"):
        athena_ingestion_worker.main({})


@pytest.mark.parametrize(
    "environment",
    [
        {"TAP_ATHENA_JOB_BATCH_SIZE": "0"},
        {"TAP_ATHENA_JOB_BATCH_SIZE": "51"},
        {"TAP_ATHENA_POLL_SECONDS": "0"},
        {"TAP_ATHENA_POLL_SECONDS": "nan"},
        {"TAP_ATHENA_WAKEUP_SECONDS": "0"},
        {"TAP_ATHENA_WAKEUP_SECONDS": "61"},
        {"TAP_ATHENA_WORKER_ID": "   "},
    ],
)
def test_worker_settings_reject_unbounded_or_non_positive_values(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        athena_ingestion_worker.load_settings(environment)


@pytest.mark.skipif(
    os.getenv("TAP_RUN_REDIS_INTEGRATION") != "1",
    reason="requires the explicit real Redis integration gate",
)
def test_real_loop_claims_mysql_before_redis_ack_and_survives_stream_reset() -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        await _clean_knowledge(engine)
        redis = Redis.from_url(
            os.getenv("TAP_REDIS_URL", "redis://127.0.0.1:6379/0"),
            decode_responses=True,
        )
        stream = f"tap:test:athena-wakeup:{uuid4().hex}"
        consumer = RedisWakeupConsumer(
            redis=redis,
            stream_name=stream,
            group_name="athena-ingestion",
            consumer_name="integration-worker",
            aggregate_type="knowledge_document",
        )
        try:
            repository = MysqlDocumentRepository(sessions)
            reservation = await repository.reserve_upload(
                ReserveUpload(
                    filename="redis-order.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "9" * 64,
                    size=12,
                    now=datetime.now(),
                    staging_key=f"staging:{uuid4().hex}",
                )
            )
            await repository.activate_upload(reservation, ArtifactLocator("artifact:redis-order"))
            unrelated_id = await redis.xadd(
                stream,
                {"payload": json.dumps({"aggregateId": "turn-1", "aggregateType": "chat_turn"})},
            )
            relevant_id = await redis.xadd(
                stream,
                {
                    "payload": json.dumps(
                        {
                            "aggregateId": "doc-1",
                            "aggregateType": "knowledge_document",
                        }
                    )
                },
            )

            worker = MysqlClaimWorker(repository)
            await athena_ingestion_worker.run_worker_loop(
                worker=worker,
                wakeups=consumer,
                settings=replace(
                    athena_ingestion_worker.load_settings({}),
                    poll_seconds=0.05,
                    wakeup_seconds=0.05,
                ),
                stop=asyncio.Event(),
                max_iterations=1,
            )

            assert len(worker.claimed) == 1
            pending_after_claim = await redis.xpending(stream, "athena-ingestion")
            assert pending_after_claim["pending"] == 0
            assert unrelated_id != relevant_id

            await redis.delete(stream)
            reset_consumer = RedisWakeupConsumer(
                redis=redis,
                stream_name=stream,
                group_name="athena-ingestion",
                consumer_name="integration-worker-reset",
                aggregate_type="knowledge_document",
            )
            second = await repository.reserve_upload(
                ReserveUpload(
                    filename="redis-reset.md",
                    media_type="text/markdown",
                    source_content_hash="sha256:" + "8" * 64,
                    size=12,
                    now=datetime.now(),
                    staging_key=f"staging:{uuid4().hex}",
                )
            )
            await repository.activate_upload(second, ArtifactLocator("artifact:redis-reset"))
            reset_worker = MysqlClaimWorker(repository)
            await athena_ingestion_worker.run_worker_loop(
                worker=reset_worker,
                wakeups=reset_consumer,
                settings=replace(
                    athena_ingestion_worker.load_settings({}),
                    poll_seconds=0.05,
                    wakeup_seconds=0.05,
                ),
                stop=asyncio.Event(),
                max_iterations=1,
            )
            assert len(reset_worker.claimed) == 1
        finally:
            await redis.delete(stream)
            await redis.aclose()
            await _clean_knowledge(engine)
            await engine.dispose()

    asyncio.run(scenario())
