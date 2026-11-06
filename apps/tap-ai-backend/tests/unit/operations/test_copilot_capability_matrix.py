"""Deterministic gate tests; constructed support is not provider evidence."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.copilot_probe.evaluate import (
    REQUIRED_CAPABILITIES,
    capability_matrix,
    ready_for_runtime,
)
from scripts.copilot_probe.models import CapabilityResult


def supported(name: str) -> CapabilityResult:
    return CapabilityResult(name, "supported", (f"test-only:{name}",), None)


def test_unverified_event_identity_blocks_runtime() -> None:
    result = CapabilityResult("child_identity", "unverified", (), "未见子实例事件")
    assert not ready_for_runtime((result,), frozenset({"child_identity"}))


def test_all_fixed_required_capabilities_need_supported_evidence() -> None:
    results = tuple(supported(name) for name in sorted(REQUIRED_CAPABILITIES))
    assert ready_for_runtime(results, REQUIRED_CAPABILITIES)
    assert not ready_for_runtime(results[:-1], frozenset())
    assert not ready_for_runtime(
        results[:-1] + (CapabilityResult(results[-1].name, "supported", (), None),),
        REQUIRED_CAPABILITIES,
    )
    assert not ready_for_runtime(
        results[:-1]
        + (CapabilityResult(results[-1].name, "unsupported", ("test-only:failure",), None),),
        REQUIRED_CAPABILITIES,
    )


def test_duplicate_or_conflicting_results_block_runtime() -> None:
    results = tuple(supported(name) for name in sorted(REQUIRED_CAPABILITIES))
    assert not ready_for_runtime(results + (results[0],), REQUIRED_CAPABILITIES)
    assert not ready_for_runtime(
        results
        + (CapabilityResult(results[0].name, "unsupported", ("test-only:conflict",), None),),
        REQUIRED_CAPABILITIES,
    )


def test_matrix_preserves_unverified_scenarios_and_reports_missing_required() -> None:
    matrix = capability_matrix((supported("byok_all_calls"),))
    assert len(matrix) == 10
    assert matrix["byok_all_calls"].status == "supported"
    assert matrix["reconcile"].status == "unverified"
    assert matrix["reconcile"].evidence_refs == ()
    assert not ready_for_runtime(tuple(matrix.values()), REQUIRED_CAPABILITIES)


def test_cli_matrix_is_rerunnable_without_provider_evidence(tmp_path: Path) -> None:
    output = tmp_path / "matrix.json"
    run = subprocess.run(
        (
            sys.executable,
            "-m",
            "scripts.copilot_probe.cli",
            "--scenario",
            "matrix",
            "--output",
            str(output),
        ),
        capture_output=True,
        text=True,
        check=False,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in {"TAP_RUN_TAPPER_REAL_MODEL_SMOKE", "TAP_RUN_COPILOT_PROBE"}
        },
    )
    assert run.returncode == 0
    report = json.loads(output.read_text())
    assert report["ready_for_runtime"] is False
    assert len(report["capabilities"]) == 10
    assert {item["status"] for item in report["capabilities"]} == {"unverified"}
    assert all(not item["evidence_refs"] for item in report["capabilities"])


def test_double_opt_in_without_meter_is_blocked(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    env = dict(os.environ, TAP_RUN_TAPPER_REAL_MODEL_SMOKE="1", TAP_RUN_COPILOT_PROBE="1")
    run = subprocess.run(
        (
            sys.executable,
            "-m",
            "scripts.copilot_probe.cli",
            "--scenario",
            "simple",
            "--output",
            str(output),
        ),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert run.returncode != 0
    report = json.loads(output.read_text())
    assert report["execution"] == "blocked"
    assert report["status"] == "unverified"
    assert report["model_calls_used"] is None


@pytest.mark.parametrize(
    "enabled_flag", ("TAP_RUN_TAPPER_REAL_MODEL_SMOKE", "TAP_RUN_COPILOT_PROBE")
)
def test_single_opt_in_still_skips(tmp_path: Path, enabled_flag: str) -> None:
    output = tmp_path / "result.json"
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"TAP_RUN_TAPPER_REAL_MODEL_SMOKE", "TAP_RUN_COPILOT_PROBE"}
    }
    env[enabled_flag] = "1"
    run = subprocess.run(
        (
            sys.executable,
            "-m",
            "scripts.copilot_probe.cli",
            "--scenario",
            "simple",
            "--output",
            str(output),
        ),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert run.returncode == 0
    report = json.loads(output.read_text())
    assert report["execution"] == "skipped"
    assert report["status"] == "unverified"
