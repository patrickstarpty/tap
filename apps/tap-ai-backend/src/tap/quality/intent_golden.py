#!/usr/bin/env python3
"""Golden-case schema and scoring for the `AnswerPlan.agent_intent` six-value
classification (spec 4.2, Decision A). Shared by `scripts/run-intent-golden.py`,
mirroring how `tap.quality.answer_golden` is shared by the answer golden
runner/evaluator -- every function here is pure (no I/O); the script owns
file I/O, the real/fake `ModelGatewayPlanner` call and the CLI.

The fake-mode gate (`fake_gate_passed`) checks that every case was driven
through the real production planning assembly (`AnswerPlanner(
ModelGatewayPlanner(gateway, ...))`, as `scripts/run-intent-golden.py`
assembles it) and ended with a valid `agent_intent`, with zero cases where
the model was attempted and rejected (`plannerSource == "fallback"`), and
`plannerSource == "model"` for every case the production planner's own
pre-model heuristic marks `modelEligible` (derived via `AnswerPlanner.
plan()` called with `model=None`, not a re-implementation of its routing
regexes). A `"heuristic"` `plannerSource` on a case that is *not*
`modelEligible` (the model never attempted -- production's own pre-model
routing decided it) is legitimate, not a wiring defect. This never checks
classification accuracy, which depends on the particular (deterministic or
real) model behind the gateway. The real-mode gate (`real_gate_passed`,
Decision E) is the accuracy check, run later by a human against a real
model.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tap.modules.chat.domain.answer_plan import AGENT_INTENTS

SCHEMA_VERSION = "intent-golden-v1"
OBSERVATIONS_SCHEMA_VERSION = "intent-golden-observations-v1"
REPORT_SCHEMA_VERSION = "intent-golden-report-v1"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_LOCALES = ("zh", "en")
# Every agent intent needs at least this many golden cases, so the real-mode
# per-intent recall gate (Decision E) always has a nonvacuous denominator.
_MIN_CASES_PER_INTENT = 5


# --- Golden set schema -------------------------------------------------------


@dataclass(frozen=True)
class GoldenIntentCase:
    id: str
    question: str
    locale: str
    expected_intent: str


@dataclass(frozen=True)
class IntentGoldenSet:
    schema_version: str
    labeled_by: str
    labeled_at: str
    cases: tuple[GoldenIntentCase, ...]


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


def _case(value: object) -> GoldenIntentCase:
    case = _mapping(value, "golden case")
    case_id = _nonblank_string(case.get("id"), "case id")
    question = _nonblank_string(case.get("question"), f"case {case_id} question")
    locale = case.get("locale")
    if locale not in _LOCALES:
        raise ValueError(f"case {case_id} locale must be 'zh' or 'en'")
    expected_intent = case.get("expectedIntent")
    if expected_intent not in AGENT_INTENTS:
        raise ValueError(f"case {case_id} expectedIntent must be one of {sorted(AGENT_INTENTS)}")
    return GoldenIntentCase(
        id=case_id, question=question, locale=locale, expected_intent=expected_intent
    )


def validate_intent_golden(value: object) -> IntentGoldenSet:
    """Validate an `intent-golden-v1` document; pure, no I/O.

    Raises `ValueError` unless every one of the six `AGENT_INTENTS` values
    has at least `_MIN_CASES_PER_INTENT` cases (Decision E's recall gate
    needs a nonvacuous denominator per intent)."""
    document = _mapping(value, "intent golden set")
    if document.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("unsupported intent golden set schema")
    labeled_by = _nonblank_string(document.get("labeledBy"), "labeledBy")
    labeled_at = document.get("labeledAt")
    if not isinstance(labeled_at, str) or _DATE.fullmatch(labeled_at) is None:
        raise ValueError("labeledAt must match YYYY-MM-DD")
    cases_raw = _array(document.get("cases"), "cases")
    if not cases_raw:
        raise ValueError("intent golden set requires at least one case")

    seen_ids: set[str] = set()
    cases: list[GoldenIntentCase] = []
    for raw in cases_raw:
        case = _case(raw)
        if case.id in seen_ids:
            raise ValueError(f"case id {case.id} is not unique")
        seen_ids.add(case.id)
        cases.append(case)

    counts: dict[str, int] = {intent: 0 for intent in AGENT_INTENTS}
    for case in cases:
        counts[case.expected_intent] += 1
    short = {intent: count for intent, count in counts.items() if count < _MIN_CASES_PER_INTENT}
    if short:
        raise ValueError(
            f"intent golden set requires at least {_MIN_CASES_PER_INTENT} cases per "
            f"agent intent, short: {short}"
        )

    return IntentGoldenSet(
        schema_version=SCHEMA_VERSION,
        labeled_by=labeled_by,
        labeled_at=labeled_at,
        cases=tuple(cases),
    )


# --- Observations schema -----------------------------------------------------


def _array_of_mappings(value: object, name: str) -> list[dict[str, Any]]:
    return [_mapping(item, f"{name} entry") for item in _array(value, name)]


def validate_intent_observations(value: object) -> dict[str, Any]:
    """Validate an `intent-golden-observations-v1` document; pure, no I/O.

    Returns a new dict (never mutates `value`) with `observations` normalized
    to a list of mappings."""
    observations = dict(_mapping(value, "observations"))
    if observations.get("schemaVersion") != OBSERVATIONS_SCHEMA_VERSION:
        raise ValueError("unsupported intent golden observations schema")
    if observations.get("mode") not in ("fake", "real"):
        raise ValueError("observations mode must be 'fake' or 'real'")
    observations["observations"] = _array_of_mappings(
        observations.get("observations"), "observations"
    )
    return observations


def observations_by_id(items: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    by_id: dict[str, Mapping[str, Any]] = {}
    for item in items:
        item_id = item.get("id")
        if isinstance(item_id, str):
            by_id[item_id] = item
    return by_id


# --- Scoring + gates ----------------------------------------------------------


def compute_intent_metrics(
    golden: IntentGoldenSet, observations: Mapping[str, Mapping[str, Any]]
) -> dict[str, float]:
    """Score `observations` (by case id) against `golden`; pure, no I/O.

    Requires every case to have an observation (fails closed instead of
    silently shrinking a metric's denominator). Keys: `accuracy` (overall)
    and `recall.<intent>` for each of the six `AGENT_INTENTS` (1.0 when an
    intent has no golden cases, matching `tap.quality.answer_golden`'s
    vacuous-metric convention -- `validate_intent_golden` makes that
    unreachable for a validated golden set, but `compute_intent_metrics` is
    usable standalone against handmade data in tests)."""
    missing = [case.id for case in golden.cases if case.id not in observations]
    if missing:
        raise ValueError(f"observations are missing cases: {missing}")

    correct = 0
    per_intent_hits: dict[str, int] = {intent: 0 for intent in AGENT_INTENTS}
    per_intent_total: dict[str, int] = {intent: 0 for intent in AGENT_INTENTS}

    for case in golden.cases:
        observation = observations[case.id]
        observed_intent = observation.get("agentIntent")
        per_intent_total[case.expected_intent] += 1
        if observed_intent == case.expected_intent:
            correct += 1
            per_intent_hits[case.expected_intent] += 1

    metrics: dict[str, float] = {
        "accuracy": (correct / len(golden.cases)) if golden.cases else 1.0,
    }
    for intent in AGENT_INTENTS:
        total = per_intent_total[intent]
        metrics[f"recall.{intent}"] = (per_intent_hits[intent] / total) if total else 1.0
    return metrics


def fake_gate_passed(
    golden: IntentGoldenSet, observations: Mapping[str, Mapping[str, Any]]
) -> bool:
    """Fake-mode gate: full case coverage, zero `"fallback"` observations
    (the model was never attempted-and-rejected), every observed
    `agentIntent` is a member of `AGENT_INTENTS`, and `plannerSource ==
    "model"` for every case marked `modelEligible` (see below).

    Fix round 1 (I-2): a `"heuristic"` `plannerSource` is legitimate, not a
    wiring defect by itself -- `AnswerPlanner.plan()` only attempts a model
    call when its own pre-model heuristics already decided the route is
    `retrieve`/`graph` and the question is not ambiguous (see
    `scripts/run-intent-golden.py`'s module docstring). Requiring
    `plannerSource == "model"` for every case would reject cases production
    itself never sends to the model (e.g. an ambiguous-reference question
    that routes to `clarify` before any model call is attempted).

    Fix round 2 (gap): round 1 reasoned that `plannerSource` could only be
    `"heuristic"` when the model was genuinely never invoked, on the
    assumption that *invoked* and *eligible* always coincide -- but a
    regression where `AnswerPlanner.plan()` silently stopped attempting a
    model call for an eligible question (e.g. its model dependency
    accidentally became `None`) would show up as an ordinary, accepted
    `"heuristic"` observation, not a `"fallback"`, and this gate would not
    notice. `scripts/run-intent-golden.py` now records `modelEligible`
    independently of `plannerSource` -- derived from the real
    `AnswerPlanner.plan()` called with `model=None` (its own pre-model
    heuristic, never a re-implementation of the routing regexes) -- and
    this gate cross-checks the two: every `modelEligible` case must show
    `plannerSource == "model"`, closing exactly that gap.

    This only checks wiring -- it says nothing about classification
    accuracy, which is the real-mode gate's job."""
    for case in golden.cases:
        observation = observations.get(case.id)
        if observation is None:
            return False
        source = observation.get("plannerSource")
        if source not in ("model", "heuristic"):
            return False
        if observation.get("agentIntent") not in AGENT_INTENTS:
            return False
        if observation.get("modelEligible") and source != "model":
            return False
    return True


def real_gate_passed(metrics: Mapping[str, float]) -> bool:
    """Real-mode gate (Decision E): overall accuracy >= 0.8 and every
    per-intent recall >= 0.6; pure, no I/O."""
    if metrics.get("accuracy", 0.0) < 0.8:
        return False
    for intent in AGENT_INTENTS:
        if metrics.get(f"recall.{intent}", 0.0) < 0.6:
            return False
    return True
