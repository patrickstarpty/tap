"""Bounded subprocess capture for isolated Copilot protocol probes."""

import asyncio
import json
import os
import signal
from dataclasses import dataclass
from pathlib import Path

_LINE_LIMIT = 16_384
_CAPTURE_LIMIT = 65_536


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
    truncated: bool


def classify_capture(
    exit_code: int | None, timed_out: bool, terminal_seen: bool
) -> str:
    """Only a verified protocol terminal plus zero exit indicates completion."""
    if timed_out:
        return "timed_out"
    if exit_code != 0:
        return "failed" if exit_code is not None else "incomplete"
    return "complete" if terminal_seen else "incomplete"


def parse_jsonl(
    lines: tuple[str, ...],
) -> tuple[tuple[dict[str, object], ...], tuple[int, ...]]:
    """Parse syntactic JSON objects; this does not establish provider semantics."""
    events: list[dict[str, object]] = []
    invalid: list[int] = []
    for number, line in enumerate(lines, 1):
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeError):
            invalid.append(number)
            continue
        if isinstance(value, dict):
            events.append(value)
        else:
            invalid.append(number)
    return tuple(events), tuple(invalid)


async def run_probe(spec: ProbeRun) -> ProbeCapture:
    """Run argv in its own process group and retain bounded output in memory."""
    if not spec.argv or spec.timeout_seconds <= 0:
        raise ValueError("argv and positive timeout_seconds are required")
    process = await asyncio.create_subprocess_exec(
        *spec.argv,
        cwd=spec.cwd,
        env=spec.env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=4096,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    remaining = _CAPTURE_LIMIT
    truncated = False

    async def read_lines(stream: asyncio.StreamReader) -> tuple[str, ...]:
        nonlocal remaining, truncated
        lines: list[str] = []
        current = bytearray()
        line_truncated = False

        def emit() -> None:
            nonlocal current, line_truncated, remaining, truncated
            if current or line_truncated:
                if remaining:
                    kept = bytes(current[:remaining])
                    remaining -= len(kept)
                    lines.append(kept.decode("utf-8", errors="replace"))
                    if len(kept) < len(current):
                        truncated = True
                else:
                    truncated = True
            current = bytearray()
            line_truncated = False

        while chunk := await stream.read(4096):
            for part in chunk.split(b"\n")[:-1]:
                space = _LINE_LIMIT - len(current)
                current.extend(part[:space])
                if len(part) > space:
                    line_truncated = truncated = True
                emit()
            tail = chunk.split(b"\n")[-1]
            space = _LINE_LIMIT - len(current)
            current.extend(tail[:space])
            if len(tail) > space:
                line_truncated = truncated = True
        emit()
        return tuple(lines)

    out_task = asyncio.create_task(read_lines(process.stdout))
    err_task = asyncio.create_task(read_lines(process.stderr))
    timed_out = False
    try:
        try:
            await asyncio.wait_for(process.wait(), spec.timeout_seconds)
        except TimeoutError:
            timed_out = True
        finally:
            # Descendants can keep inherited pipes open after the parent exits.
            # Kill the session group even when process.wait() already returned.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        stdout_lines, stderr_lines = await asyncio.wait_for(
            asyncio.gather(out_task, err_task), 2
        )
        return ProbeCapture(
            process.returncode, stdout_lines, stderr_lines, timed_out, truncated
        )
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        for task in (out_task, err_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(out_task, err_task, return_exceptions=True)
