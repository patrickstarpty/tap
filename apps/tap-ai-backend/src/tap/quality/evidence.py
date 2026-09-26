#!/usr/bin/env python3
"""Shared fail-closed evidence validation for real quality candidates."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")


def canonical_digest(value: object) -> str:
    material = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "sha256:" + hashlib.sha256(material).hexdigest()


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return value


def _array(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} must be a sha256 digest")
    return value


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a bounded identifier")
    return value


def validate_candidate_batch(
    batch_value: object,
    *,
    expected_case_ids: Iterable[str],
    dataset_material: object,
    config_material: object,
    model_material: object,
) -> dict[str, int]:
    """Require current output, invocation and named review evidence for every case."""
    batch = _mapping(batch_value, "candidate batch")
    if batch.get("schemaVersion") != "quality-candidate-evidence-v1":
        raise ValueError("unsupported candidate evidence schema")
    if batch.get("runStatus") != "completed" or batch.get("executionMode") != "real":
        raise ValueError("candidate batch requires completed real execution")
    run_id = _identifier(batch.get("runId"), "runId")
    expected_digests = {
        "datasetDigest": canonical_digest(dataset_material),
        "configDigest": canonical_digest(config_material),
        "modelDigest": canonical_digest(model_material),
    }
    for name, expected in expected_digests.items():
        if _digest(batch.get(name), name) != expected:
            raise ValueError(f"{name} does not bind the current material")

    expected_ids = tuple(expected_case_ids)
    if not expected_ids or len(expected_ids) != len(set(expected_ids)):
        raise ValueError("expected case IDs must be nonempty and unique")
    cases = [_mapping(value, "candidate case") for value in _array(batch.get("cases"), "cases")]
    case_ids = [str(case.get("caseId")) for case in cases]
    if len(case_ids) != len(set(case_ids)) or set(case_ids) != set(expected_ids):
        raise ValueError("candidate batch must contain every expected case exactly once")

    request_ids: set[str] = set()
    reviewers: set[str] = set()
    for case in cases:
        case_id = _identifier(case.get("caseId"), "caseId")
        if case.get("status") != "completed" or case.get("executionMode") != "real":
            raise ValueError(f"case {case_id} requires completed real execution")
        request_id = _identifier(case.get("requestId"), "requestId")
        if request_id in request_ids:
            raise ValueError("provider request IDs must be unique")
        request_ids.add(request_id)
        request_digest = _digest(case.get("requestDigest"), "requestDigest")
        if "output" not in case or case["output"] is None:
            raise ValueError(f"case {case_id} requires complete output")
        output_digest = _digest(case.get("outputDigest"), "outputDigest")
        if canonical_digest(case["output"]) != output_digest:
            raise ValueError(f"case {case_id} output digest does not match complete output")
        if case.get("reviewedOutputDigest") != output_digest:
            raise ValueError(f"case {case_id} reviewedOutputDigest is stale")
        candidate_digest = canonical_digest(
            {
                "caseId": case_id,
                "requestDigest": request_digest,
                "outputDigest": output_digest,
                **expected_digests,
            }
        )
        if case.get("candidateDigest") != candidate_digest:
            raise ValueError(f"case {case_id} candidate digest is stale")
        if case.get("reviewedCandidateDigest") != candidate_digest:
            raise ValueError(f"case {case_id} review is not bound to the current candidate")
        judgments = [
            _mapping(value, "review judgment")
            for value in _array(case.get("reviewJudgments"), "reviewJudgments")
        ]
        if not judgments:
            raise ValueError(f"case {case_id} requires a named human review")
        names: set[str] = set()
        for judgment in judgments:
            reviewer = _identifier(judgment.get("reviewer"), "reviewer")
            if reviewer in names or reviewer.startswith(("machine-", "pending-", "human:")):
                raise ValueError(f"case {case_id} reviewer identity is invalid")
            names.add(reviewer)
            reviewers.add(reviewer)
            if judgment.get("approved") is not True:
                raise ValueError(f"case {case_id} requires approved human review")
            if judgment.get("reviewRunId") != run_id:
                raise ValueError(f"case {case_id} has a prefilled reviewer from another run")

    return {
        "caseCount": len(cases),
        "requestCount": len(request_ids),
        "reviewerCount": len(reviewers),
    }


def test_design_materials(
    profile_value: object, *, binding_digests: dict[str, str]
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """Return the immutable Test Design dataset, thresholds and model binding."""
    profile = _mapping(profile_value, "test design profile")
    dataset = _mapping(profile.get("dataset"), "dataset")
    bindings = _mapping(profile.get("bindings"), "bindings")
    cases = [_mapping(value, "case") for value in _array(profile.get("cases"), "cases")]
    dataset_material: dict[str, object] = {
        "profileId": profile.get("profileId"),
        "version": dataset.get("version"),
        "cases": [
            {
                "caseId": item.get("caseId"),
                "intent": item.get("intent"),
                "source": item.get("source"),
                "criticalRequirements": item.get("criticalRequirements"),
            }
            for item in cases
        ],
    }
    config_material: dict[str, object] = {
        "minimumBusinessIntents": 50,
        "schemaAndBdd": "100%",
        "unsupportedSourceFacts": 0,
        "criticalRequirementCoverage": ">=90%",
        "withoutCriticalCorrection": ">=80%",
    }
    model_material: dict[str, object] = {
        name: bindings.get(name)
        for name in (
            "modelAlias",
            "actualModel",
            "agentRevisionId",
            "skillRevisionIds",
        )
    }
    model_material.update(binding_digests)
    return dataset_material, config_material, model_material


def graph_materials(
    profile_value: object,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """Return Graph human-label inputs separately from observed candidate outputs."""
    profile = _mapping(profile_value, "graph profile")
    dataset = _mapping(profile.get("dataset"), "dataset")
    documents = [
        _mapping(value, "document") for value in _array(profile.get("documents"), "documents")
    ]
    labels = [_mapping(value, "label") for value in _array(profile.get("labels"), "labels")]
    dataset_material: dict[str, object] = {
        "profileId": profile.get("profileId"),
        "version": dataset.get("version"),
        "documents": documents,
        "labels": [
            {
                key: value
                for key, value in label.items()
                if key
                not in {
                    "predicted",
                    "correct",
                    "evidenceResolvable",
                    "provenanceComplete",
                    "reviewer",
                }
            }
            for label in labels
        ],
    }
    config_material: dict[str, object] = {
        "minimumDocuments": 20,
        "minimumLabels": 200,
        "evidenceProvenanceResolution": "100%",
        "extractedEdgeEvidencePrecision": "100%",
        "relationPrecision": ">=90%",
        "incorrectEntityMergeRate": "<=1%",
        "inferredProvenanceCompleteness": "100%",
        "minimumRevisionsPerCandidate": 2,
    }
    model_material = dict(_mapping(profile.get("bindings"), "bindings"))
    return dataset_material, config_material, model_material


def graph_candidate_sets(profile_value: object) -> list[tuple[str, tuple[str, str]]]:
    """Derive deterministic two-revision candidates from the complete Graph corpus."""
    profile = _mapping(profile_value, "graph profile")
    documents = sorted(
        (_mapping(value, "document") for value in _array(profile.get("documents"), "documents")),
        key=lambda value: str(value.get("documentId")),
    )
    if len(documents) % 2:
        raise ValueError("multi-revision Graph corpus must contain an even document count")
    result: list[tuple[str, tuple[str, str]]] = []
    for offset in range(0, len(documents), 2):
        revisions = []
        for document in documents[offset : offset + 2]:
            document_id = _identifier(document.get("documentId"), "documentId")
            content_digest = _digest(document.get("contentDigest"), "contentDigest")
            revisions.append(
                document_id + "-revision-" + content_digest.removeprefix("sha256:")[:12]
            )
        result.append((f"graph-set-{offset // 2 + 1:03d}", (revisions[0], revisions[1])))
    return result


def validate_journey_evidence(
    evidence_value: object,
    *,
    required_kinds: Iterable[str],
    dataset_digest: str,
    config_digest: str,
    model_digest: str,
) -> None:
    """Bind contract/API/browser/restart evidence to the same candidate run inputs."""
    required = tuple(required_kinds)
    items = [
        _mapping(value, "journey evidence") for value in _array(evidence_value, "journeyEvidence")
    ]
    kinds = [str(item.get("kind")) for item in items]
    if len(kinds) != len(set(kinds)) or set(kinds) != set(required):
        raise ValueError("journey evidence must cover every required kind exactly once")
    execution_ids: set[str] = set()
    for item in items:
        kind = _identifier(item.get("kind"), "journey evidence kind")
        if item.get("status") != "passed" or item.get("executionMode") != "real":
            raise ValueError(f"{kind} journey requires passed real execution")
        execution_id = _identifier(item.get("executionId"), "executionId")
        if execution_id in execution_ids:
            raise ValueError("journey execution IDs must be unique")
        execution_ids.add(execution_id)
        _digest(item.get("artifactDigest"), "artifactDigest")
        for name, expected in (
            ("datasetDigest", dataset_digest),
            ("configDigest", config_digest),
            ("modelDigest", model_digest),
        ):
            if item.get(name) != expected:
                raise ValueError(f"{kind} journey {name} is stale")
