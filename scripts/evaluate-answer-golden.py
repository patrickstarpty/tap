#!/usr/bin/env python3
"""Evaluate answer-golden observations against the capability-1 answer golden set.

A thin CLI wraps the pure `evaluate()` function, mirroring
`scripts/evaluate-graph-relations.py`: `evaluate()` never touches the
filesystem, `main()` does the I/O (reading files, writing the report).

Metrics (see `evaluate()`):
- `citationSourcePrecision`: of the sources cited across `answerable` cases,
  the fraction that are in that case's `expectedSources`.
- `expectedSourceRecall`: of each `answerable` case's `expectedSources`, the
  fraction actually cited.
- `factRecall`: of each case's `expectedFacts` phrases, the fraction present
  verbatim in the observed answer text, averaged over cases that declare any.
- `abstainAccuracy`: fraction of `abstain` cases the observation abstained on.
- `falsePremiseHandled`: fraction of `false_premise` cases where the
  observation either abstained or stated one of its `expectedFacts`
  correction phrases.
- `forbiddenFactRate`: of cases that declare `forbiddenFacts`, the fraction
  whose answer text contains one of them (lower is better).

Fake-mode gate (`evaluate()`'s `passed`, no `--real`): every golden case has
an observation and `citationSourcePrecision == 1.0`. This says nothing about
answer quality -- fake mode's scoped-retrieval wiring guarantees citations
never leave `expectedSources`; it only checks the pipeline and evaluator
plumbing end to end.

Real-mode gate (`--real` with `--baseline-observations`): every metric must
be no worse than the baseline (`forbiddenFactRate` must be no higher), via
`gate_passed()`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPORT_SCHEMA_VERSION = "answer-golden-report-v1"
_KINDS = ("answerable", "abstain", "false_premise")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_GATED_METRICS = (
    "citationSourcePrecision",
    "expectedSourceRecall",
    "factRecall",
    "abstainAccuracy",
    "falsePremiseHandled",
)


# --- Golden set schema (duplicated from run-answer-golden.py; each script
# stays a self-contained thin CLI over its own pure functions) -------------


@dataclass(frozen=True)
class GoldenCase:
    id: str
    question: str
    locale: str
    kind: str
    expected_sources: tuple[str, ...]
    expected_facts: tuple[str, ...]
    forbidden_facts: tuple[str, ...]


@dataclass(frozen=True)
class GoldenSet:
    schema_version: str
    labeled_by: str
    labeled_at: str
    cases: tuple[GoldenCase, ...]


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _array(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _nonblank_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value


def _string_array(value: object, name: str) -> tuple[str, ...]:
    return tuple(_nonblank_string(item, f"{name} item") for item in _array(value, name))


def _case(value: object) -> GoldenCase:
    case = _mapping(value, "golden case")
    case_id = _nonblank_string(case.get("id"), "case id")
    question = _nonblank_string(case.get("question"), f"case {case_id} question")
    locale = case.get("locale")
    if locale not in ("zh", "en"):
        raise ValueError(f"case {case_id} locale must be 'zh' or 'en'")
    kind = case.get("kind")
    if kind not in _KINDS:
        raise ValueError(f"case {case_id} kind must be one of {_KINDS}")
    expected_sources = _string_array(
        case.get("expectedSources"), f"case {case_id} expectedSources"
    )
    if not expected_sources:
        raise ValueError(f"case {case_id} requires at least one expected source")
    expected_facts = (
        _string_array(case.get("expectedFacts"), f"case {case_id} expectedFacts")
        if "expectedFacts" in case
        else ()
    )
    forbidden_facts = (
        _string_array(case.get("forbiddenFacts"), f"case {case_id} forbiddenFacts")
        if "forbiddenFacts" in case
        else ()
    )
    return GoldenCase(
        id=case_id,
        question=question,
        locale=locale,
        kind=kind,
        expected_sources=expected_sources,
        expected_facts=expected_facts,
        forbidden_facts=forbidden_facts,
    )


def validate_golden(value: object) -> GoldenSet:
    """Validate an `answer-golden-v1` document; pure, no I/O."""
    document = _mapping(value, "golden set")
    if document.get("schemaVersion") != "answer-golden-v1":
        raise ValueError("unsupported answer golden set schema")
    labeled_by = _nonblank_string(document.get("labeledBy"), "labeledBy")
    labeled_at = document.get("labeledAt")
    if not isinstance(labeled_at, str) or _DATE.fullmatch(labeled_at) is None:
        raise ValueError("labeledAt must match YYYY-MM-DD")
    cases_raw = _array(document.get("cases"), "cases")
    if not cases_raw:
        raise ValueError("golden set requires at least one case")
    seen_ids: set[str] = set()
    cases: list[GoldenCase] = []
    for raw in cases_raw:
        case = _case(raw)
        if case.id in seen_ids:
            raise ValueError(f"case id {case.id} is not unique")
        seen_ids.add(case.id)
        cases.append(case)
    return GoldenSet(
        schema_version="answer-golden-v1",
        labeled_by=labeled_by,
        labeled_at=labeled_at,
        cases=tuple(cases),
    )


def _canonical_digest(value: object) -> str:
    material = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "sha256:" + hashlib.sha256(material).hexdigest()


def _validate_observations(value: object) -> dict[str, Any]:
    observations = _mapping(value, "observations")
    if observations.get("schemaVersion") != "answer-golden-observations-v1":
        raise ValueError("unsupported answer golden observations schema")
    if observations.get("mode") not in ("fake", "real"):
        raise ValueError("observations mode must be 'fake' or 'real'")
    items = [
        _mapping(item, "observation entry")
        for item in _array(observations.get("observations"), "observations")
    ]
    observations["observations"] = items
    return observations


def _by_id(items: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    by_id: dict[str, Mapping[str, Any]] = {}
    for item in items:
        item_id = item.get("id")
        if isinstance(item_id, str):
            by_id[item_id] = item
    return by_id


def compute_metrics(
    golden_set: GoldenSet, observations_by_id: Mapping[str, Mapping[str, Any]]
) -> dict[str, float]:
    """Score `observations_by_id` against `golden_set`; pure, no I/O.

    Requires every case to have an observation (fails closed instead of
    silently shrinking a metric's denominator)."""
    missing = [
        case.id for case in golden_set.cases if case.id not in observations_by_id
    ]
    if missing:
        raise ValueError(f"observations are missing cases: {missing}")

    cite_hits = cite_total = 0
    recall_hits = recall_total = 0
    fact_rates: list[float] = []
    abstain_hits = abstain_total = 0
    fp_hits = fp_total = 0
    forbidden_hits = forbidden_total = 0

    for case in golden_set.cases:
        observation = observations_by_id[case.id]
        answer_text = str(observation.get("answer") or "")
        abstained = bool(observation.get("abstained"))
        cited_sources = [
            source
            for source in observation.get("citedSources") or ()
            if isinstance(source, str)
        ]

        if case.kind == "answerable":
            if cited_sources:
                cite_total += len(cited_sources)
                cite_hits += sum(
                    1 for source in cited_sources if source in case.expected_sources
                )
            recall_total += len(case.expected_sources)
            recall_hits += sum(
                1 for source in case.expected_sources if source in cited_sources
            )

        if case.expected_facts:
            hits = sum(1 for fact in case.expected_facts if fact in answer_text)
            fact_rates.append(hits / len(case.expected_facts))

        if case.kind == "abstain":
            abstain_total += 1
            if abstained:
                abstain_hits += 1

        if case.kind == "false_premise":
            fp_total += 1
            corrected = any(fact in answer_text for fact in case.expected_facts)
            if abstained or corrected:
                fp_hits += 1

        if case.forbidden_facts:
            forbidden_total += 1
            if any(fact in answer_text for fact in case.forbidden_facts):
                forbidden_hits += 1

    return {
        "citationSourcePrecision": (cite_hits / cite_total) if cite_total else 1.0,
        "expectedSourceRecall": (recall_hits / recall_total) if recall_total else 1.0,
        "factRecall": (sum(fact_rates) / len(fact_rates)) if fact_rates else 1.0,
        "abstainAccuracy": (abstain_hits / abstain_total) if abstain_total else 1.0,
        "falsePremiseHandled": (fp_hits / fp_total) if fp_total else 1.0,
        "forbiddenFactRate": (forbidden_hits / forbidden_total)
        if forbidden_total
        else 0.0,
    }


def gate_passed(metrics: Mapping[str, float], baseline: Mapping[str, float]) -> bool:
    """Real-mode regression gate: every metric at least matches the baseline,
    except `forbiddenFactRate`, which must not exceed it; pure, no I/O."""
    for name in _GATED_METRICS:
        if metrics[name] < baseline[name]:
            return False
    return metrics["forbiddenFactRate"] <= baseline["forbiddenFactRate"]


def evaluate(
    golden: object, observations: object, *, baseline: object | None = None
) -> dict[str, object]:
    """Score `observations` against `golden`, returning the report (pure, no I/O)."""
    golden_set = validate_golden(golden)
    observations_map = _validate_observations(observations)
    observations_by_id = _by_id(observations_map["observations"])
    metrics = compute_metrics(golden_set, observations_by_id)

    passed: bool
    baseline_metrics: dict[str, float] | None = None
    if baseline is None:
        passed = metrics["citationSourcePrecision"] == 1.0
    else:
        baseline_map = _validate_observations(baseline)
        baseline_metrics = compute_metrics(
            golden_set, _by_id(baseline_map["observations"])
        )
        passed = gate_passed(metrics, baseline_metrics)

    report: dict[str, object] = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "mode": observations_map.get("mode"),
        "build": observations_map.get("build"),
        "goldenDigest": _canonical_digest(golden),
        "observationsDigest": _canonical_digest(observations),
        "metrics": metrics,
        "passed": passed,
    }
    if baseline_metrics is not None:
        report["baselineMetrics"] = baseline_metrics
    return report


def validate_real(observations: object, *, baseline: object | None) -> None:
    """Fail closed on real-mode gate preconditions before the report is trusted."""
    observations_map = _mapping(observations, "observations")
    if observations_map.get("mode") != "real":
        raise ValueError("real mode requires observations mode 'real'")
    if baseline is None:
        raise ValueError("real mode requires --baseline-observations")
    baseline_map = _mapping(baseline, "baseline observations")
    if baseline_map.get("mode") != "real":
        raise ValueError("real mode requires baseline observations mode 'real'")


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline-observations", type=Path)
    parser.add_argument("--real", action="store_true")
    arguments = parser.parse_args()

    try:
        golden = _load_json(arguments.golden)
        observations = _load_json(arguments.observations)
        baseline = (
            _load_json(arguments.baseline_observations)
            if arguments.baseline_observations is not None
            else None
        )
        if arguments.real:
            validate_real(observations, baseline=baseline)
        report = evaluate(golden, observations, baseline=baseline)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
