#!/usr/bin/env python3
"""Evaluate relation-answer observations against the Graph golden set.

Mirrors `scripts/evaluate-quality-graph.py`: a thin CLI wraps a pure `evaluate()`
function. `evaluate()` never touches the filesystem — it validates its in-memory
arguments with `tap.quality.graph_relations.validate_golden` / `validate_regression`
and scores R citations with `match_question` / `aggregate` / `grounded_rate`.
`validate_real()` and `main()` do the I/O: reading files, resolving the golden
set's corpus manifest, and writing the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tap.quality.evidence import canonical_digest
from tap.quality.graph_corpus import manifest_digest, validate_manifest
from tap.quality.graph_relations import (
    aggregate,
    grounded_rate,
    match_question,
    validate_golden,
    validate_regression,
)

REPORT_SCHEMA_VERSION = "graph-relation-report-v1"
_FAKE_MODEL_PREFIXES = ("fake/", "pending/", "simulated/")


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return dict(value)


def _answers(value: object, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be an array")
    return [_mapping(item, f"{name} entry") for item in value]


def _validate_observations(value: object) -> dict[str, Any]:
    observations = _mapping(value, "observations")
    if observations.get("schemaVersion") != "graph-relation-observations-v1":
        raise ValueError("unsupported graph relation observations schema")
    observations["answers"] = _answers(observations.get("answers"), "answers")
    return observations


def _citations_for(answer: Mapping[str, Any] | None) -> Sequence[Mapping[str, object]]:
    if answer is None:
        return ()
    citations = answer.get("citations")
    if not isinstance(citations, Sequence) or isinstance(citations, (str, bytes)):
        return ()
    return tuple(item for item in citations if isinstance(item, Mapping))


def _group_answers(
    answers: Sequence[Mapping[str, Any]], group_id: str
) -> list[Mapping[str, Any]]:
    return [answer for answer in answers if answer.get("group") == group_id]


def evaluate(
    golden: object,
    observations: object,
    *,
    regression: object | None = None,
    baseline: object | None = None,
) -> dict[str, object]:
    """Score `observations` against `golden`, returning the report (pure, no I/O)."""
    golden_set = validate_golden(golden)
    observations_map = _validate_observations(observations)

    answers_by_question: dict[str, Mapping[str, Any]] = {}
    for answer in observations_map["answers"]:
        if answer.get("group") != "golden":
            continue
        question_id = answer.get("questionId")
        if isinstance(question_id, str):
            answers_by_question[question_id] = answer

    results = [
        match_question(question, _citations_for(answers_by_question.get(question.id)))
        for question in golden_set.questions
    ]
    questions_report = [
        {
            "id": result.question_id,
            "status": result.status,
            "correctEdges": list(result.correct_edges),
            "missedEdges": list(result.missed_edges),
            "wrongEdges": list(result.wrong_edges),
            "chunkCitations": result.chunk_citations,
        }
        for result in results
    ]
    summary = aggregate(results)

    regression_groups: list[dict[str, object]] = []
    if regression is not None:
        regression_set = validate_regression(regression)
        baseline_map = (
            _validate_observations(baseline)
            if baseline is not None
            else {"answers": []}
        )
        for group in regression_set.groups:
            grounded, total = grounded_rate(
                _group_answers(observations_map["answers"], group.id)
            )
            baseline_grounded, baseline_total = grounded_rate(
                _group_answers(baseline_map["answers"], group.id)
            )
            group_passed = grounded * baseline_total >= baseline_grounded * total
            regression_groups.append(
                {
                    "id": group.id,
                    "grounded": grounded,
                    "total": total,
                    "baselineGrounded": baseline_grounded,
                    "baselineTotal": baseline_total,
                    "passed": group_passed,
                }
            )

    passed = bool(summary["passed"]) and all(
        bool(group["passed"]) for group in regression_groups
    )

    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "executionMode": observations_map.get("executionMode"),
        "goldenDigest": canonical_digest(golden),
        "observationsDigest": canonical_digest(observations),
        "graphVersion": observations_map.get("graphVersion"),
        "questions": questions_report,
        "summary": summary,
        "regression": {"groups": regression_groups},
        "passed": passed,
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"real mode requires {message}")


def validate_real(
    golden_path: Path,
    golden: object,
    observations: object,
    *,
    regression: object | None,
    baseline: object | None,
) -> None:
    """Fail closed on every real-mode gate before the report is trusted (does I/O)."""
    try:
        validate_golden(golden, require_human=True)
    except ValueError as error:
        raise ValueError(
            f"real mode requires a human-labeled golden set: {error}"
        ) from error

    observations_map = _mapping(observations, "observations")
    _require(
        observations_map.get("executionMode") == "real",
        "observations executionMode 'real'",
    )
    _require(
        observations_map.get("extractionMode") == "model",
        "observations extractionMode 'model'",
    )

    model = observations_map.get("model")
    actual_model = model.get("actual") if isinstance(model, Mapping) else None
    _require(
        isinstance(actual_model, str)
        and not actual_model.startswith(_FAKE_MODEL_PREFIXES),
        "observations model.actual to be a real model, not a placeholder",
    )

    golden_map = _mapping(golden, "golden set")
    _require(
        observations_map.get("goldenDigest") == canonical_digest(golden_map),
        "observations goldenDigest to match the current golden set",
    )

    corpus = _mapping(golden_map.get("corpus"), "golden corpus")
    manifest_relative = corpus.get("manifest")
    if not isinstance(manifest_relative, str) or not manifest_relative:
        raise ValueError(
            "real mode requires golden corpus.manifest to be a nonblank string"
        )
    manifest_path = (golden_path.parent / manifest_relative).resolve()
    try:
        manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_manifest(manifest_raw)
    except (OSError, ValueError) as error:
        raise ValueError(
            f"real mode requires a valid corpus manifest: {error}"
        ) from error
    _require(
        observations_map.get("corpusDigest") == manifest_digest(manifest_raw),
        "observations corpusDigest to match the corpus manifest",
    )

    _require(regression is not None, "--regression")
    _require(baseline is not None, "--baseline-observations")
    baseline_map = _mapping(baseline, "baseline observations")
    _require(
        baseline_map.get("graphVersion") == observations_map.get("graphVersion"),
        "baseline graphVersion to match the observations graphVersion",
    )
    _require(
        baseline_map.get("corpusDigest") == observations_map.get("corpusDigest"),
        "baseline corpusDigest to match the observations corpusDigest",
    )
    _require(
        baseline_map.get("graphReasoning") is False,
        "baseline graphReasoning to be False",
    )


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _evaluator_digest() -> str:
    return "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--regression", type=Path)
    parser.add_argument("--baseline-observations", type=Path)
    parser.add_argument("--real", action="store_true")
    arguments = parser.parse_args()

    try:
        golden = _load_json(arguments.golden)
        observations = _load_json(arguments.observations)
        regression = _load_json(arguments.regression) if arguments.regression else None
        baseline = (
            _load_json(arguments.baseline_observations)
            if arguments.baseline_observations
            else None
        )
        if arguments.real:
            validate_real(
                arguments.golden,
                golden,
                observations,
                regression=regression,
                baseline=baseline,
            )
        report = evaluate(
            golden, observations, regression=regression, baseline=baseline
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    report["evaluatorDigest"] = _evaluator_digest()
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
