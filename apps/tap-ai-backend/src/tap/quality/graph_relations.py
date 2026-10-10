"""Golden-question schema, validation and R-citation matching for Graph relation acceptance.

`GOLDEN_SCHEMA` documents the accepted shape as JSON Schema (also used to digest the
contract). The repository has no `jsonschema` dependency, so `validate_golden` is a
hand-rolled validator that enforces every constraint the schema describes, plus the
domain rules (known relation types, manifest-bound sources, human-labeling gates)
that JSON Schema cannot express.

The matcher below (`match_question`, `aggregate`, `grounded_rate`, `edge_citations`)
scores a golden question's R citations against its expected edges; it is pure and
does no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from tap.modules.graph.domain.vocabulary import RELATION_TYPES, normalize_key

GOLDEN_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schemaVersion", "labeledBy", "labeledAt", "corpus", "questions"],
    "properties": {
        "schemaVersion": {"const": "graph-relation-golden-v1"},
        "labeledBy": {"type": "string", "minLength": 1},
        "labeledAt": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
        "corpus": {
            "type": "object",
            "required": ["manifest", "digest"],
            "properties": {
                "manifest": {"type": "string"},
                "digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            },
        },
        "questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 30,
            "items": {
                "type": "object",
                "required": [
                    "id",
                    "question",
                    "locale",
                    "expectedEntities",
                    "expectedEdges",
                    "expectedSources",
                ],
                "properties": {
                    "id": {"type": "string", "pattern": "^q-[0-9]{2,3}$"},
                    "question": {"type": "string", "minLength": 1},
                    "locale": {"enum": ["zh", "en"]},
                    "expectedEntities": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["label"],
                            "properties": {
                                "label": {"type": "string"},
                                "aliases": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "maxItems": 10,
                                },
                            },
                        },
                    },
                    "expectedEdges": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"$ref": "#/$defs/edge"},
                    },
                    "expectedSources": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
    "$defs": {
        "edgeCore": {
            "type": "object",
            "required": ["subject", "relationType", "object"],
            "properties": {
                "subject": {"type": "string"},
                "relationType": {"type": "string"},
                "object": {"type": "string"},
            },
        },
        "edge": {
            "allOf": [{"$ref": "#/$defs/edgeCore"}],
            "properties": {
                "alternatives": {"type": "array", "items": {"$ref": "#/$defs/edgeCore"}}
            },
        },
    },
}

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_QUESTION_ID = re.compile(r"q-[0-9]{2,3}\Z")
_PLACEHOLDER_LABELER_PREFIXES = ("pending-", "generated-", "machine-", "fixture")
_MIN_QUESTIONS = 1
_MAX_QUESTIONS = 30
_MIN_HUMAN_QUESTIONS = 20
_MAX_HUMAN_QUESTIONS = 30
_MAX_ALIASES = 10


@dataclass(frozen=True)
class ExpectedEdge:
    subject: str
    relation_type: str
    object: str
    alternatives: tuple["ExpectedEdge", ...] = ()


@dataclass(frozen=True)
class GoldenQuestion:
    id: str
    question: str
    locale: str
    expected_entities: tuple[tuple[str, tuple[str, ...]], ...]
    expected_edges: tuple[ExpectedEdge, ...]
    expected_sources: tuple[str, ...]


@dataclass(frozen=True)
class GoldenSet:
    labeled_by: str
    labeled_at: str
    corpus_manifest: str
    corpus_digest: str
    questions: tuple[GoldenQuestion, ...]


@dataclass(frozen=True)
class RegressionGroup:
    id: str
    required_count: int
    questions: tuple[str, ...]


@dataclass(frozen=True)
class RegressionSet:
    groups: tuple[RegressionGroup, ...]
    complete: bool


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


def _string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _edge_core(value: object, question_id: str) -> tuple[str, str, str]:
    edge = _mapping(value, f"question {question_id} expected edge")
    subject = _nonblank_string(edge.get("subject"), f"question {question_id} edge subject")
    relation_type = edge.get("relationType")
    if not isinstance(relation_type, str) or relation_type not in RELATION_TYPES:
        raise ValueError(f"question {question_id} edge relationType must be one of RELATION_TYPES")
    obj = _nonblank_string(edge.get("object"), f"question {question_id} edge object")
    return subject, relation_type, obj


def _edge(value: object, question_id: str) -> ExpectedEdge:
    edge = _mapping(value, f"question {question_id} expected edge")
    subject, relation_type, obj = _edge_core(edge, question_id)
    alternatives: tuple[ExpectedEdge, ...] = ()
    if "alternatives" in edge:
        raw_alternatives = _array(
            edge.get("alternatives"), f"question {question_id} edge alternatives"
        )
        alternatives = tuple(
            ExpectedEdge(subject=alt_subject, relation_type=alt_relation, object=alt_object)
            for alt_subject, alt_relation, alt_object in (
                _edge_core(alternative, question_id) for alternative in raw_alternatives
            )
        )
    return ExpectedEdge(
        subject=subject, relation_type=relation_type, object=obj, alternatives=alternatives
    )


def _entity(value: object, question_id: str) -> tuple[str, tuple[str, ...]]:
    entity = _mapping(value, f"question {question_id} expected entity")
    label = _nonblank_string(entity.get("label"), f"question {question_id} entity label")
    aliases_raw = entity.get("aliases", [])
    aliases = (
        _array(aliases_raw, f"question {question_id} entity aliases") if "aliases" in entity else []
    )
    if len(aliases) > _MAX_ALIASES:
        raise ValueError(
            f"question {question_id} entity aliases must have at most {_MAX_ALIASES} items"
        )
    return label, tuple(_string(alias, f"question {question_id} entity alias") for alias in aliases)


def _question(
    value: object, *, corpus_ids: frozenset[str] | None, seen_ids: set[str]
) -> GoldenQuestion:
    question = _mapping(value, "golden question")
    question_id = question.get("id")
    if not isinstance(question_id, str) or _QUESTION_ID.fullmatch(question_id) is None:
        raise ValueError(f"question id {question_id!r} must match q-[0-9]{{2,3}}")
    if question_id in seen_ids:
        raise ValueError(f"question id {question_id} is not unique")
    seen_ids.add(question_id)

    text = _nonblank_string(question.get("question"), f"question {question_id} text")
    locale = question.get("locale")
    if locale not in ("zh", "en"):
        raise ValueError(f"question {question_id} locale must be 'zh' or 'en'")

    entities_raw = _array(
        question.get("expectedEntities"), f"question {question_id} expectedEntities"
    )
    if not entities_raw:
        raise ValueError(f"question {question_id} requires at least one expected entity")
    entities = tuple(_entity(entity, question_id) for entity in entities_raw)

    edges_raw = _array(question.get("expectedEdges"), f"question {question_id} expectedEdges")
    if not edges_raw:
        raise ValueError(f"question {question_id} requires at least one expected edge")
    edges = tuple(_edge(edge, question_id) for edge in edges_raw)

    sources_raw = _array(question.get("expectedSources"), f"question {question_id} expectedSources")
    if not sources_raw:
        raise ValueError(f"question {question_id} requires at least one expected source")
    sources = tuple(
        _nonblank_string(source, f"question {question_id} expected source")
        for source in sources_raw
    )
    if corpus_ids is not None:
        unknown = [source for source in sources if source not in corpus_ids]
        if unknown:
            raise ValueError(
                f"question {question_id} expectedSources must be manifest entry ids: {unknown}"
            )

    return GoldenQuestion(
        id=question_id,
        question=text,
        locale=locale,
        expected_entities=entities,
        expected_edges=edges,
        expected_sources=sources,
    )


def validate_golden(
    value: object,
    *,
    require_human: bool = False,
    corpus_ids: frozenset[str] | None = None,
) -> GoldenSet:
    """Validate a golden-question document against `GOLDEN_SCHEMA` plus domain rules."""
    document = _mapping(value, "golden set")
    required_fields = {"schemaVersion", "labeledBy", "labeledAt", "corpus", "questions"}
    missing = required_fields - document.keys()
    if missing:
        raise ValueError(f"golden set is missing required fields: {sorted(missing)}")
    if document.get("schemaVersion") != "graph-relation-golden-v1":
        raise ValueError("unsupported golden set schema")

    labeled_by = document.get("labeledBy")
    if not isinstance(labeled_by, str) or not labeled_by:
        raise ValueError("labeledBy must be a nonblank string")

    labeled_at = document.get("labeledAt")
    if not isinstance(labeled_at, str) or _DATE.fullmatch(labeled_at) is None:
        raise ValueError("labeledAt must match YYYY-MM-DD")

    corpus = _mapping(document.get("corpus"), "corpus")
    manifest = corpus.get("manifest")
    if not isinstance(manifest, str):
        raise ValueError("corpus.manifest must be a string")
    digest = corpus.get("digest")
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise ValueError("corpus.digest must be a sha256 digest")

    questions_raw = _array(document.get("questions"), "questions")
    if not (_MIN_QUESTIONS <= len(questions_raw) <= _MAX_QUESTIONS):
        raise ValueError(
            f"questions must contain {_MIN_QUESTIONS}-{_MAX_QUESTIONS} items, "
            f"got {len(questions_raw)}"
        )

    seen_ids: set[str] = set()
    questions = tuple(
        _question(item, corpus_ids=corpus_ids, seen_ids=seen_ids) for item in questions_raw
    )

    if require_human:
        if labeled_by.startswith(_PLACEHOLDER_LABELER_PREFIXES):
            raise ValueError("require_human golden set must not use a placeholder labeler")
        if not (_MIN_HUMAN_QUESTIONS <= len(questions) <= _MAX_HUMAN_QUESTIONS):
            raise ValueError(
                f"require_human golden set must contain {_MIN_HUMAN_QUESTIONS}-"
                f"{_MAX_HUMAN_QUESTIONS} questions, got {len(questions)}"
            )

    return GoldenSet(
        labeled_by=labeled_by,
        labeled_at=labeled_at,
        corpus_manifest=manifest,
        corpus_digest=digest,
        questions=questions,
    )


def validate_regression(value: object) -> RegressionSet:
    """Parse the regression-question groups, flagging whether every group is filled."""
    document = _mapping(value, "regression questions")
    if document.get("schemaVersion") != "graph-regression-questions-v1":
        raise ValueError("unsupported regression questions schema")
    groups_raw = _array(document.get("groups"), "groups")
    if not groups_raw:
        raise ValueError("regression questions must declare at least one group")

    groups: list[RegressionGroup] = []
    seen_ids: set[str] = set()
    complete = True
    for raw in groups_raw:
        group = _mapping(raw, "regression group")
        group_id = group.get("id")
        if not isinstance(group_id, str) or not group_id:
            raise ValueError("regression group id must be a nonblank string")
        if group_id in seen_ids:
            raise ValueError(f"regression group id {group_id} is not unique")
        seen_ids.add(group_id)

        required_count = group.get("requiredCount")
        if (
            not isinstance(required_count, int)
            or isinstance(required_count, bool)
            or required_count < 0
        ):
            raise ValueError(
                f"regression group {group_id} requiredCount must be a nonnegative integer"
            )

        questions_raw = _array(group.get("questions"), f"regression group {group_id} questions")
        questions = tuple(
            _nonblank_string(question, f"regression group {group_id} question")
            for question in questions_raw
        )
        if len(questions) < required_count:
            complete = False
        groups.append(
            RegressionGroup(id=group_id, required_count=required_count, questions=questions)
        )

    return RegressionSet(groups=tuple(groups), complete=complete)


# --- R-citation matching and report aggregation (Task 2) -----------------------------

SYMMETRIC_RELATIONS: frozenset[str] = frozenset({"CONFLICTS_WITH", "RELATED_TO"})


@dataclass(frozen=True)
class EdgeCitation:
    citation_id: str
    evidence_label: str
    edge_id: str
    subject_label: str
    object_label: str
    relation_type: str
    relation_label: str


@dataclass(frozen=True)
class QuestionResult:
    question_id: str
    status: Literal["pass", "miss", "wrong"]
    correct_edges: tuple[str, ...]
    missed_edges: tuple[str, ...]
    wrong_edges: tuple[str, ...]
    chunk_citations: int


def _edge_text(subject: str, relation_type: str, obj: str) -> str:
    return f"{subject} -{relation_type}-> {obj}"


def _optional_str(value: object) -> str:
    """Display fields are cosmetic: default to "" instead of rejecting the citation."""
    return value if isinstance(value, str) else ""


def _parse_edge_citation(citation: object) -> EdgeCitation | None:
    """Parse one `kind == "edge"` citation, or `None` if it cannot even be scored.

    Only `subject.label`, `object.label` and `relationType` are required — those are
    the fields `_edge_matches` needs. `citationId`, `evidenceLabel`, `edgeId` and
    `relationLabel` are display-only and default to `""` when missing or the wrong
    type, so their absence never drops or misclassifies an otherwise-scorable citation.
    """
    if not isinstance(citation, Mapping) or citation.get("kind") != "edge":
        return None

    relation_type = citation.get("relationType")
    if not isinstance(relation_type, str):
        return None

    subject = citation.get("subject")
    if not isinstance(subject, Mapping):
        return None
    subject_label = subject.get("label")
    if not isinstance(subject_label, str):
        return None

    obj = citation.get("object")
    if not isinstance(obj, Mapping):
        return None
    object_label = obj.get("label")
    if not isinstance(object_label, str):
        return None

    return EdgeCitation(
        citation_id=_optional_str(citation.get("citationId")),
        evidence_label=_optional_str(citation.get("evidenceLabel")),
        edge_id=_optional_str(citation.get("edgeId")),
        subject_label=subject_label,
        object_label=object_label,
        relation_type=relation_type,
        relation_label=_optional_str(citation.get("relationLabel")),
    )


def edge_citations(citations: Sequence[Mapping[str, object]]) -> tuple[EdgeCitation, ...]:
    """Pick out `kind == "edge"` citations that carry the scoring fields.

    An entry missing `subject.label`, `object.label` or `relationType` cannot be
    represented as an `EdgeCitation` and is skipped here. `match_question` detects
    those same entries itself (via `_parse_edge_citation`) so it can score them as
    wrong edges instead of silently dropping them.
    """
    return tuple(
        parsed for citation in citations if (parsed := _parse_edge_citation(citation)) is not None
    )


def _expected_keys(question: GoldenQuestion, label: str) -> frozenset[str]:
    """Keys a citation's label may use to refer to `label`: itself plus any aliases."""
    keys = {normalize_key(label)}
    normalized_label = normalize_key(label)
    for entity_label, aliases in question.expected_entities:
        if normalize_key(entity_label) == normalized_label:
            keys.add(normalize_key(entity_label))
            keys.update(normalize_key(alias) for alias in aliases)
    return frozenset(keys)


