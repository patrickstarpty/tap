"""Bounded, task-owned OpenCode subprocess capture."""

import asyncio
import os
import signal
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from scripts.opencode_probe.legacy import LegacyClient

_MAX_LINE = 4096
_MAX_LINES = 512
_READ_CHUNK = 4096
_REAP_SECONDS = 3


@dataclass(frozen=True)
class ProbeRun:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    timeout_seconds: int


@dataclass(frozen=True)
class ProbeCapture:
    exit_code: int | None
    stdout_lines: tuple[str, ...]
    stderr_lines: tuple[str, ...]
    timed_out: bool
    truncated: bool = False


def classify_capture(
    exit_code: int | None, timed_out: bool, terminal_seen: bool
) -> str:
    """Process status alone never establishes session completion."""
    if timed_out:
        return "timed_out"
    if exit_code != 0:
        return "failed"
    return "complete" if terminal_seen else "incomplete"


async def _read_lines(stream: asyncio.StreamReader) -> tuple[tuple[str, ...], bool]:
    lines: list[str] = []
    pending = bytearray()
    truncated = False
    while chunk := await stream.read(_READ_CHUNK):
        for byte in chunk:
            if byte == 10:
                if len(lines) < _MAX_LINES:
                    lines.append(pending.decode("utf-8", errors="replace"))
                else:
                    truncated = True
                pending.clear()
            elif len(pending) < _MAX_LINE:
                pending.append(byte)
            else:
                truncated = True
    if pending:
        if len(lines) < _MAX_LINES:
            lines.append(pending.decode("utf-8", errors="replace"))
        else:
            truncated = True
    return tuple(lines), truncated


def _signal_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


async def _reap(process: asyncio.subprocess.Process) -> None:
    _signal_group(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), 0.2)
    except TimeoutError:
        pass
    # The leader may already have exited while children still own the pipes.
    _signal_group(process.pid, signal.SIGKILL)
    await asyncio.wait_for(process.wait(), _REAP_SECONDS)


async def run_probe(spec: ProbeRun) -> ProbeCapture:
    """Run one explicit argv with bounded dual-stream capture and group cleanup."""
    if not spec.argv or spec.timeout_seconds <= 0 or not spec.cwd.is_dir():
        raise ValueError("invalid probe process specification")
    process = await asyncio.create_subprocess_exec(
        *spec.argv,
        cwd=spec.cwd,
        env=spec.env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        limit=_READ_CHUNK * 2,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout_task = asyncio.create_task(_read_lines(process.stdout))
    stderr_task = asyncio.create_task(_read_lines(process.stderr))
    timed_out = False
    try:
        try:
            async with asyncio.timeout(spec.timeout_seconds):
                await process.wait()
                await asyncio.gather(stdout_task, stderr_task)
        except TimeoutError:
            timed_out = True
        finally:
            await _reap(process)
        stdout, stdout_cut = await asyncio.wait_for(stdout_task, _REAP_SECONDS)
        stderr, stderr_cut = await asyncio.wait_for(stderr_task, _REAP_SECONDS)
        return ProbeCapture(
            process.returncode, stdout, stderr, timed_out, stdout_cut or stderr_cut
        )
    finally:
        await _reap(process)
        for task in (stdout_task, stderr_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)


@asynccontextmanager
async def serve_probe(
    binary_argv: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    *,
    timeout_seconds: int,
) -> AsyncIterator[LegacyClient]:
    """Start pinned legacy serve, verify /doc, then reap its process group."""
    if not binary_argv or not env.get("OPENCODE_SERVER_PASSWORD"):
        raise ValueError("serve requires explicit binary and task password")
    version = await run_probe(
        ProbeRun(binary_argv + ("--version",), cwd, env, min(timeout_seconds, 10))
    )
    if version.exit_code != 0 or version.stdout_lines != ("1.19.5",):
        raise ValueError("installed OpenCode version mismatch")
    with socket.socket() as selector:
        selector.bind(("127.0.0.1", 0))
        port = selector.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        *binary_argv,
        "serve",
        "--pure",
        "--hostname",
        "127.0.0.1",
        "--port",
        str(port),
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout_task = asyncio.create_task(_read_lines(process.stdout))
    stderr_task = asyncio.create_task(_read_lines(process.stderr))
    client = LegacyClient(
        f"http://127.0.0.1:{port}", env["OPENCODE_SERVER_PASSWORD"], timeout_seconds=1
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            while True:
                if process.returncode is not None:
                    raise RuntimeError("OpenCode serve exited before preflight")
                try:
                    await asyncio.to_thread(client.verify_schema)
                    break
                except OSError:
                    await asyncio.sleep(0.05)
            yield client
    finally:
        await _reap(process)
        await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task, return_exceptions=True),
            _REAP_SECONDS,
        )
