"""Deterministic failure boundaries for live OpenCode scenario supervision."""

import hashlib
import signal
import subprocess
import sys
import threading
import time

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


def test_first_action_count_is_strict_append_only_marker(tmp_path) -> None:
    marker = tmp_path / "first-action.log"
    assert scenarios._first_action_count(tmp_path) is None
    marker.write_bytes(b"start\n")
    assert scenarios._first_action_count(tmp_path) == 1
    marker.write_bytes(b"start\nstart\n")
    assert scenarios._first_action_count(tmp_path) == 2
    marker.write_bytes(b"start\ninvalid\n")
    assert scenarios._first_action_count(tmp_path) is None


def test_supervisor_waiter_has_one_durable_first_action_and_stops_before_second(tmp_path) -> None:
    from scripts.opencode_probe.resume_fixture import prepare_resume_fixture

    digest = prepare_resume_fixture(tmp_path)
    script = tmp_path / "waiter.py"
    assert digest == hashlib.sha256(script.read_bytes()).hexdigest()
    before = script.stat().st_mtime_ns
    assert prepare_resume_fixture(tmp_path) == digest
    assert script.stat().st_mtime_ns == before
    process = subprocess.Popen((sys.executable, str(script)), cwd=tmp_path)
    try:
        deadline = time.monotonic() + 3
        while not (tmp_path / "first.txt").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "first.txt").read_text() == "first"
        assert (tmp_path / "owned.pid").read_text() == str(process.pid)
        assert (tmp_path / "first-action.log").read_bytes() == b"start\n"
        assert scenarios._first_action_count(tmp_path) == 1
    finally:
        process.terminate()
        process.wait(timeout=3)
    assert not (tmp_path / "second.txt").exists()
    assert scenarios._first_action_count(tmp_path) == 1
    script.write_text("changed")
    with pytest.raises(ValueError, match="differs"):
        prepare_resume_fixture(tmp_path)
    assert script.read_text() == "changed"


def test_resume_conditions_record_missing_observations(tmp_path) -> None:
    conditions = scenarios._resume_preconditions(
        tmp_path, active_root=True, active_bash=False, marker={"identity_verified": False}
    )
    assert conditions == {
        "first_present": False,
        "second_absent": True,
        "root_busy": True,
        "bash_running": False,
        "owned_process_verified": False,
        "first_action_count": None,
        "fixture_unchanged": False,
    }


def test_resume_conditions_reject_changed_supervisor_fixture(tmp_path) -> None:
    from scripts.opencode_probe.resume_fixture import prepare_resume_fixture

    digest = prepare_resume_fixture(tmp_path)
    (tmp_path / "first.txt").write_text("first")
    (tmp_path / "first-action.log").write_text("start\n")
    good = scenarios._resume_preconditions(
        tmp_path,
        active_root=True,
        active_bash=True,
        marker={"identity_verified": True},
        fixture_digest=digest,
    )
    assert good["fixture_unchanged"] is True
    (tmp_path / "waiter.py").write_text("changed")
    bad = scenarios._resume_preconditions(
        tmp_path,
        active_root=True,
        active_bash=True,
        marker={"identity_verified": True},
        fixture_digest=digest,
    )
    assert bad["fixture_unchanged"] is False


def test_generated_script_source_and_artifact_digest_are_bounded(tmp_path) -> None:
    script = tmp_path / "square.py"
    script.write_text("print(4, 9)\n")
    assert scenarios._read_bounded_script(script) == "print(4, 9)\n"
    assert scenarios._bounded_artifact_digest(script) is not None
    script.write_bytes(b"x" * 4097)
    assert scenarios._read_bounded_script(script) is None
    script.write_bytes(b"x" * 1_048_577)
    assert scenarios._bounded_artifact_digest(script) is None


def test_permission_target_rejects_oversized_generated_artifact(tmp_path) -> None:
    (tmp_path / "denied.txt").write_bytes(b"x" * 1_048_577)
    assert scenarios._permission_target_digest(tmp_path) == "unsafe"


def test_generated_script_stdout_stops_at_capture_cap(tmp_path) -> None:
    script = tmp_path / "square.py"
    script.write_text("import time\nprint('x' * 300, flush=True)\ntime.sleep(10)\n")
    started = time.monotonic()
    exit_code, stdout, limit = scenarios._run_square_script(script, tmp_path)
    assert exit_code is None
    assert stdout is None
    assert limit == "stdout_cap"
    assert time.monotonic() - started < 2


