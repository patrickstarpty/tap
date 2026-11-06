"""Real Copilot scenarios; deferred until isolated BYOK execution is ready."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.copilot_probe.evaluate import CAPABILITIES


@pytest.mark.parametrize("scenario", CAPABILITIES)
def test_real_copilot_capability(scenario: str, tmp_path: Path) -> None:
    if (
        os.environ.get("TAP_RUN_TAPPER_REAL_MODEL_SMOKE") != "1"
        or os.environ.get("TAP_RUN_COPILOT_PROBE") != "1"
    ):
        pytest.skip("Both real-model and Copilot probe opt-in flags are required")

    output = tmp_path / f"{scenario}.json"
    run = subprocess.run(
        (
            sys.executable,
            "-m",
            "scripts.copilot_probe.cli",
            "--scenario",
            scenario,
            "--output",
            str(output),
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(output.read_text())
    if report["execution"] != "executed":
        pytest.fail(f"{scenario}: {report['execution']}; {report['reason']}")
    assert run.returncode == 0
    assert report["status"] == "supported"
    assert report["evidence_refs"]
    assert report["budget_enforced"] is True
    assert isinstance(report["model_calls_used"], int)
    assert report["model_calls_used"] <= report["max_model_calls"]
