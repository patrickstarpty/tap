"""Pure helpers for exporting Graph edge/merge human-review samples and tallying
their reviewed verdicts.

`sample_rows` picks a deterministic, seeded subset of candidate rows (edges or
cross-source nodes) for a CSV export; `edge_csv_row`/`merge_csv_row` shape one
candidate plus its resolved context into the exact exported CSV columns, leaving
the review columns (`verdict`/`reviewer`/`reviewedAt`/`note`) blank for a human
to fill in; `tally` reads a reviewed CSV back and scores it once every row's
review is complete, using the same ratio/maximum algorithm as
`scripts/evaluate-quality-graph.py:39-52`.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import Literal

EDGE_COLUMNS: tuple[str, ...] = (
    "edgeId",
    "graphVersion",
    "subjectLabel",
    "subjectType",
    "relationType",
    "relationLabel",
    "objectLabel",
    "objectType",
    "confidence",
    "origin",
    "sourceTitle",
    "chunkId",
    "evidenceSnippet",
    "verdict",
    "reviewer",
    "reviewedAt",
    "note",
)

MERGE_COLUMNS: tuple[str, ...] = (
    "nodeId",
    "graphVersion",
    "label",
    "nodeType",
    "aliases",
    "sourceCount",
    "sourceTitles",
    "mergeRules",
    "mergedFromCount",
    "verdict",
    "reviewer",
    "reviewedAt",
    "note",
)

_ID_FIELDS: tuple[str, ...] = ("edgeId", "edge_id", "nodeId", "node_id")
_PLACEHOLDER_REVIEWER_PREFIXES = ("pending-", "machine-")


def _primary_key(row: Mapping[str, object]) -> str:
    """Return a stable sort key for one candidate row.

    Prefers the row's own edge/node identity column (whichever is present); falls
    back to the row's full sorted contents so even an id-less fixture sorts
    deterministically.
    """
    for field in _ID_FIELDS:
        if field in row:
            return str(row[field])
    return str(sorted(row.items(), key=lambda item: item[0]))


def sample_rows(
    rows: Sequence[Mapping[str, object]], *, count: int, seed: int
) -> tuple[Mapping[str, object], ...]:
    """Deterministically sample up to `count` candidate rows for a given `seed`.

    Rows are sorted by primary key first so the same `(rows, count, seed)` always
    samples the same subset regardless of the caller's original row order.
    `count > len(rows)` returns every row.
    """
    ordered = sorted(rows, key=_primary_key)
    return tuple(random.Random(seed).sample(ordered, min(count, len(ordered))))


def is_cross_source(sources: Sequence[Mapping[str, object]]) -> bool:
    """A node is a merge candidate once its sources span >=2 source revisions."""
    return len({source["source_revision_id"] for source in sources}) >= 2


def edge_csv_row(
    edge: Mapping[str, object],
    subject: Mapping[str, object],
    object_: Mapping[str, object],
    evidence: Mapping[str, object],
    *,
    snippet_limit: int = 300,
) -> dict[str, str]:
    """Shape one `EXTRACTED` edge candidate plus its first evidence row into an
    `EDGE_COLUMNS` CSV row. The review columns are left blank for the reviewer."""
    snippet = str(evidence.get("snippet", ""))[:snippet_limit]
    return {
        "edgeId": str(edge["edge_id"]),
        "graphVersion": str(edge["version"]),
        "subjectLabel": str(subject["label"]),
        "subjectType": str(subject["node_type"]),
        "relationType": str(edge["relation_type"]),
        "relationLabel": str(edge["relation_label"]),
        "objectLabel": str(object_["label"]),
        "objectType": str(object_["node_type"]),
        "confidence": str(edge["confidence"]),
        "origin": str(edge["origin"]),
        "sourceTitle": str(evidence.get("source_title", "")),
        "chunkId": str(evidence.get("chunk_id", "")),
        "evidenceSnippet": snippet,
        "verdict": "",
        "reviewer": "",
        "reviewedAt": "",
        "note": "",
    }


def _merged_from_count(entry: Mapping[str, object]) -> int:
    merged_from = entry.get("merged_from", ())
    if not isinstance(merged_from, Sequence):
        return 0
    return len(merged_from)


def merge_csv_row(
    node: Mapping[str, object],
    aliases: Sequence[str],
    sources: Sequence[Mapping[str, object]],
    merge_log: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    """Shape one cross-source merge candidate into a `MERGE_COLUMNS` CSV row.

    `aliases`/`sourceTitles`/`mergeRules` are `|`-joined; the review columns are
    left blank for the reviewer. `sourceTitles` dedupes by title, ordered by
    `source_revision_id` for determinism; `mergeRules` dedupes the rules recorded
    in `graph_merge_log` for this node.
    """
    ordered_sources = sorted(sources, key=lambda item: str(item["source_revision_id"]))
    revision_ids = {str(source["source_revision_id"]) for source in ordered_sources}
    titles: list[str] = []
    for source in ordered_sources:
        title = str(source.get("source_title", ""))
        if title not in titles:
            titles.append(title)
    merged_from_count = sum(_merged_from_count(entry) for entry in merge_log)
    rules = sorted({str(entry["rule"]) for entry in merge_log})
    return {
        "nodeId": str(node["node_id"]),
        "graphVersion": str(node["version"]),
        "label": str(node["label"]),
        "nodeType": str(node["node_type"]),
        "aliases": "|".join(str(alias) for alias in aliases),
        "sourceCount": str(len(revision_ids)),
        "sourceTitles": "|".join(titles),
        "mergeRules": "|".join(rules),
        "mergedFromCount": str(merged_from_count),
        "verdict": "",
        "reviewer": "",
        "reviewedAt": "",
        "note": "",
    }


def _ratio(numerator: int, denominator: int, required: int) -> dict[str, object]:
    return {
        "actual": f"{numerator}/{denominator}",
        "required": f">={required}%",
        "passed": denominator > 0 and numerator * 100 >= denominator * required,
    }


def _maximum(numerator: int, denominator: int, maximum: int) -> dict[str, object]:
    return {
        "actual": f"{numerator}/{denominator}",
        "required": f"<={maximum}%",
        "passed": denominator > 0 and numerator * 100 <= denominator * maximum,
    }


def tally(
    rows: Sequence[Mapping[str, str]], *, kind: Literal["edges", "merges"]
) -> dict[str, object]:
    """Score a reviewed sample CSV: every row's `verdict` must be `correct` or
    `wrong`, with a nonblank `reviewer`/`reviewedAt` where `reviewer` is not a
    placeholder (`pending-`/`machine-` prefixed); otherwise raises
    `ValueError("sample review incomplete")`.

    `kind="edges"` requires >=85% correct (`_ratio`); `kind="merges"` requires
    <=5% wrong (`_maximum`) — the same ratio/maximum algorithm as
    `scripts/evaluate-quality-graph.py:39-52`.
    """
    total = len(rows)
    correct = 0
    for row in rows:
        verdict = row.get("verdict")
        reviewer = row.get("reviewer")
        reviewed_at = row.get("reviewedAt")
        if verdict not in ("correct", "wrong"):
            raise ValueError("sample review incomplete")
        if (
            not isinstance(reviewer, str)
            or not reviewer.strip()
            or reviewer.startswith(_PLACEHOLDER_REVIEWER_PREFIXES)
        ):
            raise ValueError("sample review incomplete")
        if not isinstance(reviewed_at, str) or not reviewed_at.strip():
            raise ValueError("sample review incomplete")
        if verdict == "correct":
            correct += 1
    wrong = total - correct
    if kind == "edges":
        return {"total": total, "correctCount": correct, **_ratio(correct, total, 85)}
    return {"total": total, "wrongCount": wrong, **_maximum(wrong, total, 5)}


__all__ = [
    "EDGE_COLUMNS",
    "MERGE_COLUMNS",
    "edge_csv_row",
    "is_cross_source",
    "merge_csv_row",
    "sample_rows",
    "tally",
]
