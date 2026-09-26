#!/usr/bin/env python3
"""Deterministically evaluate QUALITY-GRAPH-01 human labels."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from tap.modules.ai.domain.models import schema_digest, text_digest
from tap.modules.graph.adapters.model_gateway_extraction import (
    GRAPH_EXTRACTION_PROMPT,
    GRAPH_EXTRACTION_SCHEMA,
)

from tap.quality.evidence import (
    canonical_digest,
    graph_candidate_sets,
    graph_materials,
    validate_candidate_batch,
    validate_journey_evidence,
)

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def current_bindings() -> dict[str, str]:
    return {
        "promptDigest": text_digest(GRAPH_EXTRACTION_PROMPT),
        "schemaDigest": schema_digest(GRAPH_EXTRACTION_SCHEMA),
        "evaluatorDigest": "sha256:"
        + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def _ratio(numerator: int, denominator: int, required: int) -> dict[str, object]:
    return {
        "actual": f"{numerator}/{denominator}",
        "required": "100%" if required == 100 else f">={required}%",
        "passed": denominator > 0 and numerator * 100 >= denominator * required,
    }


def _maximum(numerator: int, denominator: int, maximum: int) -> dict[str, object]:
    return {
        "actual": f"{numerator}/{denominator}",
        "required": f"<={maximum}%",
        "passed": denominator > 0 and numerator * 100 <= denominator * maximum,
    }


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _array(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _validate(
    profile: object,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    root = _mapping(profile, "profile")
    if (
        root.get("schemaVersion") != "quality-graph-profile-v1"
        or root.get("profileId") != "QUALITY-GRAPH-01"
    ):
        raise ValueError("unsupported graph quality profile")
    documents = [
        _mapping(item, "document")
        for item in _array(root.get("documents"), "documents")
    ]
    labels = [_mapping(item, "label") for item in _array(root.get("labels"), "labels")]
    if len({item.get("documentId") for item in documents}) != len(documents):
        raise ValueError("document identities must be unique")
    if len({item.get("labelId") for item in labels}) != len(labels):
        raise ValueError("label identities must be unique")
    for document in documents:
        content = document.get("content")
        content_digest = document.get("contentDigest")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("document content must be nonblank")
        if _DIGEST.fullmatch(str(content_digest)) is None:
            raise ValueError("document content digest must be SHA-256")
        if content_digest != "sha256:" + hashlib.sha256(content.encode()).hexdigest():
            raise ValueError("document content digest does not match content")
    document_ids = {item["documentId"] for item in documents}
    for label in labels:
        if label.get("documentId") not in document_ids:
            raise ValueError("graph judgment document does not exist")
        if label.get("kind") not in {"node", "edge", "merge"}:
            raise ValueError("unsupported graph judgment kind")
        if label.get("origin") not in {"EXTRACTED", "INFERRED"}:
            raise ValueError("unsupported graph relation origin")
        if any(
            type(label.get(name)) is not bool
            for name in (
                "predicted",
                "correct",
                "evidenceResolvable",
                "provenanceComplete",
            )
        ):
            raise ValueError("graph judgments require literal boolean observations")
        if not isinstance(label.get("reviewer"), str) or not label["reviewer"].strip():
            raise ValueError("graph judgment requires reviewer provenance")
    return root, documents, labels


def review_result_for_revisions(
    documents: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    revisions: tuple[str, str],
) -> dict[str, object]:
    """Return every reviewed Graph judgment used by a candidate's metrics."""
    revision_to_document = {
        str(document["documentId"])
        + "-revision-"
        + str(document["contentDigest"]).removeprefix("sha256:")[:12]: str(
            document["documentId"]
        )
        for document in documents
    }
    document_ids = {revision_to_document[revision] for revision in revisions}
    return {
        "labels": [
            {
                "labelId": label.get("labelId"),
                "documentId": label.get("documentId"),
                "predicted": label.get("predicted"),
                "correct": label.get("correct"),
                "evidenceResolvable": label.get("evidenceResolvable"),
                "provenanceComplete": label.get("provenanceComplete"),
                "reviewer": label.get("reviewer"),
            }
            for label in sorted(labels, key=lambda value: str(value.get("labelId")))
            if label.get("documentId") in document_ids
        ]
    }


