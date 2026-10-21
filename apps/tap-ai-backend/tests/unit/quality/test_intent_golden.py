"""Exercise the intent golden set's schema validation and scoring against the
shared pure functions in `tap.quality.intent_golden`, plus the script-level
real-mode refusal in `scripts/run-intent-golden.py` (loaded via
`importlib.util` because the script's filename has hyphens, following the
same pattern `tests/unit/quality/test_answer_golden.py` uses for
`scripts/run-answer-golden.py`)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tap.modules.chat.domain.answer_plan import AGENT_INTENTS
from tap.quality.intent_golden import (
    compute_intent_metrics,
    fake_gate_passed,
    observations_by_id,
    real_gate_passed,
    validate_intent_golden,
)

ROOT = Path(__file__).resolve().parents[5]
RUN_SCRIPT = ROOT / "scripts" / "run-intent-golden.py"
GOLDEN_PATH = ROOT / "apps/tap-ai-backend/tests/fixtures/quality/intents/golden-v1.json"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def run_module() -> ModuleType:
    return _load(RUN_SCRIPT, "run_intent_golden")


def _golden_json() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


# --- golden fixture shape -----------------------------------------------------


def test_fixture_has_five_cases_per_intent() -> None:
    golden = validate_intent_golden(_golden_json())

    assert len(golden.cases) >= 30
    counts = {intent: 0 for intent in AGENT_INTENTS}
    for case in golden.cases:
        counts[case.expected_intent] += 1
    for intent, count in counts.items():
        assert count >= 5, f"{intent} has only {count} cases"

    ids = [case.id for case in golden.cases]
    assert len(ids) == len(set(ids)), "case ids must be unique"

    locales = {case.locale for case in golden.cases}
    assert locales == {"zh", "en"}

    out_of_scope_en = [
        case
        for case in golden.cases
        if case.expected_intent == "out_of_scope" and case.locale == "en"
    ]
    assert len(out_of_scope_en) >= 2


def test_validate_intent_golden_rejects_short_intent_class() -> None:
    document = {
        "schemaVersion": "intent-golden-v1",
        "labeledBy": "unit-test",
        "labeledAt": "2026-10-19",
        "cases": [
            {
                "id": f"c{index}",
                "question": f"question {index}",
                "locale": "en",
                "expectedIntent": "knowledge_qa",
            }
            for index in range(5)
        ]
        + [
            {
                "id": "clarify-only",
                "question": "it changed how?",
                "locale": "en",
                "expectedIntent": "clarify",
            }
        ],
    }
    with pytest.raises(ValueError, match="at least 5 cases per agent intent"):
        validate_intent_golden(document)


def test_validate_intent_golden_rejects_duplicate_ids() -> None:
    cases = [
        {
            "id": "dup",
            "question": f"q{index}",
            "locale": "en",
            "expectedIntent": "knowledge_qa",
        }
        for index in range(2)
    ]
    document = {
        "schemaVersion": "intent-golden-v1",
        "labeledBy": "unit-test",
        "labeledAt": "2026-10-19",
        "cases": cases,
    }
    with pytest.raises(ValueError, match="not unique"):
        validate_intent_golden(document)


def test_validate_intent_golden_rejects_unknown_intent() -> None:
    document = {
        "schemaVersion": "intent-golden-v1",
        "labeledBy": "unit-test",
        "labeledAt": "2026-10-19",
        "cases": [
            {
                "id": "bad-intent",
                "question": "q",
                "locale": "en",
                "expectedIntent": "not_a_real_intent",
            }
        ],
    }
    with pytest.raises(ValueError, match="expectedIntent"):
        validate_intent_golden(document)


# --- metrics on handmade observations ----------------------------------------


def _handmade_golden() -> dict:
    cases = []
    for intent in sorted(AGENT_INTENTS):
        for index in range(5):
            cases.append(
                {
                    "id": f"{intent}-{index}",
                    "question": f"{intent} question {index}",
                    "locale": "en",
                    "expectedIntent": intent,
                }
            )
    return {
        "schemaVersion": "intent-golden-v1",
        "labeledBy": "unit-test",
        "labeledAt": "2026-10-19",
        "cases": cases,
    }


def test_compute_intent_metrics_scores_accuracy_and_per_intent_recall() -> None:
    golden = validate_intent_golden(_handmade_golden())
    observations = observations_by_id(
        [
            {
                "id": case.id,
                "agentIntent": case.expected_intent,
                "plannerSource": "model",
            }
            for case in golden.cases
        ]
    )

    metrics = compute_intent_metrics(golden, observations)

    assert metrics["accuracy"] == pytest.approx(1.0)
    for intent in AGENT_INTENTS:
        assert metrics[f"recall.{intent}"] == pytest.approx(1.0)


def test_compute_intent_metrics_requires_an_observation_for_every_case() -> None:
    golden = validate_intent_golden(_handmade_golden())
    with pytest.raises(ValueError, match="missing cases"):
        compute_intent_metrics(golden, {})


def test_fake_gate_accepts_model_and_heuristic_but_forbids_fallback() -> None:
    """Fix round 1 (I-2): `"heuristic"` is a legitimate `plannerSource` --
    `AnswerPlanner.plan()` only attempts a model call when its own pre-model
    heuristics already decided the route is `retrieve`/`graph`, so a case
    that never reached the model by design must not fail the gate. Only
    `"fallback"` (the model was attempted and rejected) and missing
    coverage are wiring defects."""
    golden = validate_intent_golden(_handmade_golden())

    model_sourced = observations_by_id(
        [
            {"id": case.id, "agentIntent": case.expected_intent, "plannerSource": "model"}
            for case in golden.cases
        ]
    )
    assert fake_gate_passed(golden, model_sourced) is True

    one_heuristic = dict(model_sourced)
    first_id = golden.cases[0].id
    one_heuristic[first_id] = {
        **model_sourced[first_id],
        "plannerSource": "heuristic",
    }
    assert fake_gate_passed(golden, one_heuristic) is True

    one_fallback = dict(model_sourced)
    one_fallback[first_id] = {
        **model_sourced[first_id],
        "plannerSource": "fallback",
    }
    assert fake_gate_passed(golden, one_fallback) is False

    missing_case = {
        case_id: value for case_id, value in model_sourced.items() if case_id != first_id
    }
    assert fake_gate_passed(golden, missing_case) is False


def test_fake_gate_requires_model_for_every_model_eligible_case() -> None:
    """Fix round 2 (gap): a `"heuristic"` observation on a case the production
    planner's own pre-model heuristic marks `modelEligible` is exactly the
    regression this closes -- `plan()` silently stopped attempting a model
    call for a question it should have tried. A non-eligible case staying
    `"heuristic"` is still fine."""
    golden = validate_intent_golden(_handmade_golden())

    baseline = observations_by_id(
        [
            {
                "id": case.id,
                "agentIntent": case.expected_intent,
                "plannerSource": "model",
                "modelEligible": True,
            }
            for case in golden.cases
        ]
    )
    assert fake_gate_passed(golden, baseline) is True

    first_id = golden.cases[0].id
    eligible_but_heuristic = dict(baseline)
    eligible_but_heuristic[first_id] = {
        **baseline[first_id],
        "plannerSource": "heuristic",
    }
    assert fake_gate_passed(golden, eligible_but_heuristic) is False

    ineligible_and_heuristic = dict(baseline)
    ineligible_and_heuristic[first_id] = {
        **baseline[first_id],
        "plannerSource": "heuristic",
        "modelEligible": False,
    }
    assert fake_gate_passed(golden, ineligible_and_heuristic) is True


def test_fake_gate_requires_agent_intent_to_be_a_known_value() -> None:
    golden = validate_intent_golden(_handmade_golden())
    observations = observations_by_id(
        [
            {"id": case.id, "agentIntent": case.expected_intent, "plannerSource": "model"}
            for case in golden.cases
        ]
    )
    first_id = golden.cases[0].id
    observations[first_id] = {**observations[first_id], "agentIntent": "not_a_real_intent"}

    assert fake_gate_passed(golden, observations) is False


def test_real_gate_thresholds() -> None:
    passing_metrics = {"accuracy": 0.8, **{f"recall.{intent}": 0.6 for intent in AGENT_INTENTS}}
    assert real_gate_passed(passing_metrics) is True

    failing_accuracy = {**passing_metrics, "accuracy": 0.79}
    assert real_gate_passed(failing_accuracy) is False

    failing_recall = dict(passing_metrics)
    one_intent = next(iter(AGENT_INTENTS))
    failing_recall[f"recall.{one_intent}"] = 0.59
    assert real_gate_passed(failing_recall) is False


# --- script-level real-mode refusal ------------------------------------------


def test_real_mode_refuses_without_tap_run_quality_intents(
    run_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("TAP_RUN_QUALITY_INTENTS", raising=False)
    observations_path = tmp_path / "observations.json"
    report_path = tmp_path / "report.json"
    argv = [
        "run-intent-golden.py",
        "--mode",
        "real",
        "--golden",
        str(GOLDEN_PATH),
        "--observations",
        str(observations_path),
        "--report",
        str(report_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    exit_code = run_module.main()

    assert exit_code == 2
    assert not observations_path.exists()
    assert not report_path.exists()
    assert "TAP_RUN_QUALITY_INTENTS" in capsys.readouterr().err


def test_real_mode_refuses_without_model_execution_authorization(
    run_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TAP_RUN_QUALITY_INTENTS", "1")
    monkeypatch.delenv("TAP_QUALITY_MODEL_EXECUTION_AUTHORIZATION", raising=False)
    observations_path = tmp_path / "observations.json"
    report_path = tmp_path / "report.json"
    argv = [
        "run-intent-golden.py",
        "--mode",
        "real",
        "--golden",
        str(GOLDEN_PATH),
        "--observations",
        str(observations_path),
        "--report",
        str(report_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    exit_code = run_module.main()

    assert exit_code == 2
    assert not observations_path.exists()
    assert not report_path.exists()
    assert "TAP_QUALITY_MODEL_EXECUTION_AUTHORIZATION" in capsys.readouterr().err


# --- fake mode drives the real production planning assembly -----------------


def test_fake_mode_drives_the_real_planner_with_the_deterministic_gateway(
    run_module: ModuleType,
    tmp_path: Path,
) -> None:
    """Fix round 1 (I-1): the recurring defect on this branch is wiring only a
    named layer; this proves `run-intent-golden.py --mode fake` goes through
    the real production assembly `AnswerPlanner(ModelGatewayPlanner(...))`
    (not a stub planner and not a bypass of `AnswerPlanner`'s heuristics/
    validation/fallback) backed by `DeterministicModelGateway`, that every
    case is reported with an observed (not assumed) `plannerSource`, and
    that the model is never attempted-and-rejected (`"fallback"`) against
    the always-schema-conformant deterministic gateway."""
    observations_path = tmp_path / "observations.json"
    report_path = tmp_path / "report.json"

    exit_code = run_module.main(
        [
            "--mode",
            "fake",
            "--golden",
            str(GOLDEN_PATH),
            "--observations",
            str(observations_path),
            "--report",
            str(report_path),
        ]
    )

    assert exit_code == 0
    observations = json.loads(observations_path.read_text(encoding="utf-8"))
    assert observations["mode"] == "fake"
    golden = validate_intent_golden(_golden_json())
    by_id = observations_by_id(observations["observations"])
    assert set(by_id) == {case.id for case in golden.cases}
    planner_sources = {observation["plannerSource"] for observation in by_id.values()}
    assert planner_sources <= {"model", "heuristic"}
    assert "fallback" not in planner_sources
    # The deterministic gateway only ever reaches the model if
    # `AnswerPlanner.plan()`'s pre-model heuristics decide the question is
    # eligible; a fixture with no model-eligible case at all would make
    # this gate vacuous.
    assert "model" in planner_sources
    # Fix round 2: every modelEligible case must have been model-sourced --
    # the gate's own invariant, re-checked directly against the raw
    # observations (not just via `fakeGatePassed`).
    model_eligible_count = 0
    for observation in by_id.values():
        assert observation["agentIntent"] in AGENT_INTENTS
        if observation["modelEligible"]:
            model_eligible_count += 1
            assert observation["plannerSource"] == "model"
    assert model_eligible_count > 0

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["fakeGatePassed"] is True


@pytest.mark.asyncio
async def test_fake_gate_fails_when_a_model_eligible_case_never_calls_the_model(
    run_module: ModuleType,
) -> None:
    """Fix round 2 (gap): reproduces the exact regression this round's fix
    guards against -- the production planner's model dependency silently
    becoming unavailable (`AnswerPlanner(None)`) for a question that is
    `modelEligible`. This uses the real production `AnswerPlanner` with
    `model=None`, not a mock, to simulate it; `modelEligible` is derived the
    same way (`run_module._model_eligible`), so the mismatch is exactly what
    `fake_gate_passed` is built to catch."""
    from tap.modules.chat.application.plan_answer import AnswerPlanner
    from tap.quality.intent_golden import GoldenIntentCase, IntentGoldenSet

    case = GoldenIntentCase(
        id="regression-check",
        question="测试用例评审的通过标准是什么？",
        locale="zh",
        expected_intent="knowledge_qa",
    )
    golden = IntentGoldenSet(
        schema_version="intent-golden-v1",
        labeled_by="unit-test",
        labeled_at="2026-10-19",
        cases=(case,),
    )

    model_eligible = await run_module._model_eligible(case)
    assert model_eligible is True  # sanity: a plain factual question is model-eligible

    # The regression: the planner's model dependency silently became `None`,
    # so `AnswerPlanner.plan()` takes its `self._model is None` short-circuit
    # and never attempts a model call, even though the question is eligible.
    broken_plan = await AnswerPlanner(None).plan(
        run_module._planning_input(case, source_ids=run_module._SOURCE_IDS)
    )
    observations = observations_by_id(
        [
            {
                "id": case.id,
                "agentIntent": broken_plan.agent_intent,
                "plannerSource": "heuristic",
                "modelEligible": model_eligible,
            }
        ]
    )

    assert fake_gate_passed(golden, observations) is False


@pytest.mark.asyncio
async def test_fake_mode_surfaces_the_planners_agent_intent_not_the_heuristic_default(
    run_module: ModuleType,
) -> None:
    """Fix round 1 (I-2): proves end-to-end `agent_intent` propagation through
    the real `AnswerPlanner(ModelGatewayPlanner(...))` assembly -- if a
    future regression broke `plan_answer.py`'s `build(...)` from forwarding
    the model's `raw["agent_intent"]` into the final `AnswerPlan` (Task 4),
    this test would fail. The stub model returns `agent_intent:
    "document_analysis"`, which `derive_agent_intent` would never produce on
    its own for a plain factual `retrieve` question (it would derive
    `"knowledge_qa"`) -- so the only way the final plan can show
    `"document_analysis"` is if the model's classification actually
    survived into the validated `AnswerPlan`."""
    from tap.quality.intent_golden import GoldenIntentCase

    case = GoldenIntentCase(
        id="propagation-check",
        question="测试用例评审的通过标准是什么？",
        locale="zh",
        expected_intent="knowledge_qa",
    )

    async def stub_model(value, timeout):  # noqa: ANN001, ANN201
        return {
            "intent": "factual_lookup",
            "route": "retrieve",
            "confidence": 0.9,
            "standalone_query": value.original_question,
            "missing": [],
            "queries": [
                {
                    "id": "q1",
                    "text": value.original_question,
                    "depends_on": [],
                    "evidence_goal": value.original_question,
                    "source_ids": list(value.source_ids),
                }
            ],
            "agent_intent": "document_analysis",
            "intent_reason": "stub propagation check",
        }

    plan, model_called = await run_module._plan_case(case, stub_model)

    assert model_called is True
    assert plan.degradation_reason is None
    assert plan.agent_intent == "document_analysis"
