"""Deterministic failure boundaries for live OpenCode scenario supervision."""

import signal
import subprocess
import threading

import pytest
from scripts.opencode_probe import scenarios
from scripts.opencode_probe.gateway import CallRecord
from scripts.opencode_probe.ledger import ProbeLedger


class ClosingGate:
    def __init__(self, *, append_on_close: bool) -> None:
        self.append_on_close = append_on_close
        self.calls = 0
        self.records: list[CallRecord] = []
        self.denials: list[object] = []
        self.budget_exceeded = False
        self._lock = threading.Lock()
        self._closed = False
        self._server: object | None = object()
        self._thread: object | None = object()

    def __enter__(self) -> "ClosingGate":
        return self

    def __exit__(self, *_args: object) -> None:
        if self.append_on_close:
            self.records.append(
                CallRecord("probe_test", None, "ses_test", 200, True, 100, 10, 0.01)
            )
        self._closed = True
        self._server = None
        self._thread = None


@pytest.mark.parametrize("scenario", ["simple-question", "config-freeze"])
def test_admitted_call_without_record_keeps_worst_case_after_close(tmp_path, scenario: str) -> None:
    ledger = ProbeLedger(tmp_path / "ledger.jsonl")
    reservation_id = ledger.reserve(scenario, 2, 0.20)
    report: dict[str, object] = {}
    gate = ClosingGate(append_on_close=False)
    with scenarios._accounted_gate(gate, ledger, reservation_id, 0.052224, report):
        gate.calls = 1
    assert gate._closed
    assert report["gate"]["calls"] == 1
    assert report["gate"]["unrecorded_calls"] == 1
    assert ledger.totals() == (1, 0.052224)


@pytest.mark.parametrize("scenario", ["simple-question", "config-freeze"])
def test_gateway_close_record_is_included_before_settlement(tmp_path, scenario: str) -> None:
    ledger = ProbeLedger(tmp_path / "ledger.jsonl")
    reservation_id = ledger.reserve(scenario, 2, 0.20)
    report: dict[str, object] = {}
    gate = ClosingGate(append_on_close=True)
    with scenarios._accounted_gate(gate, ledger, reservation_id, 0.052224, report):
        gate.calls = 1
    assert report["gate"]["records"][0]["gate_request_id"] == "probe_test"
    assert report["gate"]["unrecorded_calls"] == 0
    assert ledger.totals() == (1, 0.01)


def test_permission_category_comes_from_structured_tool_error() -> None:
    assert (
        scenarios._tool_denial_category(
            {"status": "error", "error": {"name": "PermissionDeniedError"}}
        )
        == "opencode_permission_denied"
    )
    assert scenarios._tool_denial_category({"status": "error", "error": {"code": "EACCES"}}) is None
    assert (
        scenarios._tool_denial_category(
            {"status": "error", "error": "Permission denied by filesystem"}
        )
        is None
    )


def test_permission_target_requires_exact_workspace_file_not_same_basename(tmp_path) -> None:
    expected = tmp_path / "denied.txt"
    unrelated = tmp_path.parent / "denied.txt"
    assert scenarios._tool_target_matches(str(expected), tmp_path)
    assert scenarios._tool_target_matches("denied.txt", tmp_path)
    assert not scenarios._tool_target_matches(str(unrelated), tmp_path)
    assert not scenarios._tool_target_matches("../denied.txt", tmp_path)


def test_permission_facts_keep_only_linked_target_and_denial_category(tmp_path) -> None:
    class Client:
        def _request(self, _method, path):
            if path == "/session/ses_root/message":
                return 200, []
            if path == "/session/ses_root/children":
                return 200, [{"id": "ses_child", "parentID": "ses_root", "agent": "qae"}]
            if path == "/session/ses_child/message":
                return 200, [
                    {
                        "parts": [
                            {
                                "type": "tool",
                                "tool": "write",
                                "state": {
                                    "status": "error",
                                    "input": {"filePath": str(tmp_path / "denied.txt")},
                                    "error": {"name": "PermissionDeniedError"},
                                },
                            }
                        ]
                    }
                ]
            raise AssertionError(path)

    facts = scenarios._facts(Client(), "ses_root", tmp_path)
    tool = facts["children"][0]["tools"][0]
    assert tool == {
        "tool": "write",
        "status": "error",
        "file": "denied.txt",
        "target_is_denied_workspace_file": True,
        "denial_category": "opencode_permission_denied",
    }


