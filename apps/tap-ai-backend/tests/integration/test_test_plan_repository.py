from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.litellm import ProviderModelMapping
from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
from tap.modules.ai.application.assets import validation_asset_seed
from tap.modules.chat.adapters.mysql import chat_turn
from tap.modules.chat.adapters.mysql_conversations import (
    conversation,
    turn_answer_evidence_snapshot,
    turn_input_snapshot,
)
from tap.modules.knowledge.adapters.mysql_review import MysqlKnowledgeReviewRepository
from tap.modules.knowledge.application.review import KnowledgeReviewApplication
from tap.modules.test_management.adapters.model_gateway_generation import (
    design_model_revision_id,
)
from tap.modules.test_management.adapters.mysql import MysqlTestPlanRepository
from tap.modules.test_management.domain.models import (
    RequirementScopeItem,
    RequirementScopeSnapshot,
)
from tap.modules.test_management.domain.models import (
    TestPlanGenerationRequest as PlanGenerationRequest,
)
from tap.platform.db.project_scope import scope_values
from tap.platform.db.schema import outbox
from tests.integration.test_knowledge_publication import (
    NOW,
    ReadyProjection,
    approved_review,
    seed_authority,
)

TEST_DESIGN_MODEL_MAPPING = ProviderModelMapping("fake", "deterministic-chat-v1")


def _repository(sessions):  # type: ignore[no-untyped-def]
    return MysqlTestPlanRepository(
        sessions,
        scope=VALIDATION_SCOPE,
        model_mapping=TEST_DESIGN_MODEL_MAPPING,
    )


async def _seed_test_design_authority(sessions):  # type: ignore[no-untyped-def]
    await seed_authority(sessions)
    await MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).seed(
        validation_asset_seed(VALIDATION_SCOPE)
    )
    repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
    review = approved_review("krv_test_design_generation")
    await repository.create_review(review)
    return await KnowledgeReviewApplication(repository, ReadyProjection()).publish_review(
        review.review_id,
        generation="generation-001",
        idempotency_key="publish-test-design-generation",
        actor_id="synthetic-reviewer-02",
        expected_version=review.version,
        now=NOW,
    )


async def _seed_completed_turn(
    sessions,
    *,
    turn_id: str = "turn_checkout",
    source_revision_id: str = "source_revision_checkout",
) -> None:
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
                snapshot={
                    "model_alias": "tapper-chat",
                    "agent_revision_id": "validation-knowledge-agent-v2",
                    "skill_revision_ids": ["validation-citation-skill-v2"],
                },
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
                snapshot={
                    "citations": [
                        {
                            "sourceRevisionId": source_revision_id,
                            "documentRevisionId": "document_revision_checkout",
                            "chunkId": "chunk_checkout",
                            "contentDigest": "sha256:" + "a" * 64,
                            "claimText": "Checkout creates an order",
                            "origin": "SOURCE",
                        }
                    ]
                },
                created_at=now,
            )
        )


def _request(publication=None, **changes: str) -> PlanGenerationRequest:  # type: ignore[no-untyped-def]
    scope_id = "checkout_scope_v1" if publication is None else publication.publication_id
    scope_version = 1 if publication is None else publication.version
    source_revision_id = (
        "source_revision_checkout" if publication is None else publication.source_revision_ids[0]
    )
    requirement_id = (
        "requirement_checkout" if publication is None else publication.approved_item_ids[0]
    )
    values = {
        "project_id": VALIDATION_SCOPE.project_id,
        "conversation_id": "conversation_checkout",
        "turn_id": "turn_checkout",
        "input_snapshot_digest": "sha256:" + "1" * 64,
        "answer_evidence_snapshot_digest": "sha256:" + "2" * 64,
        "model_alias": "tapper-chat",
        "agent_revision_id": "validation-knowledge-agent-v2",
        "skill_revision_ids": ("validation-citation-skill-v2",),
        "objective": "Design checkout tests",
        "idempotency_key": "quality_test_design_checkout",
        "requirement_scope": RequirementScopeSnapshot.create(
            scope_id=scope_id,
            version=scope_version,
            requirements=(
                RequirementScopeItem(
                    requirement_id,
                    source_revision_id,
                    "paragraph:1",
                ),
            ),
        ),
        "approved_knowledge_revision_ids": (source_revision_id,),
        "model_revision_id": design_model_revision_id("tapper-chat", TEST_DESIGN_MODEL_MAPPING),
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
        publication = await _seed_test_design_authority(sessions)
        await _seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        repository = _repository(sessions)
        request = _request(publication)

        first = await repository.request_generation(
            VALIDATION_SCOPE, request, now=datetime(2026, 9, 13, 12, 1)
        )
        replay = await repository.request_generation(
            VALIDATION_SCOPE, request, now=datetime(2026, 9, 13, 12, 2)
        )

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
        {"model_alias": "other-model"},
        {"agent_revision_id": "other-agent"},
        {"skill_revision_ids": ("other-skill",)},
    ],
)
async def test_generation_job_rejects_tampered_or_cross_turn_snapshots(
    owned_project_mysql, changes
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        publication = await _seed_test_design_authority(sessions)
        await _seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        repository = _repository(sessions)
        with pytest.raises(ValueError, match="snapshot|Turn|governance|model route"):
            await repository.request_generation(
                VALIDATION_SCOPE,
                _request(publication, **changes),
                now=datetime(2026, 9, 13, 12, 1),
            )
    finally:
        await engine.dispose()
