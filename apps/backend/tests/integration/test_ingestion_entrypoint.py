from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import signal
import time
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from pymilvus.decorators import _log_rpc_error
from redis.asyncio import Redis
from sqlalchemy import text

from tap.entrypoints import athena_ingestion_worker
from tap.entrypoints.athena_runtime import AthenaSettings, create_worker_runtime
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


def _emit_provider_rpc_error(details: str) -> None:
    try:
        raise RuntimeError(details)
    except RuntimeError:
        _log_rpc_error("synthetic_call", "RPC error", details, time.monotonic())


def _athena_environment(**overrides: str) -> dict[str, str]:
    values = {
        "ATHENA_API_HOST": "127.0.0.1",
        "ATHENA_WEB_HOST": "127.0.0.1",
        "ATHENA_POLL_SECONDS": "0.01",
        "ATHENA_JOB_BATCH_SIZE": "10",
        "ATHENA_WORKER_ID": "entrypoint-worker",
        "TAP_ATHENA_COMPOSE_PROJECT": "tap-athena-e2e",
        "TAP_DATABASE_URL": ("mysql+asyncmy://tap:test@127.0.0.1:13306/tap?charset=utf8mb4"),
        "TAP_ALEMBIC_DATABASE_URL": (
            "mysql+pymysql://tap:test@127.0.0.1:13306/tap?charset=utf8mb4"
        ),
        "TAP_REDIS_URL": "redis://127.0.0.1:16379/0",
        "TAP_REDIS_COMMAND_STREAM": "tap-athena-e2e:commands",
        "LITELLM_BASE_URL": "http://127.0.0.1:14000",
        "LITELLM_MODEL": "openai/test-chat",
        "LITELLM_EMBEDDING_MODEL": "openai/test-embedding",
        "MILVUS_URI": "http://127.0.0.1:29530",
    }
    values.update(overrides)
    return values


def _worker_settings() -> athena_ingestion_worker.WorkerSettings:
    return athena_ingestion_worker.load_settings(_athena_environment())


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


class OrderedWorker(ScanningWorker):
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        super().__init__()
        self.events = events
        self.fail = fail

    async def run_once(self, limit: int) -> WorkerRun:
        del limit
        self.events.append("db-scan")
        if self.fail:
            raise ValueError("worker-failed")
        return await super().run_once(1)


class CancelledWorker(OrderedWorker):
    async def run_once(self, limit: int) -> WorkerRun:
        del limit
        self.events.append("db-scan")
        raise asyncio.CancelledError("worker-cancelled")


class OrderedWakeups(LostWakeups):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events
        self.wakeup = object()

    async def wait(self, *, max_wait_seconds: float):
        del max_wait_seconds
        self.events.append("wait")
        return self.wakeup

    async def ack(self, wakeup):  # type: ignore[no-untyped-def]
        assert wakeup is self.wakeup
        self.events.append("ack")


