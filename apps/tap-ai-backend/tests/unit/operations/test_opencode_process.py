"""Process and CLI evidence for the pinned OpenCode probe."""

import asyncio
import os
import sys
from pathlib import Path

import pytest
from scripts.opencode_probe.process import ProbeRun, classify_capture, run_probe

STUB = Path(__file__).resolve().parents[2] / "fixtures/opencode/stub_cli.py"


def spec(tmp_path: Path, mode: str, timeout: int = 3) -> ProbeRun:
    return ProbeRun(
        (sys.executable, str(STUB), mode), tmp_path, {"PATH": os.environ["PATH"]}, timeout
    )


def test_zero_exit_without_terminal_is_incomplete() -> None:
    assert classify_capture(0, False, False) == "incomplete"
    assert classify_capture(None, True, False) == "timed_out"
    assert classify_capture(0, False, True) == "complete"
    assert classify_capture(7, False, True) == "failed"


@pytest.mark.asyncio
async def test_interleaved_streams_and_split_json_are_captured(tmp_path: Path) -> None:
    result = await run_probe(spec(tmp_path, "interleaved"))
    assert result.exit_code == 0
    assert result.stdout_lines == ('{"type":"step_finish","sessionID":"ses_test"}',)
    assert result.stderr_lines == ("diagnostic",)
    assert not result.timed_out


@pytest.mark.asyncio
async def test_invalid_json_and_nonzero_exit_remain_evidence(tmp_path: Path) -> None:
    result = await run_probe(spec(tmp_path, "invalid"))
    assert result.stdout_lines == ("{invalid",)
    assert result.exit_code == 7
    assert classify_capture(result.exit_code, result.timed_out, False) == "failed"


@pytest.mark.asyncio
async def test_oversized_line_fails_closed(tmp_path: Path) -> None:
    result = await run_probe(spec(tmp_path, "long"))
    assert result.exit_code is not None
    assert result.truncated
    assert all(len(line) <= 4096 for line in result.stdout_lines)


@pytest.mark.asyncio
async def test_timeout_reaps_child_after_leader_exits(tmp_path: Path) -> None:
    result = await run_probe(spec(tmp_path, "orphan", timeout=1))
    assert result.timed_out
    child_pid = int((tmp_path / "child.pid").read_text())
    await asyncio.sleep(0.1)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


@pytest.mark.asyncio
async def test_zero_exit_reaps_descendant_with_closed_stdio(tmp_path: Path) -> None:
    result = await run_probe(spec(tmp_path, "closed_stdio_orphan"))
    assert result.exit_code == 0
    assert not result.timed_out
    child_pid = int((tmp_path / "child.pid").read_text())
    await asyncio.sleep(0.1)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


@pytest.mark.asyncio
async def test_timeout_force_kills_term_ignoring_descendant(tmp_path: Path) -> None:
    result = await run_probe(spec(tmp_path, "stubborn", timeout=1))
    assert result.timed_out
    child_pid = int((tmp_path / "child.pid").read_text())
    await asyncio.sleep(0.1)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


@pytest.mark.asyncio
async def test_cancellation_reaps_process_group(tmp_path: Path) -> None:
    task = asyncio.create_task(run_probe(spec(tmp_path, "hang", timeout=10)))
    for _ in range(100):
        if (tmp_path / "child.pid").exists():
            break
        await asyncio.sleep(0.01)
    assert (tmp_path / "child.pid").exists()
    child_pid = int((tmp_path / "child.pid").read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.1)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


@pytest.mark.asyncio
async def test_serve_preflight_keeps_server_alive_until_context_exit(tmp_path: Path) -> None:
    from scripts.opencode_probe.cli import make_child_env
    from scripts.opencode_probe.models import ProbeAgent
    from scripts.opencode_probe.process import serve_probe
    from scripts.opencode_probe.profiles import export_probe_config, export_profiles

    digest = export_probe_config(tmp_path)
    profiles = export_profiles(
        tmp_path,
        (ProbeAgent("orchestrator", "r1", "fixture", "Inspect", ("read",), True),),
        frozenset({"orchestrator"}),
    )
    env = make_child_env(tmp_path, "http://127.0.0.1:54321", "scoped", "fixture-password")
    async with serve_probe(
        (sys.executable, str(STUB)),
        tmp_path,
        env,
        timeout_seconds=5,
        profile_hashes=profiles,
        config_digest=digest,
    ) as client:
        client.verify_schema()
        assert (tmp_path / "serve.pid").exists()
        pid = int((tmp_path / "serve.pid").read_text())
        os.kill(pid, 0)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_serve_rejects_unapproved_effective_config_before_launch(tmp_path: Path) -> None:
    from scripts.opencode_probe.cli import make_child_env
    from scripts.opencode_probe.models import ProbeAgent
    from scripts.opencode_probe.process import serve_probe
    from scripts.opencode_probe.profiles import export_probe_config, export_profiles

    digest = export_probe_config(tmp_path)
    profiles = export_profiles(
        tmp_path,
        (ProbeAgent("orchestrator", "r1", "fixture", "Inspect", ("read",), True),),
        frozenset({"orchestrator"}),
    )
    env = make_child_env(tmp_path, "http://127.0.0.1:54321", "scoped", "fixture-password")
    env["STUB_BAD_CONFIG"] = "1"
    with pytest.raises(ValueError, match="effective"):
        async with serve_probe(
            (sys.executable, str(STUB)),
            tmp_path,
            env,
            timeout_seconds=5,
            profile_hashes=profiles,
            config_digest=digest,
        ):
            pytest.fail("unapproved config reached serve")
    assert not (tmp_path / "serve.pid").exists()
