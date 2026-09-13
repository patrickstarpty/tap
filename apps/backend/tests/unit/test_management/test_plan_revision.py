from dataclasses import replace
from datetime import datetime

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.test_management.application.plans import TestPlans as PlanService
from tap.modules.test_management.domain.models import (
    BddKeyword,
    CitationOrigin,
    IdentityOrigin,
    RevisionStatus,
)
from tap.modules.test_management.domain.models import (
    TestCase as PlanCase,
)
from tap.modules.test_management.domain.models import (
    TestPlanCitation as PlanCitation,
)
from tap.modules.test_management.domain.models import (
    TestPlanGenerationRequest as PlanGenerationRequest,
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
from tap.modules.test_management.domain.validation import validate_publishable


def _revision() -> PlanRevision:
    return PlanRevision.create(
        test_plan_id="tp_checkout",
        revision_id="tpr_checkout_v1",
        version=1,
        title="Checkout validation",
        objective="Prove a customer can complete checkout.",
        scope_items=("Web checkout",),
        prerequisites=("A sellable product exists",),
        risks=("Payment authorization can fail",),
        cases=(
            PlanCase(
                "tc_checkout_happy",
                1,
                "Successful checkout",
                "Complete a card purchase",
                True,
                (
                    PlanScenario(
                        "ts_checkout_happy",
                        1,
                        "Approved payment",
                        (
                            PlanStep(
                                "tps_checkout_given",
                                1,
                                BddKeyword.GIVEN,
                                "a product is in the cart",
                            ),
                            PlanStep(
                                "tps_checkout_when",
                                2,
                                BddKeyword.WHEN,
                                "the customer submits valid card details",
                            ),
                            PlanStep(
                                "tps_checkout_then",
                                3,
                                BddKeyword.THEN,
                                "the order is confirmed",
                                "An immutable order confirmation is shown.",
                                True,
                            ),
                        ),
                    ),
                ),
            ),
        ),
        citations=(
            PlanCitation(
                "tpc_checkout_requirement",
                "source_revision_checkout",
                "document_revision_checkout",
                "chunk_checkout",
                "sha256:" + "a" * 64,
                "Checkout requires an order confirmation.",
                CitationOrigin.SOURCE,
            ),
        ),
        assumptions=(),
        unknowns=(),
        coverage_gaps=(),
        origin=IdentityOrigin.VALIDATION,
    )


def test_revision_has_stable_content_digest_and_forks_published_content() -> None:
    draft = _revision()
    assert draft.status is RevisionStatus.DRAFT
    assert draft.content_digest.startswith("sha256:")
    assert draft.content_digest == _revision().content_digest

    published = replace(draft, status=RevisionStatus.PUBLISHED)
    fork = published.fork("tpr_checkout_v2", 2)

    assert fork.status is RevisionStatus.DRAFT
    assert fork.adopted_from_revision_id == published.revision_id
    assert fork.version == 2
    assert fork.content_digest == published.content_digest


@pytest.mark.parametrize(
    "steps,message",
    [
        (
            (
                PlanStep("step_when", 1, BddKeyword.WHEN, "checkout is submitted"),
                PlanStep("step_then", 2, BddKeyword.THEN, "the order succeeds", "Order exists"),
            ),
            "Given",
        ),
        (
            (
                PlanStep("step_given", 1, BddKeyword.GIVEN, "a cart exists"),
                PlanStep("step_when", 2, BddKeyword.WHEN, "checkout is submitted"),
                PlanStep("step_then", 3, BddKeyword.THEN, "the order succeeds"),
            ),
            "expected result",
        ),
    ],
)
def test_publish_validation_rejects_invalid_bdd_and_missing_expected_result(
    steps: tuple[PlanStep, ...], message: str
) -> None:
    revision = _revision()
    scenario = replace(revision.cases[0].scenarios[0], steps=steps)
    changed = replace(
        revision,
        cases=(replace(revision.cases[0], scenarios=(scenario,)),),
    ).with_recomputed_digest()

    with pytest.raises(ValueError, match=message):
        validate_publishable(changed)


def test_publish_validation_rejects_inferred_graph_as_fact() -> None:
    revision = _revision()
    inferred = replace(
        revision.citations[0],
        citation_id="tpc_inferred",
        origin=CitationOrigin.GRAPH_INFERRED,
    )
    changed = replace(revision, citations=(inferred,)).with_recomputed_digest()

    with pytest.raises(ValueError, match="INFERRED"):
        validate_publishable(changed)


def test_revision_rejects_duplicate_or_unstable_identities() -> None:
    revision = _revision()
    duplicate = replace(revision.cases[0], case_id="tc_checkout_happy", ordinal=2)
    with pytest.raises(ValueError, match="unique"):
        replace(revision, cases=(revision.cases[0], duplicate)).with_recomputed_digest()


def test_generation_request_freezes_both_turn_snapshot_digests() -> None:
    request = PlanGenerationRequest.create(
        project_id="tapper-demo",
        conversation_id="conversation_checkout",
        turn_id="turn_checkout",
        input_snapshot_digest="sha256:" + "1" * 64,
        answer_evidence_snapshot_digest="sha256:" + "2" * 64,
        model_alias="tapper-chat",
        agent_revision_id="validation_knowledge_agent_v1",
        skill_revision_ids=("validation_test_design_skill_v1",),
        objective="Design checkout tests",
        idempotency_key="quality_test_design_checkout",
    )
    replay = PlanGenerationRequest.create(
        project_id="tapper-demo",
        conversation_id="conversation_checkout",
        turn_id="turn_checkout",
        input_snapshot_digest="sha256:" + "1" * 64,
        answer_evidence_snapshot_digest="sha256:" + "2" * 64,
        model_alias="tapper-chat",
        agent_revision_id="validation_knowledge_agent_v1",
        skill_revision_ids=("validation_test_design_skill_v1",),
        objective="Design checkout tests",
        idempotency_key="quality_test_design_checkout",
    )

    assert request == replay
    assert request.request_digest.startswith("sha256:")
    assert request.job_id.startswith("tpj_")
    assert request.revision_id.startswith("tpr_")

    with pytest.raises(ValueError, match="digest"):
        PlanGenerationRequest.create(
            project_id="tapper-demo",
            conversation_id="conversation_checkout",
            turn_id="turn_checkout",
            input_snapshot_digest="missing",
            answer_evidence_snapshot_digest="sha256:" + "2" * 64,
            model_alias="tapper-chat",
            agent_revision_id="validation_knowledge_agent_v1",
            skill_revision_ids=("validation_test_design_skill_v1",),
            objective="Design checkout tests",
            idempotency_key="quality_test_design_checkout",
        )


@pytest.mark.asyncio
async def test_published_revision_is_forked_as_a_new_draft() -> None:
    published = replace(_revision(), status=RevisionStatus.PUBLISHED)

    class Repository:
        async def get_revision(self, scope, test_plan_id, revision_id):
            assert (scope, test_plan_id, revision_id) == (
                VALIDATION_SCOPE,
                published.test_plan_id,
                published.revision_id,
            )
            return published

        async def create_draft(self, scope, revision, *, now):
            assert scope == VALIDATION_SCOPE
            return replace(revision, created_at=now)

        async def publish_revision(self, scope, revision_id, expected_version, validation_digest):
            raise AssertionError("publication is not part of forking")

    created = await PlanService(Repository()).fork_revision(
        VALIDATION_SCOPE,
        published.test_plan_id,
        published.revision_id,
        revision_id="tpr_checkout_v2",
        version=2,
        now=datetime(2026, 9, 13, 13, 0),
    )

    assert created.status is RevisionStatus.DRAFT
    assert created.adopted_from_revision_id == published.revision_id
    assert created.content_digest == published.content_digest
