"""Unit tests for the pure Graph sample-export and tally module."""

from __future__ import annotations

import pytest

from tap.quality.graph_samples import (
    EDGE_COLUMNS,
    MERGE_COLUMNS,
    edge_csv_row,
    is_cross_source,
    merge_csv_row,
    sample_rows,
    tally,
)

_EDGE_ROWS = tuple(
    {"edge_id": f"edge-{i:03d}", "version": 1, "relation_type": "GOVERNS"} for i in range(80)
)


def test_sample_rows_is_deterministic_for_a_seed_and_bounded() -> None:
    first = sample_rows(_EDGE_ROWS, count=10, seed=42)
    second = sample_rows(_EDGE_ROWS, count=10, seed=42)
    assert first == second
    assert len(first) == 10

    different_seed = sample_rows(_EDGE_ROWS, count=10, seed=7)
    assert different_seed != first

    everything = sample_rows(_EDGE_ROWS, count=1000, seed=42)
    assert len(everything) == len(_EDGE_ROWS)
    assert set(row["edge_id"] for row in everything) == {row["edge_id"] for row in _EDGE_ROWS}


def test_edge_and_merge_csv_rows_use_exact_columns() -> None:
    edge = {
        "edge_id": "edge-1",
        "version": 3,
        "relation_type": "GOVERNS",
        "relation_label": "需要",
        "origin": "EXTRACTED",
        "confidence": 0.9,
    }
    subject = {"label": "保单", "node_type": "ENTITY"}
    object_ = {"label": "理赔", "node_type": "ENTITY"}
    evidence = {
        "source_title": "AIA policy terms",
        "chunk_id": "chunk-1",
        "snippet": "字" * 400,
    }
    row = edge_csv_row(edge, subject, object_, evidence)
    assert set(row) == set(EDGE_COLUMNS)
    assert len(row["evidenceSnippet"]) == 300
    assert row["verdict"] == ""
    assert row["reviewer"] == ""
    assert row["reviewedAt"] == ""
    assert row["note"] == ""

    node = {"node_id": "node-1", "version": 3, "label": "保单", "node_type": "ENTITY"}
    aliases = ["policy", "保险合同"]
    sources = [
        {"source_revision_id": "rev-1", "source_title": "Doc A"},
        {"source_revision_id": "rev-2", "source_title": "Doc B"},
    ]
    merge_log = [{"rule": "EMBEDDING", "merged_from": [("a", "b")]}]
    merge_row = merge_csv_row(node, aliases, sources, merge_log)
    assert set(merge_row) == set(MERGE_COLUMNS)
    assert merge_row["aliases"] == "policy|保险合同"
    assert merge_row["sourceTitles"] == "Doc A|Doc B"
    assert merge_row["mergeRules"] == "EMBEDDING"
    assert merge_row["mergedFromCount"] == "1"
    assert merge_row["sourceCount"] == "2"


def test_cross_source_requires_two_distinct_source_revisions() -> None:
    same_revision = [{"source_revision_id": "rev-1"}, {"source_revision_id": "rev-1"}]
    different_revisions = [{"source_revision_id": "rev-1"}, {"source_revision_id": "rev-2"}]
    assert is_cross_source([{"source_revision_id": "rev-1"}]) is False
    assert is_cross_source(same_revision) is False
    assert is_cross_source(different_revisions) is True


def _reviewed_row(
    columns: tuple[str, ...], *, verdict: str, reviewer: str, reviewed_at: str
) -> dict[str, str]:
    row = {column: "" for column in columns}
    row.update({"verdict": verdict, "reviewer": reviewer, "reviewedAt": reviewed_at})
    return row


def _verdicts(
    *, correct: int, wrong: int, blank: int = 0, columns: tuple[str, ...] = EDGE_COLUMNS
) -> list[dict[str, str]]:
    rows = [
        _reviewed_row(columns, verdict="correct", reviewer="human:zhang", reviewed_at="2026-10-06")
        for _ in range(correct)
    ]
    rows += [
        _reviewed_row(columns, verdict="wrong", reviewer="human:zhang", reviewed_at="2026-10-06")
        for _ in range(wrong)
    ]
    rows += [_reviewed_row(columns, verdict="", reviewer="", reviewed_at="") for _ in range(blank)]
    return rows


def test_tally_thresholds_and_incomplete_review() -> None:
    assert tally(_verdicts(correct=43, wrong=7), kind="edges")["passed"] is True
    assert tally(_verdicts(correct=42, wrong=8), kind="edges")["passed"] is False
    assert (
        tally(_verdicts(correct=29, wrong=1, columns=MERGE_COLUMNS), kind="merges")["passed"]
        is True
    )
    assert (
        tally(_verdicts(correct=28, wrong=2, columns=MERGE_COLUMNS), kind="merges")["passed"]
        is False
    )
    with pytest.raises(ValueError, match="incomplete"):
        tally(_verdicts(correct=49, wrong=0, blank=1), kind="edges")


def test_tally_rejects_placeholder_reviewer() -> None:
    rows = _verdicts(correct=5, wrong=0)
    rows[0]["reviewer"] = "pending-review"
    with pytest.raises(ValueError, match="incomplete"):
        tally(rows, kind="edges")

    rows2 = _verdicts(correct=5, wrong=0)
    rows2[0]["reviewer"] = "machine-generated"
    with pytest.raises(ValueError, match="incomplete"):
        tally(rows2, kind="edges")


def test_tally_rejects_placeholder_reviewed_at() -> None:
    rows = _verdicts(correct=5, wrong=0)
    rows[0]["reviewedAt"] = "pending-review"
    with pytest.raises(ValueError, match="incomplete"):
        tally(rows, kind="edges")

    rows2 = _verdicts(correct=5, wrong=0)
    rows2[0]["reviewedAt"] = "machine-generated"
    with pytest.raises(ValueError, match="incomplete"):
        tally(rows2, kind="edges")


def test_tally_rejects_rows_whose_columns_do_not_match_the_expected_header() -> None:
    rows = _verdicts(correct=5, wrong=0)
    del rows[0]["note"]
    rows[0]["unexpectedColumn"] = "x"
    with pytest.raises(ValueError, match="incomplete"):
        tally(rows, kind="edges")

    merge_rows = _verdicts(correct=5, wrong=0, columns=EDGE_COLUMNS)
    with pytest.raises(ValueError, match="incomplete"):
        tally(merge_rows, kind="merges")
