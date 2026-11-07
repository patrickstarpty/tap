"""Docker lifecycle for isolated, policy-gated browser processes."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .contracts import (
    BrowserBundle,
    BrowserExecution,
    BrowserLimits,
    BrowserObservation,
    Evidence,
)
from .policy import (
    execution_succeeded,
    validate_evidence_path,
    validate_origin,
    validate_url,
)

_ID = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
_ACTIONS = {
    "navigate",
    "snapshot",
    "click",
    "fill",
    "select",
    "press",
    "wait",
    "screenshot",
}


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str


class DockerClient(Protocol):
    async def run(
        self,
        argv: tuple[str, ...],
        stdin: bytes | None = None,
        *,
        max_stdout: int = 1_000_000,
    ) -> CommandResult: ...


class EvidenceSink(Protocol):
    def persist(
        self,
        project_id: str,
        task_id: str,
        execution_id: str,
        kind: str,
        data: bytes,
        max_bytes: int,
    ) -> Evidence: ...


class SubprocessDockerClient:
    async def run(
        self,
        argv: tuple[str, ...],
        stdin: bytes | None = None,
        *,
        max_stdout: int = 1_000_000,
    ) -> CommandResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None

        async def bounded(stream: asyncio.StreamReader, maximum: int) -> bytes:
            chunks: list[bytes] = []
            total = 0
            while chunk := await stream.read(65_536):
                total += len(chunk)
                if total > maximum:
                    raise ValueError("Docker command output exceeds limit")
                chunks.append(chunk)
            return b"".join(chunks)

        stdout_task: asyncio.Task[bytes] | None = None
        stderr_task: asyncio.Task[bytes] | None = None
        try:
            if stdin:
                process.stdin.write(stdin)
                await process.stdin.drain()
            process.stdin.close()
            stdout_task = asyncio.create_task(bounded(process.stdout, max_stdout))
            stderr_task = asyncio.create_task(bounded(process.stderr, 100_000))
            stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
            await process.wait()
            return CommandResult(
                process.returncode or 0, stdout.decode(), stderr.decode()
            )
        except ValueError:
            if process.returncode is None:
                process.kill()
            if stdout_task is not None and stderr_task is not None:
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            await asyncio.gather(process.stdout.read(), process.stderr.read())
            await process.wait()
            return CommandResult(1, "", "Docker command output exceeds limit")
        except BaseException:
            if process.returncode is None:
                process.kill()
            if stdout_task is not None and stderr_task is not None:
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            await asyncio.gather(process.stdout.read(), process.stderr.read())
            await process.wait()
            raise


@dataclass(frozen=True)
class InspectorHandle:
    task_id: str
    container: str
    network: str
    proxy: str
    allowed_origins: tuple[str, ...]


class BrowserRuntime:
    def __init__(
        self,
        client: DockerClient,
        image: str,
        *,
        evidence_sink: EvidenceSink | None = None,
        project_id: str | None = None,
        test_target: str | None = None,
        seccomp_profile: Path | None = None,
    ):
        if not image:
            raise ValueError("browser image required")
        self.client = client
        self.image = image
        self.test_target = test_target
        self.evidence_sink = evidence_sink
        self.project_id = project_id
        self.seccomp_profile = (
            seccomp_profile
            or Path(__file__).resolve().parents[5]
            / "deploy/browser/seccomp-profile.json"
        )
        self._handles: dict[str, InspectorHandle] = {}
        self._executions: dict[str, tuple[str, str, str, str]] = {}
        self._execution_cancelled: set[str] = set()
        self._execution_cancelling: set[str] = set()
        self._execution_cancel_events: dict[str, asyncio.Event] = {}

    async def _require(self, argv: tuple[str, ...], stdin: bytes | None = None) -> str:
        result = await self.client.run(argv, stdin)
        if result.exit_code:
            raise RuntimeError(f"Docker command failed: {result.stderr[:200]}")
        return result.stdout.strip()

    async def _create_environment(
        self,
        task_id: str,
        origins: tuple[str, ...],
        deadline: int,
        mounts: tuple[str, ...] = (),
        *,
        limits: BrowserLimits | None = None,
    ) -> tuple[str, str, str]:
        if not _ID.fullmatch(task_id):
            raise ValueError("invalid task ID")
        if not origins or any(validate_origin(origin) != origin for origin in origins):
            raise ValueError("invalid browser origins")
        network = f"tapper-browser-{task_id}"
        proxy = f"{network}-proxy"
        browser = f"{network}-runtime"
        label = ("--label", f"tap.browser.task={task_id}")
        bounds = limits or BrowserLimits(deadline_seconds=deadline)
        await self._require(
            ("docker", "network", "create", "--internal", *label, network)
        )
        try:
            proxy_env = json.dumps(
                {"allowed_origins": origins, "test_target": self.test_target},
                separators=(",", ":"),
            )
            await self._require(
                (
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    proxy,
                    *label,
                    "--network",
                    "bridge",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,size=16m",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--user",
                    "10001:10001",
                    "--memory",
                    "128m",
                    "--cpus",
                    "0.5",
                    "--pids-limit",
                    "64",
                    "-e",
                    f"TAP_BROWSER_PROXY_POLICY={proxy_env}",
                    self.image,
                    "node",
                    "/opt/browser/site-proxy.mjs",
                )
            )
            await self._require(("docker", "network", "connect", network, proxy))
            raw = await self._require(("docker", "inspect", proxy))
            proxy_ip = json.loads(raw)[0]["NetworkSettings"]["Networks"][network][
                "IPAddress"
            ]
            if not re.fullmatch(r"[0-9.]+", proxy_ip):
                raise RuntimeError("proxy has no IPv4 address")
            await self._require(
                (
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    browser,
                    *label,
                    "--network",
                    network,
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,size=256m",
                    "--tmpfs",
                    "/home/browser:rw,nosuid,nodev,size=32m,uid=10001,gid=10001",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--security-opt",
                    f"seccomp={self.seccomp_profile}",
                    "--user",
                    "10001:10001",
                    "--memory",
                    f"{bounds.memory_mb}m",
                    "--cpus",
                    str(bounds.cpus),
                    "--pids-limit",
                    str(bounds.pids),
                    "-e",
                    f"TAP_BROWSER_PROXY=http://{proxy_ip}:3128",
                    "-e",
                    f"TAP_BROWSER_SESSION={task_id}",
                    *mounts,
                    self.image,
                    "sleep",
                    str(min(deadline + 30, 630)),
                )
            )
            # The helper only changes this browser container's network namespace.
            await self._require(
                (
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    f"container:{browser}",
                    "--cap-drop",
                    "ALL",
                    "--cap-add",
                    "NET_ADMIN",
                    "--user",
                    "0:0",
                    "--security-opt",
                    "no-new-privileges",
                    self.image,
                    "sh",
                    "/opt/browser/network-policy.sh",
                    proxy_ip,
                    "3128",
                )
            )
            return network, proxy, browser
        except BaseException:
            await self._cleanup(network, proxy, browser)
            raise

    async def _cleanup(self, network: str, proxy: str, browser: str) -> bool:
        stopped = True
        expected_task = network.removeprefix("tapper-browser-")
        for name in (browser, proxy):
            inspected = await self.client.run(("docker", "inspect", name))
            if inspected.exit_code != 0:
                continue
            try:
                detail = json.loads(inspected.stdout)[0]
                if detail["Config"]["Labels"].get("tap.browser.task") != expected_task:
                    stopped = False
                    continue
            except (ValueError, IndexError, KeyError, TypeError):
                stopped = False
                continue
            result = await self.client.run(("docker", "stop", "--time", "2", name))
            if result.exit_code != 0:
                confirm = await self.client.run(
                    ("docker", "inspect", "--format", "{{.State.Running}}", name)
                )
                if confirm.exit_code == 0 and confirm.stdout.strip() != "false":
                    stopped = False
                    continue
            removed = await self.client.run(("docker", "rm", name))
            stopped &= removed.exit_code == 0
        if stopped:
            removed_network = await self.client.run(
                ("docker", "network", "rm", network)
            )
            stopped &= removed_network.exit_code == 0
        return stopped

    async def start_inspector(
        self, task_id: str, allowed_origins: tuple[str, ...], deadline: int
    ) -> InspectorHandle:
        if not 1 <= deadline <= 600:
            raise ValueError("invalid deadline")
        network, proxy, browser = await self._create_environment(
            task_id, allowed_origins, deadline
        )
        try:
            await self._require(
                ("docker", "exec", "-d", browser, "node", "/opt/browser/inspector.mjs")
            )
            for attempt in range(20):
                ready = await self.client.run(
                    ("docker", "exec", browser, "test", "-S", "/tmp/inspector.sock")
                )
                if ready.exit_code == 0:
                    break
                await asyncio.sleep(0.25)
            else:
                raise RuntimeError("inspector did not become ready")
        except BaseException:
            await self._cleanup(network, proxy, browser)
            raise
        handle = InspectorHandle(task_id, browser, network, proxy, allowed_origins)
        self._handles[task_id] = handle
        return handle

    def _owned(self, handle: InspectorHandle) -> None:
        if self._handles.get(handle.task_id) is not handle:
            raise ValueError("cross-task or stale browser handle")

    async def observe(
        self, handle: InspectorHandle, action: dict[str, Any]
    ) -> BrowserObservation:
        self._owned(handle)
        if action.get("type") not in _ACTIONS:
            raise ValueError("unsupported browser action")
        if action["type"] == "navigate":
            validate_url(str(action.get("url", "")), handle.allowed_origins)
        if len(json.dumps(action)) > 16_384:
            raise ValueError("browser action too large")
        payload = json.dumps(action, separators=(",", ":")).encode()
        result = await self.client.run(
            ("docker", "exec", "-i", handle.container, "node", "/opt/browser/ctl.mjs"),
            payload,
        )
        if result.exit_code:
            raise RuntimeError("browser inspection failed")
        data = json.loads(result.stdout)
        screenshot_data = data.pop("screenshot_data", None)
        if screenshot_data is not None:
            if self.evidence_sink is None or self.project_id is None:
                raise RuntimeError("inspector screenshot sink required")
            image = base64.b64decode(screenshot_data, validate=True)
            if len(image) > 2_000_000:
                raise RuntimeError("inspector screenshot too large")
            stored = self.evidence_sink.persist(
                self.project_id,
                handle.task_id,
                handle.task_id,
                "screenshot",
                image,
                2_000_000,
            )
            data["screenshot_ref"] = stored.object_ref
        return BrowserObservation(**data)

    async def cancel(self, handle: InspectorHandle) -> dict[str, Any]:
        self._owned(handle)
        stopped = await self._cleanup(handle.network, handle.proxy, handle.container)
        if stopped:
            del self._handles[handle.task_id]
        return {
            "task_id": handle.task_id,
            "stopped": stopped,
            "status": "canceled" if stopped else "unknown",
        }

    async def cancel_execution(self, execution_id: str, task_id: str) -> dict[str, Any]:
        active = self._executions.get(execution_id)
        if active is None or active[0] != task_id:
            raise ValueError("cross-task or inactive execution")
        _, network, proxy, browser = active
        if execution_id in self._execution_cancelling:
            raise ValueError("execution cancellation already active")
        self._execution_cancelling.add(execution_id)
        try:
            stopped = await self._cleanup(network, proxy, browser)
            if stopped:
                self._execution_cancelled.add(execution_id)
        finally:
            self._execution_cancel_events[execution_id].set()
        return {
            "execution_id": execution_id,
            "stopped": stopped,
            "status": "canceled" if stopped else "unknown",
        }

    async def execute(
        self,
        bundle: BrowserBundle,
        inputs: dict[str, Any],
        limits: BrowserLimits,
        *,
        execution_id: str | None = None,
    ) -> BrowserExecution:
        task_id = str(inputs.get("task_id", ""))
        if not _ID.fullmatch(task_id):
            raise ValueError("invalid task ID")
        if self.evidence_sink is None:
            raise ValueError("execution evidence sink required")
        if (
            hashlib.sha256(
                json.dumps(
                    inputs.get("input_data", {}), sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            != bundle.input_sha256
        ):
            raise ValueError("frozen input hash mismatch")
        execution_id = execution_id or uuid.uuid4().hex
        if not _ID.fullmatch(execution_id) or execution_id in self._executions:
            raise ValueError("invalid or active execution ID")
        started_at = datetime.now(UTC).isoformat()
        with tempfile.TemporaryDirectory(prefix="tapper-browser-") as temp:
            root = Path(temp)
            frozen = root / "frozen"
            output = root / "output"
            frozen.mkdir(mode=0o700)
            output.mkdir(mode=0o777)
            (frozen / "flow.spec.ts").write_bytes(bundle.script_bytes)
            (frozen / "inputs.json").write_text(
                json.dumps(inputs.get("input_data", {}))
            )
            (frozen / "policy.json").write_text(
                json.dumps({"allowed_origins": bundle.allowed_origins})
            )
            resource_id = f"{task_id[:40]}-{execution_id[:12]}"
            mounts = (
                "--mount",
                f"type=bind,src={frozen},dst=/opt/browser/frozen,readonly",
                "--tmpfs",
                "/opt/browser/output:rw,nosuid,nodev,size=16m,nr_inodes=256,uid=10001,gid=10001",
            )
            network, proxy, browser = await self._create_environment(
                resource_id,
                bundle.allowed_origins,
                limits.deadline_seconds,
                mounts,
                limits=limits,
            )
            self._executions[execution_id] = (task_id, network, proxy, browser)
            self._execution_cancel_events[execution_id] = asyncio.Event()

            def canceled_receipt() -> BrowserExecution:
                return BrowserExecution(
                    execution_id,
                    bundle.project_id,
                    task_id,
                    bundle.bundle_id,
                    bundle.input_sha256,
                    "canceled",
                    started_at,
                    datetime.now(UTC).isoformat(),
                    {},
                    (),
                    (),
                    "canceled",
                )

            async def was_canceled() -> bool:
                if execution_id in self._execution_cancelling:
                    await self._execution_cancel_events[execution_id].wait()
                return execution_id in self._execution_cancelled

            # A distinct execution container receives only this frozen entry and output directory.
            result: CommandResult | None = None
            environment_stopped = False
            try:
                try:
                    result = await asyncio.wait_for(
                        self.client.run(
                            (
                                "docker",
                                "exec",
                                "-i",
                                browser,
                                "node",
                                "/opt/browser/runner.mjs",
                            ),
                            json.dumps(
                                {"deadline_seconds": limits.deadline_seconds}
                            ).encode(),
                        ),
                        timeout=limits.deadline_seconds + 5,
                    )
                except TimeoutError:
                    return BrowserExecution(
                        execution_id,
                        bundle.project_id,
                        task_id,
                        bundle.bundle_id,
                        bundle.input_sha256,
                        "failed",
                        started_at,
                        datetime.now(UTC).isoformat(),
                        {},
                        (),
                        (),
                        "timeout",
                    )
                if await was_canceled():
                    return canceled_receipt()
                exported = await self.client.run(
                    (
                        "docker",
                        "exec",
                        "-i",
                        browser,
                        "node",
                        "/opt/browser/export.mjs",
                    ),
                    max_stdout=24_000_000,
                )
                if exported.exit_code != 0:
                    if await was_canceled():
                        return canceled_receipt()
                    raise RuntimeError(
                        f"browser evidence collection failed: {exported.stderr[:300]}"
                    )
                files = json.loads(exported.stdout)
                if not isinstance(files, list) or len(files) > 32:
                    raise RuntimeError("invalid browser evidence export")
                collected = 0
                for item in files:
                    if not isinstance(item, dict):
                        raise RuntimeError("invalid browser evidence export")
                    name = item.get("name")
                    encoded = item.get("data")
                    if not isinstance(name, str) or not isinstance(encoded, str):
                        raise RuntimeError("invalid browser evidence export")
                    path = validate_evidence_path(output, name)
                    if path.exists():
                        raise RuntimeError("duplicate browser evidence")
                    if len(encoded) > 4 * ((limits.max_output_bytes + 2) // 3):
                        raise RuntimeError("browser evidence exceeds limit")
                    data = base64.b64decode(encoded, validate=True)
                    collected += len(data)
                    if collected > limits.max_output_bytes:
                        raise RuntimeError("browser evidence exceeds limit")
                    with path.open("xb") as stream:
                        stream.write(data)
                environment_stopped = await self._cleanup(network, proxy, browser)
                if not environment_stopped:
                    raise RuntimeError("browser cleanup unconfirmed")
                receipt_path = validate_evidence_path(output, "result.json")
                if (
                    not receipt_path.is_file()
                    or receipt_path.stat().st_size > limits.max_output_bytes
                ):
                    raise RuntimeError("trusted browser result missing or oversized")
                result_bytes = receipt_path.read_bytes()
                receipt = json.loads(result_bytes)
                runner_receipt = json.loads(result.stdout) if result.stdout else {}
                if not runner_receipt.get("nonce") or receipt.get(
                    "nonce"
                ) != runner_receipt.get("nonce"):
                    raise RuntimeError("untrusted browser result")
                evidence: list[Evidence] = []
                total_bytes = len(result_bytes)
                for item in receipt.get("evidence", []):
                    kind = item.get("kind")
                    if kind not in {"screenshot", "trace", "result"}:
                        raise RuntimeError("invalid evidence kind")
                    path = validate_evidence_path(output, str(item.get("name", "")))
                    if not path.is_file():
                        raise RuntimeError("evidence missing")
                    size = path.stat().st_size
                    total_bytes += size
                    if total_bytes > limits.max_output_bytes:
                        raise RuntimeError("browser evidence exceeds limit")
                    content = path.read_bytes()
                    if kind == "screenshot" and not content.startswith(
                        b"\x89PNG\r\n\x1a\n"
                    ):
                        raise RuntimeError("invalid screenshot evidence")
                    if kind == "trace" and not content.startswith(b"PK\x03\x04"):
                        raise RuntimeError("invalid trace evidence")
                    stored = self.evidence_sink.persist(
                        bundle.project_id,
                        task_id,
                        execution_id,
                        kind,
                        content,
                        limits.max_output_bytes,
                    )
                    if (
                        stored.sha256 != hashlib.sha256(content).hexdigest()
                        or stored.size_bytes != size
                    ):
                        raise RuntimeError("evidence sink returned invalid receipt")
                    evidence.append(stored)
                stored_result = self.evidence_sink.persist(
                    bundle.project_id,
                    task_id,
                    execution_id,
                    "result",
                    result_bytes,
                    limits.max_output_bytes,
                )
                if stored_result.sha256 != hashlib.sha256(
                    result_bytes
                ).hexdigest() or stored_result.size_bytes != len(result_bytes):
                    raise RuntimeError("evidence sink returned invalid result receipt")
                evidence.append(stored_result)
                kinds = {item.kind for item in evidence}
                complete = {"screenshot", "trace", "result"}.issubset(kinds)
                passed = int(receipt.get("passed", 0))
                skipped = int(receipt.get("skipped", 0))
                success = execution_succeeded(
                    result.exit_code, passed, skipped, complete
                )
                return BrowserExecution(
                    execution_id,
                    bundle.project_id,
                    task_id,
                    bundle.bundle_id,
                    bundle.input_sha256,
                    "passed" if success else "failed",
                    started_at,
                    datetime.now(UTC).isoformat(),
                    receipt,
                    tuple(receipt.get("steps", [])),
                    tuple(evidence),
                    None if success else "execution_failed",
                )
            finally:
                if execution_id in self._execution_cancelling:
                    await self._execution_cancel_events[execution_id].wait()
                if (
                    not environment_stopped
                    and execution_id not in self._execution_cancelled
                ):
                    stopped = await self._cleanup(network, proxy, browser)
                    if not stopped:
                        raise RuntimeError("browser termination unconfirmed")
                self._executions.pop(execution_id, None)
                self._execution_cancelled.discard(execution_id)
                self._execution_cancelling.discard(execution_id)
                self._execution_cancel_events.pop(execution_id, None)
