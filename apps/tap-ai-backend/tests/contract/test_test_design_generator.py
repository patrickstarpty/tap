from copy import deepcopy

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.schema import check_schema
from tap.modules.ai.domain.models import ModelCallAudit, ModelOperation, ModelResult, ModelUsage
from tap.modules.test_management.adapters.model_gateway_generation import (
    TEST_DESIGN_SCHEMA,
    ModelGatewayTestDesign,
    design_model_revision_id,
)
from tap.modules.test_management.domain.models import (
    RequirementScopeItem,
    RequirementScopeSnapshot,
)
from tap.modules.test_management.domain.models import (
    TestPlanGenerationRequest as PlanGenerationRequest,
)
from tap.modules.test_management.ports.generation import TestDesignContext as DesignContext


class Gateway:
    def __init__(self, output):
        self.output = output
        self.requests = []

    async def generate_structured(self, request):
        self.requests.append(request)
        return ModelResult(
            output=self.output,
            actual_model="provider/model",
            actual_provider="provider",
            usage=ModelUsage(10, 20),
            audit=ModelCallAudit(
                scope=VALIDATION_SCOPE,
                alias=request.alias,
                operation=ModelOperation.STRUCTURED,
                prompt_digest=request.prompt_digest,
                schema_digest=request.schema_digest,
                context_digest="sha256:" + "c" * 64,
                idempotency_key=request.idempotency_key,
                actual_provider="provider",
                actual_model="provider/model",
                usage=ModelUsage(10, 20),
            ),
        )


def test_model_revision_binds_the_litellm_model_name() -> None:
    first = design_model_revision_id("qwen-plus")
    changed = design_model_revision_id("qwen-max")

    assert first.startswith("tmr_")
    assert first != changed


def _context() -> DesignContext:
    requirement_scope = RequirementScopeSnapshot.create(
        scope_id="checkout_scope_v1",
        version=1,
        requirements=tuple(
            RequirementScopeItem(
                f"requirement_{index:02d}",
                f"source_revision_{index:02d}",
                f"section:{index:02d}",
            )
            for index in range(1, 11)
        ),
    )
    request = PlanGenerationRequest.create(
        project_id=VALIDATION_SCOPE.project_id,
        conversation_id="conversation_checkout",
        turn_id="turn_checkout",
        input_snapshot_digest="sha256:" + "1" * 64,
        answer_evidence_snapshot_digest="sha256:" + "2" * 64,
        model_alias="qwen-plus",
        agent_revision_id="validation-test-design-agent-v1",
        skill_revision_ids=("validation-test-design-skill-v1",),
        objective="Design checkout tests",
        idempotency_key="test-design-checkout",
        requirement_scope=requirement_scope,
        approved_knowledge_revision_ids=("source_revision_checkout",),
        model_revision_id="qwen-plus-2026-09",
    )
    return DesignContext(
        VALIDATION_SCOPE,
        request,
        {
            "message": "Design checkout tests",
            "agent_revision_digest": "sha256:" + "d" * 64,
            "skill_revision_digests": ["sha256:" + "e" * 64],
        },
        {
            "citations": [
                {
                    "citationSnapshotId": "citation_checkout",
                    "sourceRevisionId": "source_revision_checkout",
                    "documentRevisionId": "document_revision_checkout",
                    "chunkId": "chunk_checkout",
                    "contentDigest": "sha256:" + "a" * 64,
                    "claimText": "Checkout creates an order",
                    "origin": "SOURCE",
                }
            ]
        },
    )


def _output() -> dict[str, object]:
    return {
        "title": "Checkout",
        "objective": "Verify checkout",
        "scope": ["Web checkout"],
        "prerequisites": ["A product exists"],
        "risks": ["Payment can fail"],
        "cases": [
            {
                "id": "case_checkout",
                "title": "Approved payment",
                "objective": "Complete checkout",
                "critical": True,
                "coveredRequirementIds": [
                    "requirement_01",
                    "requirement_02",
                    "requirement_03",
                    "requirement_04",
                    "requirement_05",
                    "requirement_06",
                ],
                "scenarios": [
                    {
                        "id": "scenario_checkout",
                        "title": "Approved card",
                        "steps": [
                            {
                                "id": "step_given",
                                "keyword": "Given",
                                "text": "a product is in the cart",
                                "expectedResult": "",
                                "critical": False,
                                "citationIds": [],
                                "unknownIds": [],
                            },
                            {
                                "id": "step_when",
                                "keyword": "When",
                                "text": "checkout is submitted",
                                "expectedResult": "",
                                "critical": False,
                                "citationIds": [],
                                "unknownIds": [],
                            },
                            {
                                "id": "step_then",
                                "keyword": "Then",
                                "text": "the order is confirmed",
                                "expectedResult": "An order confirmation is visible",
                                "critical": True,
                                "citationIds": ["citation_checkout"],
                                "unknownIds": [],
                            },
                        ],
                    }
                ],
            }
        ],
        "citations": [
            {
                "citationSnapshotId": "citation_checkout",
            }
        ],
        "assumptions": [],
        "unknowns": [],
        "coverageGaps": [
            {
                "id": f"gap_{index:02d}",
                "requirementRef": f"requirement_{index:02d}",
                "reason": "The retrieved evidence did not cover this requirement.",
                "severity": "HIGH",
            }
            for index in range(7, 11)
        ],
    }


