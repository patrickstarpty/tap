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
    RegressionGroup,
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


def _group_rate(
    answers: Sequence[Mapping[str, Any]], group: RegressionGroup
) -> tuple[int, int]:
    """Score a regression group over its *declared* questions, not just the observed ones.

    A question the regression document declares but that is absent from `answers` counts
    as not grounded (fail closed) instead of shrinking the denominator — the total is
    always `len(group.questions)`. An answer tagged with this group's id but naming a
    question the regression document never declared is rejected outright: the regression
    document, not the observations file, is the source of truth for group membership.
    """
    declared = group.questions
    declared_ids = frozenset(declared)
    present: dict[str, Mapping[str, Any]] = {}
    for answer in answers:
        if answer.get("group") != group.id:
            continue
        question_id = answer.get("questionId")
        if not isinstance(question_id, str) or question_id not in declared_ids:
            raise ValueError(
                f"regression group {group.id} observed an undeclared question id "
                f"{question_id!r}"
            )
        present[question_id] = answer
    grounded, _ = grounded_rate(
        [present[question_id] for question_id in declared if question_id in present]
    )
    return grounded, len(declared)


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
            grounded, total = _group_rate(observations_map["answers"], group)
            baseline_grounded, baseline_total = _group_rate(
                baseline_map["answers"], group
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


def _answered_question_ids(answers: object, group_id: str) -> set[str]:
    """Question ids an observations/baseline document's `answers` actually names
    for `group_id`, regardless of whether those answers are well-formed -- used
    only to check *presence*, not to score anything."""
    ids: set[str] = set()
    if not isinstance(answers, Sequence) or isinstance(answers, (str, bytes)):
        return ids
    for answer in answers:
        if not isinstance(answer, Mapping) or answer.get("group") != group_id:
            continue
        question_id = answer.get("questionId")
        if isinstance(question_id, str):
            ids.add(question_id)
    return ids


def _require_group_answered(
    answers: object, group_id: str, declared: Sequence[str], *, document_name: str
) -> None:
    """I1: a partial baseline (or observations) silently shrinks `_group_rate`'s
    denominator to only the answers actually present, making the regression check
    vacuous -- a baseline missing every answer for a group always "passes" no matter
    what the candidate observations do. Require every question the regression
    document declares for this group to have an answer on file instead."""
    missing = sorted(set(declared) - _answered_question_ids(answers, group_id))
    if missing:
        raise ValueError(
            f"real mode requires {document_name} to contain an answer for every "
            f"declared question in regression group {group_id}: missing {missing}"
        )


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
        and bool(actual_model)
        and not actual_model.startswith(_FAKE_MODEL_PREFIXES),
        "observations model.actual to be a nonblank real model, not a placeholder",
    )

    golden_map = _mapping(golden, "golden set")
    _require(
        observations_map.get("goldenDigest") == canonical_digest(golden_map),
        "observations goldenDigest to match the current golden set",
    )

    # M2: a baseline accidentally passed as --observations (e.g. a copy-paste mistake)
    # must be rejected, not silently scored as the candidate run.
    _require(
        observations_map.get("graphVersion") is not None,
        "observations graphVersion to be non-null",
    )
    _require(
        observations_map.get("graphReasoning") is True,
        "observations graphReasoning to be True (reject a baseline passed as "
        "--observations)",
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
        manifest_entries = validate_manifest(manifest_raw)
    except (OSError, ValueError) as error:
        raise ValueError(
            f"real mode requires a valid corpus manifest: {error}"
        ) from error

    # I2: bind the golden set to this exact manifest content, and re-validate it with
    # the manifest's entry ids so an expectedSources typo (or a stale, unrelated
    # manifest) cannot slip past the digest match above.
    _require(
        corpus.get("digest") == manifest_digest(manifest_raw),
        "golden corpus.digest to match the corpus manifest",
    )
    corpus_ids = frozenset(entry.id for entry in manifest_entries)
    try:
        validate_golden(golden, require_human=True, corpus_ids=corpus_ids)
    except ValueError as error:
        raise ValueError(
            f"real mode requires golden expectedSources to be manifest entry ids: "
            f"{error}"
        ) from error

    _require(
        observations_map.get("corpusDigest") == manifest_digest(manifest_raw),
        "observations corpusDigest to match the corpus manifest",
    )

    _require(regression is not None, "--regression")
    _require(baseline is not None, "--baseline-observations")
    try:
        regression_set = validate_regression(regression)
    except ValueError as error:
        raise ValueError(
            f"real mode requires a valid regression document: {error}"
        ) from error
    _require(
        regression_set.complete,
        "a complete regression set (every group's declared questions filled in)",
    )
    baseline_map = _mapping(baseline, "baseline observations")
    _require(
        baseline_map.get("graphVersion") is not None,
        "baseline graphVersion to be non-null",
    )
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
    _require(
        baseline_map.get("executionMode") == "real",
        "baseline executionMode 'real'",
    )
    _require(
        baseline_map.get("extractionMode") == "model",
        "baseline extractionMode 'model'",
    )
    baseline_model = baseline_map.get("model")
    baseline_alias = (
        baseline_model.get("alias") if isinstance(baseline_model, Mapping) else None
    )
    observations_model = observations_map.get("model")
    observations_alias = (
        observations_model.get("alias")
        if isinstance(observations_model, Mapping)
        else None
    )
    _require(
        baseline_alias == observations_alias,
        "baseline model.alias to match the observations model.alias",
    )
    _require(
        baseline_map.get("goldenDigest") == observations_map.get("goldenDigest"),
        "baseline goldenDigest to match the observations goldenDigest",
    )

    # I1: fail closed on a partial baseline (or partial observations) instead of
    # letting `_group_rate`'s declared-question denominator quietly shrink.
    for group in regression_set.groups:
        _require_group_answered(
            baseline_map.get("answers"),
            group.id,
            group.questions,
            document_name="baseline observations",
        )
        _require_group_answered(
            observations_map.get("answers"),
            group.id,
            group.questions,
            document_name="observations",
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
        if (arguments.regression is None) != (arguments.baseline_observations is None):
            raise ValueError(
                "--regression and --baseline-observations must be provided together"
            )
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
    except (ValueError, OSError) as error:
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
