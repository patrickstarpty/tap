"""Score the Skill activation precision golden set (spec 7) with the pure
functions in `tap.quality.skill_activation_golden`: every case is judged
against every candidate Skill, split into should-activate and
should-not-activate pairs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tap.modules.ai.domain.skill_metadata import SKILL_CATEGORIES
from tap.quality.skill_activation_golden import (
    ActivationMetrics,
    compute_activation_metrics,
    gate_passed,
    no_skill_cases_with_any_activation,
    validate_activation_golden,
)

ROOT = Path(__file__).resolve().parents[5]
FIXTURE_DIR = ROOT / "apps/tap-ai-backend/tests/fixtures/quality/skill-activation"


def _skill(name: str) -> dict[str, str]:
    return {"name": name, "description": f"Handles {name} requests.", "category": "other"}


def _case(case_id: str, expected: list[str]) -> dict[str, Any]:
    return {
        "id": case_id,
        "question": f"Question {case_id}?",
        "evidence": [f"Evidence for {case_id}."],
        "expected": expected,
    }


def _golden(skills: list[str], cases: list[tuple[str, list[str]]]) -> dict[str, Any]:
    document = {
        "version": "skill-activation-golden-v1",
        "skills": [_skill(name) for name in skills],
        "cases": [_case(case_id, expected) for case_id, expected in cases],
    }
    validate_activation_golden(document)
    return document


def test_golden_has_ten_cases_and_valid_skill_names() -> None:
    golden = json.loads((FIXTURE_DIR / "golden-v1.json").read_text(encoding="utf-8"))

    validate_activation_golden(golden)

    assert golden["version"] == "skill-activation-golden-v1"
    assert len(golden["cases"]) == 10
    names = {skill["name"] for skill in golden["skills"]}
    assert len(names) == len(golden["skills"])
    assert all(skill["category"] in SKILL_CATEGORIES for skill in golden["skills"])
    assert all(set(case["expected"]) <= names for case in golden["cases"])
    # Both rates need a nonvacuous denominator: some cases must activate a
    # Skill and some must activate none.
    assert any(case["expected"] for case in golden["cases"])
    assert any(not case["expected"] for case in golden["cases"])

    observations = json.loads(
        (FIXTURE_DIR / "fixture-observations-v1.json").read_text(encoding="utf-8")
    )
    assert observations["version"] == "skill-activation-observations-v1"
    metrics = compute_activation_metrics(
        golden,
        {case_id: frozenset(names) for case_id, names in observations["observations"].items()},
    )
    assert gate_passed(metrics)


def test_metrics_count_should_and_should_not_pairs() -> None:
    # Two Skills, ten cases, each expecting exactly one Skill: ten
    # should-activate pairs and ten should-not-activate pairs.
    golden = _golden(
        ["alpha", "beta"],
        [(f"c{index}", ["alpha" if index <= 5 else "beta"]) for index in range(1, 11)],
    )
    observations = {
        f"c{index}": frozenset({"alpha" if index <= 5 else "beta"}) for index in range(1, 9)
    }
    observations["c9"] = frozenset()
    observations["c10"] = frozenset()
    # One wrong activation alongside a correct one.
    observations["c1"] = frozenset({"alpha", "beta"})

    metrics = compute_activation_metrics(golden, observations)

    assert metrics.should_rate == 0.8
    assert metrics.should_not_rate == 0.9
    assert len(metrics.pairs) == 20
    assert sum(pair.expected for pair in metrics.pairs) == 10
    wrong = [pair for pair in metrics.pairs if pair.activated and not pair.expected]
    assert [(pair.case_id, pair.skill_name) for pair in wrong] == [("c1", "beta")]


def test_gate_requires_both_rates_at_least_080() -> None:
    assert gate_passed(ActivationMetrics(should_rate=0.8, should_not_rate=0.8, pairs=()))
    assert not gate_passed(ActivationMetrics(should_rate=0.79, should_not_rate=1.0, pairs=()))
    assert not gate_passed(ActivationMetrics(should_rate=1.0, should_not_rate=0.79, pairs=()))


def test_unknown_skill_name_in_observation_counts_as_wrong_activation() -> None:
    golden = _golden(["alpha"], [("c1", ["alpha"]), ("c2", [])])

    metrics = compute_activation_metrics(
        golden, {"c1": frozenset({"alpha"}), "c2": frozenset({"made-up-skill"})}
    )

    assert metrics.should_rate == 1.0
    # (c2, alpha) is a correct non-activation; (c2, made-up-skill) is an
    # extra wrong activation.
    assert metrics.should_not_rate == 0.5
    unknown = [pair for pair in metrics.pairs if pair.skill_name == "made-up-skill"]
    assert len(unknown) == 1
    assert unknown[0].case_id == "c2"
    assert unknown[0].activated and not unknown[0].expected


def test_no_skill_cases_with_any_activation_counts_cases_not_pairs() -> None:
    golden = _golden(["alpha", "beta"], [("c1", ["alpha"]), ("c2", []), ("c3", []), ("c4", [])])

    metrics = compute_activation_metrics(
        golden,
        {
            # Not a no-skill case: its extra activation is not counted here.
            "c1": frozenset({"alpha", "beta"}),
            "c2": frozenset({"alpha", "beta"}),
            "c3": frozenset(),
            "c4": frozenset({"made-up-skill"}),
        },
    )

    assert no_skill_cases_with_any_activation(metrics) == (2, 3)
