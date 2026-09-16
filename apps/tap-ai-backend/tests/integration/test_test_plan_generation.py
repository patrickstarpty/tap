from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql_conversations import turn_artifact_link
from tap.modules.test_management.adapters.mysql import MysqlTestPlanRepository
from tap.modules.test_management.domain.validation import RevisionConflict
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
