import importlib.util
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts/evaluate-quality-test-design.py"
GENERATOR = ROOT / "scripts/generate-quality-test-design-profile.py"


def _evaluator():
    spec = importlib.util.spec_from_file_location("evaluate_quality_test_design", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generator():
    spec = importlib.util.spec_from_file_location("generate_quality_test_design_profile", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _profile() -> dict:
    cases = []
    for index in range(50):
        cases.append(
            {
                "caseId": f"intent-{index:03d}",
                "intent": f"Verify business workflow {index}",
                "source": "A successful operation creates an immutable record.",
                "observation": {
                    "schemaValid": True,
                    "bddValid": True,
                    "unsupportedFactCount": 0,
                    "criticalCovered": 1,
                    "criticalTotal": 1,
                    "criticalCorrectionRequired": False,
                },
                "reviewerJudgments": [
                    {"reviewer": "patrick", "approved": True},
                ],
            }
        )
    return {
        "schemaVersion": "quality-test-profile-v1",
        "profileId": "QUALITY-TEST-01",
        "dataset": {"version": "unit-v1", "reviewStatus": "approved"},
        "bindings": {"actualModel": "provider/model"},
        "cases": cases,
    }


def test_quality_test_thresholds_pass_complete_observations() -> None:
    report = _evaluator().evaluate(_profile())
    assert report["passed"] is True
    assert report["metrics"]["criticalRequirementCoverage"]["actual"] == "50/50"


@pytest.mark.parametrize(
    ("mutation", "metric"),
    [
        (
            lambda profile: profile["cases"][0]["observation"].update(schemaValid=False),
            "schemaAndBdd",
        ),
        (lambda profile: profile["cases"][0]["observation"].update(bddValid=False), "schemaAndBdd"),
        (
            lambda profile: profile["cases"][0]["observation"].update(unsupportedFactCount=1),
            "unsupportedSourceFacts",
        ),
        (
            lambda profile: [
                profile["cases"][index]["observation"].update(criticalCovered=0)
                for index in range(6)
            ],
            "criticalRequirementCoverage",
        ),
        (
            lambda profile: [
                profile["cases"][index]["observation"].update(criticalCorrectionRequired=True)
                for index in range(11)
            ],
            "withoutCriticalCorrection",
        ),
    ],
)
def test_quality_test_reports_specific_failure(mutation, metric) -> None:
    profile = _profile()
    mutation(profile)
    report = _evaluator().evaluate(profile)
    assert report["passed"] is False
    assert report["metrics"][metric]["passed"] is False


def test_real_gate_requires_one_named_approved_reviewer() -> None:
    module = _evaluator()
    profile = _profile()
    profile["bindings"].update(module.current_bindings())
    for case in profile["cases"]:
        case["reviewerJudgments"] = []
    with pytest.raises(ValueError, match="one approved named reviewer"):
        module.evaluate(profile, real=True)


def test_reviewer_name_rejects_role_prefixes() -> None:
    module = _evaluator()
    for reviewer in ("human:patrick", "machine-self-review"):
        profile = _profile()
        profile["cases"][0]["reviewerJudgments"][0]["reviewer"] = reviewer
        with pytest.raises(ValueError, match="without a prefix"):
            module.evaluate(profile)


def test_generated_profile_is_ready_for_one_named_reviewer_gate() -> None:
    profile = _generator().build()

    assert profile["dataset"] == {
        "version": "candidate-v1",
        "reviewStatus": "approved",
        "labelingMethod": "named-review-with-deterministic-adjudication",
    }


def test_real_make_target_uses_validated_model_timeout(tmp_path: Path) -> None:
    log = tmp_path / "timeouts.log"
    uv = tmp_path / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "${TAPPER_MODEL_TIMEOUT_SECONDS:-unset}" '
        '>> "$TAP_TEST_TIMEOUT_LOG"\n',
        encoding="utf-8",
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{tmp_path}:{environment['PATH']}",
            "TAP_RUN_QUALITY_TEST_01": "1",
            "TAP_TEST_TIMEOUT_LOG": str(log),
        }
    )

    subprocess.run(
        ["make", "--no-print-directory", "quality-test-design-real"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert log.read_text(encoding="utf-8").splitlines()[0] == "60"
