"""Frozen, permission-bounded Copilot profile export."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.copilot_probe.models import ProbeAgent
from scripts.copilot_probe.process import ProbeRun, classify_capture, parse_jsonl, run_probe
from scripts.copilot_probe.profiles import export_profiles


def agent(
    agent_id: str,
    *,
    description: str = "负责审查",
    tools: tuple[str, ...] = ("read",),
    enabled: bool = True,
    revision_id: str = "r1",
    instruction: str = "核对证据",
) -> ProbeAgent:
    return ProbeAgent(agent_id, revision_id, description, instruction, tools, enabled)


def test_profiles_include_all_allowed_agents_without_prefix(tmp_path: Path) -> None:
    agents = tuple(agent(x) for x in ("orchestrator", "qae", "sdet"))
    files = export_profiles(tmp_path, agents, frozenset({"orchestrator", "qae"}))
    assert set(files) == {".github/agents/orchestrator.agent.md", ".github/agents/qae.agent.md"}
    for relative, digest in files.items():
        data = (tmp_path / relative).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest


def test_disabled_and_unauthorized_agents_are_not_exported(tmp_path: Path) -> None:
    files = export_profiles(tmp_path, (agent("ba", enabled=False), agent("sde")), frozenset({"ba"}))
    assert files == {}
    assert not (tmp_path / ".github/agents").exists()


@pytest.mark.parametrize("agent_id", ("../escape", "nested/agent", "Bad", "_bad", "", "a.b"))
def test_invalid_agent_id_is_rejected(tmp_path: Path, agent_id: str) -> None:
    with pytest.raises(ValueError):
        export_profiles(tmp_path, (agent(agent_id),), frozenset({agent_id}))
    assert not (tmp_path / ".github/agents").exists()


def test_duplicate_agent_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_profiles(tmp_path, (agent("qae"), agent("qae")), frozenset({"qae"}))
    assert not (tmp_path / ".github/agents").exists()


def test_blank_description_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_profiles(tmp_path, (agent("qae", description="  \n"),), frozenset({"qae"}))


def test_yaml_description_and_body_newlines_are_encoded_safely(tmp_path: Path) -> None:
    export_profiles(
        tmp_path,
        (agent("qae", description='review: "evidence"\nnext', instruction="first\n---\nlast"),),
        frozenset({"qae"}),
    )
    content = (tmp_path / ".github/agents/qae.agent.md").read_text()
    assert 'description: "review: \\"evidence\\"\\nnext"' in content
    assert content.startswith("---\n")
    assert content.count("\n---\n") == 2
    assert "first\n---\nlast" in content


def test_empty_tools_are_explicitly_denied(tmp_path: Path) -> None:
    export_profiles(tmp_path, (agent("qae", tools=()),), frozenset({"qae"}))
    content = (tmp_path / ".github/agents/qae.agent.md").read_text()
    assert "tools: []\n" in content
    assert "strict-tools-list: true\n" in content


def test_unknown_tool_is_rejected_without_partial_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_profiles(
            tmp_path, (agent("qae"), agent("sdet", tools=("*",))), frozenset({"qae", "sdet"})
        )
    assert not (tmp_path / ".github/agents").exists()


def test_existing_snapshot_is_verified_without_rewrite(tmp_path: Path) -> None:
    agents = (agent("qae"),)
    first = export_profiles(tmp_path, agents, frozenset({"qae"}))
    profile = tmp_path / ".github/agents/qae.agent.md"
    before = profile.stat().st_mtime_ns
    bytes_before = profile.read_bytes()
    second = export_profiles(tmp_path, agents, frozenset({"qae"}))
    assert first == second
    assert profile.stat().st_mtime_ns == before
    assert profile.read_bytes() == bytes_before


def test_changed_revision_cannot_replace_frozen_snapshot(tmp_path: Path) -> None:
    export_profiles(tmp_path, (agent("qae"),), frozenset({"qae"}))
    profile = tmp_path / ".github/agents/qae.agent.md"
    before = profile.read_bytes()
    with pytest.raises(ValueError):
        export_profiles(tmp_path, (agent("qae", revision_id="r2"),), frozenset({"qae"}))
    assert profile.read_bytes() == before


def test_symlinked_agents_parent_cannot_escape_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "work"
    root.mkdir()
    (root / ".github").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        export_profiles(root, (agent("qae"),), frozenset({"qae"}))
    assert list(outside.iterdir()) == []


def test_symlinked_root_cannot_export_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "linked-workspace"
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        export_profiles(root, (agent("qae"),), frozenset({"qae"}))

    assert list(outside.iterdir()) == []


STUB = Path(__file__).resolve().parents[2] / "fixtures/copilot/stub_cli.py"


def stub_spec(mode: str, tmp_path: Path, *, seconds: int = 2) -> ProbeRun:
    return ProbeRun((sys.executable, str(STUB), mode), tmp_path, dict(os.environ), seconds)


def test_zero_exit_without_terminal_is_incomplete() -> None:
    assert classify_capture(0, False, False) == "incomplete"
    assert classify_capture(None, True, False) == "timed_out"
    assert classify_capture(0, False, True) == "complete"
    assert classify_capture(2, False, True) == "failed"


def test_interleaved_streams_and_fragmented_json_are_captured(tmp_path: Path) -> None:
    capture = asyncio.run(run_probe(stub_spec("interleaved", tmp_path)))
    assert capture.exit_code == 0
    assert capture.stdout_lines == ('{"event":"first"}', '{"event":"second"}')
    assert capture.stderr_lines == ("warning one", "warning two")
    assert parse_jsonl(capture.stdout_lines) == (({"event": "first"}, {"event": "second"}), ())
    assert classify_capture(capture.exit_code, capture.timed_out, False) == "incomplete"


def test_invalid_json_is_reported_without_inventing_event(tmp_path: Path) -> None:
    capture = asyncio.run(run_probe(stub_spec("invalid", tmp_path)))
    assert parse_jsonl(capture.stdout_lines) == ((), (1,))


def test_long_line_has_bounded_capture(tmp_path: Path) -> None:
    capture = asyncio.run(run_probe(stub_spec("long", tmp_path)))
    assert capture.exit_code == 0
    assert len(capture.stdout_lines) == 1
    assert len(capture.stdout_lines[0].encode()) <= 16384
    assert capture.truncated


def test_total_capture_is_bounded(tmp_path: Path) -> None:
    capture = asyncio.run(run_probe(stub_spec("many", tmp_path)))
    assert capture.exit_code == 0
    assert sum(len(x.encode()) for x in (*capture.stdout_lines, *capture.stderr_lines)) <= 65536
    assert capture.truncated


def test_nonzero_exit_is_failure(tmp_path: Path) -> None:
    capture = asyncio.run(run_probe(stub_spec("nonzero", tmp_path)))
    assert capture.exit_code == 7
    assert classify_capture(capture.exit_code, capture.timed_out, True) == "failed"


def test_timeout_kills_parent_and_descendant(tmp_path: Path) -> None:
    capture = asyncio.run(run_probe(stub_spec("wait", tmp_path, seconds=1)))
    assert capture.timed_out
    assert classify_capture(capture.exit_code, capture.timed_out, False) == "timed_out"
    pid = int((tmp_path / "child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_parent_exit_still_kills_descendant(tmp_path: Path) -> None:
    capture = asyncio.run(run_probe(stub_spec("orphan", tmp_path, seconds=1)))
    assert capture.exit_code == 0
    pid = int((tmp_path / "child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_cancel_kills_descendant(tmp_path: Path) -> None:
    async def invoke() -> None:
        task = asyncio.create_task(run_probe(stub_spec("wait", tmp_path, seconds=10)))
        for _ in range(100):
            if (tmp_path / "child.pid").exists():
                break
            await asyncio.sleep(0.01)
        assert (tmp_path / "child.pid").exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(invoke())
    pid = int((tmp_path / "child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_cli_without_verified_meter_skips_and_records_unverified(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    run = subprocess.run(
        (
            sys.executable,
            "-m",
            "scripts.copilot_probe.cli",
            "--scenario",
            "simple",
            "--output",
            str(output),
            "--max-seconds",
            "5",
            "--max-model-calls",
            "1",
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0
    report = json.loads(output.read_text())
    assert report["status"] == "unverified"
    assert report["budget_enforced"] is False
    assert report["model_calls_used"] is None


def test_cli_rejects_unknown_scenario(tmp_path: Path) -> None:
    run = subprocess.run(
        (
            sys.executable,
            "-m",
            "scripts.copilot_probe.cli",
            "--scenario",
            "private-data",
            "--output",
            str(tmp_path / "result.json"),
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode != 0
    assert not (tmp_path / "result.json").exists()
