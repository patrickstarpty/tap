"""Opt-in macOS developer coding process with a private home and OS sandbox."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from tap.modules.workflows.adapters.opencode_runtime import CodingConfig


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class LocalCodingContainer:
    """Use the existing mailbox/client protocol without a Docker dependency.

    This developer adapter requires macOS sandbox-exec. It never silently falls
    back to an unsandboxed model shell. Ports and all configuration are per task.
    """

    def __init__(
        self,
        config: CodingConfig,
        mailbox_root: Path,
        *,
        opencode_binary: Path,
        node_binary: Path,
        client_path: Path,
    ) -> None:
        self.config = config
        self.mailbox_root = mailbox_root.resolve(strict=True)
        self.binary = opencode_binary.resolve(strict=True)
        self.node = node_binary.resolve(strict=True)
        self.client_source = client_path.resolve(strict=True)
        self.root = Path(tempfile.mkdtemp(prefix="tapper-local-coding-")).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.sandbox_profile = self.root / "sandbox.sb"
        self.environment: dict[str, str] = {}
        self._daemon: asyncio.subprocess.Process | None = None
        self._serve_pid: int | None = None
        self._serve_identity: str | None = None
        self._serve_children_checked = False
        self._stopped = False
        self._deadline = 0.0
        self._log: Any = None

    def _prepare(self, config: dict[str, Any], token: str, seconds: int) -> None:
        if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
            raise RuntimeError("local coding requires the macOS sandbox")
        if not token or not 1 <= seconds <= 86400 or not os.access(self.binary, os.X_OK):
            raise ValueError("invalid local coding configuration")
        scripts = self.root / "client"
        scripts.mkdir(mode=0o700)
        ripgrep = shutil.which("rg")
        if ripgrep is None:
            raise RuntimeError("local coding requires ripgrep")
        shutil.copyfile(ripgrep, scripts / "rg")
        (scripts / "rg").chmod(0o500)
        for name in (
            "opencode-client.mjs",
            "browser-tools.mjs",
            "browserctl.mjs",
            "model-shim.mjs",
        ):
            shutil.copyfile(self.client_source.parent / name, scripts / name)
        for name in ("home", "data", "cache", "state", "tmp"):
            (self.workspace / name).mkdir(parents=True, mode=0o700)
        # Prevent OpenCode from preparing a project config in an inherited home.
        (self.root / "config/opencode").mkdir(parents=True, mode=0o700)
        (self.root / "config/opencode/.gitignore").touch(mode=0o400)
        (self.root / "config/opencode").chmod(0o500)
        serve_port, shim_port = _port(), _port()
        while serve_port == shim_port:
            shim_port = _port()
        frozen = copy.deepcopy(config)
        frozen["plugin"] = [(scripts / "browser-tools.mjs").as_uri()]
        frozen["provider"]["litellm"]["options"]["baseURL"] = f"http://127.0.0.1:{shim_port}/v1"
        self.environment = {
            "PATH": f"{scripts}:{self.node.parent}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.workspace / "home"),
            "SHELL": "/bin/sh",
            "TMPDIR": str(self.workspace / "tmp"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_DATA_HOME": str(self.workspace / "data"),
            "XDG_CACHE_HOME": str(self.workspace / "cache"),
            "XDG_STATE_HOME": str(self.workspace / "state"),
            "OPENCODE_CONFIG_CONTENT": json.dumps(frozen, separators=(",", ":")),
            "OPENCODE_SERVER_PASSWORD": secrets.token_urlsafe(24),
            "TAP_MODEL_SHIM_TOKEN": token,
            "TAP_CODING_LOCAL": "1",
            "TAP_CODING_WORKSPACE": str(self.workspace),
            "TAP_OPENCODE_BINARY": str(self.binary),
            "TAP_CODING_MAILBOX": str(self.mailbox_root),
            "TAP_CODING_PORT": str(serve_port),
            "TAP_MODEL_SHIM_PORT": str(shim_port),
            "TAP_CODING_SERVE_NONCE": secrets.token_hex(16),
            "TAP_CODING_DEADLINE_SECONDS": str(seconds),
            **{
                name: "1"
                for name in (
                    "OPENCODE_DISABLE_PROJECT_CONFIG",
                    "OPENCODE_DISABLE_DEFAULT_PLUGINS",
                    "OPENCODE_DISABLE_MODELS_FETCH",
                    "OPENCODE_DISABLE_AUTOUPDATE",
                    "OPENCODE_DISABLE_CLAUDE_CODE",
                    "OPENCODE_DISABLE_LSP_DOWNLOAD",
                )
            },
        }

        def quote(value: object) -> str:
            return json.dumps(str(value))

        exceptions = (
            f"(require-not (subpath {quote(self.root)})) "
            f"(require-not (subpath {quote(self.mailbox_root)})) "
            f"(require-not (literal {quote(self.binary)})) "
            f"(require-not (literal {quote(self.node)}))"
        )
        profile = [
            "(version 1)",
            "(allow default)",
            "(deny file-write*)",
            "(deny network*)",
            "(deny file-read-data (require-all "
            '(regex #"^/private/(tmp|var/folders/[^/]+/[^/]+/T)/(tapper-|tap-browser-)[^/]+(/|$)") '
            + exceptions
            + "))",
            "(deny file-read-data (require-all "
            + f"(subpath {quote(Path.home())}) "
            + exceptions
            + "))",
            "(allow file-write* "
            + " ".join(
                f"(subpath {quote(p)})"
                for p in (
                    self.workspace,
                    self.mailbox_root / "requests",
                    self.mailbox_root / "acks",
                )
            )
            + ' (literal "/dev/null") (literal "/dev/tty"))',
            "(allow network-inbound "
            + " ".join(f'(local ip "localhost:{p}")' for p in (serve_port, shim_port))
            + ")",
            "(allow network-outbound "
            + " ".join(f'(remote ip "localhost:{p}")' for p in (serve_port, shim_port))
            + ")",
        ]
        self.sandbox_profile.write_text("\n".join(profile))
        self.sandbox_profile.chmod(0o600)

    def _argv(self, mode: str, *args: str) -> tuple[str, ...]:
        return (
            "/usr/bin/sandbox-exec",
            "-f",
            str(self.sandbox_profile),
            str(self.node),
            str(self.root / "client/opencode-client.mjs"),
            mode,
            *args,
        )

    async def _client(
        self,
        mode: str,
        *args: str,
        data: bytes | None = None,
        timeout: float = 5,
        max_stdout: int = 600_000,
    ) -> dict[str, Any]:
        if self._daemon is None or self._daemon.returncode is not None:
            raise RuntimeError("local coding daemon is not running")
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            child = await asyncio.create_subprocess_exec(
                *self._argv(mode, *args),
                cwd=self.workspace,
                env=self.environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=output,
                stderr=errors,
                start_new_session=True,
            )
            try:
                await asyncio.wait_for(child.communicate(data), timeout)
            finally:
                if child.returncode is None:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await asyncio.wait_for(child.wait(), 3)
            output.seek(0)
            raw = output.read(max_stdout + 1)
            if child.returncode != 0 or len(raw) > max_stdout:
                errors.seek(0)
                reason = self._diagnostic(errors.read(4096))
                raise RuntimeError(
                    f"local coding client failed ({mode}; exit={child.returncode}; {reason})"
                )
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("invalid local coding response")
            return value

    async def start(self, config: dict[str, Any], shim_token: str, deadline_seconds: int) -> None:
        if self._daemon is not None or self._stopped:
            raise RuntimeError("local coding adapter cannot restart")
        self._deadline = time.monotonic() + deadline_seconds
        self._prepare(config, shim_token, deadline_seconds)
        self._log = tempfile.TemporaryFile()
        self._daemon = await asyncio.create_subprocess_exec(
            *self._argv("daemon"),
            cwd=self.workspace,
            env=self.environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=self._log,
            stderr=self._log,
            start_new_session=True,
        )
        deadline = min(self._deadline, time.monotonic() + 45)
        while (remaining := deadline - time.monotonic()) > 0:
            if self._daemon.returncode is not None:
                raise RuntimeError("local coding daemon exited before readiness")
            try:
                identity = await self._client("inspect", timeout=min(3, remaining))
                self._serve_pid = int(identity["pid"])
                identity_text = await self._process_identity(self._serve_pid)
                expected = (
                    f"serve --hostname 127.0.0.1 --port {self.environment['TAP_CODING_PORT']}"
                )
                if (
                    not identity_text
                    or str(self.binary) not in identity_text
                    or expected not in identity_text
                ):
                    raise RuntimeError("local serve process identity mismatch")
                self._serve_identity = identity_text
                self._serve_children_checked = True
                return
            except (RuntimeError, TimeoutError):
                await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise TimeoutError("OpenCode local readiness deadline")

    async def set_frozen_input(self, data: bytes, digest: str) -> None:
        if not 0 < len(data) <= 32768 or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("invalid frozen coding input")
        await self._client("set-input", digest, data=data)

    async def set_goal(self, text: str) -> None:
        if not text or len(text.encode()) > 20000:
            raise ValueError("invalid coding goal")
        await self._client(
            "set-goal", data=text.replace("/workspace/", f"{self.workspace}/").encode()
        )

    async def run_prompt(self) -> dict[str, Any]:
        return await self._client(
            "run", timeout=max(0.01, self._deadline - time.monotonic()), max_stdout=4000
        )

    async def collect(self) -> dict[str, Any]:
        return await self._client("collect")

    @staticmethod
    def _diagnostic(raw: bytes) -> str:
        # Client stderr may include filesystem paths or external error text. Only
        # fixed categories leave this adapter; never log arbitrary stderr/config.
        message = raw.decode("utf8", errors="replace").strip()
        known = {
            "invalid generated script",
            "invalid input request",
            "invalid frozen input",
            "invalid serve identity",
            "serve identity no longer owns process",
            "session observation unavailable",
            "prompt admission failed",
            "event stream unavailable",
            "saved session identity invalid",
            "session create failed",
            "serve password missing",
            "OpenCode version mismatch",
            "native browser tool unavailable",
            "coding daemon deadline exceeded",
        }
        if message in known:
            return message
        if "EACCES" in message or "EPERM" in message or "permission denied" in message:
            return "filesystem permission denied"
        if "timed out" in message or "deadline" in message:
            return "client deadline exceeded"
        return "unclassified client error"

    @staticmethod
    async def _process_identity(pid: int) -> str | None:
        check = await asyncio.create_subprocess_exec(
            "/bin/ps",
            "-p",
            str(pid),
            "-o",
            "lstart=",
            "-o",
            "command=",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            command, _ = await asyncio.wait_for(check.communicate(), 3)
        except BaseException:
            if check.returncode is None:
                check.kill()
                await asyncio.wait_for(check.wait(), 3)
            raise
        return command.decode().strip() if check.returncode == 0 else None

    async def _capture_unready_serve(self) -> bool:
        """Inspect direct children while our daemon is stopped, before any kill.

        Freezing the daemon closes the race where a new detached child could be
        spawned after the inventory. Same-group version probes die with daemon.
        """
        assert self._daemon is not None
        query = await asyncio.create_subprocess_exec(
            "/usr/bin/pgrep",
            "-P",
            str(self._daemon.pid),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            raw, _ = await asyncio.wait_for(query.communicate(), 3)
        except BaseException:
            if query.returncode is None:
                query.kill()
                await asyncio.wait_for(query.wait(), 3)
            raise
        if query.returncode not in (0, 1):
            return False
        expected = f"serve --hostname 127.0.0.1 --port {self.environment['TAP_CODING_PORT']}"
        for value in raw.split():
            if not value.isdigit():
                return False
            pid = int(value)
            try:
                group = os.getpgid(pid)
            except ProcessLookupError:
                continue
            if group == self._daemon.pid:
                continue
            identity = await self._process_identity(pid)
            if identity is None:
                continue
            if (
                self._serve_pid is not None
                or group != pid
                or str(self.binary) not in identity
                or expected not in identity
            ):
                return False
            self._serve_pid = pid
            self._serve_identity = identity
        self._serve_children_checked = True
        return True

    async def _kill_owned_serve(self) -> bool:
        if self._serve_pid is None:
            return self._daemon is None or self._serve_children_checked
        try:
            group = os.getpgid(self._serve_pid)
        except ProcessLookupError:
            return True
        # Compare command AND process birth time before escalating. A reused PID
        # or an unverified readiness record must never authorize a host signal.
        if (
            group != self._serve_pid
            or self._serve_identity is None
            or await self._process_identity(self._serve_pid) != self._serve_identity
        ):
            return False
        try:
            os.killpg(self._serve_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return True

    async def stop(self) -> bool:
        if self._stopped:
            return True
        if self._daemon is not None and self._daemon.returncode is None:
            if self._serve_pid is None and not self._serve_children_checked:
                try:
                    os.kill(self._daemon.pid, signal.SIGSTOP)
                except ProcessLookupError:
                    return False
                captured = False
                try:
                    captured = await self._capture_unready_serve()
                finally:
                    # Queue termination while frozen, then let the daemon reap.
                    try:
                        os.killpg(self._daemon.pid, signal.SIGTERM)
                        os.kill(self._daemon.pid, signal.SIGCONT)
                    except ProcessLookupError:
                        pass
                if not captured:
                    return False
            try:
                os.killpg(self._daemon.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._daemon.wait(), 5)
            except TimeoutError:
                if not await self._kill_owned_serve():
                    return False
                # Let the daemon reap its child first, then kill our still-live
                # direct subprocess only if it also refuses bounded shutdown.
                try:
                    await asyncio.wait_for(self._daemon.wait(), 2)
                except TimeoutError:
                    try:
                        os.killpg(self._daemon.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(self._daemon.wait(), 2)
                    except TimeoutError:
                        return False
        if not await self._kill_owned_serve():
            return False
        if self._serve_pid is not None:
            deadline = time.monotonic() + 2
            while True:
                try:
                    os.killpg(self._serve_pid, 0)
                except ProcessLookupError:
                    break
                if time.monotonic() >= deadline:
                    return False
                await asyncio.sleep(0.05)
        if self._log is not None:
            self._log.close()
        config_dir = self.root / "config/opencode"
        if config_dir.is_dir():
            config_dir.chmod(0o700)
        shutil.rmtree(self.root)
        self._stopped = True
        return True
