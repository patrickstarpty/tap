"""Opt-in negative full-chain fixture setup, without external/provider I/O."""

import importlib.util
import sys
from pathlib import Path

import pytest


def runner():
    path = Path(__file__).resolve().parents[5] / "scripts/run-browser-core-e2e.py"
    spec = importlib.util.spec_from_file_location("diagnostics_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_diagnostics_flag_only_allows_owned_synthetic_inline_form(monkeypatch):
    module = runner()
    base = [
        "runner",
        "--lca-diagnostics",
        "--lca-bridge",
        "--mode",
        "synthetic",
        "--execution-mode",
        "local",
        "--site",
        "owned",
        "--case",
        "form",
        "--opencode-binary",
        sys.executable,
    ]
    monkeypatch.setattr(sys, "argv", base)
    assert module.parse_args().lca_diagnostics is True
    for extra in [
        ["--mode", "real"],
        ["--site", "public"],
        ["--case", "lookup"],
        ["--lca-catalog"],
    ]:
        monkeypatch.setattr(sys, "argv", base + extra)
        with pytest.raises(SystemExit):
            module.parse_args()


def test_diagnostics_fixture_requests_only_readonly_or_pure_rejected_action():
    module = runner()
    actions = [
        {"type": "navigate", "url": "http://example.test/review"},
        {"type": "plan", "steps": []},
    ]
    result = module.diagnostics_fixture_actions(actions)
    assert [item.get("type") for item in result[:3]] == ["navigate", "plan", "snapshot"]
    assert result[3] == {"native_diagnostics_read": True}
    assert all(item.get("selector", "").startswith("aria/") for item in result[4:])
    assert all(item.get("type") not in {"fill", "select", "press"} for item in result)


def durable_fixture(tmp_path):
    import json

    snapshot = {"taskId": "task", "status": "failed"}
    event = {
        "node_id": "node",
        "attempt": 1,
        "schema_version": 1,
        "diagnostics_available": True,
        "diagnostics": [
            {"sequence": 1, "tool": "read", "status": "completed", "category": "none"},
            {
                "sequence": 2,
                "tool": "tap_browser",
                "status": "error",
                "category": "server_rejected",
            },
        ],
    }
    audit = {
        "status": "failed",
        "budget_used": {"model_calls": 1},
        "events": [
            {"kind": "browser.tool_diagnostics", "payload": event},
            {"kind": "browser.no_progress", "payload": {}},
            {"kind": "action.reserved", "payload": {"action_id": "call"}},
            {"kind": "action.settled", "payload": {"action_id": "call"}},
        ],
    }
    ledger = {
        "actions": [{"request": {"type": "snapshot"}, "status": "completed"}],
        "sessions": [{"stop_receipt": {"stopped": True}}],
    }
    for name, body in [
        ("form-snapshot.json", snapshot),
        ("task-audit.json", audit),
        ("browser-session-ledger.json", ledger),
    ]:
        (tmp_path / name).write_text(json.dumps(body))
    return audit, ledger


def test_durable_verifier_reads_export_and_writes_only_safe_summary(tmp_path):
    durable_fixture(tmp_path)
    runner().verify_diagnostics_evidence(tmp_path)
    assert (tmp_path / "diagnostics-durable-safe-summary.json").is_file()


@pytest.mark.parametrize(
    "failure", ["missing", "unavailable", "empty", "canary", "write", "unsettled", "wrong_status"]
)
def test_durable_verifier_rejects_incomplete_or_unsafe_evidence(tmp_path, failure):
    import json

    audit, ledger = durable_fixture(tmp_path)
    diagnostics = audit["events"][0]["payload"]
    if failure == "missing":
        audit["events"].pop(0)
    elif failure == "unavailable":
        diagnostics["diagnostics_available"] = False
    elif failure == "empty":
        diagnostics["diagnostics"] = []
    elif failure == "canary":
        diagnostics["diagnostics"][0]["raw"] = "DIAGNOSTIC_CANARY"
    elif failure == "write":
        ledger["actions"][0]["request"]["type"] = "click"
    elif failure == "unsettled":
        audit["events"].pop()
    elif failure == "wrong_status":
        audit["status"] = "succeeded"
    (tmp_path / "task-audit.json").write_text(json.dumps(audit))
    (tmp_path / "browser-session-ledger.json").write_text(json.dumps(ledger))
    with pytest.raises(AssertionError):
        runner().verify_diagnostics_evidence(tmp_path)
