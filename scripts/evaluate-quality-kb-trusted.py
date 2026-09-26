#!/usr/bin/env python3
"""Evaluate RFC-011 trusted-knowledge metrics without rewriting V1 results."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from tap.quality.evidence import validate_candidate_batch

_TOPICS = frozenset({"amount", "percentage", "date", "condition", "exception"})


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _array(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _integer(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a literal boolean")
    return value


def _ratio(numerator: int, denominator: int, required: int) -> dict[str, object]:
    return {
        "actual": f"{numerator}/{denominator}",
        "required": "100%" if required == 100 else f">={required}%",
        "passed": denominator > 0 and numerator * 100 >= denominator * required,
    }


def _legacy_metrics(value: object) -> dict[str, dict[str, object]]:
    raw = _mapping(value, "legacyV1Metrics")
    leakage = _integer(raw.get("leakageCount"), "leakageCount")
    return {
        "zeroLeakage": {"actual": leakage, "required": 0, "passed": leakage == 0},
        "anchorResolution": _ratio(
            _integer(raw.get("anchorResolved"), "anchorResolved"),
            _integer(raw.get("anchorTotal"), "anchorTotal"),
            100,
        ),
        "groundedClaimCitationPrecision": _ratio(
            _integer(raw.get("groundedSupported"), "groundedSupported"),
            _integer(raw.get("groundedTotal"), "groundedTotal"),
            100,
        ),
        "retrievalRecallAt10": _ratio(
            _integer(raw.get("retrievalRelevantAt10"), "retrievalRelevantAt10"),
            _integer(raw.get("retrievalRelevantTotal"), "retrievalRelevantTotal"),
            90,
        ),
        "abstainAccuracy": _ratio(
            _integer(raw.get("abstainCorrect"), "abstainCorrect"),
            _integer(raw.get("abstainTotal"), "abstainTotal"),
            90,
        ),
    }


def evaluate(profile_value: object, *, real: bool = False) -> dict[str, object]:
    profile = _mapping(profile_value, "profile")
    if (
        profile.get("schemaVersion") != "quality-kb-trusted-profile-v1"
        or profile.get("profileId") != "QUALITY-KB-TRUSTED-01"
    ):
        raise ValueError("unsupported trusted knowledge profile")
    dataset = _mapping(profile.get("dataset"), "dataset")
    documents = [
        _mapping(value, "document")
        for value in _array(profile.get("documents"), "documents")
    ]
    cases = [_mapping(value, "case") for value in _array(profile.get("cases"), "cases")]
    file_splits: dict[str, str] = {}
    for document in documents:
        file_id = document.get("fileId")
        split = document.get("split")
        if not isinstance(file_id, str) or not file_id:
            raise ValueError("fileId must be nonblank")
        if split not in {"tuning", "acceptance"}:
            raise ValueError("document split must be tuning or acceptance")
        if file_id in file_splits:
            raise ValueError("tuning and acceptance sets must split by file")
        file_splits[file_id] = split
    if len(documents) < 100:
        raise ValueError("trusted knowledge gate requires at least 100 files")
    if len(cases) < 200:
        raise ValueError("trusted knowledge gate requires at least 200 questions")
    case_ids: set[str] = set()
    topic_counts: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    for case in cases:
        case_id = case.get("caseId")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise ValueError("question case IDs must be nonblank and unique")
        case_ids.add(case_id)
        if case.get("fileId") not in file_splits:
            raise ValueError("question references an unknown file")
        topic = case.get("topic")
        if topic not in _TOPICS:
            raise ValueError("question topic is not an enabled critical-fact type")
        topic_counts[str(topic)] += 1
        totals["relevant50"] += _integer(
            case.get("retrievalRelevantAt50"), "retrievalRelevantAt50"
        )
        totals["relevantTotal"] += _integer(
            case.get("retrievalRelevantTotal"), "retrievalRelevantTotal"
        )
        totals["top10Covered"] += _integer(
            case.get("top10EvidenceCovered"), "top10EvidenceCovered"
        )
        totals["top10Total"] += _integer(
            case.get("top10EvidenceTotal"), "top10EvidenceTotal"
        )
        totals["correct"] += int(
            _boolean(case.get("correctAndSufficient"), "correctAndSufficient")
        )
        totals["located"] += int(
            _boolean(case.get("citationLocated"), "citationLocated")
        )
        requires_notice = _boolean(
            case.get("requiresNoAnswerOrConflict"), "requiresNoAnswerOrConflict"
        )
        notice_correct = _boolean(
            case.get("noAnswerOrConflictCorrect"), "noAnswerOrConflictCorrect"
        )
        totals["noticeTotal"] += int(requires_notice)
        totals["noticeCorrect"] += int(requires_notice and notice_correct)
        totals["criticalErrors"] += _integer(
            case.get("criticalFactErrorCount"), "criticalFactErrorCount"
        )
    if set(topic_counts) != _TOPICS or any(
        count < 20 for count in topic_counts.values()
    ):
        raise ValueError(
            "every enabled critical-fact topic requires at least 20 questions"
        )

    legacy = _legacy_metrics(profile.get("legacyV1Metrics"))
    trusted: dict[str, dict[str, object]] = {
        "minimumFiles": {
            "actual": len(documents),
            "required": ">=100",
            "passed": len(documents) >= 100,
        },
        "minimumQuestions": {
            "actual": len(cases),
            "required": ">=200",
            "passed": len(cases) >= 200,
        },
        "retrievalRecallAt50": _ratio(
            totals["relevant50"], totals["relevantTotal"], 95
        ),
        "top10EvidenceCoverage": _ratio(
            totals["top10Covered"], totals["top10Total"], 90
        ),
        "correctAndSufficientAnswer": _ratio(totals["correct"], len(cases), 90),
        "citationLocation": _ratio(totals["located"], len(cases), 98),
        "noAnswerConflictNotice": _ratio(
            totals["noticeCorrect"], totals["noticeTotal"], 95
        ),
        "zeroKnownCriticalFactErrors": {
            "actual": totals["criticalErrors"],
            "required": 0,
            "passed": totals["criticalErrors"] == 0,
        },
    }
    if real:
        if dataset.get("reviewStatus") != "approved":
            raise ValueError("real trusted knowledge gate requires approved review")
        bindings = _mapping(profile.get("bindings"), "bindings")
        actual_model = bindings.get("actualModel")
        if not isinstance(actual_model, str) or actual_model.startswith(
            ("fake/", "pending/", "simulated/")
        ):
            raise ValueError("real trusted knowledge gate requires a real model")
        dataset_material = {
            "profileId": profile["profileId"],
            "version": dataset.get("version"),
            "documents": documents,
            "cases": cases,
        }
        config_material = {
            "legacyV1Thresholds": {
                "leakage": 0,
                "anchor": "100%",
                "precision": "100%",
                "recallAt10": ">=90%",
                "abstain": ">=90%",
            },
            "trustedThresholds": {
                "recallAt50": ">=95%",
                "top10Evidence": ">=90%",
                "correctSufficient": ">=90%",
                "citationLocation": ">=98%",
                "noAnswerConflict": ">=95%",
                "criticalFactErrors": 0,
            },
        }
        validate_candidate_batch(
            profile.get("runEvidence"),
            expected_case_ids=sorted(case_ids),
            dataset_material=dataset_material,
            config_material=config_material,
            model_material=bindings,
        )
    return {
        "profileId": profile["profileId"],
        "legacyV1Metrics": legacy,
        "trustedKnowledgeMetrics": trusted,
        "topicQuestionCounts": dict(sorted(topic_counts.items())),
        "passed": all(item["passed"] for item in legacy.values())
        and all(item["passed"] for item in trusted.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--real", action="store_true")
    arguments = parser.parse_args()
    profile = json.loads(arguments.profile.read_text(encoding="utf-8"))
    report = evaluate(profile, real=arguments.real)
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
