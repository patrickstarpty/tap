#!/usr/bin/env python3
"""Deterministically evaluate QUALITY-KB-01 labels against runner observations."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\Z")
_CASE_TYPES = frozenset({"answerable", "conflict", "unauthorized", "abstain"})
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


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _text(value: object, name: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > maximum:
        raise ValueError(f"{name} must be a bounded non-empty string")
    return value


def _digest_text(value: object, name: str) -> str:
    result = _text(value, name, 71)
    if _DIGEST.fullmatch(result) is None:
        raise ValueError(f"{name} must be a sha256 digest")
    return result


def _strings(value: object, name: str, *, allow_empty: bool = True) -> list[str]:
    result = [_text(item, name, 256) for item in _sequence(value, name)]
    if not allow_empty and not result:
        raise ValueError(f"{name} must not be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return result


def dataset_digest(dataset_value: object) -> str:
    """Bind every human-authored label and governance input, never observations."""
    return _digest(_mapping(dataset_value, "dataset document"))


def expected_cache_key(dataset_value: object, case_id: str) -> str:
    return _digest({"datasetDigest": dataset_digest(dataset_value), "caseId": case_id})


def _evaluator_digest() -> str:
    return "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _validate_evidence(raw: object, case_id: str) -> dict[str, Any]:
    evidence = _mapping(raw, f"case {case_id} expected evidence")
    for name in ("evidenceId", "sourceId", "documentRevisionId", "chunkId"):
        _text(evidence.get(name), f"case {case_id} {name}", 256)
    locator = _mapping(evidence.get("locator"), f"case {case_id} locator")
    _text(locator.get("type"), f"case {case_id} locator type", 32)
    _digest_text(evidence.get("evidenceDigest"), f"case {case_id} evidence digest")
    return evidence


def validate_dataset(
    dataset_value: object, *, min_cases: int = 100
) -> list[dict[str, Any]]:
    """Validate four closed human-label semantics and duplicate resistance."""
    if type(min_cases) is not int or min_cases < 1:
        raise ValueError("min_cases must be a positive integer")
    root = _mapping(dataset_value, "dataset document")
    if root.get("schemaVersion") != "quality-kb-dataset-v1":
        raise ValueError("unsupported quality dataset schema")
    if root.get("profileId") != "QUALITY-KB-01":
        raise ValueError("unsupported quality profile ID")
    metadata = _mapping(root.get("dataset"), "dataset metadata")
    _text(metadata.get("version"), "dataset version", 128)
    if metadata.get("provenance") is not None:
        _text(metadata["provenance"], "dataset provenance", 1024)
    bindings = _mapping(root.get("bindings"), "bindings")
    _text(bindings.get("modelAlias"), "modelAlias", 256)
    for name in ("policyDigest", "promptDigest", "agentRevisionDigest", "schemaDigest"):
        if bindings.get(name) is not None:
            _digest_text(bindings[name], name)
    for value in _sequence(
        bindings.get("skillRevisionDigests"), "skillRevisionDigests"
    ):
        _digest_text(value, "skillRevisionDigest")

    cases = [_mapping(item, "case") for item in _sequence(root.get("cases"), "cases")]
    ids: set[str] = set()
    questions: set[str] = set()
    content_digests: set[str] = set()
    counts: Counter[str] = Counter()
    for case in cases:
        case_id = _text(case.get("caseId"), "caseId", 128)
        if case_id in ids:
            raise ValueError("case IDs must be unique")
        ids.add(case_id)
        case_type = _text(case.get("caseType"), f"case {case_id} type", 32)
        if case_type not in _CASE_TYPES:
            raise ValueError(f"case {case_id} has unsupported caseType")
        counts[case_type] += 1
        question = " ".join(_text(case.get("question"), "question").split()).casefold()
        if question in questions:
            raise ValueError("dataset contains duplicate case content")
        questions.add(question)
        _text(case.get("projectId"), "projectId", 128)
        authorized = set(
            _strings(case.get("authorizedSourceIds"), "authorizedSourceIds")
        )
        selected = set(
            _strings(
                case.get("selectedSourceIds"), "selectedSourceIds", allow_empty=False
            )
        )
        authorization = case.get("authorizationExpected")
        should_abstain = case.get("shouldAbstain")
        conflict = case.get("conflictLabel")
        if authorization not in {"allow", "deny"} or type(should_abstain) is not bool:
            raise ValueError(f"case {case_id} has invalid authorization/abstain labels")
        if conflict is not None:
            _text(conflict, "conflictLabel", 256)
        evidence = [
            _validate_evidence(item, case_id)
            for item in _sequence(case.get("expectedEvidence"), "expectedEvidence")
        ]
        evidence_ids = [str(item["evidenceId"]) for item in evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError(f"case {case_id} expected evidence IDs must be unique")
        claims = [
            _mapping(item, "expected claim")
            for item in _sequence(case.get("expectedClaims"), "expectedClaims")
        ]
        claim_ids: set[str] = set()
        for claim in claims:
            claim_id = _text(claim.get("claimId"), "expected claimId", 128)
            if claim_id in claim_ids:
                raise ValueError(f"case {case_id} expected claim IDs must be unique")
            claim_ids.add(claim_id)
            _digest_text(claim.get("textDigest"), "expected claim textDigest")
            supported = set(
                _strings(
                    claim.get("supportedEvidenceIds"),
                    "supportedEvidenceIds",
                    allow_empty=False,
                )
            )
            if not supported <= set(evidence_ids):
                raise ValueError(
                    f"case {case_id} claim support references unknown evidence"
                )
        _text(case.get("labelProvenance"), "labelProvenance", 1024)

        if case_type == "answerable" and (should_abstain or authorization != "allow"):
            raise ValueError(
                f"answerable case {case_id} must set shouldAbstain=false and allow"
            )
        if case_type == "answerable" and (
            not evidence
            or not claims
            or not selected <= authorized
            or conflict is not None
        ):
            raise ValueError(
                f"answerable case {case_id} requires authorized evidence and claims"
            )
        if case_type == "conflict" and (
            not should_abstain
            or authorization != "allow"
            or conflict is None
            or len(evidence) < 2
            or not selected <= authorized
            or claims
        ):
            raise ValueError(f"conflict case {case_id} has inconsistent labels")
        if case_type == "unauthorized" and (
            not should_abstain
            or authorization != "deny"
            or selected <= authorized
            or evidence
            or claims
            or conflict is not None
        ):
            raise ValueError(f"unauthorized case {case_id} has inconsistent labels")
        if case_type == "abstain" and (
            not should_abstain
            or authorization != "allow"
            or not selected <= authorized
            or evidence
            or claims
            or conflict is not None
        ):
            raise ValueError(f"abstain case {case_id} has inconsistent labels")
        content_hash = _digest(
            {
                key: value
                for key, value in case.items()
                if key not in {"caseId", "labelProvenance"}
            }
        )
        if content_hash in content_digests:
            raise ValueError("dataset contains duplicate case content")
        content_digests.add(content_hash)

    if len(cases) >= 100:
        missing = _CASE_TYPES - counts.keys()
        if missing:
            raise ValueError(
                "dataset is missing case categories: " + ", ".join(sorted(missing))
            )
        for name in sorted(_CASE_TYPES):
            if counts[name] * 10 < len(cases) or counts[name] * 10 > len(cases) * 7:
                raise ValueError(
                    f"dataset case category distribution is unreasonable: {name}"
                )
    return sorted(cases, key=lambda item: str(item["caseId"]))


def _ratio(
    name: str, numerator: int, denominator: int, required: int
) -> dict[str, object]:
    return {
        "metric": name,
        "actual": f"{numerator}/{denominator}",
        "required": "100%" if required == 100 else f">={required}%",
        "passed": denominator > 0 and numerator * 100 >= denominator * required,
    }


def _anchor_key(value: dict[str, Any], *, resolved: bool) -> tuple[object, ...]:
    return (
        value.get("sourceId"),
        value.get("documentRevisionId"),
        value.get("chunkId"),
        _canonical(value.get("locator")),
        value.get("resolvedEvidenceDigest" if resolved else "evidenceDigest"),
    )


def _validate_execution(
    root: dict[str, Any], case_id: str, observation: dict[str, Any], call_ids: set[str]
) -> tuple[int, int, int, set[tuple[str, str]]]:
    execution = _mapping(observation.get("execution"), f"case {case_id} execution")
    timeout_ms = execution.get("timeoutMs")
    duration_ms = execution.get("durationMs")
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 120_000:
        raise ValueError(f"case {case_id} timeout evidence is invalid")
    if type(duration_ms) is not int or not 0 <= duration_ms <= timeout_ms:
        raise ValueError(f"case {case_id} duration evidence is invalid")
    if _UTC.fullmatch(_text(execution.get("startedAtUtc"), "startedAtUtc", 64)) is None:
        raise ValueError(f"case {case_id} start time is invalid")
    if _digest_text(execution.get("cacheKey"), "cacheKey") != expected_cache_key(
        root, case_id
    ):
        raise ValueError(f"case {case_id} cache key is invalid")
    cache_hit = execution.get("cacheHit")
    if type(cache_hit) is not bool:
        raise ValueError(f"case {case_id} cacheHit must be boolean")
    attempts = [
        _mapping(item, "attempt")
        for item in _sequence(execution.get("attempts"), "attempts")
    ]
    if cache_hit and attempts:
        raise ValueError(f"case {case_id} cache hit cannot claim provider attempts")
    if not cache_hit and not attempts:
        raise ValueError(f"case {case_id} cache miss has no provider attempt")
    identities: set[tuple[str, str]] = set()
    provider_calls = 0
    for index, attempt in enumerate(attempts, 1):
        if attempt.get("attempt") != index:
            raise ValueError(f"case {case_id} attempt order is invalid")
        retry_reason = attempt.get("retryReason")
        if (index == 1 and retry_reason is not None) or (
            index > 1 and not isinstance(retry_reason, str)
        ):
            raise ValueError(f"case {case_id} retry reason evidence is invalid")
        calls = [
            _mapping(item, "provider call")
            for item in _sequence(attempt.get("providerCalls"), "providerCalls")
        ]
        if not calls:
            raise ValueError(
                f"case {case_id} attempt has no captured ModelGateway call"
            )
        for call in calls:
            provider = _text(call.get("actualProvider"), "actualProvider", 256)
            model = _text(call.get("actualModel"), "actualModel", 256)
            identities.add((provider, model))
            _text(call.get("operation"), "operation", 32)
            _digest_text(call.get("promptDigest"), "promptDigest")
            provider_id = call.get("providerRequestId")
            gateway_id = call.get("gatewayCallId")
            if not isinstance(provider_id, str) and not isinstance(gateway_id, str):
                raise ValueError(f"case {case_id} provider call has no call ID")
            unique_id = f"{provider_id}:{gateway_id}"
            if unique_id in call_ids:
                raise ValueError("provider call IDs must be globally unique")
            call_ids.add(unique_id)
            provider_calls += 1
    return provider_calls, int(cache_hit), max(0, len(attempts) - 1), identities


def evaluate_run(
    dataset_value: object,
    observations_value: object | None,
    *,
    min_cases: int = 100,
    require_real: bool = False,
    approved_mapping: tuple[str, str, str] | None = None,
    provider_call_budget: int = 1000,
) -> dict[str, object]:
    cases = validate_dataset(dataset_value, min_cases=min_cases)
    root = _mapping(dataset_value, "dataset document")
    metadata = _mapping(root["dataset"], "dataset metadata")
    bindings = _mapping(root["bindings"], "bindings")
    digest = dataset_digest(root)
    failures: list[str] = []
    if metadata.get("labelingMethod") != "human":
        failures.append("dataset must be human-labeled")
    if not metadata.get("provenance"):
        failures.append("dataset human-label provenance is missing")
    if len(cases) < min_cases:
        failures.append(f"actual case count {len(cases)} is below required {min_cases}")

    observed_by_id: dict[str, dict[str, Any]] = {}
    declared_execution: dict[str, Any] | None = None
    if observations_value is not None:
        observations = _mapping(observations_value, "observations")
        if observations.get("schemaVersion") != "quality-kb-observations-v1":
            raise ValueError("unsupported quality observations schema")
        if observations.get("datasetDigest") != digest:
            raise ValueError("observations dataset digest mismatch")
        _text(observations.get("runId"), "runId", 256)
        declared_execution = _mapping(
            observations.get("execution"), "observations execution"
        )
        for item in _sequence(observations.get("cases"), "observation cases"):
            observation = _mapping(item, "observation case")
            case_id = _text(observation.get("caseId"), "observation caseId", 128)
            if case_id in observed_by_id:
                raise ValueError("observation case IDs must be unique")
            observed_by_id[case_id] = observation
        if not set(observed_by_id) <= {str(item["caseId"]) for item in cases}:
            raise ValueError("observations contain unknown case IDs")

    totals: Counter[str] = Counter()
    call_ids: set[str] = set()
    cache_keys: set[str] = set()
    provider_calls = cache_hits = retry_count = 0
    actual_identities: set[tuple[str, str]] = set()
    case_reports: list[dict[str, object]] = []
    for case in cases:
        case_id = str(case["caseId"])
        case_observation = observed_by_id.get(case_id)
        if case_observation is None:
            totals["skipped"] += 1
            case_reports.append(
                {
                    "caseId": case_id,
                    "caseType": case["caseType"],
                    "status": "skipped",
                    "leakage": [],
                }
            )
            continue
        execution = _mapping(case_observation.get("execution"), "case execution")
        cache_key = _digest_text(execution.get("cacheKey"), "cacheKey")
        if cache_key in cache_keys:
            raise ValueError("cache keys must be unique per case")
        cache_keys.add(cache_key)
        calls, hits, retries, identities = _validate_execution(
            root, case_id, case_observation, call_ids
        )
        provider_calls += calls
        cache_hits += hits
        retry_count += retries
        actual_identities |= identities

        selected = set(case["selectedSourceIds"])
        project = case["projectId"]
        leakage: list[str] = []
        retrieved_keys: set[tuple[str, str, str]] = set()
        for raw_hit in _sequence(case_observation.get("retrieved"), "retrieved"):
            hit = _mapping(raw_hit, "retrieval hit")
            rank = hit.get("rank")
            if type(rank) is not int or not 1 <= rank <= 100:
                raise ValueError("retrieval rank is invalid")
            identity = (
                _text(hit.get("sourceId"), "sourceId"),
                _text(hit.get("documentRevisionId"), "documentRevisionId"),
                _text(hit.get("chunkId"), "chunkId"),
            )
            if rank <= 10:
                retrieved_keys.add(identity)
            if hit.get("projectId") != project:
                leakage.append(
                    f"retrieval:{identity[2]}:project:{hit.get('projectId')}"
                )
            if identity[0] not in selected:
                leakage.append(
                    f"retrieval:{identity[2]}:unselected-source:{identity[0]}"
                )
        expected = [
            _mapping(item, "expected evidence") for item in case["expectedEvidence"]
        ]
        expected_retrieval = {
            (
                str(item["sourceId"]),
                str(item["documentRevisionId"]),
                str(item["chunkId"]),
            )
            for item in expected
        }
        totals["relevant"] += len(expected_retrieval)
        totals["retrieved"] += len(expected_retrieval & retrieved_keys)

        citations = [
            _mapping(item, "citation")
            for item in _sequence(case_observation.get("citations"), "citations")
        ]
        citation_by_id: dict[str, dict[str, Any]] = {}
        expected_anchor_keys = {_anchor_key(item, resolved=False) for item in expected}
        evidence_by_anchor = {
            _anchor_key(item, resolved=False): str(item["evidenceId"])
            for item in expected
        }
        for citation in citations:
            citation_id = _text(citation.get("citationId"), "citationId", 128)
            if citation_id in citation_by_id:
                raise ValueError("citation IDs must be unique per case")
            citation_by_id[citation_id] = citation
            source_id = _text(citation.get("sourceId"), "citation sourceId")
            if citation.get("projectId") != project:
                leakage.append(
                    f"citation:{citation_id}:project:{citation.get('projectId')}"
                )
            if source_id not in selected:
                leakage.append(f"citation:{citation_id}:unselected-source:{source_id}")
            totals["anchors"] += 1
            totals["resolved"] += int(
                _anchor_key(citation, resolved=True) in expected_anchor_keys
            )

        expected_claims = {
            str(item["textDigest"]): set(item["supportedEvidenceIds"])
            for item in case["expectedClaims"]
        }
        citation_evidence = {
            cid: evidence_by_anchor.get(_anchor_key(item, resolved=True))
            for cid, item in citation_by_id.items()
        }
        claims = [
            _mapping(item, "claim")
            for item in _sequence(case_observation.get("claims"), "claims")
        ]
        for claim in claims:
            text_hash = _digest_text(claim.get("textDigest"), "claim textDigest")
            for citation_id in _strings(
                claim.get("citationIds"), "claim citationIds", allow_empty=False
            ):
                if citation_id not in citation_by_id:
                    raise ValueError("claim references unknown citation")
                totals["relations"] += 1
                totals["grounded"] += int(
                    citation_evidence[citation_id]
                    in expected_claims.get(text_hash, set())
                )
        abstained = case_observation.get("abstained")
        if type(abstained) is not bool:
            raise ValueError("observed abstained must be boolean")
        totals["abstain"] += int(bool(case["shouldAbstain"]))
        totals["abstainCorrect"] += int(bool(case["shouldAbstain"]) and abstained)
        response_valid = (
            (abstained and not claims and not citations)
            if case["shouldAbstain"]
            else (not abstained and bool(claims) and bool(citations))
        )
        if not response_valid:
            failures.append(f"case {case_id} response shape contradicts human labels")
        totals["leakage"] += len(leakage)
        case_reports.append(
            {
                "caseId": case_id,
                "caseType": case["caseType"],
                "status": "evaluated",
                "leakage": sorted(leakage),
                "responseShapeValid": response_valid,
            }
        )

    derived_execution = {
        "providerCallBudget": provider_call_budget,
        "providerCalls": provider_calls,
        "cacheHits": cache_hits,
        "retryCount": retry_count,
        "maxRetriesPerCase": 2,
    }
    if declared_execution is not None and declared_execution != derived_execution:
        failures.append(
            "runner execution aggregate does not match immutable case evidence"
        )
    if provider_calls > provider_call_budget:
        failures.append("provider call count exceeds bounded budget")
    if totals["skipped"]:
        failures.append(f"skipped case count {totals['skipped']} exceeds required 0")
    if totals["leakage"]:
        failures.append(f"leakage count {totals['leakage']} exceeds required 0")
    if require_real:
        if approved_mapping is None:
            failures.append("external approved model mapping is required")
        else:
            provider, model, approval_digest = approved_mapping
            _digest_text(approval_digest, "approval digest")
            if (provider, model) not in actual_identities:
                failures.append(
                    "captured response/audit identity does not match approved mapping"
                )

    thresholds: dict[str, dict[str, object]] = {
        "minimumCases": {
            "actual": len(cases),
            "required": min_cases,
            "passed": len(cases) >= min_cases,
        },
        "zeroSkipped": {
            "actual": totals["skipped"],
            "required": 0,
            "passed": totals["skipped"] == 0,
        },
        "zeroLeakage": {
            "actual": totals["leakage"],
            "required": 0,
            "passed": totals["leakage"] == 0,
        },
        "anchorResolution": _ratio(
            "anchorResolution", totals["resolved"], totals["anchors"], 100
        ),
        "groundedClaimCitationPrecision": _ratio(
            "groundedClaimCitationPrecision",
            totals["grounded"],
            totals["relations"],
            100,
        ),
        "retrievalRecallAt10": _ratio(
            "retrievalRecallAt10", totals["retrieved"], totals["relevant"], 90
        ),
        "abstainAccuracy": _ratio(
            "abstainAccuracy", totals["abstainCorrect"], totals["abstain"], 90
        ),
    }
    for threshold in thresholds.values():
        if not threshold["passed"]:
            failures.append(
                f"{threshold.get('metric', 'quality')} threshold failed: {threshold['actual']}"
            )
    metrics = {
        "actualCaseCount": len(cases),
        "skippedCaseCount": totals["skipped"],
        "leakageCount": totals["leakage"],
        "anchorResolved": totals["resolved"],
        "anchorTotal": totals["anchors"],
        "groundedSupported": totals["grounded"],
        "groundedTotal": totals["relations"],
        "retrievalRelevantAt10": totals["retrieved"],
        "retrievalRelevantTotal": totals["relevant"],
        "abstainCorrect": totals["abstainCorrect"],
        "abstainTotal": totals["abstain"],
    }
    return {
        "schemaVersion": "quality-kb-report-v2",
        "profileId": "QUALITY-KB-01",
        "status": "pass" if not failures else "fail",
        "datasetVersion": metadata.get("version"),
        "datasetDigest": digest,
        "evaluatorDigest": _evaluator_digest(),
        "configDigest": _digest(
            {
                "minCases": min_cases,
                "thresholds": _THRESHOLDS,
                "providerCallBudget": provider_call_budget,
            }
        ),
        "bindings": bindings,
        "approvedMappingDigest": approved_mapping[2] if approved_mapping else None,
        "capturedActualIdentities": [
            {"provider": p, "model": m} for p, m in sorted(actual_identities)
        ],
        "execution": derived_execution,
        "metrics": metrics,
        "thresholds": thresholds,
        "failures": list(dict.fromkeys(failures)),
        "cases": case_reports,
    }


def _load_json(path: Path) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(), object_pairs_hook=unique)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read quality data: {path}") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--min-cases", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        report = evaluate_run(
            _load_json(args.dataset),
            _load_json(args.observations) if args.observations else None,
            min_cases=args.min_cases,
        )
    except (TypeError, ValueError) as error:
        print(f"quality-kb input invalid: {error}", file=sys.stderr)
        return 2
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized)
    else:
        sys.stdout.write(serialized)
    metrics = _mapping(report["metrics"], "metrics")
    if report["status"] != "pass":
        print(
            f"QUALITY-KB-01 failed: cases={metrics['actualCaseCount']} (required {args.min_cases}); skipped={metrics['skippedCaseCount']} (required 0); leakage={metrics['leakageCount']} (required 0)",
            file=sys.stderr,
        )
        for failure in _sequence(report["failures"], "failures"):
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(
        f"QUALITY-KB-01 passed: cases={metrics['actualCaseCount']} skipped=0 leakage=0",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
