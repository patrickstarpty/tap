from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
from tap.modules.ai.application.assets import validation_asset_seed
from tap.modules.chat.adapters.mysql_conversations import (
    turn_answer_evidence_snapshot,
    turn_input_snapshot,
)
from tap.modules.governance.adapters.schema import project_audit
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_answer_snapshot,
    knowledge_answer_source,
    knowledge_citation_snapshot,
)
from tap.modules.knowledge.adapters.mysql_review import MysqlKnowledgeReviewRepository
from tap.modules.knowledge.application.review import KnowledgeReviewApplication
from tap.modules.test_management.adapters.deterministic_generation import (
    DeterministicTestDesign,
)
from tap.modules.test_management.adapters.mysql import (
    MysqlTestPlanRepository,
    test_plan_revision,
)
from tap.modules.test_management.application.publish import PublishTestPlan
from tap.modules.test_management.domain.models import (
    BddKeyword,
    CitationOrigin,
    IdentityOrigin,
    ReviewDisposition,
)
from tap.modules.test_management.domain.models import (
    TestCase as PlanCase,
)
from tap.modules.test_management.domain.models import (
    TestPlanCitation as PlanCitation,
)
from tap.modules.test_management.domain.models import (
    TestPlanRevision as PlanRevision,
)
from tap.modules.test_management.domain.models import (
    TestPlanStep as PlanStep,
)
from tap.modules.test_management.domain.models import (
    TestScenario as PlanScenario,
)
from tap.modules.test_management.domain.validation import RevisionConflict
from tap.platform.db.project_scope import scope_values
from tap.platform.db.schema import outbox
from tests.integration.test_knowledge_publication import (
    NOW,
    ReadyProjection,
    approved_review,
    seed_authority,
)
from tests.integration.test_test_plan_repository import (
    TEST_DESIGN_MODEL_MAPPING,
    _repository,
    _request,
    _seed_completed_turn,
    _seed_test_design_authority,
)


