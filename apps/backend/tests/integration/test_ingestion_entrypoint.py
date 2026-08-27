from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from tap.entrypoints import athena_ingestion_worker
from tap.modules.knowledge.application.ingestion import WorkerRun
from tap.platform.messaging.redis_wakeup import RedisWakeupConsumer


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
def test_real_redis_consumer_filters_generically_and_acks_only_after_claim_attempt() -> None:
    async def scenario() -> None:
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

            wakeup = await consumer.wait(max_wait_seconds=1.0)

            assert wakeup is not None
            assert wakeup.aggregate_id == "doc-1"
            pending_before_claim = await redis.xpending(stream, "athena-ingestion")
            assert pending_before_claim["pending"] == 1
            await consumer.ack(wakeup)
            pending_after_claim = await redis.xpending(stream, "athena-ingestion")
            assert pending_after_claim["pending"] == 0
            assert unrelated_id != relevant_id
        finally:
            await redis.delete(stream)
            await redis.aclose()

    asyncio.run(scenario())
