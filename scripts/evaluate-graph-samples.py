#!/usr/bin/env python3
"""Score reviewed Graph edge/merge sample CSVs against the acceptance gate
(spec 4.3): edge accuracy >=85%, incorrect-merge rate <=5%."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from tap.quality.graph_samples import tally


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def evaluate(edges_path: Path, merges_path: Path) -> dict[str, object]:
    edge_rows = _read_csv(edges_path)
    merge_rows = _read_csv(merges_path)
    edge_accuracy = tally(edge_rows, kind="edges")
    incorrect_merge_rate = tally(merge_rows, kind="merges")
    return {
        "edgeAccuracy": edge_accuracy,
        "incorrectMergeRate": incorrect_merge_rate,
        "passed": bool(edge_accuracy["passed"])
        and bool(incorrect_merge_rate["passed"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edges", type=Path, required=True)
    parser.add_argument("--merges", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    report = evaluate(arguments.edges, arguments.merges)
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
