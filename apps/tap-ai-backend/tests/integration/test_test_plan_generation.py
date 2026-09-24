from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.mysql_checkpointer import graph_run
from tap.modules.chat.adapters.mysql_conversations import turn_artifact_link
from tap.modules.test_management.adapters.mysql import (
    MysqlTestPlanRepository,
    test_plan_generation_job,
    test_plan_revision,
)
from tap.modules.test_management.domain.models import GenerationJobStatus
from tap.modules.test_management.domain.validation import RevisionConflict
from tap.platform.db.project_scope import scope_values
from tests.integration.test_test_plan_publish import _draft
from tests.integration.test_test_plan_repository import (
    _request,
    _seed_completed_turn,
)


@pytest.mark.asyncio
async def test_generation_worker_reclaims_expired_job_and_commits_draft_with_artifact_link(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _seed_completed_turn(sessions)
        repository = MysqlTestPlanRepository(sessions, scope=VALIDATION_SCOPE)
        request = _request()
        await repository.request_generation(
            VALIDATION_SCOPE, request, now=datetime(2026, 9, 13, 12, 0)
        )
        first = (
            await repository.claim_generation_jobs(
                VALIDATION_SCOPE,
                worker_id="worker_first",
                now=datetime(2026, 9, 13, 12, 1),
                lease_duration=timedelta(seconds=60),
                limit=1,
            )
        )[0]
        context = await repository.generation_context(VALIDATION_SCOPE, first)
        assert context.request == request

        reclaimed = (
            await repository.claim_generation_jobs(
                VALIDATION_SCOPE,
                worker_id="worker_restart",
                now=datetime(2026, 9, 13, 12, 2, 1),
                lease_duration=timedelta(seconds=60),
                limit=1,
            )
        )[0]
        generated = replace(
            _draft(),
            test_plan_id=request.test_plan_id,
            revision_id=request.revision_id,
        ).with_recomputed_digest()
        with pytest.raises(RevisionConflict, match="lease"):
            await repository.complete_generation(
                VALIDATION_SCOPE,
                first,
                generated,
                now=datetime(2026, 9, 13, 12, 2, 2),
            )
        draft = await repository.complete_generation(
            VALIDATION_SCOPE,
            reclaimed,
            generated,
            now=datetime(2026, 9, 13, 12, 2, 3),
        )

        assert draft.status.value == "DRAFT"
        assert (
            await repository.get_generation_job(VALIDATION_SCOPE, request.job_id)
        ).status.value == "DRAFT_READY"
        assert (
            await repository.claim_generation_jobs(
                VALIDATION_SCOPE,
                worker_id="worker_duplicate",
                now=datetime(2026, 9, 13, 12, 4),
                lease_duration=timedelta(seconds=60),
                limit=1,
            )
            == ()
        )
        async with sessions() as session:
            link = (
                (
                    await session.execute(
                        select(turn_artifact_link).where(
                            turn_artifact_link.c.artifact_kind == "test-plan"
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert link["artifact_id"] == request.test_plan_id
        assert link["artifact_digest"] == generated.content_digest
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_generation_completion_rolls_back_business_graph_and_outbox_together(
    owned_project_mysql, monkeypatch
) -> None:
    async def fail_event(*_args, **_kwargs):
        raise RuntimeError("injected completion outbox failure")

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _seed_completed_turn(sessions)
        repository = MysqlTestPlanRepository(sessions, scope=VALIDATION_SCOPE)
        request = _request()
        await repository.request_generation(
            VALIDATION_SCOPE, request, now=datetime(2026, 9, 13, 12, 0)
        )
        claim = (
            await repository.claim_generation_jobs(
                VALIDATION_SCOPE,
                worker_id="worker",
                now=datetime(2026, 9, 13, 12, 1),
                lease_duration=timedelta(seconds=60),
                limit=1,
            )
        )[0]
        async with sessions() as session, session.begin():
            await session.execute(
                insert(graph_run).values(
                    **scope_values(VALIDATION_SCOPE),
                    run_id=request.job_id,
                    graph_version="test-design-generation-v1",
                    state_schema_version=1,
                    execution_mode="durable",
                    reasoning_mode="workflow",
                    status="RUNNING",
                    waiting_reason=None,
                    current_checkpoint_id="checkpoint-final",
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                    attempt_count=1,
                    budget={},
                    created_at=datetime(2026, 9, 13, 12, 1),
                    updated_at=datetime(2026, 9, 13, 12, 1),
                )
            )
        monkeypatch.setattr(
            "tap.modules.test_management.adapters.mysql.write_project_event", fail_event
        )
        generated = replace(
            _draft(),
            test_plan_id=request.test_plan_id,
            revision_id=request.revision_id,
        ).with_recomputed_digest()

        with pytest.raises(RuntimeError, match="injected completion outbox failure"):
            await repository.complete_generation(
                VALIDATION_SCOPE,
                claim,
                generated,
                now=datetime(2026, 9, 13, 12, 1, 1),
            )

        async with sessions() as session:
            revision_count = await session.scalar(
                select(func.count())
                .select_from(test_plan_revision)
                .where(test_plan_revision.c.revision_id == request.revision_id)
            )
            link_count = await session.scalar(
                select(func.count())
                .select_from(turn_artifact_link)
                .where(turn_artifact_link.c.artifact_id == request.test_plan_id)
            )
            job_status = await session.scalar(
                select(test_plan_generation_job.c.status).where(
                    test_plan_generation_job.c.job_id == request.job_id
                )
            )
            graph_status = await session.scalar(
                select(graph_run.c.status).where(graph_run.c.run_id == request.job_id)
            )
        assert revision_count == 0
        assert link_count == 0
        assert job_status == GenerationJobStatus.RUNNING.value
        assert graph_status == "RUNNING"
    finally:
        await engine.dispose()
