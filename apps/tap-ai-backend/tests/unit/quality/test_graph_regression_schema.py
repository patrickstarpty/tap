from __future__ import annotations

import json
from pathlib import Path

import pytest

from tap.quality.graph_relations import RegressionSet, validate_regression

_SKELETON = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "quality"
    / "graph-real"
    / "regression-questions.json"
)


def _document(groups: list[dict[str, object]]) -> dict[str, object]:
    return {"schemaVersion": "graph-regression-questions-v1", "groups": groups}


def test_skeleton_regression_questions_are_incomplete_with_required_counts():
    raw = json.loads(_SKELETON.read_text(encoding="utf-8"))
    regression_set = validate_regression(raw)

    assert isinstance(regression_set, RegressionSet)
    assert regression_set.complete is False
    by_id = {group.id: group for group in regression_set.groups}
    assert by_id["prompt-suggestions-2026-10-05"].required_count == 17
    assert by_id["prompt-suggestions-2026-10-05"].questions == ()
    assert by_id["general"].required_count == 10
    assert by_id["general"].questions == ()


def test_complete_when_every_group_meets_its_required_count():
    document = _document(
        [
            {"id": "group-a", "requiredCount": 2, "questions": ["q1", "q2"]},
            {"id": "group-b", "requiredCount": 0, "questions": []},
        ]
    )
    regression_set = validate_regression(document)

    assert regression_set.complete is True
    assert regression_set.groups[0].questions == ("q1", "q2")


def test_incomplete_when_any_group_is_short_of_its_required_count():
    document = _document(
        [
            {"id": "group-a", "requiredCount": 2, "questions": ["q1"]},
            {"id": "group-b", "requiredCount": 0, "questions": []},
        ]
    )
    regression_set = validate_regression(document)

    assert regression_set.complete is False


def test_duplicate_group_id_is_rejected():
    document = _document(
        [
            {"id": "group-a", "requiredCount": 1, "questions": ["q1"]},
            {"id": "group-a", "requiredCount": 1, "questions": ["q2"]},
        ]
    )
    with pytest.raises(ValueError, match="not unique"):
        validate_regression(document)


def test_required_count_must_be_a_nonnegative_integer():
    negative = _document([{"id": "group-a", "requiredCount": -1, "questions": []}])
    with pytest.raises(ValueError):
        validate_regression(negative)

    boolean_count = _document([{"id": "group-a", "requiredCount": True, "questions": []}])
    with pytest.raises(ValueError):
        validate_regression(boolean_count)
