#!/usr/bin/env python3
"""Golden-case schema, fact matching and scoring for the capability-1 answer
golden set. Shared by `scripts/run-answer-golden.py` and
`scripts/evaluate-answer-golden.py`, mirroring how `tap.quality.graph_relations`
is shared by the graph-relations runner and evaluator -- every function here is
pure (no I/O); the scripts own file I/O, the real chat pipeline and the CLIs.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "answer-golden-v1"
OBSERVATIONS_SCHEMA_VERSION = "answer-golden-observations-v1"
REPORT_SCHEMA_VERSION = "answer-golden-report-v1"
KINDS = ("answerable", "abstain", "false_premise")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_MARKDOWN_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>~|%])")

# Metrics required to be at least as good as the baseline in the real-mode gate
# (`forbiddenFactRate` is handled separately: it must not be *higher*).
GATED_METRICS = (
    "citationSourcePrecision",
    "expectedSourceRecall",
    "factRecall",
    "abstainAccuracy",
    "falsePremiseHandled",
)


# --- Golden set schema ------------------------------------------------------


@dataclass(frozen=True)
class GoldenCase:
    id: str
    question: str
    locale: str
    kind: str
    expected_sources: tuple[str, ...]
    # Each entry is a group of alternative phrasings; a case's fact (or
    # forbidden fact) is "present" if any phrasing in its group matches.
    expected_facts: tuple[tuple[str, ...], ...]
    forbidden_facts: tuple[tuple[str, ...], ...]


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


def _fact_group(value: object, name: str) -> tuple[str, ...]:
    """A fact entry is either a single phrase or a list of alternative phrasings."""
    if isinstance(value, str):
        return (_nonblank_string(value, name),)
    return _string_array(value, name)


def _fact_groups(value: object, name: str) -> tuple[tuple[str, ...], ...]:
    groups: list[tuple[str, ...]] = []
    for index, entry in enumerate(_array(value, name)):
        group = _fact_group(entry, f"{name}[{index}]")
        if not group:
            raise ValueError(f"{name}[{index}] must declare at least one phrasing")
        groups.append(group)
    return tuple(groups)


def _case(value: object) -> GoldenCase:
    case = _mapping(value, "golden case")
    case_id = _nonblank_string(case.get("id"), "case id")
    question = _nonblank_string(case.get("question"), f"case {case_id} question")
    locale = case.get("locale")
    if locale not in ("zh", "en"):
        raise ValueError(f"case {case_id} locale must be 'zh' or 'en'")
    kind = case.get("kind")
    if kind not in KINDS:
        raise ValueError(f"case {case_id} kind must be one of {KINDS}")
    expected_sources = _string_array(case.get("expectedSources"), f"case {case_id} expectedSources")
    if not expected_sources:
        raise ValueError(f"case {case_id} requires at least one expected source")
    expected_facts = (
        _fact_groups(case.get("expectedFacts"), f"case {case_id} expectedFacts")
        if "expectedFacts" in case
        else ()
    )
    forbidden_facts = (
        _fact_groups(case.get("forbiddenFacts"), f"case {case_id} forbiddenFacts")
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


def validate_golden(value: object, *, known_sources: frozenset[str] | None = None) -> GoldenSet:
    """Validate an `answer-golden-v1` document; pure, no I/O."""
    document = _mapping(value, "golden set")
    if document.get("schemaVersion") != SCHEMA_VERSION:
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
        if known_sources is not None:
            unknown = [source for source in case.expected_sources if source not in known_sources]
            if unknown:
                raise ValueError(
                    f"case {case.id} expectedSources must be corpus document names: {unknown}"
                )
        cases.append(case)
    return GoldenSet(
        schema_version=SCHEMA_VERSION,
        labeled_by=labeled_by,
        labeled_at=labeled_at,
        cases=tuple(cases),
    )


# --- Fact matching (normalized, group-aware) --------------------------------


def normalize_fact_text(text: str) -> str:
    """Normalize `text` for substring fact matching: NFKC (folds full-width
    ASCII/digits/percent to their ASCII forms), strip markdown backslash
    escapes, and remove whitespace entirely (incidental spacing differences
    between a fact phrase and the model's answer text must not break a
    match); pure, no I/O."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _MARKDOWN_ESCAPE.sub(r"\1", normalized)
    return "".join(normalized.split())