def _mock_waiter_process(
    monkeypatch,
    workspace,
    *,
    pgid: int = 44,
    started: str = "Sat Nov  7 12:34:56 2026",
    cwd=None,
    script=None,
) -> None:
    (workspace / "waiter.py").write_text("print('synthetic')")
    script = script or workspace / "waiter.py"
    cwd = cwd or workspace

    def fake_run(args, **_kwargs):
        if args[:3] == ("ps", "-p", "123"):
            return subprocess.CompletedProcess(
                args, 0, f"123 {pgid} {started} python3 python3 {script}\n", ""
            )
        if args[:5] == ("lsof", "-a", "-p", "123", "-d"):
            return subprocess.CompletedProcess(args, 0, f"p123\nfcwd\nn{cwd}\n", "")
        raise AssertionError(args)

    monkeypatch.setattr(scenarios.subprocess, "run", fake_run)


def test_owned_marker_rejects_unrelated_same_basename_and_group(tmp_path, monkeypatch) -> None:
    (tmp_path / "owned.pid").write_text("123")
    _mock_waiter_process(monkeypatch, tmp_path, script=tmp_path.parent / "other" / "waiter.py")
    assert scenarios._owned_marker_status(tmp_path, 44)["identity_verified"] is False
    _mock_waiter_process(monkeypatch, tmp_path, pgid=99)
    assert scenarios._owned_marker_status(tmp_path, 44)["identity_verified"] is False


def test_owned_marker_rejects_unrelated_cwd(tmp_path, monkeypatch) -> None:
    (tmp_path / "owned.pid").write_text("123")
    _mock_waiter_process(monkeypatch, tmp_path, cwd=tmp_path.parent)
    assert scenarios._owned_marker_status(tmp_path, 44)["identity_verified"] is False


def test_owned_marker_revalidates_start_identity_before_signal(tmp_path, monkeypatch) -> None:
    (tmp_path / "owned.pid").write_text("123")
    _mock_waiter_process(monkeypatch, tmp_path)
    prior = scenarios._owned_marker_status(tmp_path, 44)
    assert prior["identity_verified"] is True
    _mock_waiter_process(monkeypatch, tmp_path, started="Sat Nov  7 12:35:57 2026")
    signalled = []
    monkeypatch.setattr(scenarios.os, "kill", lambda pid, sig: signalled.append((pid, sig)))
    assert scenarios._signal_owned_waiter(tmp_path, 44, prior) is False
    assert signalled == []


def test_owned_marker_signals_only_revalidated_process(tmp_path, monkeypatch) -> None:
    (tmp_path / "owned.pid").write_text("123")
    _mock_waiter_process(monkeypatch, tmp_path)
    prior = scenarios._owned_marker_status(tmp_path, 44)
    signalled = []
    monkeypatch.setattr(scenarios.os, "kill", lambda pid, sig: signalled.append((pid, sig)))
    assert scenarios._signal_owned_waiter(tmp_path, 44, prior) is True
    assert signalled == [(123, signal.SIGTERM)]


def test_profile_provenance_changes_with_revision() -> None:
    old = scenarios._profiles(orchestrator_revision="r1")
    new = scenarios._profiles(orchestrator_revision="r2")
    hashes = {".opencode/agents/orchestrator.md": "a" * 64}
    first = scenarios._profile_provenance(old, hashes)
    second = scenarios._profile_provenance(new, hashes)
    assert first["revisions"]["orchestrator"] == "r1"
    assert second["revisions"]["orchestrator"] == "r2"
    assert first["aggregate_digest"] != second["aggregate_digest"]
