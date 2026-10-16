"""Exercise the capability-1 answer golden set's schema validation and
scoring, directly against the shared pure functions in
`tap.quality.answer_golden`, plus the script-level real-mode refusal in
`scripts/run-answer-golden.py` (loaded via `importlib.util` because the
script's filename has hyphens, following the same pattern
`tests/unit/quality/test_evaluate_graph_samples.py` uses for
`scripts/evaluate-graph-samples.py`).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tap.quality.answer_golden import (
    cited_sources_from_claims,
    compute_metrics,
    fact_group_present,
    fake_gate_passed,
    gate_passed,
    normalize_fact_text,
    observations_by_id,
    rank_paragraphs,
    validate_golden,
)

ROOT = Path(__file__).resolve().parents[5]
RUN_SCRIPT = ROOT / "scripts" / "run-answer-golden.py"
GOLDEN_PATH = ROOT / "apps/tap-ai-backend/tests/fixtures/quality/answers/golden-v1.json"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses with `from __future__ import annotations` resolve field types via
    # `sys.modules[cls.__module__]`, so the module must be registered before exec.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def run_module() -> ModuleType:
    return _load(RUN_SCRIPT, "run_answer_golden")


def _golden_json() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


# --- golden fixture shape ----------------------------------------------------


def test_golden_fixture_is_valid() -> None:
    corpus_dir = GOLDEN_PATH.parent
    known_sources = frozenset(path.stem for path in corpus_dir.glob("*.md"))
    assert known_sources, "fixture corpus must contain at least one document"

    golden = validate_golden(_golden_json(), known_sources=known_sources)

    assert len(golden.cases) == 30
    kinds = [case.kind for case in golden.cases]
    assert kinds.count("answerable") == 20
    assert kinds.count("abstain") == 6
    assert kinds.count("false_premise") == 4

    ids = [case.id for case in golden.cases]
    assert len(ids) == len(set(ids)), "case ids must be unique"

    for case in golden.cases:
        assert set(case.expected_sources) <= known_sources


def test_golden_fixture_expected_facts_are_not_lifted_from_the_question_text() -> None:
    """A fact phrase that is itself verbatim in the question can be satisfied by an
    answer that merely echoes the question, without the model actually having
    retrieved or stated the fact -- a false-positive risk the golden set must not
    contain for `answerable` cases."""
    golden = validate_golden(_golden_json())
    for case in golden.cases:
        if case.kind != "answerable":
            continue
        normalized_question = normalize_fact_text(case.question)
        for group in case.expected_facts:
            for phrase in group:
                assert normalize_fact_text(phrase) not in normalized_question, (
                    f"case {case.id} expectedFacts phrase {phrase!r} is lifted "
                    "verbatim from its own question text"
                )


def test_golden_fixture_false_premise_forbidden_facts_are_phrasing_groups() -> None:
    golden = validate_golden(_golden_json())
    false_premise = [case for case in golden.cases if case.kind == "false_premise"]
    assert len(false_premise) == 4
    for case in false_premise:
        assert len(case.forbidden_facts) >= 1
        for group in case.forbidden_facts:
            assert 2 <= len(group) <= 3


# --- fact normalization and grouping ----------------------------------------


def test_normalize_fact_text_folds_full_width_and_markdown_escapes() -> None:
    assert normalize_fact_text("１５个工作日") == normalize_fact_text("15个工作日")
    assert normalize_fact_text("80\\%") == normalize_fact_text("80%")
    assert normalize_fact_text("15 个   工作日") == normalize_fact_text("15个工作日")


def test_fact_group_present_matches_any_alternative() -> None:
    group = ["犹豫期是30天", "犹豫期为30天", "30天的犹豫期"]
    assert fact_group_present(group, "本保单犹豫期为30天，请知悉。") is True
    assert fact_group_present(group, "本保单犹豫期为15天，请知悉。") is False
    # Full-width digits in the haystack still match an ASCII-digit group.
    assert fact_group_present(["30%"], "额度为３０％。") is True


# --- pure retrieval/citation helpers -----------------------------------------


def test_rank_paragraphs_orders_by_bigram_overlap_then_input_order() -> None:
    paragraphs = [
        ("doc-a", "完全不相关的内容"),
        ("doc-b", "犹豫期是15日"),
        ("doc-c", "犹豫期是15日"),
    ]
    ranked = rank_paragraphs("犹豫期是多少日", paragraphs, limit=2)
    assert ranked == (("doc-b", "犹豫期是15日"), ("doc-c", "犹豫期是15日"))


def test_rank_paragraphs_respects_limit() -> None:
    paragraphs = [(f"doc-{index}", f"段落{index}的犹豫期") for index in range(10)]
    ranked = rank_paragraphs("犹豫期", paragraphs, limit=3)
    assert len(ranked) == 3


def test_cited_sources_from_claims_dedupes_in_first_seen_order() -> None:
    claims = [
        {"text": "a", "evidenceLabels": ["S1", "S2"]},
        {"text": "b", "evidenceLabels": ["S3"]},
        {"text": "c", "evidenceLabels": ["S1"]},
    ]
    label_sources = {"S1": "doc-a", "S2": "doc-b", "S3": "doc-a"}
    assert cited_sources_from_claims(claims, label_sources) == ("doc-a", "doc-b")


def test_cited_sources_from_claims_ignores_malformed_labels() -> None:
    claims = [{"text": "a", "evidenceLabels": "not-a-list"}, {"text": "b"}]
    assert cited_sources_from_claims(claims, {"S1": "doc-a"}) == ()


# --- metrics on handmade observations ---------------------------------------


def _handmade_golden() -> dict:
    return {
        "schemaVersion": "answer-golden-v1",
        "labeledBy": "unit-test",
        "labeledAt": "2026-10-16",
        "cases": [
            {
                "id": "x1",
                "question": "q1",
                "locale": "zh",
                "kind": "answerable",
                "expectedSources": ["doc-a", "doc-b"],
                "expectedFacts": ["fact1", "fact2"],
                "forbiddenFacts": [],
            },
            {
                "id": "x2",
                "question": "q2",
                "locale": "zh",
                "kind": "abstain",
                "expectedSources": ["doc-a"],
                "expectedFacts": [],
                "forbiddenFacts": [],
            },
            {
                "id": "x3",
                "question": "q3",
                "locale": "zh",
                "kind": "false_premise",
                "expectedSources": ["doc-a"],
                "expectedFacts": ["corrected"],
                "forbiddenFacts": ["wrong"],
            },
        ],
    }


def test_metrics_on_handmade_observations() -> None:
    golden = validate_golden(_handmade_golden())
    observations = observations_by_id(
        [
            {
                "id": "x1",
                "abstained": False,
                "answer": "fact1 only here",
                "citedSources": ["doc-a", "doc-c"],
                "claimCount": 1,
            },
            {"id": "x2", "abstained": True, "answer": "", "citedSources": [], "claimCount": 0},
            {
                "id": "x3",
                "abstained": False,
                "answer": "corrected text with wrong mention",
                "citedSources": ["doc-a"],
                "claimCount": 1,
            },
        ]
    )

    metrics = compute_metrics(golden, observations)

    assert metrics["citationSourcePrecision"] == pytest.approx(0.5)
    assert metrics["expectedSourceRecall"] == pytest.approx(0.5)
    assert metrics["factRecall"] == pytest.approx(0.75)
    assert metrics["abstainAccuracy"] == pytest.approx(1.0)
    assert metrics["falsePremiseHandled"] == pytest.approx(1.0)
    assert metrics["forbiddenFactRate"] == pytest.approx(1.0)
    assert metrics["invalidCount"] == pytest.approx(0.0)
    # citationSourcePrecision (0.5) and expectedSourceRecall (0.5) are below the
    # fake-mode gate's required 1.0.
    assert fake_gate_passed(golden, observations, metrics) is False


def test_metrics_treat_invalid_observations_as_failures() -> None:
    golden = validate_golden(_handmade_golden())
    observations = observations_by_id(
        [
            {
                "id": "x1",
                "invalid": True,
                "abstained": True,  # must be ignored: invalid overrides it
                "answer": "",
                "citedSources": [],
                "claimCount": 0,
            },
            {"id": "x2", "invalid": True, "abstained": True, "answer": "", "citedSources": []},
            {"id": "x3", "invalid": True, "abstained": True, "answer": "", "citedSources": []},
        ]
    )

    metrics = compute_metrics(golden, observations)

    assert metrics["invalidCount"] == pytest.approx(3.0)
    # x1 is answerable and invalid: precision denominator grows with zero credit,
    # recall gets no credit either.
    assert metrics["citationSourcePrecision"] == pytest.approx(0.0)
    assert metrics["expectedSourceRecall"] == pytest.approx(0.0)
    # x2 is abstain but invalid, so it must not count as a legitimate abstention.
    assert metrics["abstainAccuracy"] == pytest.approx(0.0)
    # x3 is false_premise but invalid, so it must not count as handled.
    assert metrics["falsePremiseHandled"] == pytest.approx(0.0)


def test_fake_gate_requires_every_answerable_case_to_produce_a_claim() -> None:
    golden = validate_golden(_handmade_golden())
    observations = observations_by_id(
        [
            {
                "id": "x1",
                "abstained": False,
                "answer": "fact1 fact2 here",
                "citedSources": ["doc-a", "doc-b"],
                "claimCount": 0,  # wiring regression: cites correctly but produced no claims
            },
            {"id": "x2", "abstained": True, "answer": "", "citedSources": [], "claimCount": 0},
            {
                "id": "x3",
                "abstained": True,
                "answer": "",
                "citedSources": [],
                "claimCount": 0,
            },
        ]
    )
    metrics = compute_metrics(golden, observations)
    assert metrics["citationSourcePrecision"] == pytest.approx(1.0)
    assert metrics["expectedSourceRecall"] == pytest.approx(1.0)
    assert fake_gate_passed(golden, observations, metrics) is False


# --- real-mode baseline gate direction ---------------------------------------


def test_gate_passed_fails_below_baseline_and_passes_when_equal() -> None:
    baseline_metrics = {
        "citationSourcePrecision": 1.0,
        "expectedSourceRecall": 1.0,
        "factRecall": 0.8,
        "abstainAccuracy": 1.0,
        "falsePremiseHandled": 1.0,
        "forbiddenFactRate": 0.0,
        "invalidCount": 0.0,
    }
    worse_fact_recall = dict(baseline_metrics, factRecall=0.7)
    equal_metrics = dict(baseline_metrics)

    assert gate_passed(worse_fact_recall, baseline_metrics) is False
    assert gate_passed(equal_metrics, baseline_metrics) is True


def test_gate_passed_fails_when_forbidden_fact_rate_rises() -> None:
    baseline_metrics = {
        "citationSourcePrecision": 1.0,
        "expectedSourceRecall": 1.0,
        "factRecall": 1.0,
        "abstainAccuracy": 1.0,
        "falsePremiseHandled": 1.0,
        "forbiddenFactRate": 0.0,
        "invalidCount": 0.0,
    }
    worse = dict(baseline_metrics, forbiddenFactRate=0.1)
    better = dict(baseline_metrics, forbiddenFactRate=0.0)

    assert gate_passed(worse, baseline_metrics) is False
    assert gate_passed(better, baseline_metrics) is True


def test_gate_passed_fails_when_invalid_count_rises() -> None:
    baseline_metrics = {
        "citationSourcePrecision": 1.0,
        "expectedSourceRecall": 1.0,
        "factRecall": 1.0,
        "abstainAccuracy": 1.0,
        "falsePremiseHandled": 1.0,
        "forbiddenFactRate": 0.0,
        "invalidCount": 1.0,
    }
    worse = dict(baseline_metrics, invalidCount=2.0)
    equal = dict(baseline_metrics, invalidCount=1.0)

    assert gate_passed(worse, baseline_metrics) is False
    assert gate_passed(equal, baseline_metrics) is True


def test_compute_metrics_requires_an_observation_for_every_case() -> None:
    golden = validate_golden(
        {
            "schemaVersion": "answer-golden-v1",
            "labeledBy": "unit-test",
            "labeledAt": "2026-10-16",
            "cases": [
                {
                    "id": "x1",
                    "question": "q1",
                    "locale": "zh",
                    "kind": "answerable",
                    "expectedSources": ["doc-a"],
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="missing cases"):
        compute_metrics(golden, {})


# --- script-level real-mode refusal ------------------------------------------


def test_real_mode_refuses_without_tap_run_quality_answers(
    run_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("TAP_RUN_QUALITY_ANSWERS", raising=False)
    observations_path = tmp_path / "observations.json"
    argv = [
        "run-answer-golden.py",
        "--mode",
        "real",
        "--golden",
        str(GOLDEN_PATH),
        "--observations",
        str(observations_path),
        "--corpus-dir",
        str(GOLDEN_PATH.parent),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    exit_code = run_module.main()

    assert exit_code == 2
    assert not observations_path.exists()
    assert "TAP_RUN_QUALITY_ANSWERS" in capsys.readouterr().err