@pytest.mark.asyncio
async def test_test_design_generation_is_schema_locked_grounded_and_draft_only() -> None:
    check_schema(TEST_DESIGN_SCHEMA)
    gateway = Gateway(_output())

    draft = await ModelGatewayTestDesign(gateway).generate(_context())

    assert draft.status.value == "DRAFT"
    assert draft.citations[0].chunk_id == "chunk_checkout"
    assert draft.coverage_denominator == 10
    assert draft.covered_requirement_count == 6
    assert gateway.requests[0].operation is ModelOperation.STRUCTURED
    assert gateway.requests[0].schema_digest.startswith("sha256:")


@pytest.mark.asyncio
async def test_unknown_requirement_cannot_be_emitted_as_definite_covered_behavior() -> None:
    output = _output()
    output["unknowns"] = [
        {
            "id": "unknown_confirmation_rule",
            "text": "The confirmation rule is unresolved.",
            "requirementRef": "requirement_01",
        }
    ]
    # The model deliberately omits unknownIds from the Then step; the server-owned
    # requirement binding must still reject this contradiction.
    gateway = Gateway(output)

    with pytest.raises(ValueError, match="unknown requirements"):
        await ModelGatewayTestDesign(gateway).generate(_context())


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["status", "citation", "bdd"])
async def test_malformed_ungrounded_or_privileged_model_output_fails_closed(mutation) -> None:
    output = deepcopy(_output())
    if mutation == "status":
        output["status"] = "PUBLISHED"
    elif mutation == "citation":
        output["citations"][0]["citationSnapshotId"] = "citation_other"  # type: ignore[index]
    else:
        output["cases"][0]["scenarios"][0]["steps"][0]["keyword"] = "When"  # type: ignore[index]

    with pytest.raises(ValueError):
        await ModelGatewayTestDesign(Gateway(output)).generate(_context())


@pytest.mark.asyncio
async def test_citation_fields_cannot_be_composed_from_different_evidence_rows() -> None:
    context = _context()
    evidence = [
        *context.answer_evidence_snapshot["citations"],  # type: ignore[misc]
        {
            "citationSnapshotId": "citation_other",
            "sourceRevisionId": "source_revision_other",
            "documentRevisionId": "document_revision_other",
            "chunkId": "chunk_other",
            "contentDigest": "sha256:" + "b" * 64,
            "claimText": "A different authorized claim",
            "origin": "SOURCE",
        },
    ]
    composite = deepcopy(_output())
    composite["citations"][0]["citationSnapshotId"] = "citation_other"  # type: ignore[index]
    composite["cases"][0]["scenarios"][0]["steps"][2]["citationIds"] = [  # type: ignore[index]
        "citation_other"
    ]
    mixed_context = DesignContext(
        context.scope,
        context.request,
        context.input_snapshot,
        {"citations": evidence},
    )

    draft = await ModelGatewayTestDesign(Gateway(composite)).generate(mixed_context)

    assert draft.citations[0].source_revision_id == "source_revision_other"
    assert draft.citations[0].chunk_id == "chunk_other"
    assert draft.citations[0].claim_text == "A different authorized claim"


@pytest.mark.asyncio
async def test_model_cannot_submit_mutable_citation_claim_fields() -> None:
    composite = deepcopy(_output())
    composite["citations"][0]["claimText"] = "A claim not present in that evidence row"  # type: ignore[index]

    with pytest.raises(ValueError, match="malformed"):
        await ModelGatewayTestDesign(Gateway(composite)).generate(_context())


@pytest.mark.asyncio
async def test_unknown_business_condition_cannot_be_emitted_as_a_definite_then() -> None:
    output = deepcopy(_output())
    output["unknowns"] = [
        {
            "id": "unknown_retry_policy",
            "text": "Whether retry is permitted",
            "requirementRef": "requirement_01",
        }
    ]
    output["cases"][0]["scenarios"][0]["steps"][2]["unknownIds"] = [  # type: ignore[index]
        "unknown_retry_policy"
    ]

    with pytest.raises(ValueError, match="unknown"):
        await ModelGatewayTestDesign(Gateway(output)).generate(_context())
