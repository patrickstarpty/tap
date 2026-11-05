"""MySQL lock contention on any job claim must not kill its worker loop.

Each scenario runs the real repository and the real worker loop against the
owned isolated MySQL. `lock_contention_support.inject_lock_errors` makes the
claim's locking read lose to InnoDB (1213 deadlock victim / 1205 lock-wait
timeout) at the driver boundary: when every bounded attempt of one poll loses,
that poll claims nothing and leaves no lease behind, and a later poll claims
the job exactly once. Requires `TAP_RUN_MYSQL_INTEGRATION=1`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from tap.entrypoints import relay_reconciler, tapper_generation_worker, tapper_ingestion_worker
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql import chat_event, chat_turn
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
from tap.modules.chat.adapters.mysql_suggestions import (
    MysqlSuggestionStore,
    prompt_suggestion_refresh,
)
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.chat.application.suggestions import SuggestionRefreshWorker
from tap.modules.chat.domain.conversations import TurnInput
from tap.modules.chat.domain.suggestions import RefreshReason, SuggestionKey
from tap.modules.graph.adapters.mysql import graph_extraction_job
from tap.modules.graph.adapters.mysql_jobs import MysqlGraphJobStore
from tap.modules.graph.application.worker import GraphWorker
from tap.modules.graph.domain.jobs import GraphJobRequest
from tap.modules.test_management.adapters.mysql import test_plan_generation_job
from tap.modules.test_management.application.generation import (
    TestDesignWorker as DesignWorker,
)
from tap.platform.db.project_scope import scope_predicates
from tap.platform.db.schema import outbox
from tap.platform.db.session import create_engine_and_session_factory
from tests.integration.lock_contention_support import (
    IdleWakeups,
    inject_lock_errors,
    locking_read_of,
    worker_settings,
)
from tests.integration.plan_generation_support import (
    generation_request,
    plan_repository,
    seed_completed_turn,
    seed_test_design_authority,
)
from tests.owned_mysql import owned_project_database_url

LEASE = timedelta(seconds=60)


class _NeverCalled:
    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        raise AssertionError(f"unexpected collaborator call {name}")


def _turn_input() -> TurnInput:
    return TurnInput(
        message="Contended claim",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        resolved_resources=(),
        agent_revision_id="validation-knowledge-agent-v1",
        agent_revision_digest="sha256:" + "a" * 64,
        skill_revision_ids=("validation-citation-skill-v1",),
        skill_revision_digests=("sha256:" + "b" * 64,),
        retrieval_policy_digest="sha256:" + "c" * 64,
    )


async def _row(sessions, table, *predicates):  # type: ignore[no-untyped-def]
    async with sessions() as session:
        return (
            (
                await session.execute(
                    select(table).where(*scope_predicates(table, VALIDATION_SCOPE), *predicates)
                )
            )
            .mappings()
            .one()
        )


def _scenario(owned_project_mysql, body) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        database_url = owned_project_database_url(owned_project_mysql)
        engine, sessions = create_engine_and_session_factory(database_url)
        try:
            await body(database_url, engine, sessions)
        finally:
            await engine.dispose()

    asyncio.run(run())


async def _run_shared_loop(worker, database_url: str, *, worker_id: str, iterations: int) -> None:  # type: ignore[no-untyped-def]
    await tapper_ingestion_worker.run_worker_loop(
        worker=worker,
        wakeups=IdleWakeups(),  # type: ignore[arg-type]
        settings=worker_settings(database_url, worker_id=worker_id),
        stop=asyncio.Event(),
        max_iterations=iterations,
    )


@pytest.mark.parametrize("code", [1213, 1205])
def test_prompt_suggestion_claim_contention_skips_one_poll(owned_project_mysql, code: int) -> None:
    now = datetime(2026, 10, 22, 9, 0, 0, tzinfo=timezone.utc)
    key = SuggestionKey(actor_id=VALIDATION_SCOPE.actor_id, locale="en")

    class FailingService:
        async def refresh(self, refresh_key):  # type: ignore[no-untyped-def]
            raise RuntimeError(f"model unavailable for {refresh_key.locale}")

    async def body(database_url: str, engine: AsyncEngine, sessions) -> None:  # type: ignore[no-untyped-def]
        store = MysqlSuggestionStore(sessions, scope=VALIDATION_SCOPE)
        await store.request_refresh(key, RefreshReason.FILTERED, now=now)
        remaining = [100]
        inject_lock_errors(
            engine,
            matches=locking_read_of("prompt_suggestion_refresh", skip_locked=True),
            remaining=remaining,
            code=code,
        )
        assert (
            await store.claim_due(worker_id="contended", now=now, limit=10, lease_duration=LEASE)
            == ()
        )
        row = await _row(sessions, prompt_suggestion_refresh)
        assert row["lease_token"] is None and row["lease_owner"] is None

        remaining[0] = 3  # the loop's first poll loses every attempt
        refresher = SuggestionRefreshWorker(
            store=store,
            service=FailingService(),  # type: ignore[arg-type]
            worker_id="contended",
            clock=lambda: now,
            scope=VALIDATION_SCOPE,
        )
        await _run_shared_loop(refresher, database_url, worker_id="contended", iterations=3)

        assert remaining[0] == 0
        row = await _row(sessions, prompt_suggestion_refresh)
        assert row["attempt_count"] == 1  # claimed exactly once, then failed
        assert row["failure_code"] == "RuntimeError"
        assert row["lease_token"] is None

    _scenario(owned_project_mysql, body)


def test_graph_extraction_claim_contention_skips_one_poll(owned_project_mysql) -> None:
    class UnavailableArtifacts:
        async def read_chunks(self, locator):  # type: ignore[no-untyped-def]
            raise RuntimeError(f"artifact store unavailable for {locator}")

    async def body(database_url: str, engine: AsyncEngine, sessions) -> None:  # type: ignore[no-untyped-def]
        jobs = MysqlGraphJobStore(sessions)
        requested_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=5)
        request = GraphJobRequest.create(
            scope=VALIDATION_SCOPE,
            revision_id="source-revision-contended",
            chunks_locator="art-contended.chunks",
            extraction_profile_digest="sha256:" + "1" * 64,
            model_alias="qwen-plus",
        )
        await jobs.request(VALIDATION_SCOPE, request, now=requested_at)
        remaining = [100]
        inject_lock_errors(
            engine,
            matches=locking_read_of("graph_extraction_job", skip_locked=True),
            remaining=remaining,
        )
        assert (
            await jobs.claim(
                VALIDATION_SCOPE,
                worker_id="contended",
                now=requested_at + timedelta(seconds=1),
                lease_duration=LEASE,
                limit=5,
            )
            == ()
        )
        row = await _row(sessions, graph_extraction_job)
        assert row["status"] == "PENDING" and row["lease_token"] is None
        assert row["attempt_count"] == 0

        remaining[0] = 3
        worker = GraphWorker(
            jobs=jobs,
            artifacts=UnavailableArtifacts(),  # type: ignore[arg-type]
            extractor=_NeverCalled(),  # type: ignore[arg-type]
            scope=VALIDATION_SCOPE,
            worker_id="contended",
            lease_duration=LEASE,
            claim_interval_seconds=0.01,
        )
        await _run_shared_loop(worker, database_url, worker_id="contended", iterations=3)

        assert remaining[0] == 0
        row = await _row(sessions, graph_extraction_job)
        assert row["status"] == "FAILED" and row["failure_code"] == "RuntimeError"
        assert row["attempt_count"] == 1

    _scenario(owned_project_mysql, body)


def test_test_design_claim_contention_skips_one_poll(owned_project_mysql) -> None:
    async def body(database_url: str, engine: AsyncEngine, sessions) -> None:  # type: ignore[no-untyped-def]
        publication = await seed_test_design_authority(sessions)
        await seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        repository = plan_repository(sessions)
        request = generation_request(publication)
        requested_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=5)
        await repository.request_generation(VALIDATION_SCOPE, request, now=requested_at)

        remaining = [3]  # every attempt of the loop's only poll loses
        inject_lock_errors(
            engine,
            matches=locking_read_of("test_plan_generation_job", skip_locked=True),
            remaining=remaining,
            code=1205,
        )
        worker = DesignWorker(
            jobs=repository,
            generator=_NeverCalled(),  # type: ignore[arg-type]
            scope=VALIDATION_SCOPE,
            worker_id="contended",
        )
        await _run_shared_loop(worker, database_url, worker_id="contended", iterations=1)

        assert remaining[0] == 0
        job = test_plan_generation_job.c.job_id == request.job_id
        row = await _row(sessions, test_plan_generation_job, job)
        assert row["status"] == "PENDING" and row["lease_token"] is None
        assert row["lease_owner"] is None and row["attempt_count"] == 0

        claimed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        (claim,) = await repository.claim_generation_jobs(
            VALIDATION_SCOPE,
            worker_id="later",
            now=claimed_at,
            lease_duration=LEASE,
            limit=5,
        )
        assert claim.job.request.job_id == request.job_id
        row = await _row(sessions, test_plan_generation_job, job)
        assert row["attempt_count"] == 1 and row["lease_owner"] == "later"
        assert (
            await repository.claim_generation_jobs(
                VALIDATION_SCOPE,
                worker_id="duplicate",
                now=claimed_at,
                lease_duration=LEASE,
                limit=5,
            )
            == ()
        )

    _scenario(owned_project_mysql, body)


def test_chat_turn_claim_contention_polls_again_without_backoff(owned_project_mysql) -> None:
    async def body(database_url: str, engine: AsyncEngine, sessions) -> None:  # type: ignore[no-untyped-def]
        del database_url
        repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
        conversations = ConversationService(repository, scope=VALIDATION_SCOPE)
        await conversations.create("chat-contended", "turn-contended", "request-1", _turn_input())
        remaining = [100]
        inject_lock_errors(
            engine,
            matches=locking_read_of("conversation", skip_locked=False),
            remaining=remaining,
        )

        stop = asyncio.Event()
        waits: list[float] = []

        async def record_wait(event: asyncio.Event, timeout: float) -> None:
            del event
            waits.append(timeout)
            stop.set()

        await tapper_generation_worker.run_worker_loop(
            tapper_generation_worker.GenerationWorker(
                conversations=conversations, knowledge=_NeverCalled()
            ),
            stop,
            batch_size=5,
            poll_seconds=0.01,
            wait=record_wait,
        )
        # The contended poll claimed nothing: the loop waits its normal poll
        # interval, not the 1 s error backoff of a raised `run_once`.
        assert waits == [0.01]
        row = await _row(sessions, chat_turn, chat_turn.c.turn_id == "turn-contended")
        assert row["state"] == "queued" and row["processing_attempt"] == 0
        assert row["processing_lease_token"] is None
        async with sessions() as session:
            started = await session.scalar(
                select(func.count()).where(
                    chat_event.c.turn_id == "turn-contended",
                    chat_event.c.event_type == "turn.started",
                )
            )
        assert started == 0

        remaining[0] = 0
        ((_, claimed),) = await repository.claim_queued(limit=5)
        assert claimed.turn_id == "turn-contended" and claimed.attempt == 1

    _scenario(owned_project_mysql, body)


class _UnavailableRedis:
    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        async def unavailable(*args: object, **kwargs: object) -> object:
            raise ConnectionError(f"redis unavailable ({name})")

        return unavailable

    async def aclose(self) -> None:
        return None


async def _seed_outbox(sessions) -> None:  # type: ignore[no-untyped-def]
    conversations = ConversationService(
        MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE), scope=VALIDATION_SCOPE
    )
    await conversations.create("chat-relay", "turn-relay", "request-relay", _turn_input())
    async with sessions() as session:
        pending = await session.scalar(select(func.count()).where(outbox.c.status == "pending"))
    assert pending and pending >= 1


async def _run_contended_relay(  # type: ignore[no-untyped-def]
    monkeypatch, database_url: str, *, matches, remaining: list[int], iterations: int
) -> None:
    """`relay_reconciler.run` opens its own engine; inject into that one."""

    def contended_database(settings):  # type: ignore[no-untyped-def]
        relay_engine, relay_sessions = create_engine_and_session_factory(settings.database_url)
        inject_lock_errors(relay_engine, matches=matches, remaining=remaining)
        return relay_engine, relay_sessions

    monkeypatch.setattr(relay_reconciler, "_open_database", contended_database)
    monkeypatch.setattr(relay_reconciler, "create_redis_client", lambda url: _UnavailableRedis())
    await relay_reconciler.run(
        settings=relay_reconciler.RelaySettings(
            database_url=database_url,
            redis_url="redis://127.0.0.1:1/0",
            batch_size=10,
            poll_seconds=0.01,
            stream_name="tap-lock-contention:commands",
            worker_id="contended-relay",
        ),
        signal_installer=lambda stop: None,
        max_iterations=iterations,
    )


async def _outbox_states(sessions):  # type: ignore[no-untyped-def]
    async with sessions() as session:
        return (await session.execute(select(outbox.c.status, outbox.c.attempt_count))).all()


def test_relay_claim_contention_skips_one_poll(owned_project_mysql, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def body(database_url: str, engine: AsyncEngine, sessions) -> None:  # type: ignore[no-untyped-def]
        del engine
        await _seed_outbox(sessions)
        # The first iteration's reconcile and claim both lose every attempt;
        # the second iteration claims.
        remaining = [6]
        await _run_contended_relay(
            monkeypatch,
            database_url,
            matches=locking_read_of("outbox", skip_locked=True),
            remaining=remaining,
            iterations=2,
        )

        assert remaining[0] == 0
        attempts = await _outbox_states(sessions)
        # Claimed once on the second iteration; the unavailable Redis sends
        # each row back to pending for a later retry.
        assert attempts and all(row == ("pending", 1) for row in attempts)

    _scenario(owned_project_mysql, body)


def test_relay_settlement_contention_does_not_end_the_relay(
    owned_project_mysql, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    def settles_failure(statement: str) -> bool:
        # `mark_failed` is the only outbox UPDATE setting both columns.
        return (
            statement.startswith("UPDATE outbox SET")
            and "next_attempt_at" in statement
            and "last_error" in statement
        )

    async def body(database_url: str, engine: AsyncEngine, sessions) -> None:  # type: ignore[no-untyped-def]
        del engine
        await _seed_outbox(sessions)
        remaining = [1]
        await _run_contended_relay(
            monkeypatch,
            database_url,
            matches=settles_failure,
            remaining=remaining,
            iterations=3,
        )

        assert remaining[0] == 0
        # The lost settlement left its row leased ("publishing"); the later
        # iterations neither crashed nor claimed it again within the lease.
        states = await _outbox_states(sessions)
        assert ("publishing", 1) in states
        assert all(attempt == 1 for _, attempt in states)

    _scenario(owned_project_mysql, body)


def test_ingestion_stage_commit_contention_does_not_end_the_loop(owned_project_mysql) -> None:
    from tap.entrypoints.tapper_runtime import create_project_audit
    from tap.modules.knowledge.adapters.mysql_documents import (
        MysqlDocumentRepository,
        knowledge_ingestion_job,
    )
    from tap.modules.knowledge.application.ingestion import IngestionWorker
    from tap.modules.knowledge.domain.documents import canonical_sha256
    from tap.modules.knowledge.ports.documents import ArtifactLocator, ReserveUpload

    class UnavailableArtifacts:
        async def read_original(self, locator):  # type: ignore[no-untyped-def]
            raise RuntimeError(f"artifact store unavailable for {locator}")

    async def body(database_url: str, engine: AsyncEngine, sessions) -> None:  # type: ignore[no-untyped-def]
        repository = MysqlDocumentRepository(
            sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        )
        content = b"stage contention"
        reservation = await repository.reserve_upload(
            ReserveUpload(
                filename="stage.txt",
                media_type="text/plain",
                source_content_hash=canonical_sha256(content),
                size=len(content),
                now=datetime.now(),
                staging_key="staging:stage-contention",
            )
        )
        await repository.activate_upload(reservation, ArtifactLocator("artifact:stage-contention"))
        # The claim (SKIP LOCKED) succeeds; the pass's next blocking lock on
        # the job -- its stage/settlement path -- loses to InnoDB once.
        remaining = [1]
        inject_lock_errors(
            engine,
            matches=locking_read_of("knowledge_ingestion_job", skip_locked=False),
            remaining=remaining,
        )
        worker = IngestionWorker(
            repository=repository,
            artifacts=UnavailableArtifacts(),  # type: ignore[arg-type]
            parser=_NeverCalled(),  # type: ignore[arg-type]
            chunker=_NeverCalled(),  # type: ignore[arg-type]
            embeddings=_NeverCalled(),  # type: ignore[arg-type]
            index=_NeverCalled(),  # type: ignore[arg-type]
            worker_id="contended-worker",
            embedding_model_alias="text-embedding-v4",
            embedding_dimension=3,
            index_version="tapper-index-v1",
        )
        await _run_shared_loop(worker, database_url, worker_id="contended-worker", iterations=3)

        assert remaining[0] == 0
        row = await _row(sessions, knowledge_ingestion_job)
        # The lost pass keeps its live lease (reclaimed after it expires); the
        # later polls neither crash nor claim the leased job a second time.
        assert row["status"] == "processing" and row["stage"] == "stored"
        assert row["lease_owner"] == "contended-worker"

    _scenario(owned_project_mysql, body)


def test_exhausted_turn_settlement_is_reported_once_across_lost_attempts(
    owned_project_mysql, span_recorder, caplog
) -> None:  # type: ignore[no-untyped-def]
    """A claim attempt that settles an exhausted Turn and then loses to lock
    contention rolled the settlement back: its warning and span must only be
    emitted for the attempt that committed."""

    def settles_turn(statement: str) -> bool:
        # The settlement UPDATE; the claim's own UPDATE also sets the attempt.
        set_clause = statement.split(" WHERE ", 1)[0]
        return (
            set_clause.startswith("UPDATE chat_turn SET state=%s")
            and "last_sequence" in set_clause
            and "processing_attempt" not in set_clause
        )

    async def body(database_url: str, engine: AsyncEngine, sessions) -> None:  # type: ignore[no-untyped-def]
        del database_url
        repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
        conversations = ConversationService(repository, scope=VALIDATION_SCOPE)
        await conversations.create("chat-exhausted", "turn-exhausted", "request-x", _turn_input())
        async with sessions() as session, session.begin():
            await session.execute(
                chat_turn.update()
                .where(chat_turn.c.turn_id == "turn-exhausted")
                .values(processing_attempt=3)
            )
        remaining = [2]  # the first two attempts lose after settling
        inject_lock_errors(engine, matches=settles_turn, remaining=remaining)
        caplog.set_level(logging.WARNING)
        span_recorder.clear()

        batch = await repository.claim_queued(limit=5)

        assert remaining[0] == 0
        assert tuple(batch) == () and len(batch.wakeups) == 1
        row = await _row(sessions, chat_turn, chat_turn.c.turn_id == "turn-exhausted")
        assert row["state"] == "failed"
        settled = [
            r
            for r in caplog.records
            if r.getMessage() == "conversation turn settled as unclaimable"
        ]
        assert len(settled) == 1
        spans = [
            s for s in span_recorder.get_finished_spans() if s.name == "turn.attempts_exhausted"
        ]
        assert len(spans) == 1

    _scenario(owned_project_mysql, body)
