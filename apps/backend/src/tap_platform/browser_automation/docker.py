"""Docker lifecycle for isolated, policy-gated browser processes."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import re
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
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
    validate_network_mode,
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
    "read",
    "finish",
    "recording",
    "preflight",
    "scroll",
    "control_sync",
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
    """Write immutable evidence bytes only; calls run in a worker thread.

    A call already in progress may finish after deadline/cancel, so the sink must
    never publish or modify terminal execution status.
    """

    def persist(
        self,
        project_id: str,
        task_id: str,
        execution_id: str,
        kind: str,
        data: bytes,
        max_bytes: int,
        *,
        session_id: str | None = None,
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
    session_id: str | None = None
    network_mode: str = "declared_origins"
    cdp_url: str | None = None


@dataclass
class OwnedResources:
    task_id: str
    attempt_id: str
    network: str
    proxy: str
    browser: str
    network_attempted: bool = False
    proxy_attempted: bool = False
    browser_attempted: bool = False
    network_id: str | None = None
    proxy_id: str | None = None
    browser_id: str | None = None
    cleanup_confirmed: bool | None = None

    @classmethod
    def new(cls, task_id: str) -> OwnedResources:
        attempt_id = uuid.uuid4().hex
        network = f"tapper-browser-{attempt_id}"
        return cls(
            task_id, attempt_id, network, f"{network}-proxy", f"{network}-runtime"
        )


@dataclass
class ExecutionState:
    task_id: str
    resources: OwnedResources
    deadline_at: float
    owner: asyncio.Task[Any]
    finished: asyncio.Future[tuple[bool, str]]
    cancel_requested: bool = False
    stopped: bool = False
    finalizing: bool = False


class BrowserRuntime:
    def __init__(
        self,
        client: DockerClient,
        image: str,
        *,
        evidence_sink: EvidenceSink | None = None,
        project_id: str | None = None,
        test_target: str | None = None,
        test_ca_pem: str | None = None,
        seccomp_profile: Path | None = None,
    ):
        if not image:
            raise ValueError("browser image required")
        self.client = client
        self.image = image
        self.test_target = test_target
        self.test_ca_pem = test_ca_pem
        self.evidence_sink = evidence_sink
        self.project_id = project_id
        self.seccomp_profile = (
            seccomp_profile
            or Path(__file__).resolve().parents[5]
            / "deploy/browser/seccomp-profile.json"
        )
        self._handles: dict[str, InspectorHandle] = {}
        self._executions: dict[str, ExecutionState] = {}
        self._controlled_handles: set[str] = set()
        self._inspector_expiries: dict[str, asyncio.Task[None]] = {}
        self._inspector_deadlines: dict[str, float] = {}
        self._inspector_resources: dict[str, OwnedResources] = {}
        self._inspector_teardowns: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._recordings: dict[str, list[dict[str, Any]]] = {}
        self._inspector_pending: set[str] = set()
        self._inspector_starts: dict[str, asyncio.Task[Any]] = {}
        self._inspector_start_receipts: dict[
            tuple[str, str | None], asyncio.Future[dict[str, Any]]
        ] = {}
        self._inspector_start_owners: dict[str, str | None] = {}
        self._inspector_handle_owners: dict[str, str | None] = {}

    async def _call(
        self,
        argv: tuple[str, ...],
        stdin: bytes | None = None,
        *,
        deadline_at: float,
        max_stdout: int = 1_000_000,
        cleanup: bool = False,
    ) -> CommandResult:
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("browser deadline exceeded")
        return await asyncio.wait_for(
            self.client.run(argv, stdin, max_stdout=max_stdout),
            timeout=min(5, remaining) if cleanup else remaining,
        )

    async def _require(
        self,
        argv: tuple[str, ...],
        stdin: bytes | None = None,
        *,
        deadline_at: float,
        state: ExecutionState | None = None,
    ) -> str:
        if state is not None and state.cancel_requested:
            raise asyncio.CancelledError
        result = await self._call(argv, stdin, deadline_at=deadline_at)
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
        resources = resources or OwnedResources.new(task_id)
        network, proxy, browser = (
            resources.network,
            resources.proxy,
            resources.browser,
        )
        label = (
            "--label",
            f"tap.browser.task={task_id}",
            "--label",
            f"tap.browser.attempt={resources.attempt_id}",
        )
        bounds = limits or BrowserLimits(deadline_seconds=deadline)
        deadline_at = deadline_at or time.monotonic() + deadline

        async def require(argv: tuple[str, ...], stdin: bytes | None = None) -> str:
            return await self._require(
                argv, stdin, deadline_at=deadline_at, state=state
            )

        async def acquire(argv: tuple[str, ...], kind: str) -> None:
            if state is not None and state.cancel_requested:
                raise asyncio.CancelledError
            setattr(resources, f"{kind}_attempted", True)
            result = await self._call(argv, deadline_at=deadline_at)
            if result.exit_code:
                raise RuntimeError(f"Docker command failed: {result.stderr[:200]}")
            setattr(resources, f"{kind}_id", result.stdout.strip())
            if state is not None and state.cancel_requested:
                raise asyncio.CancelledError

        try:
            await acquire(
                (
                    "docker",
                    "network",
                    "create",
                    "--internal",
                    "--subnet",
                    str(
                        ipaddress.IPv4Address("10.240.0.0")
                        + (int(resources.attempt_id[:4], 16) << 4)
                    )
                    + "/28",
                    *label,
                    network,
                ),
                "network",
            )
            proxy_env = json.dumps(
                {
                    "allowed_origins": origins,
                    "public_web": network_mode == "public_web",
                    "test_target": self.test_target,
                    "test_ca_pem": self.test_ca_pem,
                },
                separators=(",", ":"),
            )
            await acquire(
                (
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    proxy,
                    *label,
                    "--network",
                    "bridge",
                    "--publish",
                    "127.0.0.1::9223",
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
                    "timeout",
                    "-s",
                    "KILL",
                    str(min(deadline + 30, 630)),
                    "node",
                    "/opt/browser/site-proxy.mjs",
                ),
                "proxy",
            )
            await require(("docker", "network", "connect", network, proxy))
            proxy_ca = ""
            if network_mode == "public_web" or any(
                origin.startswith("https://") for origin in origins
            ):
                for _ in range(20):
                    ca_result = await self._call(
                        ("docker", "exec", proxy, "cat", "/tmp/tapper-task-ca/ca.crt"),
                        deadline_at=deadline_at,
                    )
                    if ca_result.exit_code == 0:
                        proxy_ca = ca_result.stdout
                        break
                    await asyncio.sleep(
                        min(0.1, max(0, deadline_at - time.monotonic()))
                    )
                if (
                    not proxy_ca.startswith("-----BEGIN CERTIFICATE-----")
                    or len(proxy_ca) > 16_000
                ):
                    raise RuntimeError("task CA unavailable")
            raw = await require(("docker", "inspect", proxy))
            proxy_ip = json.loads(raw)[0]["NetworkSettings"]["Networks"][network][
                "IPAddress"
            ]
            if not re.fullmatch(r"[0-9.]+", proxy_ip):
                raise RuntimeError("proxy has no IPv4 address")
            await acquire(
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
                    *(
                        ("-e", "NODE_EXTRA_CA_CERTS=/home/browser/ca.crt")
                        if proxy_ca
                        else ()
                    ),
                    *mounts,
                    self.image,
                    "sleep",
                    str(min(deadline + 30, 630)),
                ),
                "browser",
            )
            # The helper only changes this browser container's network namespace.
            await require(
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
            if proxy_ca:
                await require(
                    ("docker", "exec", "-i", browser, "sh", "/opt/browser/trust-ca.sh"),
                    proxy_ca.encode(),
                )
            return network, proxy, browser
        except BaseException:
            if state is None:
                resources.cleanup_confirmed = await self._cleanup(resources)
            raise

    async def _cleanup(self, resources: OwnedResources) -> bool:
        grace_at = time.monotonic() + 25

        async def command(argv: tuple[str, ...]) -> CommandResult:
            return await self._call(argv, deadline_at=grace_at, cleanup=True)

        def owned(
            detail: dict[str, Any], expected_id: str | None, *, network: bool
        ) -> bool:
            labels = detail["Labels"] if network else detail["Config"]["Labels"]
            return (
                labels.get("tap.browser.task") == resources.task_id
                and labels.get("tap.browser.attempt") == resources.attempt_id
                and (not expected_id or detail["Id"] == expected_id)
            )

        try:
            for name, attempted, expected_id in (
                (resources.browser, resources.browser_attempted, resources.browser_id),
                (resources.proxy, resources.proxy_attempted, resources.proxy_id),
            ):
                if not attempted:
                    continue
                inspected = await command(("docker", "inspect", name))
                if inspected.exit_code:
                    if "No such" in inspected.stderr or "missing" in inspected.stderr:
                        continue
                    return False
                detail = json.loads(inspected.stdout)[0]
                if not owned(detail, expected_id, network=False):
                    return False
                result = await command(("docker", "stop", "--time", "2", name))
                if result.exit_code:
                    confirm = await command(
                        ("docker", "inspect", "--format", "{{.State.Running}}", name)
                    )
                    if confirm.exit_code or confirm.stdout.strip() != "false":
                        return False
                removed = await command(("docker", "rm", name))
                if removed.exit_code:
                    return False
            if not resources.network_attempted:
                return True
            inspected_network = await command(
                ("docker", "network", "inspect", resources.network)
            )
            if inspected_network.exit_code:
                return (
                    "No such" in inspected_network.stderr
                    or "missing" in inspected_network.stderr
                )
            network_detail = json.loads(inspected_network.stdout)[0]
            if not owned(network_detail, resources.network_id, network=True):
                return False
            return (
                await command(("docker", "network", "rm", resources.network))
            ).exit_code == 0
        except (TimeoutError, ValueError, IndexError, KeyError, TypeError):
            return False

    async def _launch_inspector(
        self,
        browser: str,
        proxy: str,
        network: str,
        *,
        deadline_at: float,
        control_enabled: bool = False,
    ) -> str | None:
        await self._require(
            (
                "docker",
                "exec",
                "-d",
                *(("-e", "TAP_BROWSER_CONTROLLED=1") if control_enabled else ()),
                browser,
                "node",
                "/opt/browser/inspector.mjs",
            ),
            deadline_at=deadline_at,
        )
        for attempt in range(20):
            ready = await self._call(
                (
                    "docker",
                    "exec",
                    browser,
                    "test",
                    "-S",
                    "/tmp/inspector.sock",
                ),
                deadline_at=deadline_at,
            )
            if ready.exit_code == 0:
                break
            await asyncio.sleep(min(0.25, max(0, deadline_at - time.monotonic())))
        else:
            raise RuntimeError("inspector did not become ready")
        if control_enabled:
            return None
        browser_details = json.loads(
            await self._require(("docker", "inspect", browser), deadline_at=deadline_at)
        )[0]
        browser_ip = browser_details["NetworkSettings"]["Networks"][network][
            "IPAddress"
        ]
        await self._require(
            (
                "docker",
                "exec",
                "-d",
                proxy,
                "node",
                "/opt/browser/cdp-relay.mjs",
                browser_ip,
            ),
            deadline_at=deadline_at,
        )
        port = await self._require(
            ("docker", "port", proxy, "9223/tcp"), deadline_at=deadline_at
        )
        return "http://" + port.strip()

    async def start_inspector(
        self,
        task_id: str,
        allowed_origins: tuple[str, ...],
        deadline: int,
        *,
        session_id: str | None = None,
        network_mode: str = "declared_origins",
        control_enabled: bool = False,
    ) -> InspectorHandle:
        if not 1 <= deadline <= 600:
            raise ValueError("invalid deadline")
        if task_id in self._handles or task_id in self._inspector_pending:
            raise ValueError("inspector already active")
        self._inspector_pending.add(task_id)
        owner = asyncio.current_task()
        assert owner is not None
        self._inspector_starts[task_id] = owner
        self._inspector_start_owners[task_id] = session_id
        finished: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._inspector_start_receipts[(task_id, session_id)] = finished
        resources = OwnedResources.new(task_id)
        try:
            deadline_at = time.monotonic() + deadline
            network, proxy, browser = await self._create_environment(
                task_id,
                allowed_origins,
                deadline,
                deadline_at=deadline_at,
                resources=resources,
                network_mode=network_mode,
            )
            cdp_url = await self._launch_inspector(
                browser,
                proxy,
                network,
                deadline_at=deadline_at,
                **({"control_enabled": True} if control_enabled else {}),
            )
            handle = InspectorHandle(
                task_id,
                browser,
                network,
                proxy,
                allowed_origins,
                session_id,
                network_mode,
                cdp_url,
            )
            if control_enabled:
                self._controlled_handles.add(task_id)
            self._handles[task_id] = handle
            self._inspector_handle_owners[task_id] = session_id
            self._inspector_resources[task_id] = resources
            self._inspector_deadlines[task_id] = deadline_at
            self._inspector_teardowns.pop(task_id, None)
            self._inspector_expiries[task_id] = asyncio.create_task(
                self._expire_inspector(handle, deadline_at)
            )
            finished.set_result(
                {"task_id": task_id, "stopped": False, "status": "active"}
            )
            return handle
        except BaseException:
            if resources.cleanup_confirmed is None:
                resources.cleanup_confirmed = await self._cleanup(resources)
            finished.set_result(
                {
                    "task_id": task_id,
                    "stopped": resources.cleanup_confirmed,
                    "status": "canceled" if resources.cleanup_confirmed else "unknown",
                }
            )
            raise
        finally:
            self._inspector_pending.discard(task_id)
            self._inspector_starts.pop(task_id, None)
            self._inspector_start_owners.pop(task_id, None)

    async def cancel_inspector(
        self, task_id: str, *, session_id: str | None = None
    ) -> dict[str, Any]:
        handle = self._handles.get(task_id)
        if (
            handle is not None
            and self._inspector_handle_owners.get(task_id) == session_id
        ):
            return await self.cancel(handle)
        owner = self._inspector_starts.get(task_id)
        finished = self._inspector_start_receipts.get((task_id, session_id))
        if (
            owner is not None
            and finished is not None
            and self._inspector_start_owners.get(task_id) == session_id
        ):
            owner.cancel()
            return await asyncio.shield(finished)
        if handle is None and finished is not None and finished.done():
            return finished.result()
        return {"task_id": task_id, "stopped": False, "status": "unknown"}

    async def _expire_inspector(
        self, handle: InspectorHandle, deadline_at: float
    ) -> None:
        try:
            await asyncio.sleep(max(0, deadline_at - time.monotonic()))
            if self._handles.get(handle.task_id) is handle:
                await self.cancel(handle)
        except asyncio.CancelledError:
            pass

    def _owned(self, handle: InspectorHandle) -> None:
        if self._handles.get(handle.task_id) is not handle:
            raise ValueError("cross-task or stale browser handle")

    async def _persist(
        self,
        project_id: str,
        task_id: str,
        execution_id: str,
        kind: str,
        data: bytes,
        max_bytes: int,
        *,
        deadline_at: float,
        state: ExecutionState | None = None,
        session_id: str | None = None,
    ) -> Evidence:
        if self.evidence_sink is None:
            raise RuntimeError("browser evidence sink required")
        if state is not None and state.cancel_requested:
            raise asyncio.CancelledError
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("browser deadline exceeded")
        sink_options = {"session_id": session_id} if session_id is not None else {}
        stored = await asyncio.wait_for(
            asyncio.to_thread(
                self.evidence_sink.persist,
                project_id,
                task_id,
                execution_id,
                kind,
                data,
                max_bytes,
                **sink_options,
            ),
            timeout=remaining,
        )
        if state is not None and state.cancel_requested:
            raise asyncio.CancelledError
        if time.monotonic() >= deadline_at:
            raise TimeoutError("browser deadline exceeded")
        return stored

    async def _inspect(
        self,
        handle: InspectorHandle,
        payload: bytes,
        *,
        deadline_at: float,
        max_stdout: int,
    ) -> CommandResult:
        return await self._call(
            ("docker", "exec", "-i", handle.container, "node", "/opt/browser/ctl.mjs"),
            payload,
            deadline_at=deadline_at,
            max_stdout=max_stdout,
        )

    async def _live_request(
        self, handle: InspectorHandle, action: dict[str, Any]
    ) -> dict[str, Any]:
        self._owned(handle)
        if handle.task_id not in self._controlled_handles:
            raise ValueError("live control unavailable for legacy inspector")
        deadline_at = self._inspector_deadlines[handle.task_id]
        if time.monotonic() >= deadline_at:
            raise TimeoutError("inspector expired")
        payload = json.dumps(action, separators=(",", ":")).encode()
        if len(payload) > 16_384:
            raise ValueError("browser action too large")
        result = await self._inspect(
            handle, payload, deadline_at=deadline_at, max_stdout=1_000_000
        )
        if result.exit_code:
            raise RuntimeError("live browser transport failed")
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or data.get("error"):
            raise ValueError("live browser request rejected")
        return data

    async def read_frame(self, handle: InspectorHandle) -> dict[str, Any]:
        # Ephemeral bytes never pass through the evidence sink.
        return await self._live_request(handle, {"type": "live_frame"})

    async def preflight_human_action(
        self, handle: InspectorHandle, action: dict[str, Any]
    ) -> dict[str, Any]:
        # Values are excluded from the private probe as well as its response.
        allowed = {
            "type",
            "selector",
            "x",
            "y",
            "delta_x",
            "delta_y",
            "frame_sequence",
            "viewport",
            "actor_kind",
            "control_epoch",
            "actor_expires_at",
        }
        probe = {key: value for key, value in action.items() if key in allowed}
        return await self._live_request(
            handle, {"type": "human_preflight", "action": probe}
        )

    async def synchronize_control(
        self, handle: InspectorHandle, controller_kind: str, epoch: int
    ) -> BrowserObservation:
        data = await self._live_request(
            handle,
            {
                "type": "control_sync",
                "controller_kind": controller_kind,
                "control_epoch": epoch,
            },
        )
        for key in ("screenshot_data", "trace_data", "video_data", "result_data"):
            data.pop(key, None)
        return BrowserObservation(**data)

    async def observe(
        self, handle: InspectorHandle, action: dict[str, Any]
    ) -> BrowserObservation:
        self._owned(handle)
        deadline_at = self._inspector_deadlines[handle.task_id]
        if action.get("type") == "recording":
            deadline_at = time.monotonic() + 30
        if time.monotonic() >= deadline_at:
            raise TimeoutError("inspector expired")
        if action.get("type") not in _ACTIONS:
            raise ValueError("unsupported browser action")
        if action["type"] == "navigate":
            validate_url(
                str(action.get("url", "")),
                handle.allowed_origins,
                network_mode=handle.network_mode,
            )
        if len(json.dumps(action)) > 16_384:
            raise ValueError("browser action too large")
        payload = json.dumps(action, separators=(",", ":")).encode()
        result = await self._inspect(
            handle,
            payload,
            deadline_at=deadline_at,
            max_stdout=100_000_000
            if action["type"] in {"finish", "recording"}
            else 1_000_000,
        )
        if result.exit_code:
            raise RuntimeError("browser inspection failed")
        data = json.loads(result.stdout)
        saved_evidence = []
        trace_data = data.pop("trace_data", None)
        video_data = data.pop("video_data", None)
        result_data = data.pop("result_data", None)
        screenshot_data = data.pop("screenshot_data", None)
        if screenshot_data is not None:
            if self.evidence_sink is None or self.project_id is None:
                raise RuntimeError("inspector screenshot sink required")
            image = base64.b64decode(screenshot_data, validate=True)
            if len(image) > 2_000_000:
                raise RuntimeError("inspector screenshot too large")
            stored = await self._persist(
                self.project_id,
                handle.task_id,
                handle.task_id,
                "screenshot",
                image,
                2_000_000,
                deadline_at=deadline_at,
                session_id=handle.session_id,
            )
            data["screenshot_ref"] = stored.object_ref
            saved_evidence.append(asdict(stored))
        for kind, value, cap in (
            ("trace", trace_data, 5_000_000),
            ("video", video_data, 64_000_000),
            ("result", result_data, 32768),
        ):
            if value is None:
                continue
            if (
                action["type"] not in {"finish", "recording"}
                or self.evidence_sink is None
                or self.project_id is None
            ):
                raise RuntimeError("finished session evidence sink required")
            content = (
                base64.b64decode(value, validate=True)
                if kind in {"trace", "video"}
                else json.dumps(value).encode()
            )
            if len(content) > cap:
                raise RuntimeError("session evidence exceeds limit")
            stored = await self._persist(
                self.project_id,
                handle.task_id,
                handle.task_id,
                kind,
                content,
                cap,
                deadline_at=deadline_at,
                session_id=handle.session_id,
            )
            saved_evidence.append(asdict(stored))
            if kind == "video":
                self._recordings[handle.task_id] = [asdict(stored)]
        if action["type"] == "finish" and not data.get("error"):
            if not {
                "screenshot",
                "trace",
                "result",
            }.issubset({item["kind"] for item in saved_evidence}):
                raise RuntimeError("incomplete finished session evidence")
            data["result"] = result_data
        data["evidence"] = tuple(saved_evidence)
        return BrowserObservation(**data)

    async def cancel(self, handle: InspectorHandle) -> dict[str, Any]:
        self._owned(handle)
        teardown = self._inspector_teardowns.get(handle.task_id)
        if teardown is None:
            teardown = asyncio.create_task(self._record_and_teardown(handle))
            self._inspector_teardowns[handle.task_id] = teardown
        return await asyncio.shield(teardown)

    async def _record_and_teardown(self, handle: InspectorHandle) -> dict[str, Any]:
        recording_error = None
        try:
            if handle.task_id not in self._recordings:
                await self.observe(handle, {"type": "recording"})
        except Exception as error:
            recording_error = str(error)[:300]
        receipt = await self._teardown_inspector(handle)
        receipt["evidence"] = self._recordings.pop(handle.task_id, [])
        if recording_error:
            receipt["recording_error"] = recording_error
        return receipt

    async def _teardown_inspector(self, handle: InspectorHandle) -> dict[str, Any]:
        resources = self._inspector_resources[handle.task_id]
        try:
            stopped = await self._cleanup(resources)
        except Exception:
            stopped = False
        if stopped:
            self._controlled_handles.discard(handle.task_id)
            self._handles.pop(handle.task_id, None)
            self._inspector_handle_owners.pop(handle.task_id, None)
            self._inspector_resources.pop(handle.task_id, None)
            self._inspector_deadlines.pop(handle.task_id, None)
            expiry = self._inspector_expiries.pop(handle.task_id, None)
            if expiry is not None and expiry is not asyncio.current_task():
                expiry.cancel()
        else:
            self._inspector_teardowns.pop(handle.task_id, None)
        return {
            "task_id": handle.task_id,
            "stopped": stopped,
            "status": "canceled" if stopped else "unknown",
        }

    async def cancel_execution(self, execution_id: str, task_id: str) -> dict[str, Any]:
        active = self._executions.get(execution_id)
        if active is None or active.task_id != task_id:
            raise ValueError("cross-task or inactive execution")
        active.cancel_requested = True
        if not active.finalizing:
            active.owner.cancel()
        try:
            stopped, status = await asyncio.wait_for(
                asyncio.shield(active.finished), timeout=30
            )
        except TimeoutError:
            stopped, status = False, "unknown"
        return {
            "execution_id": execution_id,
            "stopped": stopped,
            "status": "canceled" if stopped and status == "canceled" else "unknown",
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
        owner = asyncio.current_task()
        assert owner is not None
        resources = OwnedResources.new(task_id)
        state = ExecutionState(
            task_id,
            resources,
            time.monotonic() + limits.deadline_seconds,
            owner,
            asyncio.get_running_loop().create_future(),
        )
        self._executions[execution_id] = state

        def receipt(status: str, failure_code: str) -> BrowserExecution:
            return BrowserExecution(
                execution_id,
                bundle.project_id,
                task_id,
                bundle.bundle_id,
                bundle.input_sha256,
                status,  # type: ignore[arg-type]
                started_at,
                datetime.now(UTC).isoformat(),
                {},
                (),
                (),
                failure_code,
            )

        result: BrowserExecution = receipt("failed", "runtime_error")
        try:
            result = await self._execute_work(
                bundle, inputs, limits, execution_id, state
            )
        except asyncio.CancelledError:
            if not state.cancel_requested:
                raise
            result = receipt("canceled", "canceled")
        except TimeoutError:
            result = receipt("failed", "timeout")
        except RuntimeError as error:
            result = receipt(
                "failed",
                "evidence_limit"
                if str(error) == "browser evidence exceeds limit"
                else "runtime_error",
            )
        except Exception:
            result = receipt("failed", "runtime_error")
        finally:
            state.finalizing = True
            try:
                stopped = state.stopped or await self._cleanup(resources)
            except Exception:
                stopped = False
            if not stopped:
                result = receipt("unknown", "termination_unconfirmed")
            elif state.cancel_requested:
                result = receipt("canceled", "canceled")
            state.stopped = stopped
            if not state.finished.done():
                state.finished.set_result((stopped, result.status))
            self._executions.pop(execution_id, None)
        return result

    async def _run_replay(
        self,
        frozen: Path,
        bundle: BrowserBundle,
        limits: BrowserLimits,
        state: ExecutionState,
    ) -> tuple[CommandResult, Any]:
        task_id = state.task_id
        mounts = (
            "--mount",
            f"type=bind,src={frozen},dst=/opt/browser/frozen,readonly",
            "--tmpfs",
            "/opt/browser/output:rw,nosuid,nodev,size=16m,nr_inodes=256,uid=10001,gid=10001",
        )
        _, _, browser = await self._create_environment(
            task_id,
            bundle.allowed_origins,
            limits.deadline_seconds,
            mounts,
            limits=limits,
            deadline_at=state.deadline_at,
            state=state,
            resources=state.resources,
            network_mode=bundle.network_mode,
        )
        # A distinct execution container receives only this frozen entry and output directory.
        result: CommandResult | None = None
        result = await self._call(
            (
                "docker",
                "exec",
                "-i",
                browser,
                "node",
                "/opt/browser/runner.mjs",
            ),
            json.dumps(
                {"deadline_seconds": max(1, int(state.deadline_at - time.monotonic()))}
            ).encode(),
            deadline_at=state.deadline_at,
        )
        if state.cancel_requested:
            raise asyncio.CancelledError
        exported = await self._call(
            (
                "docker",
                "exec",
                "-i",
                browser,
                "node",
                "/opt/browser/export.mjs",
            ),
            max_stdout=110_000_000,
            deadline_at=state.deadline_at,
        )
        if exported.exit_code != 0:
            raise RuntimeError(
                f"browser evidence collection failed: {exported.stderr[:300]}"
            )
        files = json.loads(exported.stdout)
        return result, files

    async def _execute_work(
        self,
        bundle: BrowserBundle,
        inputs: dict[str, Any],
        limits: BrowserLimits,
        execution_id: str,
        state: ExecutionState,
    ) -> BrowserExecution:
        task_id = state.task_id
        assert self.evidence_sink is not None
        started_at = datetime.now(UTC).isoformat()
        with tempfile.TemporaryDirectory(prefix="tapper-browser-") as temp:
            root = Path(temp)
            frozen = root / "frozen"
            output = root / "output"
            frozen.mkdir(mode=0o700)
            output.mkdir(mode=0o777)
            (frozen / "flow.spec.ts").write_bytes(bundle.script_bytes)
            (frozen / "inputs.json").write_text(
                json.dumps(
                    inputs.get("input_data", {}), sort_keys=True, separators=(",", ":")
                )
            )
            (frozen / "policy.json").write_text(
                json.dumps(
                    {
                        "allowed_origins": bundle.allowed_origins,
                        "network_mode": bundle.network_mode,
                    }
                )
            )
            result, files = await self._run_replay(frozen, bundle, limits, state)
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
            state.stopped = await self._cleanup(state.resources)
            if not state.stopped:
                raise RuntimeError("browser cleanup unconfirmed")
            if time.monotonic() >= state.deadline_at:
                raise TimeoutError("browser deadline exceeded")
            if state.cancel_requested:
                raise asyncio.CancelledError
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
                if kind not in {"screenshot", "trace", "result", "video", "download"}:
                    raise RuntimeError("invalid evidence kind")
                path = validate_evidence_path(output, str(item.get("name", "")))
                if not path.is_file():
                    raise RuntimeError("evidence missing")
                size = path.stat().st_size
                total_bytes += size
                if total_bytes > limits.max_output_bytes:
                    raise RuntimeError("browser evidence exceeds limit")
                if kind == "download" and size > 5_000_000:
                    raise RuntimeError("download evidence exceeds limit")
                content = path.read_bytes()
                if kind == "screenshot" and not content.startswith(
                    b"\x89PNG\r\n\x1a\n"
                ):
                    raise RuntimeError("invalid screenshot evidence")
                if kind == "trace" and not content.startswith(b"PK\x03\x04"):
                    raise RuntimeError("invalid trace evidence")
                stored = await self._persist(
                    bundle.project_id,
                    task_id,
                    execution_id,
                    kind,
                    content,
                    limits.max_output_bytes,
                    deadline_at=state.deadline_at,
                    state=state,
                )
                if (
                    stored.sha256 != hashlib.sha256(content).hexdigest()
                    or stored.size_bytes != size
                ):
                    raise RuntimeError("evidence sink returned invalid receipt")
                evidence.append(stored)
            stored_result = await self._persist(
                bundle.project_id,
                task_id,
                execution_id,
                "result",
                result_bytes,
                limits.max_output_bytes,
                deadline_at=state.deadline_at,
                state=state,
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
            success = execution_succeeded(result.exit_code, passed, skipped, complete)
            if state.cancel_requested:
                raise asyncio.CancelledError
            if time.monotonic() >= state.deadline_at:
                raise TimeoutError("browser deadline exceeded")
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
