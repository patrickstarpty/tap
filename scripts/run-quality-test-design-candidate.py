#!/usr/bin/env python3
"""Run the QUALITY-TEST-01 candidate through the real structured model path."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from tap.entrypoints.tapper_runtime import TapperSettings, _create_embeddings
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.ports.gateway import ModelGateway
from tap.modules.test_management.adapters.model_gateway_generation import (
    ModelGatewayTestDesign,
)
from tap.modules.test_management.domain.models import TestPlanGenerationRequest
from tap.modules.test_management.domain.validation import validate_draft_structure
from tap.modules.test_management.ports.generation import TestDesignContext


class CapturingGateway:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.identities: set[tuple[str, str]] = set()
        self.invocation_count = 0
        self.receipts: dict[str, tuple[str, str, str]] = {}

    async def generate_structured(self, request):  # type: ignore[no-untyped-def]
        self.invocation_count += 1
        result = await self.delegate.generate_structured(request)
        self.identities.add((result.actual_provider, result.actual_model))
        if (
            not isinstance(result.provider_request_id, str)
            or not result.provider_request_id
        ):
            raise ValueError("real model result requires a provider request id")
        self.receipts[request.idempotency_key] = (
            result.actual_provider,
            result.actual_model,
            result.provider_request_id,
        )
        return result


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _candidate_digest(
    item: dict[str, Any], *, request_digest: str, output_digest: str
) -> str:
    material = json.dumps(
        {
            "caseId": item["caseId"],
            "intent": item["intent"],
            "source": item["source"],
            "criticalRequirements": item["criticalRequirements"],
            "requestDigest": request_digest,
            "outputDigest": output_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return _digest(material)


async def run(profile: dict[str, Any]) -> dict[str, Any]:
    settings = TapperSettings.from_mapping(dict(os.environ))
    models = _create_embeddings(settings, max_retries=1)
    capture = CapturingGateway(models.gateway)
    generator = ModelGatewayTestDesign(
        cast(ModelGateway, capture),
        timeout_seconds=min(60.0, settings.model_timeout_seconds),
    )
    observations = deepcopy(profile)
    bindings = observations["bindings"]
    semaphore = asyncio.Semaphore(4)
    review_invalidated = False

    async def evaluate_case(index: int, item: dict[str, Any]) -> None:
        nonlocal review_invalidated
        source = str(item["source"])
        suffix = f"{index + 1:03d}"
        request = TestPlanGenerationRequest.create(
            project_id=VALIDATION_SCOPE.project_id,
            conversation_id=f"quality_conversation_{suffix}",
            turn_id=f"quality_turn_{suffix}",
            input_snapshot_digest=_digest(str(item["intent"])),
            answer_evidence_snapshot_digest=_digest(source),
            model_alias=str(bindings["modelAlias"]),
            agent_revision_id=str(bindings["agentRevisionId"]),
            skill_revision_ids=tuple(
                str(item) for item in bindings["skillRevisionIds"]
            ),
            objective=str(item["intent"]),
            idempotency_key=f"quality_test_design_{suffix}",
        )
        context = TestDesignContext(
            VALIDATION_SCOPE,
            request,
            {
                "message": item["intent"],
                "agent_revision_digest": _digest("validation-test-design-agent-v1"),
                "skill_revision_digests": [_digest("validation-test-design-skill-v1")],
            },
            {
                "authorizedEvidence": [
                    {
                        "sourceRevisionId": f"source_revision_{suffix}",
                        "documentRevisionId": f"document_revision_{suffix}",
                        "chunkId": f"chunk_{suffix}",
                        "contentDigest": _digest(source),
                        "content": source,
                    }
                ],
            },
        )
        observation = item["observation"]
        try:
            async with semaphore:
                revision = await generator.generate(context)
            validate_draft_structure(revision)
            output = revision.canonical_content()
            output_digest = revision.content_digest
            provider, model, provider_request_id = capture.receipts[
                request.idempotency_key
            ]
            candidate_digest = _candidate_digest(
                item,
                request_digest=request.request_digest,
                output_digest=output_digest,
            )
            review_is_current = (
                observation.get("reviewedOutputDigest") == output_digest
                and observation.get("reviewedCaseDigest") == candidate_digest
                and bool(item.get("reviewerJudgments"))
                and all(
                    judgment.get("approved") is True
                    for judgment in item["reviewerJudgments"]
                    if isinstance(judgment, dict)
                )
            )
            observation.update(
                schemaValid=True,
                bddValid=True,
                criticalTotal=len(item["criticalRequirements"]),
                outputDigest=output_digest,
                generatedOutput=output,
                candidateDigest=candidate_digest,
                providerReceipt={
                    "requestDigest": request.request_digest,
                    "outputDigest": output_digest,
                    "provider": provider,
                    "model": model,
                    "providerRequestId": provider_request_id,
                },
            )
            if not review_is_current:
                review_invalidated = True
                observation.update(
                    unsupportedFactCount=1,
                    criticalCovered=0,
                    criticalCorrectionRequired=True,
                )
                observation.pop("reviewedOutputDigest", None)
                observation.pop("reviewedCaseDigest", None)
                item["reviewerJudgments"] = []
            observation.pop("failureType", None)
            observation.pop("failureMessage", None)
        except Exception as error:
            observation.update(
                schemaValid=False,
                bddValid=False,
                unsupportedFactCount=0,
                criticalCovered=0,
                criticalTotal=len(item["criticalRequirements"]),
                criticalCorrectionRequired=True,
                failureType=type(error).__name__,
                failureMessage=str(error),
            )

    try:
        await asyncio.gather(
            *(
                evaluate_case(index, item)
                for index, item in enumerate(observations["cases"])
            )
        )
        if capture.invocation_count != len(observations["cases"]):
            raise ValueError("every quality case must invoke the real model")
        if len(capture.identities) > 1:
            raise ValueError("real model identity changed during candidate run")
        if capture.identities:
            observations["bindings"]["actualModel"] = "/".join(
                next(iter(capture.identities))
            )
        observations["dataset"]["modelObservationStatus"] = "captured"
        observations["dataset"]["providerInvocationCount"] = capture.invocation_count
        if review_invalidated:
            observations["dataset"]["reviewStatus"] = "pending"
        return observations
    finally:
        await models.aclose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", type=Path)
    parser.add_argument("--observations", type=Path, required=True)
    arguments = parser.parse_args()
    source = (
        arguments.observations if arguments.observations.exists() else arguments.profile
    )
    profile = json.loads(source.read_text(encoding="utf-8"))
    observations = asyncio.run(run(profile))
    arguments.observations.parent.mkdir(parents=True, exist_ok=True)
    arguments.observations.write_text(
        json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