def _edge_matches(expected: ExpectedEdge, citation: EdgeCitation, question: GoldenQuestion) -> bool:
    if citation.relation_type != expected.relation_type:
        return False
    subject_keys = _expected_keys(question, expected.subject)
    object_keys = _expected_keys(question, expected.object)
    cited_subject = normalize_key(citation.subject_label)
    cited_object = normalize_key(citation.object_label)
    if cited_subject in subject_keys and cited_object in object_keys:
        return True
    return (
        expected.relation_type in SYMMETRIC_RELATIONS
        and cited_subject in object_keys
        and cited_object in subject_keys
    )


def match_question(
    question: GoldenQuestion, citations: Sequence[Mapping[str, object]]
) -> QuestionResult:
    """Score one golden question's R citations against its expected edges (and alternatives).

    Each edge citation either hits an expected edge (directly, via an alternative, or in
    either direction for a symmetric relation) or is recorded as a wrong edge. A citation
    matching an alternative counts as a hit for its primary expected edge. A `kind ==
    "edge"` citation that cannot be parsed (missing `subject.label`, `object.label` or
    `relationType`) is scored as a wrong edge too — `"<malformed edge citation>"` — never
    silently dropped, so a malformed citation can only ever make a question worse, not
    better. `chunk_citations` counts only entries with `kind == "chunk"`; any other kind
    (including unrecognized ones) is ignored for that count. Any wrong edge makes the
    question `wrong`; otherwise at least one correct edge makes it `pass`; else `miss`.
    """
    correct: list[str] = []
    wrong: list[str] = []
    matched: set[int] = set()
    chunk_count = 0
    for raw_citation in citations:
        if not isinstance(raw_citation, Mapping):
            continue
        kind = raw_citation.get("kind")
        if kind == "chunk":
            chunk_count += 1
            continue
        if kind != "edge":
            continue

        citation = _parse_edge_citation(raw_citation)
        if citation is None:
            wrong.append("<malformed edge citation>")
            continue

        hit: ExpectedEdge | None = None
        for expected in question.expected_edges:
            candidates = (expected, *expected.alternatives)
            if any(_edge_matches(candidate, citation, question) for candidate in candidates):
                hit = expected
                break
        if hit is None:
            wrong.append(
                _edge_text(citation.subject_label, citation.relation_type, citation.object_label)
            )
        elif id(hit) not in matched:
            matched.add(id(hit))
            correct.append(_edge_text(hit.subject, hit.relation_type, hit.object))

    missed = tuple(
        _edge_text(expected.subject, expected.relation_type, expected.object)
        for expected in question.expected_edges
        if id(expected) not in matched
    )

    status: Literal["pass", "miss", "wrong"]
    if wrong:
        status = "wrong"
    elif correct:
        status = "pass"
    else:
        status = "miss"

    return QuestionResult(
        question_id=question.id,
        status=status,
        correct_edges=tuple(correct),
        missed_edges=missed,
        wrong_edges=tuple(wrong),
        chunk_citations=chunk_count,
    )


def aggregate(
    results: Sequence[QuestionResult], *, required_percent: int = 80
) -> dict[str, object]:
    """Summarize question results with the same ratio algorithm as `_ratio` in
    `scripts/evaluate-quality-graph.py`.
    """
    total = len(results)
    passed_count = sum(1 for result in results if result.status == "pass")
    return {
        "total": total,
        "passedCount": passed_count,
        "actual": f"{passed_count}/{total}",
        "required": f">={required_percent}%",
        "passed": total > 0 and passed_count * 100 >= total * required_percent,
    }


def grounded_rate(answers: Sequence[Mapping[str, object]]) -> tuple[int, int]:
    """Count answers that did not abstain and carry at least one citation, out of the total."""
    total = len(answers)
    grounded = 0
    for answer in answers:
        if not isinstance(answer, Mapping) or answer.get("abstained") is True:
            continue
        citations = answer.get("citations")
        is_sequence = isinstance(citations, Sequence) and not isinstance(citations, (str, bytes))
        if is_sequence and citations:
            grounded += 1
    return grounded, total
