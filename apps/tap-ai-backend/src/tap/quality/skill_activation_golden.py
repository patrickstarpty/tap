"""Golden-case schema and scoring for Skill auto-activation precision (Skill
Hub spec 7): a small set of questions, each judged against every candidate
Skill in the fixture catalog. Shared by `scripts/run-skill-activation-
golden.py`, mirroring `tap.quality.intent_golden` -- every function here is
pure (no I/O); the script owns file I/O, the real-model call and the CLI.

Each (case, candidate Skill) pair is one judgement:

- expected and activated: should-hit
- expected, not activated: should-miss
- not expected, not activated: should-not-hit
- not expected, activated: should-not-miss

An observed name that is not a fixture Skill is an extra should-not-miss
(a wrong activation). Observations are the names the model passed to
`skill_activate` in its first tool round; this module scores whatever names
it is given. The gate requires both rates to be at least 0.80.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tap.modules.ai.domain.skill_activation import CATALOG_DESCRIPTION_CHARS
from tap.modules.ai.domain.skill_metadata import SKILL_CATEGORIES

SCHEMA_VERSION = "skill-activation-golden-v1"
OBSERVATIONS_SCHEMA_VERSION = "skill-activation-observations-v1"
REPORT_SCHEMA_VERSION = "skill-activation-report-v1"
GATE_THRESHOLD = 0.80
# `KnowledgeModelGateway.answer` accepts 1-20 evidence items per call.
_MAX_EVIDENCE = 20


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
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


def _validate_skills(value: object) -> frozenset[str]:
    skills = _array(value, "skills")
    if not skills:
        raise ValueError("skills requires at least one skill")
    names: set[str] = set()
    for raw in skills:
        skill = _mapping(raw, "skill")
        name = _nonblank_string(skill.get("name"), "skill name")
        if name in names:
            raise ValueError(f"skill name {name} is not unique")
        names.add(name)
        description = _nonblank_string(skill.get("description"), f"skill {name} description")
        if len(" ".join(description.split())) > CATALOG_DESCRIPTION_CHARS:
            raise ValueError(
                f"skill {name} description exceeds {CATALOG_DESCRIPTION_CHARS} characters"
            )
        if skill.get("category") not in SKILL_CATEGORIES:
            raise ValueError(f"skill {name} category must be one of {list(SKILL_CATEGORIES)}")
    return frozenset(names)


def _validate_case(value: object, skill_names: frozenset[str]) -> str:
    case = _mapping(value, "case")
    case_id = _nonblank_string(case.get("id"), "case id")
    _nonblank_string(case.get("question"), f"case {case_id} question")
    evidence = _array(case.get("evidence"), f"case {case_id} evidence")
    if not 1 <= len(evidence) <= _MAX_EVIDENCE:
        raise ValueError(f"case {case_id} evidence requires 1-{_MAX_EVIDENCE} passages")
    for passage in evidence:
        _nonblank_string(passage, f"case {case_id} evidence passage")
    expected = _array(case.get("expected"), f"case {case_id} expected")
    if len(set(expected)) != len(expected):
        raise ValueError(f"case {case_id} expected names are not unique")
    for name in expected:
        if name not in skill_names:
            raise ValueError(f"case {case_id} expects unknown skill {name!r}")
    return case_id


def validate_activation_golden(data: object) -> None:
    """Validate a `skill-activation-golden-v1` document; raises `ValueError`."""
    document = _mapping(data, "skill activation golden set")
    if document.get("version") != SCHEMA_VERSION:
        raise ValueError(f"version must be {SCHEMA_VERSION!r}")
    skill_names = _validate_skills(document.get("skills"))
    cases = _array(document.get("cases"), "cases")
    if not cases:
        raise ValueError("cases requires at least one case")
    seen: set[str] = set()
    for raw in cases:
        case_id = _validate_case(raw, skill_names)
        if case_id in seen:
            raise ValueError(f"case id {case_id} is not unique")
        seen.add(case_id)


def validate_activation_observations(data: object) -> dict[str, frozenset[str]]:
    """Validate a `skill-activation-observations-v1` document and return its
    activated names by case id; raises `ValueError`."""
    document = _mapping(data, "skill activation observations")
    if document.get("version") != OBSERVATIONS_SCHEMA_VERSION:
        raise ValueError(f"version must be {OBSERVATIONS_SCHEMA_VERSION!r}")
    raw = _mapping(document.get("observations"), "observations")
    observations: dict[str, frozenset[str]] = {}
    for case_id, names in raw.items():
        observations[str(case_id)] = frozenset(
            _nonblank_string(name, f"observation {case_id} name")
            for name in _array(names, f"observation {case_id}")
        )
    return observations


@dataclass(frozen=True, slots=True)
class ActivationPair:
    """One (case, candidate Skill) judgement."""

    case_id: str
    skill_name: str
    expected: bool
    activated: bool

    @property
    def hit(self) -> bool:
        return self.expected == self.activated


@dataclass(frozen=True, slots=True)
class ActivationMetrics:
    should_rate: float
    should_not_rate: float
    pairs: tuple[ActivationPair, ...]


def _rate(pairs: list[ActivationPair]) -> float:
    return sum(pair.hit for pair in pairs) / len(pairs) if pairs else 1.0


def compute_activation_metrics(
    golden: Mapping[str, Any], observations: Mapping[str, frozenset[str]]
) -> ActivationMetrics:
    """Score `observations` (activated names by case id) against a validated
    `golden` set. Every case needs an observation (fails closed rather than
    shrinking a denominator); a rate with no pairs is vacuously 1.0."""
    skill_names = [str(skill["name"]) for skill in golden["skills"]]
    known = frozenset(skill_names)
    missing = [case["id"] for case in golden["cases"] if case["id"] not in observations]
    if missing:
        raise ValueError(f"observations are missing cases: {missing}")

    pairs: list[ActivationPair] = []
    for case in golden["cases"]:
        case_id = str(case["id"])
        expected = frozenset(case["expected"])
        activated = observations[case_id]
        for name in skill_names:
            pairs.append(ActivationPair(case_id, name, name in expected, name in activated))
        for name in sorted(activated - known):
            pairs.append(ActivationPair(case_id, name, expected=False, activated=True))

    return ActivationMetrics(
        should_rate=_rate([pair for pair in pairs if pair.expected]),
        should_not_rate=_rate([pair for pair in pairs if not pair.expected]),
        pairs=tuple(pairs),
    )


def gate_passed(metrics: ActivationMetrics) -> bool:
    """Both rates at least `GATE_THRESHOLD`."""
    return metrics.should_rate >= GATE_THRESHOLD and metrics.should_not_rate >= GATE_THRESHOLD
