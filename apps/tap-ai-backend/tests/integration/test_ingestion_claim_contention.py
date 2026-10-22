"""MySQL lock contention on the ingestion claim must not kill the worker.

The isolated E2E journey lost every app when `claim_jobs`' UPDATE was chosen
as an InnoDB deadlock victim (1213): the claim holds next-key locks on
`knowledge_ingestion_job` and then needs a shared lock on the job's
`knowledge_document` row, while `request_delete` holds that document row
exclusively and waits to insert its deletion job into the claim's locked
gap. The error escaped `run_worker_loop` and the supervisor stopped all apps.

The 1213 is injected at the driver boundary (`lock_contention_support`) so the
real repository, transaction rollback, worker and loop all run against the
owned isolated MySQL. Requires `TAP_RUN_MYSQL_INTEGRATION=1`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select

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
from tests.integration.lock_contention_support import (
    IdleWakeups,
    inject_lock_errors,
    worker_settings,
)
from tests.owned_mysql import owned_project_database_url

_CLAIM_UPDATE = "UPDATE knowledge_ingestion_job SET status=%s, lease_owner=%s"


class _UnavailableArtifacts:
    """The claimed job then fails its first artifact read -- a settled,
    recorded stage failure, which is all this test needs after the claim."""

    async def read_original(self, locator):  # type: ignore[no-untyped-def]
        raise RuntimeError(f"artifact store unavailable for {locator}")


class _NeverCalled:
    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        raise AssertionError(f"unexpected provider call {name}")


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
        database_url = owned_project_database_url(owned_project_mysql)
        engine, sessions = create_engine_and_session_factory(database_url)
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
            inject_lock_errors(
                engine, matches=lambda sql: sql.startswith(_CLAIM_UPDATE), remaining=remaining
            )
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
                wakeups=IdleWakeups(),  # type: ignore[arg-type]
                settings=worker_settings(database_url, worker_id="contended-worker"),
                stop=asyncio.Event(),
                max_iterations=3,
            )

            assert remaining[0] == 0
            row = await _job_row(sessions)
            assert row["status"] == "failed"
            assert row["stage"] == "parsing"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
