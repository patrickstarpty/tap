"""Exercise the capability-1 answer golden set's schema validation and
scoring, by importing the pure functions directly out of
`scripts/run-answer-golden.py` and `scripts/evaluate-answer-golden.py`.

Loaded via `importlib.util` because the scripts' filenames have hyphens,
following the same pattern `tests/unit/quality/test_evaluate_graph_samples.py`
uses for `scripts/evaluate-graph-samples.py`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[5]
RUN_SCRIPT = ROOT / "scripts" / "run-answer-golden.py"
EVALUATE_SCRIPT = ROOT / "scripts" / "evaluate-answer-golden.py"
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


@pytest.fixture(scope="module")
def evaluate_module() -> ModuleType:
    return _load(EVALUATE_SCRIPT, "evaluate_answer_golden")


def _golden_json() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def test_golden_fixture_is_valid(run_module: ModuleType) -> None:
    corpus_dir = GOLDEN_PATH.parent
    known_sources = frozenset(path.stem for path in corpus_dir.glob("*.md"))
    assert known_sources, "fixture corpus must contain at least one document"

    golden = run_module.validate_golden(_golden_json(), known_sources=known_sources)

    assert len(golden.cases) == 30
    kinds = [case.kind for case in golden.cases]
    assert kinds.count("answerable") == 20
    assert kinds.count("abstain") == 6
    assert kinds.count("false_premise") == 4

    ids = [case.id for case in golden.cases]
    assert len(ids) == len(set(ids)), "case ids must be unique"

    for case in golden.cases:
        assert set(case.expected_sources) <= known_sources


def test_evaluate_module_parses_the_same_golden_fixture(evaluate_module: ModuleType) -> None:
    golden = evaluate_module.validate_golden(_golden_json())
    assert len(golden.cases) == 30


def test_metrics_on_handmade_observations(evaluate_module: ModuleType) -> None:
    golden = {
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
    observations = {
        "schemaVersion": "answer-golden-observations-v1",
        "mode": "fake",
        "build": "test",
        "observations": [
            {
                "id": "x1",
                "abstained": False,
                "answer": "fact1 only here",
                "citedSources": ["doc-a", "doc-c"],
                "claimCount": 1,
            },
            {
                "id": "x2",
                "abstained": True,
                "answer": "",
                "citedSources": [],
                "claimCount": 0,
            },
            {
                "id": "x3",
                "abstained": False,
                "answer": "corrected text with wrong mention",
                "citedSources": ["doc-a"],
                "claimCount": 1,
            },
        ],
    }

    report = evaluate_module.evaluate(golden, observations)
    metrics = report["metrics"]

    assert metrics["citationSourcePrecision"] == pytest.approx(0.5)
    assert metrics["expectedSourceRecall"] == pytest.approx(0.5)
    assert metrics["factRecall"] == pytest.approx(0.75)
    assert metrics["abstainAccuracy"] == pytest.approx(1.0)
    assert metrics["falsePremiseHandled"] == pytest.approx(1.0)
    assert metrics["forbiddenFactRate"] == pytest.approx(1.0)
    # citationSourcePrecision (0.5) is below the fake-mode gate's required 1.0.
    assert report["passed"] is False


def test_real_gate_fails_below_baseline(evaluate_module: ModuleType) -> None:
    baseline_metrics = {
        "citationSourcePrecision": 1.0,
        "expectedSourceRecall": 1.0,
        "factRecall": 0.8,
        "abstainAccuracy": 1.0,
        "falsePremiseHandled": 1.0,
        "forbiddenFactRate": 0.0,
    }
    worse_metrics = dict(baseline_metrics, factRecall=0.7)
    equal_metrics = dict(baseline_metrics)

    assert evaluate_module.gate_passed(worse_metrics, baseline_metrics) is False
    assert evaluate_module.gate_passed(equal_metrics, baseline_metrics) is True


def test_evaluate_requires_an_observation_for_every_case(evaluate_module: ModuleType) -> None:
    golden = {
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
                "expectedFacts": [],
                "forbiddenFacts": [],
            }
        ],
    }
    observations = {
        "schemaVersion": "answer-golden-observations-v1",
        "mode": "fake",
        "build": "test",
        "observations": [],
    }
    with pytest.raises(ValueError, match="missing cases"):
        evaluate_module.evaluate(golden, observations)
