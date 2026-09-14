#!/usr/bin/env python3
"""Generate the reviewable 50-intent QUALITY-TEST-01 candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tap.modules.ai.domain.models import schema_digest, text_digest
from tap.modules.test_management.adapters.model_gateway_generation import (
    TEST_DESIGN_PROMPT,
    TEST_DESIGN_SCHEMA,
)


DOMAINS = (
    "checkout",
    "refund",
    "claims",
    "underwriting",
    "onboarding",
    "invoice",
    "shipment",
    "subscription",
    "access review",
    "incident",
)


def build() -> dict[str, object]:
    evaluator = Path("scripts/evaluate-quality-test-design.py")
    cases = []
    for index in range(50):
        domain = DOMAINS[index % len(DOMAINS)]
        number = index + 1
        cases.append(
            {
                "caseId": f"business-intent-{number:03d}",
                "intent": f"Verify {domain} rule {number} from approved enterprise knowledge",
                "source": f"For {domain} rule {number}, a valid request must be approved before one immutable result is recorded.",
                "criticalRequirements": [
                    "approval precedes recording",
                    "exactly one immutable result",
                ],
                "observation": {
                    "schemaValid": False,
                    "bddValid": False,
                    "unsupportedFactCount": 1,
                    "criticalCovered": 0,
                    "criticalTotal": 2,
                    "criticalCorrectionRequired": True,
                },
                "reviewerJudgments": [],
            }
        )
    return {
        "schemaVersion": "quality-test-profile-v1",
        "profileId": "QUALITY-TEST-01",
        "dataset": {
            "version": "candidate-v1",
            "reviewStatus": "pending",
            "labelingMethod": "named-review-with-deterministic-adjudication",
        },
        "bindings": {
            "modelAlias": "tapper-chat",
            "actualModel": "pending/real-model-run",
            "agentRevisionId": "validation-test-design-agent-v1",
            "skillRevisionIds": ["validation-test-design-skill-v1"],
            "promptDigest": text_digest(TEST_DESIGN_PROMPT),
            "schemaDigest": schema_digest(TEST_DESIGN_SCHEMA),
            "evaluatorDigest": "sha256:"
            + hashlib.sha256(evaluator.read_bytes()).hexdigest(),
        },
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