def test_generated_script_stdout_rejects_invalid_encoding(tmp_path) -> None:
    script = tmp_path / "square.py"
    script.write_text("import sys\nsys.stdout.buffer.write(b'\\xff')\n")
    exit_code, stdout, limit = scenarios._run_square_script(script, tmp_path)
    assert exit_code is None
    assert stdout is None
    assert limit == "stdout_encoding"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("print(2 ** 2, 3 ** 2)\n", "4 9"),
        (
            "print('The square of 2 is', 2 ** 2)\nprint('The square of 3 is', 3 ** 2)\n",
            "The square of 2 is 4\nThe square of 3 is 9",
        ),
    ],
)
def test_generated_script_preserves_bounded_square_outputs(tmp_path, source, expected) -> None:
    script = tmp_path / "square.py"
    script.write_text(source)
    assert scenarios._safe_square_script(source)
    assert scenarios._run_square_script(script, tmp_path) == (0, expected, None)


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


@pytest.mark.parametrize("failure", ["timeout", "oserror", "malformed"])
def test_failed_process_observation_never_authorizes_resume(tmp_path, monkeypatch, failure) -> None:
    marker = tmp_path / "owned.pid"
    marker.write_text("123")
    _mock_waiter_process(monkeypatch, tmp_path)
    prior = scenarios._owned_marker_status(tmp_path, 44)
    assert prior["identity_verified"] is True

    def failed_ps(args, **_kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 3)
        if failure == "oserror":
            raise OSError("ps unavailable")
        return subprocess.CompletedProcess(args, 0, "malformed\n", "")

    monkeypatch.setattr(scenarios.subprocess, "run", failed_ps)
    current = scenarios._owned_marker_status(tmp_path, 44)
    assert current["alive"] is None
    assert scenarios._post_abort_action(prior, current) == "unknown"


@pytest.mark.parametrize("marker_text", [None, "invalid"])
def test_missing_or_invalid_marker_never_authorizes_resume(
    tmp_path, monkeypatch, marker_text
) -> None:
    marker = tmp_path / "owned.pid"
    marker.write_text("123")
    _mock_waiter_process(monkeypatch, tmp_path)
    prior = scenarios._owned_marker_status(tmp_path, 44)
    if marker_text is None:
        marker.unlink()
    else:
        marker.write_text(marker_text)
    current = scenarios._owned_marker_status(tmp_path, 44)
    assert current["alive"] is None
    assert scenarios._post_abort_action(prior, current) == "unknown"


def test_confirmed_absent_prior_process_authorizes_resume(tmp_path, monkeypatch) -> None:
    (tmp_path / "owned.pid").write_text("123")
    _mock_waiter_process(monkeypatch, tmp_path)
    prior = scenarios._owned_marker_status(tmp_path, 44)
    assert prior["identity_verified"] is True
    monkeypatch.setattr(
        scenarios.subprocess,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 1, "", ""),
    )
    probed = []

    def no_process(pid, sig):
        probed.append((pid, sig))
        raise ProcessLookupError("no such process")

    monkeypatch.setattr(scenarios.os, "kill", no_process)
    current = scenarios._owned_marker_status(tmp_path, 44)
    assert current["alive"] is False
    assert scenarios._post_abort_action(prior, current) == "continue"
    assert probed == [(123, 0)]


def test_ps_missing_without_esrch_does_not_authorize_resume(tmp_path, monkeypatch) -> None:
    (tmp_path / "owned.pid").write_text("123")
    _mock_waiter_process(monkeypatch, tmp_path)
    prior = scenarios._owned_marker_status(tmp_path, 44)
    monkeypatch.setattr(
        scenarios.subprocess,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 1, "", ""),
    )
    probed = []
    monkeypatch.setattr(scenarios.os, "kill", lambda pid, sig: probed.append((pid, sig)))
    current = scenarios._owned_marker_status(tmp_path, 44)
    assert current["alive"] is None
    assert scenarios._post_abort_action(prior, current) == "unknown"
    assert probed == [(123, 0)]


def test_profile_provenance_changes_with_revision() -> None:
    old = scenarios._profiles(orchestrator_revision="r1")
    new = scenarios._profiles(orchestrator_revision="r2")
    hashes = {".opencode/agents/orchestrator.md": "a" * 64}
    first = scenarios._profile_provenance(old, hashes)
    second = scenarios._profile_provenance(new, hashes)
    assert first["revisions"]["orchestrator"] == "r1"
    assert second["revisions"]["orchestrator"] == "r2"
    assert first["aggregate_digest"] != second["aggregate_digest"]
