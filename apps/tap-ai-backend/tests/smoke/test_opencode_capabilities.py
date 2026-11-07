"""Explicitly opted-in, bounded real OpenCode smoke; never runs by default."""

import asyncio
import os
from pathlib import Path

import pytest
from scripts.opencode_probe.evaluate import verified_provenance
from scripts.opencode_probe.scenarios import run_scenario


@pytest.mark.skipif(
    os.environ.get("TAP_RUN_TAPPER_REAL_MODEL_SMOKE") != "1"
    or os.environ.get("TAP_RUN_OPENCODE_PROBE") != "1",
    reason="real OpenCode probe requires both explicit opt-ins",
)
def test_live_simple_question_uses_pinned_cli_and_bounded_gateway() -> None:
    gateway_env = os.environ.get("TAP_OPENCODE_GATEWAY_ENV_FILE")
    ledger_file = os.environ.get("TAP_OPENCODE_LEDGER")
    assert gateway_env and ledger_file, (
        "real smoke requires an approved local gateway env file and shared ledger"
    )
    root = Path(__file__).resolve().parents[4]
    report = asyncio.run(
        run_scenario(
            "simple-question",
            binary=root / ".superpowers/tools/opencode-1.19.5/opencode",
            gateway_env_file=Path(gateway_env),
            ledger_path=Path(ledger_file),
            max_calls=3,
            max_seconds=90,
            max_cost_usd=0.20,
        )
    )
    assert verified_provenance(report)
    assert report["status"] == "supported"
    assert report["gate"]["calls"] <= 3
    assert report["gate"]["denials"] == []
