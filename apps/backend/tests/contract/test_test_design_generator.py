from copy import deepcopy

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.schema import check_schema
from tap.modules.ai.domain.models import ModelCallAudit, ModelOperation, ModelResult, ModelUsage
from tap.modules.test_management.adapters.model_gateway_generation import (
    TEST_DESIGN_SCHEMA,
    ModelGatewayTestDesign,
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


def _context() -> DesignContext:
    request = PlanGenerationRequest.create(
        project_id=VALIDATION_SCOPE.project_id,
        conversation_id="conversation_checkout",
        turn_id="turn_checkout",
        input_snapshot_digest="sha256:" + "1" * 64,
        answer_evidence_snapshot_digest="sha256:" + "2" * 64,
        model_alias="tapper-chat",
        agent_revision_id="validation-test-design-agent-v1",
        skill_revision_ids=("validation-test-design-skill-v1",),
        objective="Design checkout tests",
        idempotency_key="test-design-checkout",
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
                    "sourceRevisionId": "source_revision_checkout",
                    "documentRevisionId": "document_revision_checkout",
                    "chunkId": "chunk_checkout",
                    "contentDigest": "sha256:" + "a" * 64,
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
                            },
                            {
                                "id": "step_when",
                                "keyword": "When",
                                "text": "checkout is submitted",
                                "expectedResult": "",
                                "critical": False,
                            },
                            {
                                "id": "step_then",
                                "keyword": "Then",
                                "text": "the order is confirmed",
                                "expectedResult": "An order confirmation is visible",
                                "critical": True,
                            },
                        ],
                    }
                ],
            }
        ],
        "citations": [
            {
                "id": "citation_checkout",
                "sourceRevisionId": "source_revision_checkout",
                "documentRevisionId": "document_revision_checkout",
                "chunkId": "chunk_checkout",
                "contentDigest": "sha256:" + "a" * 64,
                "claimText": "Checkout creates an order",
                "origin": "SOURCE",
            }
        ],
        "assumptions": [],
        "unknowns": [],
        "coverageGaps": [],
    }


@pytest.mark.asyncio
async def test_test_design_generation_is_schema_locked_grounded_and_draft_only() -> None:
    check_schema(TEST_DESIGN_SCHEMA)
    gateway = Gateway(_output())

    draft = await ModelGatewayTestDesign(gateway).generate(_context())

    assert draft.status.value == "DRAFT"
    assert draft.citations[0].chunk_id == "chunk_checkout"
    assert gateway.requests[0].operation is ModelOperation.STRUCTURED
    assert gateway.requests[0].schema_digest.startswith("sha256:")


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["status", "citation", "bdd"])
async def test_malformed_ungrounded_or_privileged_model_output_fails_closed(mutation) -> None:
    output = deepcopy(_output())
    if mutation == "status":
        output["status"] = "PUBLISHED"
    elif mutation == "citation":
        output["citations"][0]["chunkId"] = "chunk_other"  # type: ignore[index]
    else:
        output["cases"][0]["scenarios"][0]["steps"][0]["keyword"] = "When"  # type: ignore[index]

    with pytest.raises(ValueError):
        await ModelGatewayTestDesign(Gateway(output)).generate(_context())
