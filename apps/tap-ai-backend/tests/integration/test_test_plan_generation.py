from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.litellm import ProviderModelMapping
from tap.modules.ai.adapters.mysql import ai_agent_revision
from tap.modules.ai.adapters.mysql_checkpointer import graph_run, graph_settlement
from tap.modules.ai.domain.models import ModelGatewayUnavailable
from tap.modules.chat.adapters.mysql import chat_event, chat_turn
from tap.modules.chat.adapters.mysql_conversations import turn_artifact_link
from tap.modules.test_management.adapters.mysql import (
    MysqlReconciledTestDesign,
    MysqlTestPlanRepository,
    test_design_model_call,
    test_plan_generation_job,
    test_plan_revision,
)
from tap.modules.test_management.domain.models import GenerationJobStatus
from tap.modules.test_management.domain.validation import RevisionConflict
from tap.modules.test_management.ports.generation import GenerationResponseUnknown
from tap.platform.db.project_scope import scope_values
from tests.integration.test_test_plan_publish import _draft
from tests.integration.test_test_plan_repository import (
    _repository,
    _request,
    _seed_completed_turn,
    _seed_test_design_authority,
)


@pytest.mark.asyncio
async def test_generation_worker_reclaims_expired_job_and_commits_draft_with_artifact_link(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        base = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        publication = await _seed_test_design_authority(sessions)
        await _seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        repository = _repository(sessions)
        request = _request(publication)
        await repository.request_generation(VALIDATION_SCOPE, request, now=base)
        first = (
            await repository.claim_generation_jobs(
                VALIDATION_SCOPE,
                worker_id="worker_first",
                now=base + timedelta(seconds=1),
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
                now=base + timedelta(seconds=62),
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
                now=base + timedelta(seconds=63),
            )
        draft = await repository.complete_generation(
            VALIDATION_SCOPE,
            reclaimed,
            generated,
            now=base + timedelta(seconds=64),
        )

        assert draft.status.value == "DRAFT"
        assert (
            await repository.get_generation_job(VALIDATION_SCOPE, request.job_id)
        ).status.value == "DRAFT_READY"
        assert (
            await repository.claim_generation_jobs(
                VALIDATION_SCOPE,
                worker_id="worker_duplicate",
                now=base + timedelta(seconds=120),
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
            event = (
                (
                    await session.execute(
                        select(chat_event).where(
                            chat_event.c.turn_id == request.turn_id,
                            chat_event.c.event_type == "test-plan.generation.result_ready",
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert link["artifact_id"] == request.test_plan_id
        assert link["artifact_digest"] == generated.content_digest
        assert event["payload"] == {
            "jobId": request.job_id,
            "testPlanId": request.test_plan_id,
            "revisionId": request.revision_id,
            "deepLink": (
                f"/test-management/{request.test_plan_id}/revisions/{request.revision_id}"
            ),
        }
        assert event["stream_sequence"] == 3
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
        publication = await _seed_test_design_authority(sessions)
        await _seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        repository = _repository(sessions)
        request = _request(publication)
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
            settlement_count = await session.scalar(
                select(func.count())
                .select_from(graph_settlement)
                .where(graph_settlement.c.run_id == request.job_id)
            )
        assert revision_count == 0
        assert link_count == 0
        assert job_status == GenerationJobStatus.RUNNING.value
        assert graph_status == "RUNNING"
        assert settlement_count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_generation_claim_cannot_load_model_context(owned_project_mysql) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        publication = await _seed_test_design_authority(sessions)
        await _seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        repository = _repository(sessions)
        request = _request(publication)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        await repository.request_generation(VALIDATION_SCOPE, request, now=now)
        claim = (
            await repository.claim_generation_jobs(
                VALIDATION_SCOPE,
                worker_id="expired-worker",
                now=now,
                lease_duration=timedelta(seconds=60),
                limit=1,
            )
        )[0]
        async with sessions() as session, session.begin():
            await session.execute(
                update(test_plan_generation_job)
                .where(test_plan_generation_job.c.job_id == request.job_id)
                .values(lease_expires_at=func.utc_timestamp(6) - text("INTERVAL 1 SECOND"))
            )

        with pytest.raises(RevisionConflict, match="lease"):
            await repository.generation_context(VALIDATION_SCOPE, claim)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_generation_context_rejects_revoked_agent_before_model_io(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        publication = await _seed_test_design_authority(sessions)
        await _seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        repository = _repository(sessions)
        request = _request(publication)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        await repository.request_generation(VALIDATION_SCOPE, request, now=now)
        claim = (
            await repository.claim_generation_jobs(
                VALIDATION_SCOPE,
                worker_id="revoked-agent-worker",
                now=now,
                lease_duration=timedelta(seconds=60),
                limit=1,
            )
        )[0]
        async with sessions() as session, session.begin():
            await session.execute(
                update(ai_agent_revision)
                .where(ai_agent_revision.c.revision_id == request.agent_revision_id)
                .values(status="DISABLED")
            )

        with pytest.raises(ValueError, match="enabled Agent and Skill"):
            await repository.generation_context(VALIDATION_SCOPE, claim)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_generation_context_rejects_changed_provider_model_mapping(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        publication = await _seed_test_design_authority(sessions)
        await _seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        repository = _repository(sessions)
        request = _request(publication)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        await repository.request_generation(VALIDATION_SCOPE, request, now=now)
        claim = (
            await repository.claim_generation_jobs(
                VALIDATION_SCOPE,
                worker_id="changed-model-worker",
                now=now,
                lease_duration=timedelta(seconds=60),
                limit=1,
            )
        )[0]
        changed_repository = MysqlTestPlanRepository(
            sessions,
            scope=VALIDATION_SCOPE,
            model_alias="tapper-chat",
            model_mapping=ProviderModelMapping("fake", "deterministic-chat-v2"),
        )

        with pytest.raises(ValueError, match="model route"):
            await changed_repository.generation_context(VALIDATION_SCOPE, claim)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_provider_response_is_held_for_reconciliation_without_reissue(
    owned_project_mysql,
) -> None:
    class AmbiguousProvider:
        calls = 0

        async def generate(self, _context):
            self.calls += 1
            raise ModelGatewayUnavailable

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        publication = await _seed_test_design_authority(sessions)
        await _seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        repository = _repository(sessions)
        request = _request(publication)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        await repository.request_generation(VALIDATION_SCOPE, request, now=now)
        claim = (
            await repository.claim_generation_jobs(
                VALIDATION_SCOPE,
                worker_id="reconcile-worker",
                now=now,
                lease_duration=timedelta(seconds=60),
                limit=1,
            )
        )[0]
        context = await repository.generation_context(VALIDATION_SCOPE, claim)
        provider = AmbiguousProvider()
        generator = MysqlReconciledTestDesign(
            sessions,
            scope=VALIDATION_SCOPE,
            delegate=provider,
        )

        with pytest.raises(GenerationResponseUnknown, match="reconciliation"):
            await generator.generate(context)
        with pytest.raises(GenerationResponseUnknown, match="reconciliation"):
            await generator.generate(context)

        assert provider.calls == 1
        async with sessions() as session:
            state = await session.scalar(
                select(test_design_model_call.c.status).where(
                    test_design_model_call.c.job_id == request.job_id
                )
            )
        assert state == "UNKNOWN"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_running_generation_can_be_cancelled_and_not_reclaimed(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        publication = await _seed_test_design_authority(sessions)
        await _seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        repository = _repository(sessions)
        request = _request(publication)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        await repository.request_generation(VALIDATION_SCOPE, request, now=now)
        claim = (
            await repository.claim_generation_jobs(
                VALIDATION_SCOPE,
                worker_id="cancel-worker",
                now=now,
                lease_duration=timedelta(seconds=60),
                limit=1,
            )
        )[0]
        await repository.wait_generation(
            VALIDATION_SCOPE,
            claim,
            reason="human-confirmation",
            now=now + timedelta(seconds=1),
        )
        waiting = await repository.get_generation_job(VALIDATION_SCOPE, request.job_id)
        assert waiting.row_version == claim.job.row_version + 1

        canceled = await repository.cancel_generation(
            VALIDATION_SCOPE,
            request.job_id,
            expected_version=waiting.row_version,
            idempotency_key="cancel-waiting-generation",
            now=now + timedelta(seconds=2),
        )

        assert canceled.status is GenerationJobStatus.CANCELED
        assert canceled.row_version == waiting.row_version + 1
        assert canceled.failure_code == "canceled-by-user"
        replayed = await repository.cancel_generation(
            VALIDATION_SCOPE,
            request.job_id,
            expected_version=waiting.row_version,
            idempotency_key="cancel-waiting-generation",
            now=now + timedelta(seconds=3),
        )
        assert replayed.row_version == canceled.row_version
        terminal = await repository.cancel_generation(
            VALIDATION_SCOPE,
            request.job_id,
            expected_version=canceled.row_version,
            idempotency_key="cancel-terminal-generation",
            now=now + timedelta(seconds=4),
        )
        assert terminal.row_version == canceled.row_version
        assert (
            await repository.claim_generation_jobs(
                VALIDATION_SCOPE,
                worker_id="other-worker",
                now=now + timedelta(seconds=61),
                lease_duration=timedelta(seconds=60),
                limit=1,
            )
            == ()
        )
        async with sessions() as session:
            events = (
                (
                    await session.execute(
                        select(
                            chat_event.c.event_type,
                            chat_event.c.payload,
                            chat_event.c.stream_sequence,
                        )
                        .where(chat_event.c.turn_id == request.turn_id)
                        .order_by(chat_event.c.stream_sequence)
                    )
                )
                .mappings()
                .all()
            )
            last_sequence = await session.scalar(
                select(chat_turn.c.last_sequence).where(chat_turn.c.turn_id == request.turn_id)
            )
        assert [event["event_type"] for event in events] == [
            "test-plan.generation.waiting",
            "test-plan.generation.canceled",
        ]
        assert [event["stream_sequence"] for event in events] == [3, 4]
        assert events[0]["payload"]["reason"] == "human-confirmation"
        assert events[1]["payload"]["reason"] == "canceled-by-user"
        assert last_sequence == 4
    finally:
        await engine.dispose()
