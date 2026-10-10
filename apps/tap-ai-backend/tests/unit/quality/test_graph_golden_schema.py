from __future__ import annotations

import copy

import pytest

from tap.quality.graph_relations import GoldenSet, validate_golden


def _question(index: int, **overrides: object) -> dict[str, object]:
    question: dict[str, object] = {
        "id": f"q-{index:02d}",
        "question": f"Question {index}?",
        "locale": "zh",
        "expectedEntities": [{"label": f"Entity {index}", "aliases": ["alias"]}],
        "expectedEdges": [
            {"subject": f"Entity {index}", "relationType": "REQUIRES", "object": "Policy"}
        ],
        "expectedSources": ["source-a"],
    }
    question.update(overrides)
    return question


def _golden(questions: list[dict[str, object]], **overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schemaVersion": "graph-relation-golden-v1",
        "labeledBy": "qa-reviewer",
        "labeledAt": "2026-10-06",
        "corpus": {
            "manifest": "apps/tap-ai-backend/tests/fixtures/quality/graph-real/manifest.json",
            "digest": "sha256:" + "0" * 64,
        },
        "questions": questions,
    }
    document.update(overrides)
    return document


def test_golden_accepts_minimal_valid_document():
    document = _golden([_question(1)])
    golden_set = validate_golden(document)
    assert isinstance(golden_set, GoldenSet)
    assert golden_set.labeled_by == "qa-reviewer"
    assert len(golden_set.questions) == 1
    question = golden_set.questions[0]
    assert question.id == "q-01"
    assert question.expected_entities == (("Entity 1", ("alias",)),)
    assert question.expected_edges[0].relation_type == "REQUIRES"
    assert question.expected_edges[0].alternatives == ()
    assert question.expected_sources == ("source-a",)


def test_golden_rejects_unknown_relation_type_and_missing_alternatives_shape():
    unknown_relation = _golden([_question(1)])
    unknown_relation["questions"][0]["expectedEdges"][0]["relationType"] = "NOT_A_REAL_RELATION"
    with pytest.raises(ValueError):
        validate_golden(unknown_relation)

    bad_alternatives_shape = _golden([_question(1)])
    bad_alternatives_shape["questions"][0]["expectedEdges"][0]["alternatives"] = [
        {"subject": "Entity 1", "relationType": "REQUIRES"}  # missing "object"
    ]
    with pytest.raises(ValueError):
        validate_golden(bad_alternatives_shape)

    alternatives_not_a_list = _golden([_question(1)])
    alternatives_not_a_list["questions"][0]["expectedEdges"][0]["alternatives"] = "not-a-list"
    with pytest.raises(ValueError):
        validate_golden(alternatives_not_a_list)


def test_golden_accepts_well_formed_alternatives():
    document = _golden([_question(1)])
    document["questions"][0]["expectedEdges"][0]["alternatives"] = [
        {"subject": "Entity 1", "relationType": "RELATED_TO", "object": "Policy"}
    ]
    golden_set = validate_golden(document)
    alternatives = golden_set.questions[0].expected_edges[0].alternatives
    assert len(alternatives) == 1
    assert alternatives[0].relation_type == "RELATED_TO"


def test_require_human_rejects_placeholder_labeler_and_out_of_range_counts():
    twenty_questions = [_question(index) for index in range(1, 21)]

    placeholder_labeler = _golden(
        copy.deepcopy(twenty_questions), labeledBy="pending-human-labeling"
    )
    with pytest.raises(ValueError):
        validate_golden(placeholder_labeler, require_human=True)

    nineteen_questions = _golden([_question(index) for index in range(1, 20)])
    with pytest.raises(ValueError):
        validate_golden(nineteen_questions, require_human=True)

    thirty_one_questions = _golden([_question(index) for index in range(1, 32)])
    with pytest.raises(ValueError):
        validate_golden(thirty_one_questions, require_human=True)

    # Twenty valid, human-labeled questions must pass.
    valid = _golden(copy.deepcopy(twenty_questions))
    golden_set = validate_golden(valid, require_human=True)
    assert len(golden_set.questions) == 20


def test_expected_sources_must_be_manifest_ids():
    document = _golden([_question(1)])
    with pytest.raises(ValueError):
        validate_golden(document, corpus_ids=frozenset({"source-b"}))

    # Passes when the source is a known manifest id.
    golden_set = validate_golden(document, corpus_ids=frozenset({"source-a", "source-b"}))
    assert golden_set.questions[0].expected_sources == ("source-a",)
