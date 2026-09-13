#!/usr/bin/env python3
"""Evaluate QUALITY-TEST-01 observations with deterministic adjudication."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from tap.modules.ai.domain.models import schema_digest, text_digest
from tap.modules.test_management.adapters.model_gateway_generation import (
    TEST_DESIGN_PROMPT,
    TEST_DESIGN_SCHEMA,
)

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def current_bindings() -> dict[str, str]:
    return {
        "promptDigest": text_digest(TEST_DESIGN_PROMPT),
        "schemaDigest": schema_digest(TEST_DESIGN_SCHEMA),
        "evaluatorDigest": "sha256:"
        + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _array(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _ratio(numerator: int, denominator: int, required: int) -> dict[str, object]:
    return {
        "actual": f"{numerator}/{denominator}",
        "required": "100%" if required == 100 else f">={required}%",
        "passed": denominator > 0 and numerator * 100 >= denominator * required,
    }


def validate_profile(
    profile: object, *, real: bool = False
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = _mapping(profile, "profile")
    if (
        root.get("schemaVersion") != "quality-test-profile-v1"
        or root.get("profileId") != "QUALITY-TEST-01"
    ):
        raise ValueError("unsupported test design quality profile")
    cases = [_mapping(item, "case") for item in _array(root.get("cases"), "cases")]
    if len({item.get("caseId") for item in cases}) != len(cases):
        raise ValueError("case identities must be unique")
    reviewer_names: set[str] = set()
    for case in cases:
        if not isinstance(case.get("intent"), str) or not case["intent"].strip():
            raise ValueError("business intent must be nonblank")
        observation = _mapping(case.get("observation"), "observation")
        for name in ("schemaValid", "bddValid", "criticalCorrectionRequired"):
            if type(observation.get(name)) is not bool:
                raise ValueError(f"{name} must be a literal boolean")
        for name in ("unsupportedFactCount", "criticalCovered", "criticalTotal"):
            if type(observation.get(name)) is not int or observation[name] < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        judgments = [
            _mapping(item, "reviewer judgment")
            for item in _array(case.get("reviewerJudgments"), "reviewerJudgments")
        ]
        names = []
        for judgment in judgments:
            reviewer = judgment.get("reviewer")
            if not isinstance(reviewer, str) or not reviewer.strip():
                raise ValueError("reviewer must be a name")
            if (
                reviewer.startswith("human:")
                or reviewer.startswith("machine-")
                or reviewer.startswith("pending-")
            ):
                raise ValueError("reviewer must be an approved name without a prefix")
            names.append(reviewer)
            reviewer_names.add(reviewer)
            if type(judgment.get("approved")) is not bool:
                raise ValueError("reviewer approval must be a literal boolean")
        if len(names) != len(set(names)):
            raise ValueError("reviewers must be distinct within each case")
        if real and (
            not names or not all(bool(item["approved"]) for item in judgments)
        ):
            raise ValueError(
                "real Test Design gate requires one approved named reviewer per case"
            )
    if real:
        dataset = _mapping(root.get("dataset"), "dataset")
        bindings = _mapping(root.get("bindings"), "bindings")
        if (
            len(cases) < 50
            or not reviewer_names
            or dataset.get("reviewStatus") != "approved"
        ):
            raise ValueError(
                "real Test Design gate requires 50 cases and one approved named reviewer"
            )
        actual_model = bindings.get("actualModel")
        if (
            not isinstance(actual_model, str)
            or actual_model.startswith("fake/")
            or actual_model.startswith("pending/")
        ):
            raise ValueError("real Test Design gate requires a real model observation")
        for name, digest in current_bindings().items():
            if (
                _DIGEST.fullmatch(str(bindings.get(name))) is None
                or bindings[name] != digest
            ):
                raise ValueError(f"real Test Design gate requires current {name}")
    return root, cases


def evaluate(profile: object, *, real: bool = False) -> dict[str, object]:
    root, cases = validate_profile(profile, real=real)
    observations = [item["observation"] for item in cases]
    covered = sum(int(item["criticalCovered"]) for item in observations)
    total = sum(int(item["criticalTotal"]) for item in observations)
    without_correction = sum(
        not bool(item["criticalCorrectionRequired"]) for item in observations
    )
    metrics = {
        "minimumBusinessIntents": {
            "actual": len(cases),
            "required": ">=50",
            "passed": len(cases) >= 50,
        },
        "schemaAndBdd": _ratio(
            sum(
                bool(item["schemaValid"] and item["bddValid"]) for item in observations
            ),
            len(cases),
            100,
        ),
        "unsupportedSourceFacts": {
            "actual": sum(int(item["unsupportedFactCount"]) for item in observations),
            "required": "0",
            "passed": sum(int(item["unsupportedFactCount"]) for item in observations)
            == 0,
        },
        "criticalRequirementCoverage": _ratio(covered, total, 90),
        "withoutCriticalCorrection": _ratio(without_correction, len(cases), 80),
    }
    material = json.dumps(
        root, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return {
        "profileId": "QUALITY-TEST-01",
        "datasetDigest": "sha256:" + hashlib.sha256(material).hexdigest(),
        "bindings": root.get("bindings"),
        "reviewers": sorted(
            {
                judgment["reviewer"]
                for case in cases
                for judgment in case["reviewerJudgments"]
            }
        ),
        "metrics": metrics,
        "passed": all(bool(item["passed"]) for item in metrics.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--real", action="store_true")
    arguments = parser.parse_args()
    profile = json.loads(arguments.profile.read_text(encoding="utf-8"))
    report = evaluate(profile, real=arguments.real)
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
