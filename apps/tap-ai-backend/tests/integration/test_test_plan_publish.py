from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.governance.adapters.schema import project_audit
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
from tap.platform.db.schema import outbox


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
        return scope == VALIDATION_SCOPE and citation.citation_id == "tpc_checkout"

    async def is_requirement_scope_current(self, scope, revision):
        return scope == VALIDATION_SCOPE

    async def are_knowledge_versions_current(self, scope, revision):
        return scope == VALIDATION_SCOPE


@pytest.mark.asyncio
async def test_mysql_publish_is_atomic_immutable_and_emits_closed_event(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        repository = MysqlTestPlanRepository(sessions, scope=VALIDATION_SCOPE)
        draft = await repository.create_draft(
            VALIDATION_SCOPE, _draft(), now=datetime(2026, 9, 13, 12, 0)
        )
        assert (
            await repository.create_draft(
                VALIDATION_SCOPE, _draft(), now=datetime(2026, 9, 13, 12, 1)
            )
            == draft
        )

        reviewed = await repository.record_review(
            VALIDATION_SCOPE,
            draft.test_plan_id,
            draft.revision_id,
            disposition=ReviewDisposition.ACCEPTED_UNCHANGED,
            reason="Verified against the approved checkout requirement.",
            expected_version=1,
            idempotency_key="review-checkout-v1",
            now=datetime(2026, 9, 13, 12, 2),
        )
        published = await PublishTestPlan(repository, CitationAuthority()).execute(
            VALIDATION_SCOPE,
            draft.test_plan_id,
            draft.revision_id,
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
                            test_plan_revision.c.revision_id == draft.revision_id
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
                            project_audit.c.resource_id == draft.revision_id
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert row["status"] == "PUBLISHED"
        assert event["envelope"]["payload"] == {
            "revisionId": draft.revision_id,
            "contentDigest": draft.content_digest,
            "validationDigest": published.validation_digest,
        }
        assert audit["action"] == "test-plan-revision-published"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_review_disposition_summary_excludes_pending_and_source_change_preserves_history(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        repository = MysqlTestPlanRepository(sessions, scope=VALIDATION_SCOPE)
        draft = await repository.create_draft(
            VALIDATION_SCOPE, _draft(), now=datetime(2026, 9, 13, 12, 0)
        )
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
            "source_revision_checkout",
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
        assert historical.citations[0].source_revision_id == "source_revision_checkout"
    finally:
        await engine.dispose()