def _draft() -> PlanRevision:
    return PlanRevision.create(
        test_plan_id="tp_checkout",
        revision_id="tpr_checkout_v1",
        version=1,
        title="Checkout validation",
        objective="Prove checkout",
        scope_items=("Checkout",),
        prerequisites=("Product exists",),
        risks=("Payment failure",),
        cases=(
            PlanCase(
                "tc_checkout",
                1,
                "Checkout",
                "Buy product",
                True,
                (
                    PlanScenario(
                        "ts_checkout",
                        1,
                        "Approved card",
                        (
                            PlanStep("tps_given", 1, BddKeyword.GIVEN, "a cart exists"),
                            PlanStep("tps_when", 2, BddKeyword.WHEN, "checkout is submitted"),
                            PlanStep(
                                "tps_then",
                                3,
                                BddKeyword.THEN,
                                "an order is created",
                                "Order confirmation exists",
                                True,
                                ("tpc_checkout",),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        citations=(
            PlanCitation(
                "tpc_checkout",
                "source_revision_checkout",
                "document_revision_checkout",
                "chunk_checkout",
                "sha256:" + "a" * 64,
                "Checkout creates an order",
                CitationOrigin.SOURCE,
            ),
        ),
        assumptions=(),
        unknowns=(),
        coverage_gaps=(),
        origin=IdentityOrigin.VALIDATION,
    )


class CitationAuthority:
    async def is_authorized(self, scope, citation):
        return scope == VALIDATION_SCOPE

    async def is_requirement_scope_current(self, scope, revision):
        return scope == VALIDATION_SCOPE

    async def are_knowledge_versions_current(self, scope, revision):
        return scope == VALIDATION_SCOPE


class PausingPublishRepository(MysqlTestPlanRepository):
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.authority_locked = asyncio.Event()
        self.release_publish = asyncio.Event()

    async def _assert_publish_authority(self, session, scope, revision, now):  # type: ignore[no-untyped-def]
        await super()._assert_publish_authority(session, scope, revision, now)
        self.authority_locked.set()
        await self.release_publish.wait()


class PausingGenerationRepository(MysqlTestPlanRepository):
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.authority_locked = asyncio.Event()
        self.release_generation = asyncio.Event()

    async def _assert_generation_authority(  # type: ignore[no-untyped-def]
        self, session, scope, request, now
    ):
        approved = await super()._assert_generation_authority(session, scope, request, now)
        self.authority_locked.set()
        await self.release_generation.wait()
        return approved


async def _seed_governed_draft(sessions):  # type: ignore[no-untyped-def]
    await seed_authority(sessions)
    await MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).seed(
        validation_asset_seed(VALIDATION_SCOPE)
    )
    review_repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
    await review_repository.create_review(approved_review("krv_test_plan_authority"))
    review_application = KnowledgeReviewApplication(review_repository, ReadyProjection())
    publication = await review_application.publish_review(
        "krv_test_plan_authority",
        generation="generation-001",
        idempotency_key="publish-test-plan-authority",
        actor_id="synthetic-reviewer-02",
        expected_version=4,
        now=NOW,
    )
    await _seed_completed_turn(sessions)
    async with sessions() as session, session.begin():
        await session.execute(
            update(turn_input_snapshot)
            .where(turn_input_snapshot.c.turn_id == "turn_checkout")
            .values(
                snapshot={
                    "model_alias": "tapper-chat",
                    "agent_revision_id": "validation-knowledge-agent-v2",
                    "skill_revision_ids": ["validation-citation-skill-v2"],
                    "resolved_resources": [{"revision_id": "rev_mysql_001"}],
                }
            )
        )
        await session.execute(
            update(turn_answer_evidence_snapshot)
            .where(turn_answer_evidence_snapshot.c.turn_id == "turn_checkout")
            .values(
                snapshot={
                    "answer": "Checkout creates an order.",
                    "retrieval_summary": {"trace_id": "trace-authority"},
                    "citations": [{"citation_snapshot_id": "citation-authority"}],
                }
            )
        )
        await session.execute(
            insert(knowledge_answer_snapshot).values(
                **scope_values(VALIDATION_SCOPE),
                trace_id="trace-authority",
                query_hash="sha256:" + "4" * 64,
                selected_revisions_json=[
                    {
                        "document_id": "doc_mysql_publication",
                        "revision_id": "rev_mysql_001",
                    }
                ],
                created_at=NOW.replace(tzinfo=None),
            )
        )
        await session.execute(
            insert(knowledge_answer_source).values(
                **scope_values(VALIDATION_SCOPE),
                trace_id="trace-authority",
                revision_id="rev_mysql_001",
                source_id="src_" + "2" * 32,
                document_id="doc_mysql_publication",
                source_content_hash="sha256:" + "a" * 64,
                ordinal=0,
            )
        )
        await session.execute(
            insert(knowledge_citation_snapshot).values(
                **scope_values(VALIDATION_SCOPE),
                citation_id="citation-authority",
                source_id="src_" + "2" * 32,
                trace_id="trace-authority",
                document_id="doc_mysql_publication",
                revision_id="rev_mysql_001",
                chunk_id="chunk-authority",
                source_content_hash="sha256:" + "a" * 64,
                chunk_content_hash="sha256:" + "b" * 64,
                anchor_json={"inventoryItemId": approved_review().approved_item_ids[0]},
                claim_text="Checkout creates an order.",
                origin="SOURCE",
                created_at=NOW.replace(tzinfo=None),
            )
        )
    repository = _repository(sessions)
    instant = datetime.now(timezone.utc).replace(tzinfo=None)
    job = await repository.request_generation_from_turn(
        VALIDATION_SCOPE,
        conversation_id="conversation_checkout",
        turn_id="turn_checkout",
        objective="Design checkout tests",
        idempotency_key="generate-governed-checkout",
        now=instant,
    )
    claim = (
        await repository.claim_generation_jobs(
            VALIDATION_SCOPE,
            worker_id="governed-test-worker",
            now=instant + timedelta(seconds=1),
            lease_duration=timedelta(seconds=60),
            limit=1,
        )
    )[0]
    context = await repository.generation_context(VALIDATION_SCOPE, claim)
    assert context.answer_evidence_snapshot["authorizedEvidence"] == [
        {
            "citationSnapshotId": "citation-authority",
            "sourceRevisionId": "rev_mysql_001",
            "documentRevisionId": "rev_mysql_001",
            "chunkId": "chunk-authority",
            "contentDigest": "sha256:" + "b" * 64,
            "claimText": "Checkout creates an order.",
            "origin": "SOURCE",
            "anchor": {"inventoryItemId": approved_review().approved_item_ids[0]},
        }
    ]
    generated = await DeterministicTestDesign().generate(context)
    draft = await repository.complete_generation(
        VALIDATION_SCOPE,
        claim,
        generated,
        now=instant + timedelta(seconds=2),
    )
    reviewed = await repository.record_review(
        VALIDATION_SCOPE,
        draft.test_plan_id,
        draft.revision_id,
        disposition=ReviewDisposition.ACCEPTED_UNCHANGED,
        reason="Verified against the frozen approved requirement and evidence.",
        expected_version=draft.row_version,
        idempotency_key="review-governed-checkout",
        now=instant + timedelta(seconds=3),
    )
    assert job.request.requirement_scope is not None
    assert job.request.requirement_scope.scope_id == publication.publication_id
    assert job.request.agent_revision_id == "validation-knowledge-agent-v2"
    assert job.request.skill_revision_ids == ("validation-citation-skill-v2",)
    return repository, reviewed, publication, review_application


@pytest.mark.asyncio
async def test_mysql_publish_is_atomic_immutable_and_emits_closed_event(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        repository, reviewed, _, _ = await _seed_governed_draft(sessions)
        published = await PublishTestPlan(repository, CitationAuthority()).execute(
            VALIDATION_SCOPE,
            reviewed.test_plan_id,
            reviewed.revision_id,
            expected_version=reviewed.row_version,
            idempotency_key="publish-checkout-v1",
        )
        assert published.status.value == "PUBLISHED"
        assert published.validation_digest is not None

        async with sessions() as session:
            row = (
                (
                    await session.execute(
                        select(test_plan_revision).where(
                            test_plan_revision.c.revision_id == reviewed.revision_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            event = (
                (
                    await session.execute(
                        select(outbox).where(
                            outbox.c.message_type == "test-plan.revision.published"
                        )
                    )
                )
                .mappings()
                .one()
            )
            audit = (
                (
                    await session.execute(
                        select(project_audit).where(
                            project_audit.c.resource_id == reviewed.revision_id,
                            project_audit.c.action == "test-plan-revision-published",
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert row["status"] == "PUBLISHED"
        assert event["envelope"]["payload"] == {
            "revisionId": reviewed.revision_id,
            "contentDigest": reviewed.content_digest,
            "validationDigest": published.validation_digest,
        }
        assert audit["action"] == "test-plan-revision-published"
        first_fork = await repository.fork_revision(
            VALIDATION_SCOPE,
            published.test_plan_id,
            published.revision_id,
            expected_version=published.row_version,
            idempotency_key="fork-published-once",
            now=datetime(2026, 9, 13, 12, 4),
        )
        second_fork = await repository.fork_revision(
            VALIDATION_SCOPE,
            published.test_plan_id,
            published.revision_id,
            expected_version=published.row_version,
            idempotency_key="fork-published-twice",
            now=datetime(2026, 9, 13, 12, 5),
        )
        assert (first_fork.version, second_fork.version) == (2, 3)

        conflicting_edits = await asyncio.gather(
            repository.replace_draft(
                VALIDATION_SCOPE,
                replace(first_fork, title="First target").with_recomputed_digest(),
                first_fork.row_version,
                idempotency_key="cross-target-concurrent-key",
                now=datetime(2026, 9, 13, 12, 5, 1),
            ),
            repository.replace_draft(
                VALIDATION_SCOPE,
                replace(second_fork, title="Second target").with_recomputed_digest(),
                second_fork.row_version,
                idempotency_key="cross-target-concurrent-key",
                now=datetime(2026, 9, 13, 12, 5, 2),
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(item, PlanRevision) for item in conflicting_edits) == 1
        assert sum(isinstance(item, RevisionConflict) for item in conflicting_edits) == 1

        concurrent_forks = await asyncio.gather(
            *(
                repository.fork_revision(
                    VALIDATION_SCOPE,
                    published.test_plan_id,
                    published.revision_id,
                    expected_version=published.row_version,
                    idempotency_key="fork-published-concurrent",
                    now=datetime(2026, 9, 13, 12, 6),
                )
                for _ in range(2)
            )
        )
        assert concurrent_forks[0].revision_id == concurrent_forks[1].revision_id
        assert concurrent_forks[0].version == concurrent_forks[1].version == 4

        editable = replace(
            concurrent_forks[0], title="Checkout validation — revised"
        ).with_recomputed_digest()
        concurrent_edits = await asyncio.gather(
            *(
                repository.replace_draft(
                    VALIDATION_SCOPE,
                    editable,
                    concurrent_forks[0].row_version,
                    idempotency_key="edit-fork-concurrent",
                    now=datetime(2026, 9, 13, 12, 7),
                )
                for _ in range(2)
            )
        )
        assert concurrent_edits[0] == concurrent_edits[1]

        concurrent_reviews = await asyncio.gather(
            *(
                repository.record_review(
                    VALIDATION_SCOPE,
                    editable.test_plan_id,
                    editable.revision_id,
                    disposition=ReviewDisposition.ACCEPTED_MODIFIED,
                    reason="Confirmed the revised published-plan copy.",
                    expected_version=concurrent_edits[0].row_version,
                    idempotency_key="review-fork-concurrent",
                    now=datetime(2026, 9, 13, 12, 8),
                )
                for _ in range(2)
            )
        )
        assert concurrent_reviews[0] == concurrent_reviews[1]

        concurrent_published = await asyncio.gather(
            *(
                PublishTestPlan(repository, CitationAuthority()).execute(
                    VALIDATION_SCOPE,
                    editable.test_plan_id,
                    editable.revision_id,
                    expected_version=concurrent_reviews[0].row_version,
                    idempotency_key="publish-fork-concurrent",
                )
                for _ in range(2)
            )
        )
        assert concurrent_published[0] == concurrent_published[1]
        assert concurrent_published[0].status.value == "PUBLISHED"

        next_fork = await repository.fork_revision(
            VALIDATION_SCOPE,
            concurrent_published[0].test_plan_id,
            concurrent_published[0].revision_id,
            expected_version=concurrent_published[0].row_version,
            idempotency_key="fork-second-published-generation",
            now=datetime(2026, 9, 13, 12, 9),
        )
        next_edit = await repository.replace_draft(
            VALIDATION_SCOPE,
            replace(
                next_fork, title="Checkout validation — second revision"
            ).with_recomputed_digest(),
            next_fork.row_version,
            idempotency_key="edit-second-published-generation",
            now=datetime(2026, 9, 13, 12, 10),
        )
        next_review = await repository.record_review(
            VALIDATION_SCOPE,
            next_edit.test_plan_id,
            next_edit.revision_id,
            disposition=ReviewDisposition.ACCEPTED_MODIFIED,
            reason="Confirmed the second successive published-plan revision.",
            expected_version=next_edit.row_version,
            idempotency_key="review-second-published-generation",
            now=datetime(2026, 9, 13, 12, 11),
        )
        next_published = await PublishTestPlan(repository, CitationAuthority()).execute(
            VALIDATION_SCOPE,
            next_review.test_plan_id,
            next_review.revision_id,
            expected_version=next_review.row_version,
            idempotency_key="publish-second-published-generation",
        )
        assert next_published.status.value == "PUBLISHED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_review_disposition_summary_excludes_pending_and_source_change_preserves_history(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        repository, draft, _, _ = await _seed_governed_draft(sessions)
        pending = await repository.record_review(
            VALIDATION_SCOPE,
            draft.test_plan_id,
            draft.revision_id,
            disposition=ReviewDisposition.PENDING,
            reason="Waiting for business confirmation.",
            expected_version=draft.row_version,
            idempotency_key="review-checkout-pending",
            now=datetime(2026, 9, 13, 12, 1),
        )
        accepted = await repository.record_review(
            VALIDATION_SCOPE,
            draft.test_plan_id,
            draft.revision_id,
            disposition=ReviewDisposition.ACCEPTED_UNCHANGED,
            reason="Confirmed against the approved policy.",
            expected_version=pending.row_version,
            idempotency_key="review-checkout-accepted",
            now=datetime(2026, 9, 13, 12, 2),
        )
        summary = await repository.review_summary(VALIDATION_SCOPE)

        assert summary.reviewed_count == 1
        assert summary.unchanged_count == 1
        assert summary.modified_count == 0
        assert summary.rejected_count == 0
        assert summary.unchanged_adoption_rate == 1
        assert summary.total_adoption_rate == 1

        published = await PublishTestPlan(repository, CitationAuthority()).execute(
            VALIDATION_SCOPE,
            draft.test_plan_id,
            draft.revision_id,
            expected_version=accepted.row_version,
            idempotency_key="publish-checkout-reviewed-v1",
        )
        impacted = await repository.mark_source_changed(
            VALIDATION_SCOPE,
            "rev_mysql_001",
            reason="Approved source revision was superseded.",
            idempotency_key="source-change-checkout-v2",
            now=datetime(2026, 9, 13, 12, 3),
        )
        historical = await repository.get_revision(
            VALIDATION_SCOPE, published.test_plan_id, published.revision_id
        )

        assert impacted == (published.revision_id,)
        assert historical.needs_review is True
        assert historical.review_decisions[-1].disposition is ReviewDisposition.ACCEPTED_UNCHANGED
        assert historical.citations[0].source_revision_id == "rev_mysql_001"
        stale_summary = await repository.review_summary(VALIDATION_SCOPE)
        assert stale_summary.reviewed_count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("authority_change", ["replacement", "withdrawal"])
async def test_mysql_governed_publish_rejects_replaced_or_withdrawn_publication(
    owned_project_mysql,
    authority_change: str,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        repository, reviewed, publication, review_application = await _seed_governed_draft(sessions)
        if authority_change == "replacement":
            review_repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
            await review_repository.create_review(approved_review("krv_test_plan_replacement"))
            await KnowledgeReviewApplication(review_repository, ReadyProjection()).publish_review(
                "krv_test_plan_replacement",
                generation="generation-001",
                idempotency_key="publish-test-plan-replacement",
                actor_id="synthetic-reviewer-02",
                expected_version=4,
                now=NOW + timedelta(minutes=4),
            )
        else:
            await review_application.withdraw_publication(
                publication.publication_id,
                idempotency_key="withdraw-test-plan-authority",
                actor_id="synthetic-reviewer-02",
                expected_version=1,
                now=NOW + timedelta(minutes=4),
            )

        impacted = await repository.get_revision(
            VALIDATION_SCOPE, reviewed.test_plan_id, reviewed.revision_id
        )
        assert impacted.needs_review is True
        assert impacted.review_decisions[-1].disposition is ReviewDisposition.ACCEPTED_UNCHANGED

        with pytest.raises(ValueError, match="source changes require review"):
            await PublishTestPlan(repository, CitationAuthority()).execute(
                VALIDATION_SCOPE,
                reviewed.test_plan_id,
                reviewed.revision_id,
                expected_version=reviewed.row_version,
                idempotency_key=f"publish-after-{authority_change}",
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_publish_holds_authority_lock_against_concurrent_withdrawal(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _, reviewed, publication, review_application = await _seed_governed_draft(sessions)
        repository = PausingPublishRepository(
            sessions,
            scope=VALIDATION_SCOPE,
            model_alias="tapper-chat",
            model_mapping=TEST_DESIGN_MODEL_MAPPING,
        )
        publish_task = asyncio.create_task(
            PublishTestPlan(repository, CitationAuthority()).execute(
                VALIDATION_SCOPE,
                reviewed.test_plan_id,
                reviewed.revision_id,
                expected_version=reviewed.row_version,
                idempotency_key="publish-during-withdraw-race",
            )
        )
        await asyncio.wait_for(repository.authority_locked.wait(), timeout=1)
        withdraw_task = asyncio.create_task(
            review_application.withdraw_publication(
                publication.publication_id,
                idempotency_key="withdraw-during-test-plan-publish",
                actor_id="synthetic-reviewer-02",
                expected_version=1,
                now=NOW + timedelta(minutes=4),
            )
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(withdraw_task), timeout=0.1)

        repository.release_publish.set()
        published = await publish_task
        withdrawn = await withdraw_task

        assert published.status.value == "PUBLISHED"
        assert withdrawn.status == "withdrawn"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_publish_and_fork_share_revision_then_plan_lock_order(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _, reviewed, _, _ = await _seed_governed_draft(sessions)
        repository = PausingPublishRepository(
            sessions,
            scope=VALIDATION_SCOPE,
            model_alias="tapper-chat",
            model_mapping=TEST_DESIGN_MODEL_MAPPING,
        )
        publish = asyncio.create_task(
            PublishTestPlan(repository, CitationAuthority()).execute(
                VALIDATION_SCOPE,
                reviewed.test_plan_id,
                reviewed.revision_id,
                expected_version=reviewed.row_version,
                idempotency_key="publish-before-concurrent-fork",
            )
        )
        await asyncio.wait_for(repository.authority_locked.wait(), timeout=5)
        fork = asyncio.create_task(
            repository.fork_revision(
                VALIDATION_SCOPE,
                reviewed.test_plan_id,
                reviewed.revision_id,
                expected_version=reviewed.row_version,
                idempotency_key="fork-during-publish",
                now=datetime(2026, 9, 13, 12, 5),
            )
        )
        await asyncio.sleep(0.1)
        assert not fork.done()
        repository.release_publish.set()

        assert (await publish).status.value == "PUBLISHED"
        with pytest.raises(RevisionConflict, match="version"):
            await asyncio.wait_for(fork, timeout=5)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_generation_insertion_holds_authority_lock_against_withdrawal(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        publication = await _seed_test_design_authority(sessions)
        await _seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        repository = PausingGenerationRepository(
            sessions,
            scope=VALIDATION_SCOPE,
            model_alias="tapper-chat",
            model_mapping=TEST_DESIGN_MODEL_MAPPING,
        )
        request = _request(publication)
        generation_task = asyncio.create_task(
            repository.request_generation(
                VALIDATION_SCOPE,
                request,
                now=NOW + timedelta(minutes=1),
            )
        )
        await asyncio.wait_for(repository.authority_locked.wait(), timeout=1)
        review_application = KnowledgeReviewApplication(
            MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE),
            ReadyProjection(),
        )
        withdrawal_task = asyncio.create_task(
            review_application.withdraw_publication(
                publication.publication_id,
                idempotency_key="withdraw-during-test-plan-generation",
                actor_id="synthetic-reviewer-02",
                expected_version=1,
                now=NOW + timedelta(minutes=2),
            )
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(withdrawal_task), timeout=0.1)

        repository.release_generation.set()
        job = await generation_task
        withdrawn = await withdrawal_task

        assert job.status.value == "PENDING"
        assert withdrawn.status == "withdrawn"
    finally:
        await engine.dispose()
