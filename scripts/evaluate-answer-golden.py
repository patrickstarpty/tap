#!/usr/bin/env python3
"""Evaluate answer-golden observations against the capability-1 answer golden set.

A thin CLI wraps the pure scoring functions in `tap.quality.answer_golden`,
mirroring `scripts/evaluate-graph-relations.py`: nothing in that module touches
the filesystem, `main()` does the I/O (reading files, writing the report).

Metrics (see `tap.quality.answer_golden.compute_metrics`):
- `citationSourcePrecision`: of the sources cited across `answerable` cases,
  the fraction that are in that case's `expectedSources`. An `invalid`
  observation (a cited source was not ready, or the real pipeline returned an
  unusable answer) counts against this metric's denominator with zero credit.
- `expectedSourceRecall`: of each `answerable` case's `expectedSources`, the
  fraction actually cited.
- `factRecall`: of each case's `expectedFacts` groups (a group is one phrase,
  or a list of alternative phrasings -- any one matching counts), the
  fraction present in the observed answer text after normalizing whitespace,
  full-width ASCII/digits/percent and markdown escapes on both sides;
  averaged over cases that declare any.
- `abstainAccuracy`: fraction of `abstain` cases the observation legitimately
  abstained on (`invalid` observations never count as abstentions).
- `falsePremiseHandled`: fraction of `false_premise` cases where the
  observation either legitimately abstained or stated one of its
  `expectedFacts` correction phrasings.
- `forbiddenFactRate`: of cases that declare `forbiddenFacts`, the fraction
  whose answer text contains one of them (lower is better).
- `invalidCount`: how many observations were `invalid` (not a scored metric
  on its own, but gates the real-mode comparison below).

Fake-mode gate (`evaluate()`'s `passed`, no `--real`), via
`tap.quality.answer_golden.fake_gate_passed`: full case coverage,
`citationSourcePrecision == 1.0`, `expectedSourceRecall == 1.0`, and every
`answerable` case produced at least one claim. This says nothing about answer
quality -- fake mode's scoped-retrieval wiring guarantees citations never
leave `expectedSources`; it only checks the pipeline and evaluator plumbing
end to end, and catches broken wiring (e.g. after an answer-format switch)
that a vacuous "no citations observed" `citationSourcePrecision == 1.0` would
otherwise miss.

Real-mode gate (`--real` with `--baseline-observations`), via
`tap.quality.answer_golden.gate_passed`: every metric must be no worse than
the baseline (`forbiddenFactRate` must be no higher, and `invalidCount` must
be no higher).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from tap.quality.answer_golden import (
    REPORT_SCHEMA_VERSION,
    compute_metrics,
    fake_gate_passed,
    gate_passed,
    observations_by_id,
    validate_golden,
    validate_observations,
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


def evaluate(
    golden: object, observations: object, *, baseline: object | None = None
) -> dict[str, object]:
    """Score `observations` against `golden`, returning the report (pure, no I/O)."""
    golden_set = validate_golden(golden)
    observations_map = validate_observations(observations)
    by_id = observations_by_id(observations_map["observations"])
    metrics = compute_metrics(golden_set, by_id)

    passed: bool
    baseline_metrics: dict[str, float] | None = None
    if baseline is None:
        passed = fake_gate_passed(golden_set, by_id, metrics)
    else:
        baseline_map = validate_observations(baseline)
        baseline_metrics = compute_metrics(
            golden_set, observations_by_id(baseline_map["observations"])
        )
        passed = gate_passed(metrics, baseline_metrics)

    report: dict[str, object] = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "mode": observations_map.get("mode"),
        "build": observations_map.get("build"),
        "dirty": observations_map.get("dirty"),
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
    if not isinstance(observations, dict):
        raise ValueError("observations must be an object")
    if observations.get("mode") != "real":
        raise ValueError("real mode requires observations mode 'real'")
    if baseline is None:
        raise ValueError("real mode requires --baseline-observations")
    if not isinstance(baseline, dict):
        raise ValueError("baseline observations must be an object")
    if baseline.get("mode") != "real":
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