def fact_group_present(group: Sequence[str], haystack: str) -> bool:
    """Whether any phrasing in `group` is present in `haystack`, both normalized
    first; pure, no I/O."""
    normalized_haystack = normalize_fact_text(haystack)
    return any(normalize_fact_text(phrase) in normalized_haystack for phrase in group)


# --- Fake-mode chunking and candidate selection (pure) ----------------------


def chunk_document(text: str) -> tuple[str, ...]:
    """Split on blank lines into nonblank paragraphs; pure, no I/O."""
    return tuple(paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip())


def char_bigrams(text: str) -> frozenset[str]:
    """Character bigrams of `text`, ignoring whitespace; pure, no I/O."""
    compact = "".join(text.split())
    return frozenset(compact[index : index + 2] for index in range(len(compact) - 1))


def rank_paragraphs(
    question: str, paragraphs: Sequence[tuple[str, str]], *, limit: int = 6
) -> tuple[tuple[str, str], ...]:
    """Rank `(source, paragraph)` pairs by character-bigram overlap with `question`,
    descending, stable on ties; pure, no I/O. Returns the top `limit`."""
    question_bigrams = char_bigrams(question)
    scored = [
        (len(question_bigrams & char_bigrams(paragraph)), index, source, paragraph)
        for index, (source, paragraph) in enumerate(paragraphs)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple((source, paragraph) for _score, _index, source, paragraph in scored[:limit])


def cited_sources_from_claims(
    claims: Sequence[Mapping[str, Any]], label_sources: Mapping[str, str]
) -> tuple[str, ...]:
    """Unique source document names cited by `claims`' evidence labels, in first-seen
    order; pure, no I/O."""
    seen: list[str] = []
    for claim in claims:
        labels = claim.get("evidenceLabels", ())
        if not isinstance(labels, Sequence) or isinstance(labels, (str, bytes)):
            continue
        for label in labels:
            source = label_sources.get(str(label))
            if source is not None and source not in seen:
                seen.append(source)
    return tuple(seen)


# --- Observations schema + scoring ------------------------------------------


def _array_of_mappings(value: object, name: str) -> list[dict[str, Any]]:
    return [_mapping(item, f"{name} entry") for item in _array(value, name)]


def validate_observations(value: object) -> dict[str, Any]:
    """Validate an `answer-golden-observations-v1` document; pure, no I/O.

    Returns a new dict (never mutates `value`) with `observations` normalized
    to a list of mappings."""
    observations = dict(_mapping(value, "observations"))
    if observations.get("schemaVersion") != OBSERVATIONS_SCHEMA_VERSION:
        raise ValueError("unsupported answer golden observations schema")
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


def compute_metrics(
    golden_set: GoldenSet, observations: Mapping[str, Mapping[str, Any]]
) -> dict[str, float]:
    """Score `observations` (by case id) against `golden_set`; pure, no I/O.

    Requires every case to have an observation (fails closed instead of
    silently shrinking a metric's denominator). An observation with
    `invalid: true` (a cited source was not ready, or the real pipeline
    returned an unusable answer -- never a legitimate abstention) counts as a
    failure for every metric it touches: it is never treated as `abstained`,
    it contributes zero recall/precision credit for `answerable` cases (and is
    additionally counted against `citationSourcePrecision`'s denominator, since
    it cites nothing), and its empty answer text naturally fails `factRecall`
    and `falsePremiseHandled`."""
    missing = [case.id for case in golden_set.cases if case.id not in observations]
    if missing:
        raise ValueError(f"observations are missing cases: {missing}")

    cite_hits = cite_total = 0
    recall_hits = recall_total = 0
    fact_rates: list[float] = []
    abstain_hits = abstain_total = 0
    fp_hits = fp_total = 0
    forbidden_hits = forbidden_total = 0
    invalid_count = 0

    for case in golden_set.cases:
        observation = observations[case.id]
        answer_text = str(observation.get("answer") or "")
        invalid = bool(observation.get("invalid"))
        # An invalid observation is never a legitimate abstention, regardless of
        # what the `abstained` field says.
        abstained = False if invalid else bool(observation.get("abstained"))
        cited_sources = [
            source for source in observation.get("citedSources") or () if isinstance(source, str)
        ]
        if invalid:
            invalid_count += 1

        if case.kind == "answerable":
            recall_total += len(case.expected_sources)
            if invalid:
                # No citations to credit, and the denominator still grows so an
                # invalid answerable case is scored as a precision failure too.
                cite_total += 1
            elif cited_sources:
                cite_total += len(cited_sources)
                cite_hits += sum(1 for source in cited_sources if source in case.expected_sources)
            if not invalid:
                recall_hits += sum(1 for source in case.expected_sources if source in cited_sources)

        if case.expected_facts:
            hits = sum(1 for group in case.expected_facts if fact_group_present(group, answer_text))
            fact_rates.append(hits / len(case.expected_facts))

        if case.kind == "abstain":
            abstain_total += 1
            if abstained:
                abstain_hits += 1

        if case.kind == "false_premise":
            fp_total += 1
            corrected = any(fact_group_present(group, answer_text) for group in case.expected_facts)
            if abstained or corrected:
                fp_hits += 1

        if case.forbidden_facts:
            forbidden_total += 1
            if any(fact_group_present(group, answer_text) for group in case.forbidden_facts):
                forbidden_hits += 1

    return {
        "citationSourcePrecision": (cite_hits / cite_total) if cite_total else 1.0,
        "expectedSourceRecall": (recall_hits / recall_total) if recall_total else 1.0,
        "factRecall": (sum(fact_rates) / len(fact_rates)) if fact_rates else 1.0,
        "abstainAccuracy": (abstain_hits / abstain_total) if abstain_total else 1.0,
        "falsePremiseHandled": (fp_hits / fp_total) if fp_total else 1.0,
        "forbiddenFactRate": (forbidden_hits / forbidden_total) if forbidden_total else 0.0,
        "invalidCount": float(invalid_count),
    }


def fake_gate_passed(
    golden_set: GoldenSet,
    observations: Mapping[str, Mapping[str, Any]],
    metrics: Mapping[str, float],
) -> bool:
    """Fake-mode gate: full case coverage (already required by `compute_metrics`),
    `citationSourcePrecision == 1.0`, `expectedSourceRecall == 1.0`, and every
    `answerable` case produced at least one claim -- catching broken wiring
    (e.g. after an answer-format switch) that `citationSourcePrecision` alone
    would miss if it degenerated to the vacuous "no citations observed" case."""
    if metrics["citationSourcePrecision"] != 1.0 or metrics["expectedSourceRecall"] != 1.0:
        return False
    for case in golden_set.cases:
        if case.kind != "answerable":
            continue
        claim_count = observations[case.id].get("claimCount")
        if not isinstance(claim_count, int) or claim_count <= 0:
            return False
    return True


def gate_passed(metrics: Mapping[str, float], baseline: Mapping[str, float]) -> bool:
    """Real-mode regression gate: every metric at least matches the baseline,
    except `forbiddenFactRate` (must not exceed it) and `invalidCount` (the
    candidate must not have *more* invalid answers than the baseline); pure,
    no I/O."""
    for name in GATED_METRICS:
        if metrics[name] < baseline[name]:
            return False
    if metrics["forbiddenFactRate"] > baseline["forbiddenFactRate"]:
        return False
    return metrics["invalidCount"] <= baseline["invalidCount"]