class RecordingCloseable:
    def __init__(self, name: str, events: list[str], *, fail: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail = fail

    async def aclose(self) -> None:
        self.events.append(f"close:{self.name}")
        if self.fail:
            raise RuntimeError(f"close-failed:{self.name}")


class MysqlClaimWorker:
    def __init__(
        self, repository: MysqlDocumentRepository, events: list[str] | None = None
    ) -> None:
        self.repository = repository
        self.events = events
        self.claimed: tuple[str, ...] = ()

    async def run_once(self, limit: int) -> WorkerRun:
        if self.events is not None:
            self.events.append("db-scan")
        jobs = await self.repository.claim_jobs(
            worker_id="redis-order-worker",
            now=datetime.now(),
            lease_duration=timedelta(seconds=30),
            limit=limit,
        )
        self.claimed = tuple(job.job_id for job in jobs)
        return WorkerRun(len(jobs), 0, 0, 0, 0)


class RecordingWakeups:
    def __init__(self, consumer: RedisWakeupConsumer, events: list[str]) -> None:
        self.consumer = consumer
        self.events = events

    async def wait(self, *, max_wait_seconds: float):
        wakeup = await self.consumer.wait(max_wait_seconds=max_wait_seconds)
        self.events.append("wakeup" if wakeup is not None else "timeout")
        return wakeup

    async def ack(self, wakeup):  # type: ignore[no-untyped-def]
        await self.consumer.ack(wakeup)
        self.events.append("ack")


@pytest.mark.asyncio
async def test_worker_scans_mysql_when_redis_wakeup_is_lost() -> None:
    """Making Redis authoritative would strand a durable pending MySQL job."""

    worker = ScanningWorker()
    wakeups = LostWakeups()
    settings = replace(
        _worker_settings(),
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
        _worker_settings(),
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
async def test_relevant_wakeup_is_followed_by_db_scan_before_ack() -> None:
    """Moving the scan before wait can ACK a job hint before its first DB claim attempt."""

    events: list[str] = []
    await athena_ingestion_worker.run_worker_loop(
        worker=OrderedWorker(events),
        wakeups=OrderedWakeups(events),  # type: ignore[arg-type]
        settings=replace(
            _worker_settings(),
            poll_seconds=0.01,
            wakeup_seconds=0.01,
        ),
        stop=asyncio.Event(),
        max_iterations=1,
    )

    assert events == ["wait", "db-scan", "ack"]


@pytest.mark.asyncio
async def test_run_builds_signal_driven_runtime_and_closes_every_resource() -> None:
    """The process entrypoint must own stop handlers and finally-close its runtime."""

    worker = ScanningWorker()
    wakeups = LostWakeups()
    resource = Closeable()
    installed: list[asyncio.Event] = []

    async def factory(settings):  # type: ignore[no-untyped-def]
        assert settings.compose_project == "tap-athena-e2e"
        return athena_ingestion_worker.WorkerRuntime(worker, wakeups, (resource,))

    await athena_ingestion_worker.run(
        runtime_factory=factory,
        settings=AthenaSettings.from_mapping(_athena_environment()),
        signal_installer=installed.append,
        max_iterations=1,
    )

    assert worker.runs == 1
    assert len(installed) == 1
    assert resource.closed is True


@pytest.mark.asyncio
async def test_signal_install_failure_closes_runtime_before_preserving_error() -> None:
    """Once a factory returns resources, later initialization cannot leak them."""

    events: list[str] = []
    install_error = RuntimeError("signal-install-failed")
    resources = (
        RecordingCloseable("first", events),
        RecordingCloseable("last", events),
    )

    async def factory(settings):  # type: ignore[no-untyped-def]
        del settings
        return athena_ingestion_worker.WorkerRuntime(
            OrderedWorker(events),
            LostWakeups(),
            resources,
        )

    def install(stop: asyncio.Event):  # type: ignore[no-untyped-def]
        del stop
        events.append("install-signals")
        raise install_error

    with pytest.raises(RuntimeError) as captured:
        await athena_ingestion_worker.run(
            runtime_factory=factory,
            settings=AthenaSettings.from_mapping(_athena_environment()),
            signal_installer=install,
            max_iterations=1,
        )

    assert captured.value is install_error
    assert events == ["install-signals", "close:last", "close:first"]


@pytest.mark.asyncio
async def test_signal_install_and_close_failures_do_not_skip_other_resources() -> None:
    """A close failure must aggregate without hiding the initialization failure."""

    events: list[str] = []
    resources = (
        RecordingCloseable("first", events),
        RecordingCloseable("middle", events, fail=True),
        RecordingCloseable("last", events),
    )

    async def factory(settings):  # type: ignore[no-untyped-def]
        del settings
        return athena_ingestion_worker.WorkerRuntime(
            OrderedWorker(events),
            LostWakeups(),
            resources,
        )

    def install(stop: asyncio.Event):  # type: ignore[no-untyped-def]
        del stop
        events.append("install-signals")
        raise RuntimeError("signal-install-failed")

    with pytest.raises(BaseExceptionGroup) as captured:
        await athena_ingestion_worker.run(
            runtime_factory=factory,
            settings=AthenaSettings.from_mapping(_athena_environment()),
            signal_installer=install,
            max_iterations=1,
        )

    assert events == [
        "install-signals",
        "close:last",
        "close:middle",
        "close:first",
    ]
    errors = tuple(str(error) for error in captured.value.exceptions)
    assert errors == ("signal-install-failed", "close-failed:middle")


def test_signal_install_rolls_back_handlers_after_partial_failure(monkeypatch) -> None:
    """A later add failure cannot leave an earlier process handler installed."""

    events: list[str] = []

    class PartiallyFailingLoop:
        def add_signal_handler(self, signum, callback) -> None:  # type: ignore[no-untyped-def]
            del callback
            events.append(f"add:{signum.name}")
            if signum is signal.SIGTERM:
                raise RuntimeError("signal-install-failed")

        def remove_signal_handler(self, signum) -> bool:  # type: ignore[no-untyped-def]
            events.append(f"remove:{signum.name}")
            return True

    monkeypatch.setattr(
        athena_ingestion_worker.asyncio,
        "get_running_loop",
        lambda: PartiallyFailingLoop(),
    )

    with pytest.raises(RuntimeError, match="signal-install-failed"):
        athena_ingestion_worker.install_signal_handlers(asyncio.Event())

    assert events == ["add:SIGINT", "add:SIGTERM", "remove:SIGINT"]


def test_signal_removal_attempts_both_handlers_in_reverse_and_aggregates(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []

    class FailingRemovalLoop:
        def add_signal_handler(self, signum, callback) -> None:  # type: ignore[no-untyped-def]
            del callback
            events.append(f"add:{signum.name}")

        def remove_signal_handler(self, signum) -> bool:  # type: ignore[no-untyped-def]
            events.append(f"remove:{signum.name}")
            raise RuntimeError(f"remove-failed:{signum.name}")

    monkeypatch.setattr(
        athena_ingestion_worker.asyncio,
        "get_running_loop",
        lambda: FailingRemovalLoop(),
    )
    remove = athena_ingestion_worker.install_signal_handlers(asyncio.Event())

    with pytest.raises(ExceptionGroup) as captured:
        remove()

    assert events == [
        "add:SIGINT",
        "add:SIGTERM",
        "remove:SIGTERM",
        "remove:SIGINT",
    ]
    assert tuple(str(error) for error in captured.value.exceptions) == (
        "remove-failed:SIGTERM",
        "remove-failed:SIGINT",
    )


@pytest.mark.asyncio
async def test_lifecycle_attempts_every_cleanup_and_preserves_all_errors() -> None:
    """A handler or late resource failure cannot strand earlier runtime resources."""

    events: list[str] = []
    resources = (
        RecordingCloseable("first", events),
        RecordingCloseable("middle", events, fail=True),
        RecordingCloseable("last", events),
    )

    async def factory(settings):  # type: ignore[no-untyped-def]
        del settings
        return athena_ingestion_worker.WorkerRuntime(
            OrderedWorker(events, fail=True),
            LostWakeups(),
            resources,
        )

    def install(stop: asyncio.Event):  # type: ignore[no-untyped-def]
        del stop

        def remove() -> None:
            events.append("remove-handlers")
            raise RuntimeError("remove-handlers-failed")

        return remove

    with pytest.raises(BaseExceptionGroup) as captured:
        await athena_ingestion_worker.run(
            runtime_factory=factory,
            settings=AthenaSettings.from_mapping(_athena_environment()),
            signal_installer=install,
            max_iterations=1,
        )

    assert events == [
        "db-scan",
        "remove-handlers",
        "close:last",
        "close:middle",
        "close:first",
    ]
    errors = tuple(str(error) for error in captured.value.exceptions)
    assert errors == ("worker-failed", "remove-handlers-failed", "close-failed:middle")


@pytest.mark.asyncio
async def test_lifecycle_preserves_worker_cancellation_while_finishing_cleanup() -> None:
    events: list[str] = []

    async def factory(settings):  # type: ignore[no-untyped-def]
        del settings
        return athena_ingestion_worker.WorkerRuntime(
            CancelledWorker(events),
            LostWakeups(),
            (
                RecordingCloseable("first", events),
                RecordingCloseable("last", events, fail=True),
            ),
        )

    with pytest.raises(BaseExceptionGroup) as captured:
        await athena_ingestion_worker.run(
            runtime_factory=factory,
            settings=AthenaSettings.from_mapping(_athena_environment()),
            max_iterations=1,
        )

    assert events == ["db-scan", "close:last", "close:first"]
    assert isinstance(captured.value.exceptions[0], asyncio.CancelledError)
    assert str(captured.value.exceptions[1]) == "close-failed:last"


def test_main_uses_only_the_fixed_runtime_factory_and_one_settings_snapshot(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    seen: list[AthenaSettings] = []

    async def fixed_run(*, runtime_factory, settings, **_kwargs):  # type: ignore[no-untyped-def]
        assert runtime_factory is create_worker_runtime
        seen.append(settings)

    runner = asyncio.Runner()
    monkeypatch.setattr(athena_ingestion_worker, "run", fixed_run)
    monkeypatch.setattr(athena_ingestion_worker.asyncio, "run", runner.run)
    try:
        athena_ingestion_worker.main(
            _athena_environment(TAP_ATHENA_RUNTIME_FACTORY="malicious.module:arbitrary_callable")
        )
    finally:
        runner.close()

    assert len(seen) == 1
    assert seen[0].worker_id == "entrypoint-worker"
    assert seen[0].database_url.endswith("127.0.0.1:13306/tap?charset=utf8mb4")


def test_worker_main_suppresses_worker_thread_rpc_details_for_the_full_process_lifetime(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    provider_logger = logging.getLogger("pymilvus.decorators")
    tap_logger = logging.getLogger("tap.entrypoints.athena_ingestion_worker.test")
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    provider_level = provider_logger.level
    provider_propagate = provider_logger.propagate
    tap_level = tap_logger.level
    tap_propagate = tap_logger.propagate
    provider_logger.addHandler(handler)
    tap_logger.addHandler(handler)
    provider_logger.setLevel(logging.ERROR)
    provider_logger.propagate = False
    tap_logger.setLevel(logging.ERROR)
    tap_logger.propagate = False

    async def noisy_run(**_kwargs: object) -> None:
        await asyncio.to_thread(
            _emit_provider_rpc_error,
            "worker-provider-secret-rpc-detail",
        )
        tap_logger.error("WORKER_FIXED_LOG_VISIBLE")

    monkeypatch.setattr(athena_ingestion_worker, "run", noisy_run)
    try:
        athena_ingestion_worker.main(_athena_environment())
        _emit_provider_rpc_error("worker-filter-removed-after-main")
    finally:
        provider_logger.removeHandler(handler)
        tap_logger.removeHandler(handler)
        provider_logger.setLevel(provider_level)
        provider_logger.propagate = provider_propagate
        tap_logger.setLevel(tap_level)
        tap_logger.propagate = tap_propagate

    rendered = output.getvalue()
    assert "worker-provider-secret-rpc-detail" not in rendered
    assert "WORKER_FIXED_LOG_VISIBLE" in rendered
    assert "worker-filter-removed-after-main" in rendered


def test_worker_cli_redacts_runtime_primary_after_internal_cleanup(
    monkeypatch,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []

    async def fail_after_cleanup(**_kwargs: object) -> None:
        events.extend(["close:index", "close:blob", "close:engine"])
        raise RuntimeError("worker-provider-secret-primary")

    monkeypatch.setattr(athena_ingestion_worker, "run", fail_after_cleanup)

    result = athena_ingestion_worker.cli(_athena_environment())
    output = capsys.readouterr()

    assert result == 1
    assert events == ["close:index", "close:blob", "close:engine"]
    assert output.out == ""
    assert output.err == ("Athena ingestion worker failed; check local provider configuration.\n")
    assert "provider-secret" not in output.err
    assert "Traceback" not in output.err


def test_main_invalid_settings_construct_no_runtime(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    called = False

    async def forbidden_run(**_kwargs):  # type: ignore[no-untyped-def]
        nonlocal called
        called = True

    monkeypatch.setattr(athena_ingestion_worker, "run", forbidden_run)

    with pytest.raises(ValueError, match="ATHENA_API_HOST"):
        athena_ingestion_worker.main(_athena_environment(ATHENA_API_HOST="0.0.0.0"))

    assert called is False


@pytest.mark.parametrize(
    "environment",
    [
        {"ATHENA_JOB_BATCH_SIZE": "0"},
        {"ATHENA_JOB_BATCH_SIZE": "51"},
        {"ATHENA_POLL_SECONDS": "0"},
        {"ATHENA_POLL_SECONDS": "nan"},
        {"ATHENA_WORKER_ID": "   "},
    ],
)
def test_worker_settings_reject_unbounded_or_non_positive_values(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        athena_ingestion_worker.load_settings(_athena_environment(**environment))


def test_obsolete_tap_worker_values_are_not_a_second_authority() -> None:
    settings = athena_ingestion_worker.load_settings(
        _athena_environment(
            TAP_ATHENA_JOB_BATCH_SIZE="51",
            TAP_ATHENA_POLL_SECONDS="nan",
            TAP_ATHENA_WAKEUP_SECONDS="61",
            TAP_ATHENA_WORKER_ID="obsolete-worker",
        )
    )

    assert settings.job_batch_size == 10
    assert settings.poll_seconds == settings.wakeup_seconds == 0.01
    assert settings.worker_id == "entrypoint-worker"


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

            events: list[str] = []
            worker = MysqlClaimWorker(repository, events)
            await athena_ingestion_worker.run_worker_loop(
                worker=worker,
                wakeups=RecordingWakeups(consumer, events),  # type: ignore[arg-type]
                settings=replace(
                    _worker_settings(),
                    poll_seconds=0.05,
                    wakeup_seconds=0.05,
                ),
                stop=asyncio.Event(),
                max_iterations=1,
            )

            assert len(worker.claimed) == 1
            assert events == ["wakeup", "db-scan", "ack"]
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
            reset_events: list[str] = []
            reset_worker = MysqlClaimWorker(repository, reset_events)
            await athena_ingestion_worker.run_worker_loop(
                worker=reset_worker,
                wakeups=RecordingWakeups(reset_consumer, reset_events),  # type: ignore[arg-type]
                settings=replace(
                    _worker_settings(),
                    poll_seconds=0.05,
                    wakeup_seconds=0.05,
                ),
                stop=asyncio.Event(),
                max_iterations=1,
            )
            assert len(reset_worker.claimed) == 1
            assert reset_events == ["timeout", "db-scan"]
        finally:
            await redis.delete(stream)
            await redis.aclose()
            await _clean_knowledge(engine)
            await engine.dispose()

    asyncio.run(scenario())
