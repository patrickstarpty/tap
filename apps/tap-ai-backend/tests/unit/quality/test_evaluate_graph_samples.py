"""Exercise `scripts/evaluate-graph-samples.py`'s header validation and clean
error handling around `tally`'s `ValueError`.

Loaded via `importlib.util` because the script's filename has hyphens,
following the same pattern `tests/unit/quality/test_load_graph_real_corpus_upload.py`
uses for `scripts/load-graph-real-corpus.py`.
"""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from tap.quality.graph_samples import EDGE_COLUMNS, MERGE_COLUMNS

ROOT = Path(__file__).resolve().parents[5]
SCRIPT = ROOT / "scripts" / "evaluate-graph-samples.py"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evaluate_graph_samples", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        writer.writerows(rows)


def _reviewed_row(columns: tuple[str, ...], *, verdict: str) -> dict[str, str]:
    row = {column: "" for column in columns}
    row.update({"verdict": verdict, "reviewer": "human:zhang", "reviewedAt": "2026-10-06"})
    return row


def test_evaluate_rejects_a_csv_whose_header_does_not_match_the_expected_columns(
    tmp_path: Path,
) -> None:
    module = _script()
    edges_path = tmp_path / "edges.csv"
    merges_path = tmp_path / "merges.csv"
    _write_csv(
        edges_path, EDGE_COLUMNS[:-1], [dict.fromkeys(EDGE_COLUMNS[:-1], "")]
    )  # drop the last column
    _write_csv(merges_path, MERGE_COLUMNS, [_reviewed_row(MERGE_COLUMNS, verdict="correct")])
    with pytest.raises(ValueError, match="header does not match"):
        module.evaluate(edges_path, merges_path)


def test_main_catches_tally_value_error_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _script()
    edges_path = tmp_path / "edges.csv"
    merges_path = tmp_path / "merges.csv"
    incomplete_row = _reviewed_row(EDGE_COLUMNS, verdict="correct")
    incomplete_row["reviewer"] = ""  # blank reviewer -> tally raises "sample review incomplete"
    _write_csv(edges_path, EDGE_COLUMNS, [incomplete_row])
    _write_csv(merges_path, MERGE_COLUMNS, [_reviewed_row(MERGE_COLUMNS, verdict="correct")])

    exit_code = module.main(
        [
            "--edges",
            str(edges_path),
            "--merges",
            str(merges_path),
            "--report",
            str(tmp_path / "r.json"),
        ]
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert "incomplete" in captured.err


def test_main_catches_header_mismatch_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _script()
    edges_path = tmp_path / "edges.csv"
    merges_path = tmp_path / "merges.csv"
    _write_csv(edges_path, EDGE_COLUMNS[:-1], [dict.fromkeys(EDGE_COLUMNS[:-1], "")])
    _write_csv(merges_path, MERGE_COLUMNS, [_reviewed_row(MERGE_COLUMNS, verdict="correct")])

    exit_code = module.main(
        [
            "--edges",
            str(edges_path),
            "--merges",
            str(merges_path),
            "--report",
            str(tmp_path / "r.json"),
        ]
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert "header does not match" in captured.err
