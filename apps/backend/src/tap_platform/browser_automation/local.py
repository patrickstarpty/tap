"""Opt-in developer runtime: owned local processes, not container isolation."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import shutil
import signal
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .contracts import BrowserBundle, BrowserLimits
from .docker import (
    BrowserRuntime,
    CommandResult,
    EvidenceSink,
    ExecutionState,
    InspectorHandle,
    OwnedResources,
    _ID,
)
from .policy import validate_network_mode, validate_origin


class _NoDockerClient:
    async def run(
        self,
        argv: tuple[str, ...],
        stdin: bytes | None = None,
        *,
        max_stdout: int = 1_000_000,
    ) -> CommandResult:
        raise RuntimeError("local browser transport cannot invoke Docker")


class LocalBrowserRuntime(BrowserRuntime):
    """Reuse policy, receipts, evidence and cancellation with a local transport.

    Each attempt owns a private profile, socket, policy proxy and process groups.
    CPU/memory/PID container limits are unavailable in this developer-only mode.
    The deadline and evidence size limits remain enforced.
    """

    def __init__(
        self,
        *,
        evidence_sink: EvidenceSink | None = None,
        project_id: str | None = None,
        browser_dir: Path | None = None,
        node_binary: str = "node",
        test_target: str | None = None,
        executable_path: str | None = None,
        headless: bool = True,
        upstream_proxy: str | None = None,
    ) -> None:
        super().__init__(
            _NoDockerClient(),
            "local",
            evidence_sink=evidence_sink,
            project_id=project_id,
        )
        self.browser_dir = (
            browser_dir or Path(__file__).resolve().parents[5] / "deploy/browser"
        ).resolve()
        self.node_binary = node_binary
        self.executable_path = executable_path
        self.headless = headless
        if test_target:
            parsed = urlsplit(test_target)
            try:
                loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
            except ValueError:
                loopback = False
            if (
                not loopback
                or parsed.scheme not in {"http", "https"}
                or not parsed.port
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "local test target must be an explicit loopback origin"
                )
        if upstream_proxy is not None:
            match = re.fullmatch(
                r"http://(127\.\d+\.\d+\.\d+):([1-9]\d{0,4})/?", upstream_proxy
            )
            try:
                valid_upstream = bool(
                    match
                    and ipaddress.IPv4Address(match[1]).is_loopback
                    and int(match[2]) <= 65535
                )
            except ValueError:
                valid_upstream = False
            if not valid_upstream:
                raise ValueError("invalid local upstream proxy")
            if test_target:
                raise ValueError("upstream proxy cannot use synthetic targets")
        self.upstream_proxy = upstream_proxy
        self.test_target = test_target
        self._local_roots: dict[str, Path] = {}
        self._local_processes: dict[str, list[asyncio.subprocess.Process]] = {}
        self._local_envs: dict[str, dict[str, str]] = {}
        self._local_groups: dict[str, set[int]] = {}
        self._process_output: dict[
            int, tuple[asyncio.Task[bytes], asyncio.Task[bytes]]
        ] = {}

    async def _spawn(
        self,
        attempt: str,
        name: str,
        env: dict[str, str],
        stdin: bytes | None = None,
        *,
        max_stdout: int = 1_000_000,
    ) -> asyncio.subprocess.Process:
        # Register even when cancellation arrives while the subprocess is starting.
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                self.node_binary,
                str(self.browser_dir / name),
                cwd=self.browser_dir,
                env=env,
                start_new_session=True,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        )
        canceled = False
        try:
            process = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            process = await spawning
            canceled = True
        self._local_processes.setdefault(attempt, []).append(process)

        async def read(stream: asyncio.StreamReader, cap: int) -> bytes:
            chunks: list[bytes] = []
            total = 0
            while chunk := await stream.read(65536):
                total += len(chunk)
                if total > cap:
                    # Keep draining so verbose children cannot deadlock shutdown.
                    continue
                chunks.append(chunk)
            if total > cap:
                raise RuntimeError("local browser command output exceeds limit")
            return b"".join(chunks)

        assert process.stdout is not None and process.stderr is not None
        self._process_output[process.pid] = (
            asyncio.create_task(read(process.stdout, max_stdout)),
            asyncio.create_task(read(process.stderr, 100_000)),
        )
        assert process.stdin is not None
        if stdin:
            process.stdin.write(stdin)
            await process.stdin.drain()
        process.stdin.close()
        if canceled:
            raise asyncio.CancelledError
        return process

    async def _ready(
        self,
        process: asyncio.subprocess.Process,
        path: Path,
        deadline_at: float,
    ) -> None:
        while not path.exists():
            if process.returncode is not None:
                streams = self._process_output[process.pid]
                errors = await asyncio.gather(*streams, return_exceptions=True)
                detail = (
                    errors[1].decode(errors="replace")
                    if isinstance(errors[1], bytes)
                    else str(errors[1])
                )
                raise RuntimeError(
                    f"local inspector exited ({process.returncode}): {detail[-1000:]}"
                )
            if time.monotonic() >= deadline_at:
                raise TimeoutError("local browser startup deadline exceeded")
            await asyncio.sleep(min(0.05, max(0, deadline_at - time.monotonic())))

    async def _create_environment(
        self,
        task_id: str,
        origins: tuple[str, ...],
        deadline: int,
        mounts: tuple[str, ...] = (),
        *,
        limits: BrowserLimits | None = None,
        deadline_at: float | None = None,
        state: ExecutionState | None = None,
        resources: OwnedResources | None = None,
        network_mode: str = "declared_origins",
    ) -> tuple[str, str, str]:
        validate_network_mode(network_mode)
        if not _ID.fullmatch(task_id):
            raise ValueError("invalid task ID")
        if not origins or any(validate_origin(origin) != origin for origin in origins):
            raise ValueError("invalid browser origins")
        if resources is None:
            raise ValueError("owned local resources required")
        attempt = resources.attempt_id
        # Short path also fits macOS's Unix-socket pathname limit.
        root = Path(tempfile.mkdtemp(prefix="tap-browser-", dir="/tmp"))
        self._local_roots[attempt] = root
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "HOME",
                "TMPDIR",
                "LANG",
                "LC_ALL",
                "PLAYWRIGHT_BROWSERS_PATH",
            }
        }
        env.update(
            {
                "TMPDIR": str(root),
                "TAP_BROWSER_LOCAL_DIR": str(root),
                "TAP_BROWSER_LOCAL_HEADLESS": "true" if self.headless else "false",
                "TAP_BROWSER_SOCKET": str(root / "inspector.sock"),
                "TAP_BROWSER_SESSION": self._inspector_start_owners.get(task_id)
                or task_id,
                "TAP_BROWSER_RUNTIME_DIR": str(self.browser_dir),
                "TAP_BROWSER_PROXY_POLICY": json.dumps(
                    {
                        "allowed_origins": origins,
                        "public_web": network_mode == "public_web",
                        "test_target": self.test_target,
                        "upstream_proxy": self.upstream_proxy,
                    }
                ),
            }
        )
        if self.executable_path:
            env["TAP_BROWSER_LOCAL_EXECUTABLE_PATH"] = self.executable_path
        self._local_envs[attempt] = env
        process = await self._spawn(attempt, "site-proxy.mjs", env)
        await self._ready(
            process, root / "proxy-port", deadline_at or time.monotonic() + deadline
        )
        port = int((root / "proxy-port").read_text())
        if not 1 <= port <= 65535:
            raise RuntimeError("invalid local proxy port")
        env["TAP_BROWSER_PROXY"] = f"http://127.0.0.1:{port}"
        # Only the proxy needs its startup policy; child Node imports must not launch it.
        del env["TAP_BROWSER_PROXY_POLICY"]
        return attempt, attempt, str(root)

    async def _launch_inspector(
        self,
        browser: str,
        proxy: str,
        network: str,
        *,
        deadline_at: float,
        control_enabled: bool = False,
    ) -> str | None:
        root = self._local_roots[network]
        env = self._local_envs[network].copy()
        if control_enabled:
            env["TAP_BROWSER_CONTROLLED"] = "1"
        process = await self._spawn(network, "inspector.mjs", env)
        await self._ready(process, root / "inspector.sock", deadline_at)
        if control_enabled:
            return None
        await self._ready(process, root / "profile/DevToolsActivePort", deadline_at)
        port = int((root / "profile/DevToolsActivePort").read_text().splitlines()[0])
        if not 1 <= port <= 65535:
            raise RuntimeError("invalid local CDP port")
        return f"http://127.0.0.1:{port}"

    async def _inspect(
        self,
        handle: InspectorHandle,
        payload: bytes,
        *,
        deadline_at: float,
        max_stdout: int,
    ) -> CommandResult:
        async with asyncio.timeout(max(0, deadline_at - time.monotonic())):
            reader, writer = await asyncio.open_unix_connection(
                str(Path(handle.container) / "inspector.sock")
            )
            try:
                writer.write(payload + b"\n")
                await writer.drain()
                chunks: list[bytes] = []
                total = 0
                while chunk := await reader.read(65536):
                    total += len(chunk)
                    if total > max_stdout:
                        raise RuntimeError("local browser observation exceeds limit")
                    chunks.append(chunk)
                return CommandResult(0, b"".join(chunks).decode(), "")
            finally:
                writer.close()
                await writer.wait_closed()

    async def _run_replay(
        self,
        frozen: Path,
        bundle: BrowserBundle,
        limits: BrowserLimits,
        state: ExecutionState,
    ) -> tuple[CommandResult, Any]:
        attempt, _, _ = await self._create_environment(
            state.task_id,
            bundle.allowed_origins,
            limits.deadline_seconds,
            limits=limits,
            deadline_at=state.deadline_at,
            state=state,
            resources=state.resources,
            network_mode=bundle.network_mode,
        )
        root = self._local_roots[attempt]
        local_frozen = root / "frozen"
        shutil.copytree(frozen, local_frozen)
        (root / "node_modules").symlink_to(
            self.browser_dir / "node_modules", target_is_directory=True
        )
        output = root / "output"
        output.mkdir(mode=0o700)
        env = dict(self._local_envs[attempt])
        env.update(
            {
                "TAP_BROWSER_FROZEN_DIR": str(local_frozen),
                "TAP_BROWSER_INPUTS_PATH": str(local_frozen / "inputs.json"),
                "TAP_BROWSER_OUTPUT_DIR": str(output),
            }
        )

        async def run(
            name: str, stdin: bytes | None = None, *, cap: int = 1_000_000
        ) -> CommandResult:
            async with asyncio.timeout(max(0, state.deadline_at - time.monotonic())):
                process = await self._spawn(attempt, name, env, stdin, max_stdout=cap)
                stdout, stderr = await asyncio.gather(
                    *self._process_output[process.pid]
                )
                await process.wait()
                return CommandResult(
                    process.returncode or 0, stdout.decode(), stderr.decode()
                )

        result = await run(
            "runner.mjs",
            json.dumps(
                {
                    "deadline_seconds": max(
                        1, int(state.deadline_at - time.monotonic())
                    ),
                }
            ).encode(),
        )
        exported = await run("export.mjs", cap=110_000_000)
        if exported.exit_code:
            raise RuntimeError(
                f"local browser evidence collection failed: {exported.stderr[-300:]}"
            )
        return result, json.loads(exported.stdout)

    async def _owned_process_groups(self, attempt: str) -> set[int]:
        processes = self._local_processes.get(attempt, [])
        root = self._local_roots.get(attempt)
        groups = self._local_groups.setdefault(attempt, set())
        groups.update(process.pid for process in processes)
        probe = await asyncio.create_subprocess_exec(
            "ps",
            "-axo",
            "pid=,ppid=,pgid=,command=",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(probe.communicate(), timeout=3)
        except BaseException:
            if probe.returncode is None:
                probe.kill()
            await probe.wait()
            raise
        if probe.returncode:
            raise OSError("local process ownership inspection failed")
        rows = []
        for line in stdout.decode(errors="replace").splitlines():
            fields = line.split(None, 3)
            if len(fields) == 4:
                rows.append((int(fields[0]), int(fields[1]), int(fields[2]), fields[3]))
        # Capture detached Chromium groups before terminating their Node parents.
        owned = {pid for pid, _, group, _ in rows if group in groups}
        owned.update(process.pid for process in processes)
        if root is not None:
            prefixes = {f" --user-data-dir={path}/" for path in (root, root.resolve())}
            # A crashed worker may already have orphaned its browser. Its unique
            # private profile remains an ownership marker, unlike a global name.
            owned.update(
                pid
                for pid, _, group, command in rows
                if pid == group and any(prefix in command for prefix in prefixes)
            )
        while True:
            children = {pid for pid, parent, _, _ in rows if parent in owned}
            if children.issubset(owned):
                break
            owned.update(children)
        groups.update(
            group for pid, _, group, _ in rows if pid in owned and group in owned
        )
        return groups

    @staticmethod
    def _groups_alive(groups: set[int]) -> bool:
        for group in groups:
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                continue
            except PermissionError:
                # macOS may briefly deny probing a group while it is being reaped.
                return True
            return True
        return False

    async def _cleanup(self, resources: OwnedResources) -> bool:
        attempt = resources.attempt_id
        processes = self._local_processes.get(attempt, [])
        if attempt not in self._local_roots:
            return True
        try:
            groups = await self._owned_process_groups(attempt)
            for group in groups:
                try:
                    os.killpg(group, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            grace = time.monotonic() + 3
            while self._groups_alive(groups) and time.monotonic() < grace:
                await asyncio.sleep(0.05)
            for group in groups:
                try:
                    os.killpg(group, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            for process in reversed(processes):
                await asyncio.wait_for(process.wait(), timeout=3)
                tasks = self._process_output.pop(process.pid, ())
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=3
                )
            confirm = time.monotonic() + 3
            while self._groups_alive(groups) and time.monotonic() < confirm:
                await asyncio.sleep(0.05)
            if self._groups_alive(groups):
                return False
            self._local_processes.pop(attempt, None)
            self._local_envs.pop(attempt, None)
            self._local_groups.pop(attempt, None)
            root = self._local_roots.pop(attempt, None)
            if root is not None:
                shutil.rmtree(root)
            return True
        except (OSError, TimeoutError):
            return False
