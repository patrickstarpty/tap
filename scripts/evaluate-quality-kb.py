#!/usr/bin/env python3
"""Deterministically evaluate a completed QUALITY-KB-01 profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_THRESHOLDS = {
    "minimumCases": 100,
    "maximumSkippedCases": 0,
    "maximumLeakage": 0,
    "anchorResolution": "100%",
    "groundedClaimCitationPrecision": "100%",
    "retrievalRecallAt10": ">=90%",
    "abstainAccuracy": ">=90%",
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _evaluator_digest() -> str:
    return "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _text(value: object, name: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > maximum:
        raise ValueError(f"{name} must be a bounded non-empty string")
    return value


def _digest_text(value: object, name: str) -> str:
    text = _text(value, name, maximum=71)
    if _DIGEST.fullmatch(text) is None:
        raise ValueError(f"{name} must be a sha256 digest")
    return text


def _model_identity(value: object, name: str) -> str:
    text = _text(value, name, maximum=256)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", text) is None:
        raise ValueError(f"{name} is invalid")
    return text


def _strings(value: object, name: str) -> list[str]:
    values = _sequence(value, name)
    result = [_text(item, name) for item in values]
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return result


def _ratio_threshold(
    *, name: str, numerator: int, denominator: int, required_numerator: int
) -> dict[str, object]:
    passed = denominator == 0 or numerator * 100 >= denominator * required_numerator
    return {
        "actual": f"{numerator}/{denominator}",
        "required": f">={required_numerator}%" if required_numerator < 100 else "100%",
        "passed": passed,
        "metric": name,
    }


def _dataset_identity(profile: dict[str, Any]) -> dict[str, object]:
    cases = _sequence(profile.get("cases"), "cases")
    labels: list[dict[str, object]] = []
    for raw_case in cases:
        case = _mapping(raw_case, "case")
        labels.append(
            {
                "caseId": case.get("caseId"),
                "caseType": case.get("caseType"),
                "question": case.get("question"),
                "projectId": case.get("projectId"),
                "selectedSourceIds": case.get("selectedSourceIds"),
                "expectedRelevantChunkIds": case.get("expectedRelevantChunkIds"),
                "shouldAbstain": case.get("shouldAbstain"),
                "labelProvenance": case.get("labelProvenance"),
            }
        )
    return {
        "schemaVersion": profile.get("schemaVersion"),
        "profileId": profile.get("profileId"),
        "dataset": profile.get("dataset"),
        "labels": sorted(labels, key=lambda item: str(item["caseId"])),
    }


def _evaluate_case(raw_case: object) -> tuple[dict[str, object], dict[str, int]]:
    case = _mapping(raw_case, "case")
    case_id = _text(case.get("caseId"), "caseId", maximum=128)
    case_type = _text(case.get("caseType"), "caseType", maximum=32)
    if case_type not in {"answerable", "conflict", "unauthorized", "abstain"}:
        raise ValueError(
            "caseType must be answerable, conflict, unauthorized, or abstain"
        )
    _text(case.get("question"), "question", maximum=4096)
    project_id = _text(case.get("projectId"), "projectId", maximum=128)
    selected_sources = set(_strings(case.get("selectedSourceIds"), "selectedSourceIds"))
    expected_chunks = set(
        _strings(case.get("expectedRelevantChunkIds"), "expectedRelevantChunkIds")
    )
    should_abstain = case.get("shouldAbstain")
    if type(should_abstain) is not bool:
        raise ValueError("shouldAbstain must be boolean")
    _text(case.get("labelProvenance"), "labelProvenance", maximum=1024)

    observed_value = case.get("observed")
    if observed_value is None:
        return (
            {
                "caseId": case_id,
                "caseType": case_type,
                "status": "skipped",
                "leakage": [],
                "missedRelevantChunkIds": sorted(expected_chunks),
            },
            {
                "skipped": 1,
                "leakage": 0,
                "anchorResolved": 0,
                "anchorTotal": 0,
                "groundedSupported": 0,
                "groundedTotal": 0,
                "retrievalRelevantAt10": 0,
                "retrievalRelevantTotal": len(expected_chunks),
                "abstainCorrect": 0,
                "abstainTotal": int(should_abstain),
            },
        )
    observed = _mapping(observed_value, "observed")
    actual_provider = _model_identity(observed.get("actualProvider"), "actualProvider")
    actual_model = _model_identity(observed.get("actualModel"), "actualModel")
    abstained = observed.get("abstained")
    if type(abstained) is not bool:
        raise ValueError("observed.abstained must be boolean")

    leakage: list[str] = []
    retrieved_at_10: set[str] = set()
    ranks: set[int] = set()
    for raw_hit in _sequence(observed.get("retrieved"), "observed.retrieved"):
        hit = _mapping(raw_hit, "retrieval hit")
        rank = hit.get("rank")
        if type(rank) is not int or not 1 <= rank <= 100 or rank in ranks:
            raise ValueError("retrieval ranks must be unique integers from 1 to 100")
        ranks.add(rank)
        chunk_id = _text(hit.get("chunkId"), "retrieval chunkId", maximum=128)
        source_id = _text(hit.get("sourceId"), "retrieval sourceId", maximum=256)
        hit_project = _text(hit.get("projectId"), "retrieval projectId", maximum=128)
        if rank <= 10:
            retrieved_at_10.add(chunk_id)
        if hit_project != project_id:
            leakage.append(f"retrieval:{chunk_id}:project:{hit_project}")
        if source_id not in selected_sources:
            leakage.append(f"retrieval:{chunk_id}:unselected-source:{source_id}")

    citation_ids: set[str] = set()
    anchor_resolved = 0
    citations = _sequence(observed.get("citations"), "observed.citations")
    for raw_citation in citations:
        citation = _mapping(raw_citation, "citation")
        citation_id = _text(citation.get("citationId"), "citationId", maximum=128)
        if citation_id in citation_ids:
            raise ValueError("citation IDs must be unique")
        citation_ids.add(citation_id)
        source_id = _text(citation.get("sourceId"), "citation sourceId", maximum=256)
        citation_project = _text(
            citation.get("projectId"), "citation projectId", maximum=128
        )
        resolvable = citation.get("anchorResolvable")
        if type(resolvable) is not bool:
            raise ValueError("anchorResolvable must be boolean")
        anchor_resolved += int(resolvable)
        if citation_project != project_id:
            leakage.append(f"citation:{citation_id}:project:{citation_project}")
        if source_id not in selected_sources:
            leakage.append(f"citation:{citation_id}:unselected-source:{source_id}")

    grounded_supported = 0
    grounded_total = 0
    claim_ids: set[str] = set()
    claims = _sequence(observed.get("claims"), "observed.claims")
    for raw_claim in claims:
        claim = _mapping(raw_claim, "claim")
        claim_id = _text(claim.get("claimId"), "claimId", maximum=128)
        if claim_id in claim_ids:
            raise ValueError("claim IDs must be unique")
        claim_ids.add(claim_id)
        related = _strings(claim.get("citationIds"), "claim citationIds")
        if not related or not set(related) <= citation_ids:
            raise ValueError("each claim must reference returned citations")
        supported = set(
            _strings(
                claim.get("humanSupportedCitationIds"),
                "claim humanSupportedCitationIds",
            )
        )
        if not supported <= set(related):
            raise ValueError("human-supported citations must be claim citations")
        grounded_total += len(related)
        grounded_supported += len(supported)

    missed = sorted(expected_chunks - retrieved_at_10)
    response_shape_valid = (
        abstained and not claims
        if should_abstain
        else not abstained and bool(claims) and bool(citations)
    )
    case_evidence: dict[str, object] = {
        "caseId": case_id,
        "caseType": case_type,
        "status": "evaluated",
        "leakage": sorted(leakage),
        "missedRelevantChunkIds": missed,
        "anchorResolution": f"{anchor_resolved}/{len(citations)}",
        "groundedClaimCitationSupport": f"{grounded_supported}/{grounded_total}",
        "retrievalRecallAt10": f"{len(expected_chunks) - len(missed)}/{len(expected_chunks)}",
        "expectedAbstain": should_abstain,
        "observedAbstain": abstained,
        "responseShapeValid": response_shape_valid,
        "actualProvider": actual_provider,
        "actualModel": actual_model,
    }
    counts = {
        "skipped": 0,
        "leakage": len(leakage),
        "anchorResolved": anchor_resolved,
        "anchorTotal": len(citations),
        "groundedSupported": grounded_supported,
        "groundedTotal": grounded_total,
        "retrievalRelevantAt10": len(expected_chunks) - len(missed),
        "retrievalRelevantTotal": len(expected_chunks),
        "abstainCorrect": int(bool(should_abstain and abstained)),
        "abstainTotal": int(should_abstain),
    }
    return case_evidence, counts


def evaluate_profile(
    profile_value: object,
    *,
    min_cases: int = 100,
    require_real: bool = False,
    approved_mapping: tuple[str, str, str] | None = None,
) -> dict[str, object]:
    """Return canonical evidence; malformed profiles raise ValueError."""
    if type(min_cases) is not int or min_cases < 1:
        raise ValueError("min_cases must be a positive integer")
    profile = _mapping(profile_value, "profile")
    if profile.get("schemaVersion") != "quality-kb-profile-v1":
        raise ValueError("unsupported quality profile schema")
    if profile.get("profileId") != "QUALITY-KB-01":
        raise ValueError("unsupported quality profile ID")
    dataset = _mapping(profile.get("dataset"), "dataset")
    dataset_version = _text(dataset.get("version"), "dataset version", maximum=128)
    labeling_method = dataset.get("labelingMethod")
    provenance = dataset.get("provenance")
    if provenance is not None:
        _text(provenance, "dataset provenance", maximum=1024)
    bindings = _mapping(profile.get("bindings"), "bindings")
    binding_report: dict[str, object] = {
        "policyDigest": bindings.get("policyDigest"),
        "modelAlias": bindings.get("modelAlias"),
        "actualProvider": bindings.get("actualProvider"),
        "actualModel": bindings.get("actualModel"),
        "actualModelApproval": bindings.get("actualModelApproval"),
        "actualModelApprovalDigest": bindings.get("actualModelApprovalDigest"),
        "promptDigest": bindings.get("promptDigest"),
        "agentRevisionDigest": bindings.get("agentRevisionDigest"),
        "skillRevisionDigests": bindings.get("skillRevisionDigests"),
        "schemaDigest": bindings.get("schemaDigest"),
    }

    failures: list[str] = []
    if labeling_method != "human":
        failures.append("dataset must be human-labeled")
    if not isinstance(provenance, str) or not provenance.strip():
        failures.append("dataset human-label provenance is missing")

    cases = _sequence(profile.get("cases"), "cases")
    evidence: list[dict[str, object]] = []
    totals = {
        "skipped": 0,
        "leakage": 0,
        "anchorResolved": 0,
        "anchorTotal": 0,
        "groundedSupported": 0,
        "groundedTotal": 0,
        "retrievalRelevantAt10": 0,
        "retrievalRelevantTotal": 0,
        "abstainCorrect": 0,
        "abstainTotal": 0,
    }
    case_ids: set[str] = set()
    case_types: set[str] = set()
    for raw_case in cases:
        case_evidence, counts = _evaluate_case(raw_case)
        case_id = str(case_evidence["caseId"])
        if case_id in case_ids:
            raise ValueError("case IDs must be unique")
        case_ids.add(case_id)
        case_types.add(str(case_evidence["caseType"]))
        evidence.append(case_evidence)
        if (
            case_evidence["status"] == "evaluated"
            and not case_evidence["responseShapeValid"]
        ):
            expected = (
                "an abstention"
                if case_evidence["expectedAbstain"]
                else "a grounded answer"
            )
            failures.append(f"case {case_id} did not produce {expected}")
        for key in totals:
            totals[key] += counts[key]

    execution = _mapping(profile.get("execution"), "execution")
    execution_report: dict[str, int] = {}
    for name in (
        "providerCallBudget",
        "providerCalls",
        "cacheHits",
        "retryCount",
        "maxRetriesPerCase",
    ):
        value = execution.get(name)
        if type(value) is not int or value < 0:
            raise ValueError(f"execution.{name} must be a non-negative integer")
        execution_report[name] = value
    if execution_report["maxRetriesPerCase"] > 2:
        failures.append("max retries per case exceeds bounded limit 2")
    if execution_report["retryCount"] > execution_report["providerCalls"]:
        failures.append("retry count exceeds provider call count")
    if execution_report["providerCalls"] > execution_report["providerCallBudget"]:
        failures.append("provider call count exceeds bounded budget")

    if len(cases) < min_cases:
        failures.append(f"actual case count {len(cases)} is below required {min_cases}")
    required_case_types = {"answerable", "conflict", "unauthorized", "abstain"}
    if min_cases >= 100 and not required_case_types <= case_types:
        failures.append(
            "dataset is missing representative case types: "
            + ", ".join(sorted(required_case_types - case_types))
        )
    if totals["skipped"]:
        failures.append(f"skipped case count {totals['skipped']} exceeds required 0")
    if totals["leakage"]:
        failures.append(f"leakage count {totals['leakage']} exceeds required 0")

    sample_threshold = {
        "actual": len(cases),
        "required": min_cases,
        "passed": len(cases) >= min_cases,
    }
    skip_threshold = {
        "actual": totals["skipped"],
        "required": 0,
        "passed": totals["skipped"] == 0,
    }
    leakage_threshold = {
        "actual": totals["leakage"],
        "required": 0,
        "passed": totals["leakage"] == 0,
    }
    anchor_threshold = _ratio_threshold(
        name="anchorResolution",
        numerator=totals["anchorResolved"],
        denominator=totals["anchorTotal"],
        required_numerator=100,
    )
    grounded_threshold = _ratio_threshold(
        name="groundedClaimCitationPrecision",
        numerator=totals["groundedSupported"],
        denominator=totals["groundedTotal"],
        required_numerator=100,
    )
    retrieval_threshold = _ratio_threshold(
        name="retrievalRecallAt10",
        numerator=totals["retrievalRelevantAt10"],
        denominator=totals["retrievalRelevantTotal"],
        required_numerator=90,
    )
    abstain_threshold = _ratio_threshold(
        name="abstainAccuracy",
        numerator=totals["abstainCorrect"],
        denominator=totals["abstainTotal"],
        required_numerator=90,
    )
    for item in (
        anchor_threshold,
        grounded_threshold,
        retrieval_threshold,
        abstain_threshold,
    ):
        if not item["passed"]:
            failures.append(f"{item['metric']} threshold failed: {item['actual']}")

    alias = bindings.get("modelAlias")
    provider = bindings.get("actualProvider")
    model = bindings.get("actualModel")
    try:
        safe_provider = _model_identity(provider, "actualProvider")
        safe_model = _model_identity(model, "actualModel")
    except ValueError:
        safe_provider = None
        safe_model = None
        failures.append("actual provider/model binding is missing")
    if isinstance(alias, str) and safe_model == alias:
        failures.append("actual model cannot equal logical alias")
    if bindings.get("actualModelApproval") != "approved":
        failures.append("actual model is not approved")
    try:
        _digest_text(
            bindings.get("actualModelApprovalDigest"), "actualModelApprovalDigest"
        )
    except ValueError:
        failures.append("actual model approval digest is missing")
    external_approval_matched: bool | None = None
    if require_real and approved_mapping is None:
        failures.append("external approved model mapping is required")
    if approved_mapping is not None:
        approved_provider, approved_model, approval_digest = approved_mapping
        _model_identity(approved_provider, "approved provider")
        _model_identity(approved_model, "approved model")
        _digest_text(approval_digest, "approved model mapping digest")
        external_approval_matched = (
            safe_provider == approved_provider
            and safe_model == approved_model
            and bindings.get("actualModelApprovalDigest") == approval_digest
        )
        if not external_approval_matched:
            failures.append(
                "profile model identity does not match the external approved mapping"
            )
    if execution_report["providerCallBudget"] < 1:
        failures.append("real execution provider call budget is missing")
    if execution_report["providerCalls"] + execution_report["cacheHits"] < len(cases):
        failures.append(
            "real execution has fewer calls/cache hits than evaluated cases"
        )
    for item in evidence:
        if item["status"] == "skipped":
            continue
        matches = (
            item.get("actualProvider") == safe_provider
            and item.get("actualModel") == safe_model
        )
        item["modelIdentityMatchesBinding"] = matches
        if not matches:
            failures.append(
                f"case {item['caseId']} actual response model identity mismatches binding"
            )

    for name in ("policyDigest", "promptDigest", "agentRevisionDigest", "schemaDigest"):
        try:
            _digest_text(bindings.get(name), name)
        except ValueError as error:
            failures.append(str(error))
    skill_digests = bindings.get("skillRevisionDigests")
    try:
        values = _sequence(skill_digests, "skillRevisionDigests")
        if not values:
            raise ValueError("skillRevisionDigests must not be empty")
        for value in values:
            _digest_text(value, "skillRevisionDigest")
    except ValueError as error:
        failures.append(str(error))
    try:
        _text(bindings.get("modelAlias"), "modelAlias", maximum=256)
    except ValueError as error:
        failures.append(str(error))

    metrics = {
        "actualCaseCount": len(cases),
        "skippedCaseCount": totals["skipped"],
        "leakageCount": totals["leakage"],
        "anchorResolved": totals["anchorResolved"],
        "anchorTotal": totals["anchorTotal"],
        "groundedSupported": totals["groundedSupported"],
        "groundedTotal": totals["groundedTotal"],
        "retrievalRelevantAt10": totals["retrievalRelevantAt10"],
        "retrievalRelevantTotal": totals["retrievalRelevantTotal"],
        "abstainCorrect": totals["abstainCorrect"],
        "abstainTotal": totals["abstainTotal"],
    }
    thresholds = {
        "minimumCases": sample_threshold,
        "zeroSkipped": skip_threshold,
        "zeroLeakage": leakage_threshold,
        "anchorResolution": anchor_threshold,
        "groundedClaimCitationPrecision": grounded_threshold,
        "retrievalRecallAt10": retrieval_threshold,
        "abstainAccuracy": abstain_threshold,
    }
    unique_failures = list(dict.fromkeys(failures))
    return {
        "schemaVersion": "quality-kb-report-v1",
        "profileId": "QUALITY-KB-01",
        "status": "pass" if not unique_failures else "fail",
        "datasetVersion": dataset_version,
        "datasetDigest": _digest(_dataset_identity(profile)),
        "evaluatorDigest": _evaluator_digest(),
        "configDigest": _digest({"minCases": min_cases, "thresholds": _THRESHOLDS}),
        "bindings": binding_report,
        "externalApprovalMatched": external_approval_matched,
        "execution": execution_report,
        "metrics": metrics,
        "thresholds": thresholds,
        "failures": unique_failures,
        "cases": sorted(evidence, key=lambda item: str(item["caseId"])),
    }


def _load_json(path: Path) -> object:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(), object_pairs_hook=no_duplicates)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read quality profile: {path}") from error


def _pytest_evidence(path: Path) -> dict[str, int]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise ValueError("cannot read quality-kb pytest evidence") from error
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    try:
        for suite in suites:
            for name in totals:
                totals[name] += int(suite.attrib.get(name, "0"))
    except ValueError as error:
        raise ValueError("quality-kb pytest evidence has invalid counts") from error
    if (
        totals["tests"] < 1
        or totals["failures"] != 0
        or totals["errors"] != 0
        or totals["skipped"] != 0
    ):
        raise ValueError(
            "quality-kb pytest evidence must have tests and zero failures/errors/skips"
        )
    return totals


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--min-cases", type=int, default=100)
    parser.add_argument("--require-real", action="store_true")
    parser.add_argument("--pytest-report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.require_real and os.environ.get("TAP_RUN_QUALITY_KB_01") != "1":
        print("quality-kb-real requires TAP_RUN_QUALITY_KB_01=1", file=sys.stderr)
        return 2
    approved_mapping: tuple[str, str, str] | None = None
    if args.require_real:
        try:
            approved_mapping = (
                _model_identity(
                    os.environ.get("TAP_QUALITY_KB_APPROVED_PROVIDER"),
                    "approved provider",
                ),
                _model_identity(
                    os.environ.get("TAP_QUALITY_KB_APPROVED_MODEL"),
                    "approved model",
                ),
                _digest_text(
                    os.environ.get("TAP_QUALITY_KB_MODEL_APPROVAL_DIGEST"),
                    "approved model mapping digest",
                ),
            )
        except ValueError:
            print(
                "quality-kb-real requires an explicit approved actual provider/model mapping",
                file=sys.stderr,
            )
            return 2
    try:
        report = evaluate_profile(
            _load_json(args.profile),
            min_cases=args.min_cases,
            require_real=args.require_real,
            approved_mapping=approved_mapping,
        )
        if args.pytest_report is not None:
            report["pytestEvidence"] = _pytest_evidence(args.pytest_report)
    except (TypeError, ValueError) as error:
        print(f"quality-kb profile invalid: {error}", file=sys.stderr)
        return 2
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.report is None:
        sys.stdout.write(serialized)
    else:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized)
    metrics = _mapping(report["metrics"], "report metrics")
    if report["status"] != "pass":
        print(
            "QUALITY-KB-01 failed: "
            f"cases={metrics['actualCaseCount']} (required {args.min_cases}); "
            f"skipped={metrics['skippedCaseCount']} (required 0); "
            f"leakage={metrics['leakageCount']} (required 0)",
            file=sys.stderr,
        )
        for failure in _sequence(report["failures"], "report failures"):
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(
        f"QUALITY-KB-01 passed: cases={metrics['actualCaseCount']} skipped=0 leakage=0",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
