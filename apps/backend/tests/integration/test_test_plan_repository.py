from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql import chat_turn
from tap.modules.chat.adapters.mysql_conversations import (
    conversation,
    turn_answer_evidence_snapshot,
    turn_input_snapshot,
)
from tap.modules.test_management.adapters.mysql import MysqlTestPlanRepository
from tap.modules.test_management.domain.models import (
    TestPlanGenerationRequest as PlanGenerationRequest,
)
from tap.platform.db.project_scope import scope_values
from tap.platform.db.schema import outbox


async def _seed_completed_turn(sessions, *, turn_id: str = "turn_checkout") -> None:
    now = datetime(2026, 9, 13, 12, 0, 0)
    async with sessions() as session, session.begin():
        await session.execute(
            insert(conversation).values(
                **scope_values(VALIDATION_SCOPE),
                conversation_id="conversation_checkout",
                title="Checkout",
                created_at=now,
                updated_at=now,
            )
        )
        await session.execute(
            insert(chat_turn).values(
                **scope_values(VALIDATION_SCOPE),
                turn_id=turn_id,
                chat_id="conversation_checkout",
                client_request_id="request_checkout",
                message="Design checkout tests",
                state="completed",
                processing_attempt=1,
                last_sequence=2,
                created_at=now,
            )
        )
        await session.execute(
            insert(turn_input_snapshot).values(
                **scope_values(VALIDATION_SCOPE),
                snapshot_id="input_checkout",
                turn_id=turn_id,
                snapshot_digest="sha256:" + "1" * 64,
                snapshot={},
                created_at=now,
            )
        )
        await session.execute(
            insert(turn_answer_evidence_snapshot).values(
                **scope_values(VALIDATION_SCOPE),
                snapshot_id="answer_checkout",
                turn_id=turn_id,
                input_snapshot_digest="sha256:" + "1" * 64,
                answer_digest="sha256:" + "3" * 64,
                snapshot_digest="sha256:" + "2" * 64,
                snapshot={},
                created_at=now,
            )
        )


def _request(**changes: str) -> PlanGenerationRequest:
    values = {
        "project_id": VALIDATION_SCOPE.project_id,
        "conversation_id": "conversation_checkout",
        "turn_id": "turn_checkout",
        "input_snapshot_digest": "sha256:" + "1" * 64,
        "answer_evidence_snapshot_digest": "sha256:" + "2" * 64,
        "model_alias": "tapper-chat",
        "agent_revision_id": "validation_knowledge_agent_v1",
        "skill_revision_ids": ("validation_test_design_skill_v1",),
        "objective": "Design checkout tests",
        "idempotency_key": "quality_test_design_checkout",
    }
    values.update(changes)
    return PlanGenerationRequest.create(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_generation_job_binds_exact_completed_turn_snapshots_and_replays(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _seed_completed_turn(sessions)
        repository = MysqlTestPlanRepository(sessions, scope=VALIDATION_SCOPE)
        request = _request()

        first = await repository.request_generation(request, now=datetime(2026, 9, 13, 12, 1))
        replay = await repository.request_generation(request, now=datetime(2026, 9, 13, 12, 2))

        assert replay == first
        assert first.request == request
        assert first.status.value == "PENDING"
        async with sessions() as session:
            event = (
                (
                    await session.execute(
                        select(outbox).where(
                            outbox.c.message_type == "test-plan.generation.requested"
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert event["envelope"]["payload"] == {
            "revisionId": request.revision_id,
            "inputSnapshotDigest": request.input_snapshot_digest,
            "answerEvidenceSnapshotDigest": request.answer_evidence_snapshot_digest,
            "requestDigest": request.request_digest,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"input_snapshot_digest": "sha256:" + "9" * 64},
        {"answer_evidence_snapshot_digest": "sha256:" + "9" * 64},
        {"turn_id": "turn_other"},
        {"conversation_id": "conversation_other"},
    ],
)
async def test_generation_job_rejects_tampered_or_cross_turn_snapshots(
    owned_project_mysql, changes
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _seed_completed_turn(sessions)
        repository = MysqlTestPlanRepository(sessions, scope=VALIDATION_SCOPE)
        with pytest.raises(ValueError, match="snapshot|Turn"):
            await repository.request_generation(
                _request(**changes), now=datetime(2026, 9, 13, 12, 1)
            )
    finally:
        await engine.dispose()