def evaluate(profile: object) -> dict[str, object]:
    root, documents, labels = _validate(profile)
    extracted = [
        item
        for item in labels
        if item["kind"] == "edge"
        and item["origin"] == "EXTRACTED"
        and item["predicted"]
    ]
    relations = [
        item for item in labels if item["kind"] == "edge" and item["predicted"]
    ]
    merges = [item for item in labels if item["kind"] == "merge" and item["predicted"]]
    inferred = [
        item for item in labels if item["origin"] == "INFERRED" and item["predicted"]
    ]
    metrics = {
        "minimumDocuments": {
            "actual": len(documents),
            "required": ">=20",
            "passed": len(documents) >= 20,
        },
        "minimumLabels": {
            "actual": len(labels),
            "required": ">=200",
            "passed": len(labels) >= 200,
        },
        "evidenceProvenanceResolution": _ratio(
            sum(
                bool(item["evidenceResolvable"])
                or bool(item["origin"] == "INFERRED" and item["provenanceComplete"])
                for item in labels
            ),
            len(labels),
            100,
        ),
        "extractedEdgeEvidencePrecision": _ratio(
            sum(
                bool(item["correct"] and item["evidenceResolvable"])
                for item in extracted
            ),
            len(extracted),
            100,
        ),
        "relationPrecision": _ratio(
            sum(bool(item["correct"]) for item in relations), len(relations), 90
        ),
        "incorrectEntityMergeRate": _maximum(
            sum(not bool(item["correct"]) for item in merges), len(merges), 1
        ),
        "inferredProvenanceCompleteness": _ratio(
            sum(bool(item["provenanceComplete"]) for item in inferred),
            len(inferred),
            100,
        ),
    }
    material = json.dumps(
        root, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return {
        "profileId": "QUALITY-GRAPH-01",
        "datasetDigest": "sha256:" + hashlib.sha256(material).hexdigest(),
        "evaluatorDigest": "sha256:"
        + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "metrics": metrics,
        "passed": all(bool(item["passed"]) for item in metrics.values()),
    }


def validate_real_profile(profile: object) -> None:
    root, documents, labels = _validate(profile)
    if len(documents) < 20 or len(labels) < 200:
        raise ValueError("real Graph gate requires 20 documents and 200 labels")
    dataset = _mapping(root.get("dataset"), "dataset")
    if dataset.get("reviewStatus") != "approved":
        raise ValueError("real Graph gate requires approved human review")
    if any(
        str(label.get("reviewer")).startswith(
            ("machine-", "pending-", "generated-", "human:")
        )
        for label in labels
    ):
        raise ValueError("real Graph gate requires an independent human reviewer")
    bindings = _mapping(root.get("bindings"), "bindings")
    actual_model = bindings.get("actualModel")
    if not isinstance(actual_model, str) or actual_model.startswith(
        ("fake/", "pending/", "simulated/", "prebuilt/")
    ):
        raise ValueError("real Graph gate requires a real model")
    for name in ("promptDigest", "schemaDigest", "evaluatorDigest"):
        if _DIGEST.fullmatch(str(bindings.get(name))) is None:
            raise ValueError(f"real Graph gate requires {name}")
        if bindings[name] != current_bindings()[name]:
            raise ValueError(f"real Graph gate requires current {name}")
    candidate_sets = graph_candidate_sets(root)
    run_evidence = _mapping(root.get("runEvidence"), "candidate batch")
    cases = [
        _mapping(value, "candidate case")
        for value in _array(run_evidence.get("cases"), "candidate cases")
    ]
    expected_revisions = dict(candidate_sets)
    for case in cases:
        output = _mapping(case.get("output"), "candidate output")
        revisions = output.get("sourceRevisionIds")
        document_revisions = output.get("documentRevisionIds")
        expected = expected_revisions.get(str(case.get("caseId")))
        if (
            not isinstance(revisions, list)
            or len(revisions) < 2
            or len(revisions) != len(set(revisions))
            or tuple(revisions) != expected
        ):
            raise ValueError(
                "Graph candidate output must bind the exact multi-revision set"
            )
        if (
            not isinstance(document_revisions, list)
            or tuple(document_revisions) != expected
        ):
            raise ValueError("Graph candidate output has stale document revisions")
        evidence = [
            _mapping(value, "Graph evidence")
            for value in _array(output.get("evidence"), "Graph evidence")
        ]
        if (
            {item.get("sourceRevisionId") for item in evidence} != set(expected or ())
            or {item.get("documentRevisionId") for item in evidence}
            != set(expected or ())
            or any(not item.get("chunkId") for item in evidence)
        ):
            raise ValueError(
                "Graph evidence does not bind every document revision and chunk"
            )
    dataset_material, config_material, model_material = graph_materials(root)
    validate_candidate_batch(
        run_evidence,
        expected_case_ids=(case_id for case_id, _ in candidate_sets),
        dataset_material=dataset_material,
        config_material=config_material,
        model_material=model_material,
    )
    for case in cases:
        expected = expected_revisions[str(case["caseId"])]
        if case.get("reviewResult") != review_result_for_revisions(
            documents, labels, expected
        ):
            raise ValueError("Graph review result is stale")
    validate_journey_evidence(
        root.get("journeyEvidence"),
        required_kinds=("contract", "api", "answer", "browser", "restart"),
        dataset_digest=canonical_digest(dataset_material),
        config_digest=canonical_digest(config_material),
        model_digest=canonical_digest(model_material),
        candidate_run_id_value=str(run_evidence["runId"]),
        candidate_digests=(str(case["candidateDigest"]) for case in cases),
        request_ids=(str(case["requestId"]) for case in cases),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--real", action="store_true")
    arguments = parser.parse_args()
    profile = json.loads(arguments.profile.read_text(encoding="utf-8"))
    if arguments.real:
        validate_real_profile(profile)
    report = evaluate(profile)
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
