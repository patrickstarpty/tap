"""MySQL lock contention on the ingestion claim must not kill the worker.

The isolated E2E journey lost every app when `claim_jobs`' UPDATE was chosen
as an InnoDB deadlock victim (1213): the claim holds next-key locks on
`knowledge_ingestion_job` and then needs a shared lock on the job's
`knowledge_document` row, while `request_delete` holds that document row
exclusively and waits to insert its deletion job into the claim's locked
gap. The error escaped `run_worker_loop` and the supervisor stopped all apps.

The 1213 is injected at the driver boundary (the real asyncmy error class,
wrapped in SQLAlchemy's `OperationalError` as in production) so the real repository,
transaction rollback, worker and loop all run against the owned isolated
MySQL. Requires `TAP_RUN_MYSQL_INTEGRATION=1`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from asyncmy.errors import OperationalError as DriverOperationalError
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from tap.entrypoints import tapper_ingestion_worker
from tap.entrypoints.tapper_runtime import create_project_audit
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.mysql_documents import (
    MysqlDocumentRepository,
    knowledge_ingestion_job,
)
from tap.modules.knowledge.application.ingestion import IngestionWorker
from tap.modules.knowledge.domain.documents import canonical_sha256
from tap.modules.knowledge.ports.documents import ArtifactLocator, ReserveUpload
from tap.platform.db.project_scope import scope_predicates
from tap.platform.db.session import create_engine_and_session_factory
from tests.integration.test_ingestion_recovery import UnavailableWakeups, _clean

_CLAIM_UPDATE = "UPDATE knowledge_ingestion_job SET status=%s, lease_owner=%s"


class _UnavailableArtifacts:
    """The claimed job then fails its first artifact read -- a settled,
    recorded stage failure, which is all this test needs after the claim."""

    async def read_original(self, locator):  # type: ignore[no-untyped-def]
        raise RuntimeError(f"artifact store unavailable for {locator}")


class _NeverCalled:
    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        raise AssertionError(f"unexpected provider call {name}")


def _inject_claim_deadlocks(engine, remaining: list[int]) -> None:  # type: ignore[no-untyped-def]
    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def deadlock_claim(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        del conn, cursor, context, executemany
        if remaining[0] > 0 and statement.startswith(_CLAIM_UPDATE):
            remaining[0] -= 1
            # The same wrapping SQLAlchemy applies to a driver error raised
            # by the server (`DBAPIError.instance`): `.orig` is the driver's
            # own 1213, exactly what the E2E worker traceback showed.
            raise OperationalError(
                statement,
                parameters,
                DriverOperationalError(
                    1213, "Deadlock found when trying to get lock; try restarting transaction"
                ),
            )


async def _job_row(sessions):  # type: ignore[no-untyped-def]
    async with sessions() as session:
        return (
            (
                await session.execute(
                    select(knowledge_ingestion_job).where(
                        *scope_predicates(knowledge_ingestion_job, VALIDATION_SCOPE)
                    )
                )
            )
            .mappings()
            .one()
        )


def test_claim_deadlock_skips_one_poll_and_the_loop_claims_on_the_next(owned_project_mysql) -> None:  # type: ignore[no-untyped-def]
    async def scenario() -> None:
        database_url = owned_project_mysql.url.replace("+pymysql", "+asyncmy")
        engine, sessions = create_engine_and_session_factory(database_url)
        await _clean(engine)
        try:
            repository = MysqlDocumentRepository(
                sessions, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
            )
            body = b"claim contention"
            reservation = await repository.reserve_upload(
                ReserveUpload(
                    filename="contention.txt",
                    media_type="text/plain",
                    source_content_hash=canonical_sha256(body),
                    size=len(body),
                    now=datetime.now(),
                    staging_key="staging:claim-contention",
                )
            )
            await repository.activate_upload(
                reservation, ArtifactLocator("artifact:claim-contention")
            )

            # Every attempt within one poll deadlocks: the poll claims
            # nothing, and the rolled-back attempts leave no lease behind.
            remaining = [100]
            _inject_claim_deadlocks(engine, remaining)
            claimed = await repository.claim_jobs(
                worker_id="contended-worker",
                now=datetime.now(),
                lease_duration=timedelta(seconds=30),
                limit=10,
            )
            assert claimed == ()
            row = await _job_row(sessions)
            assert row["status"] == "pending"
            assert row["lease_owner"] is None
            assert row["lease_token"] is None

            # The real worker loop survives a deadlocked poll and claims the
            # job on a later one.
            remaining[0] = 3
            worker = IngestionWorker(
                repository=repository,
                artifacts=_UnavailableArtifacts(),  # type: ignore[arg-type]
                parser=_NeverCalled(),  # type: ignore[arg-type]
                chunker=_NeverCalled(),  # type: ignore[arg-type]
                embeddings=_NeverCalled(),  # type: ignore[arg-type]
                index=_NeverCalled(),  # type: ignore[arg-type]
                worker_id="contended-worker",
                embedding_model_alias="text-embedding-v4",
                embedding_dimension=3,
                index_version="tapper-index-v1",
            )
            await tapper_ingestion_worker.run_worker_loop(
                worker=worker,
                wakeups=UnavailableWakeups(),  # type: ignore[arg-type]
                settings=tapper_ingestion_worker.WorkerSettings(
                    database_url=database_url,
                    redis_url="redis://127.0.0.1:16379/0",
                    job_batch_size=10,
                    poll_seconds=0.01,
                    wakeup_seconds=0.01,
                    stream_name="tap-tapper-e2e:commands",
                    group_name="tapper-ingestion",
                    worker_id="contended-worker",
                ),
                stop=asyncio.Event(),
                max_iterations=3,
            )

            assert remaining[0] == 0
            row = await _job_row(sessions)
            assert row["status"] == "failed"
            assert row["stage"] == "parsing"
        finally:
            await _clean(engine)
            await engine.dispose()

    asyncio.run(scenario())
