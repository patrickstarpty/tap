"""One fenced OpenCode session, private model mailbox, and browser API receipts."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.client
import json
import math
import os
import re
import secrets
import tempfile
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, cast
from urllib.parse import urlsplit

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.adapters.browser_client import BrowserApiClient, canonical_json_bytes
from tap.modules.workflows.adapters.coding_mailbox import CodingMailbox, MailItem
from tap.modules.workflows.adapters.opencode_profiles import build_config
from tap.modules.workflows.application.browser_steps import BrowserProgress
from tap.modules.workflows.ports.coding import CodingResult
from tap.platform.coding.gateway import BudgetGate, CallRecord


class CodingStore(Protocol):
    async def append_event(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        lease_token: str,
        event_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> object: ...
    async def reserve_action(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        lease_token: str,
        action_id: str,
        action_kind: str,
        *,
        node_id: str | None = None,
        attempt: int = 0,
    ) -> str: ...
    async def settle_action(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        lease_token: str,
        action_id: str,
        result: dict[str, object],
        *,
        node_id: str | None = None,
        attempt: int = 0,
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class CodingConfig:
    browser_origin: str
    browser_project_id: str
    browser_token: str
    image: str
    image_id: str
    model_origin: str
    model_key: str
    max_input_tokens: int
    max_output_tokens: int
    input_price_per_million: float
    output_price_per_million: float
    max_repairs: int = 1
    model_alias: str = "qwen-plus"

    def __post_init__(self) -> None:
        if (
            not self.image
            or re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_id) is None
            or self.max_input_tokens < 1
            or not 512 <= self.max_output_tokens <= 8192
            or not 0 <= self.max_repairs <= 2
            or re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", self.model_alias) is None
            or not all(
                math.isfinite(value) and value > 0
                for value in (self.input_price_per_million, self.output_price_per_million)
            )
        ):
            raise ValueError("invalid frozen coding configuration")

    @classmethod
    def for_test(cls) -> CodingConfig:
        return cls(
            "http://127.0.0.1:8002",
            "project-a",
            "x" * 16,
            "coding:test",
            "sha256:" + "0" * 64,
            "http://127.0.0.1:4000",
            "synthetic",
            1000,
            2048,
            1.0,
            2.0,
        )


class CodingContainer(Protocol):
    async def start(
        self, config: dict[str, Any], shim_token: str, deadline_seconds: int
    ) -> None: ...
    async def set_frozen_input(self, data: bytes, digest: str) -> None: ...
    async def set_goal(self, text: str) -> None: ...
    async def run_prompt(self) -> dict[str, Any]: ...
    async def collect(self) -> dict[str, Any]: ...
    async def stop(self) -> bool: ...


async def _finish_cleanup(work: Any) -> Any:
    task = asyncio.create_task(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        return await task


@asynccontextmanager
async def _entered_gate(gate: BudgetGate, drain: Callable[[], Any]) -> Any:
    gate.__enter__()
    try:
        yield gate
    finally:
        try:
            await _finish_cleanup(asyncio.to_thread(gate.__exit__, None, None, None))
        finally:
            await _finish_cleanup(drain())


class DockerCodingContainer:
    """Only the task-owned coding image and one private mailbox are mounted."""

    _INSPECT_TIMEOUT = 3.0
    _COMMAND_STAGES = frozenset(
        {
            "coding command",
            "image inspect",
            "docker run",
            "exec inspect",
            "exec set-input",
            "exec set-goal",
            "exec run",
            "exec collect",
            "docker stop",
            "docker rm",
        }
    )

    def __init__(self, config: CodingConfig, mailbox_root: Path) -> None:
        self.config = config
        self.mailbox_root = mailbox_root
        self.name = "tapper-coding-" + secrets.token_hex(12)
        self.attempt_id = secrets.token_hex(16)
        self.container_id: str | None = None
        self._run_attempted = False
        self._stopped = False

    @staticmethod
    async def _command(
        argv: tuple[str, ...],
        *,
        data: bytes | None = None,
        timeout: float = 30,
        max_stdout: int = 600_000,
        stage: str = "coding command",
    ) -> bytes:
        if stage not in DockerCodingContainer._COMMAND_STAGES:
            raise ValueError("invalid coding command stage")
        # A canceled docker exec can leave its stdout pipe open after the CLI is
        # killed. A temporary file lets us reap the CLI without waiting for EOF
        # from that pipe; output is still read with the existing byte limit.
        with tempfile.TemporaryFile() as output:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=output,
                stderr=asyncio.subprocess.DEVNULL,
            )
            timed_out = False
            try:
                await asyncio.wait_for(process.communicate(data), timeout)
            except BaseException as error:
                try:
                    if process.returncode is None:
                        process.kill()
                except BaseException:
                    pass  # A kill race must not replace the original failure.
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except BaseException:
                    pass  # The caller still owns container/inspector stop confirmation.
                if isinstance(error, TimeoutError):
                    timed_out = True
                else:
                    raise
            if timed_out:
                raise TimeoutError(f"coding container command timed out ({stage})")
            output.seek(0)
            stdout = output.read(max_stdout + 1)
            if len(stdout) > max_stdout or process.returncode != 0:
                raise RuntimeError(f"coding container command failed ({process.returncode})")
            return stdout

    async def start(self, config: dict[str, Any], shim_token: str, deadline_seconds: int) -> None:
        if os.getuid() == 0:
            raise ValueError("coding container host must run nonroot")
        task_deadline = time.monotonic() + deadline_seconds
        actual = (
            (
                await self._command(
                    ("docker", "image", "inspect", self.config.image, "--format", "{{.Id}}"),
                    stage="image inspect",
                )
            )
            .decode()
            .strip()
        )
        if actual != self.config.image_id:
            raise ValueError("coding image digest mismatch")
        uid, gid = os.getuid(), os.getgid()
        password = secrets.token_urlsafe(24)
        env = {
            "HOME": "/workspace/home",
            "XDG_CONFIG_HOME": "/workspace/xdg-config",
            "XDG_DATA_HOME": "/workspace/xdg-data",
            "XDG_CACHE_HOME": "/workspace/xdg-cache",
            "XDG_STATE_HOME": "/workspace/xdg-state",
            "OPENCODE_CONFIG_CONTENT": json.dumps(config, separators=(",", ":")),
            "OPENCODE_SERVER_PASSWORD": password,
            "TAP_MODEL_SHIM_TOKEN": shim_token,
            "TAP_CODING_MAILBOX": "/mailbox",
            "TAP_CODING_DEADLINE_SECONDS": str(deadline_seconds),
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
            "OPENCODE_DISABLE_MODELS_FETCH": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE": "1",
            "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
        }
        argv = (
            "docker",
            "run",
            "-d",
            "--pull=never",
            "--name",
            self.name,
            "--label",
            "tap.owner=workflow-coding",
            "--label",
            f"tap.attempt={self.attempt_id}",
            "--network",
            "none",
            "--read-only",
            "--user",
            f"{uid}:{gid}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--tmpfs",
            f"/workspace:rw,nosuid,nodev,size=128m,uid={uid},gid={gid}",
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,size=16m,uid={uid},gid={gid}",
            "--mount",
            f"type=bind,src={self.mailbox_root},dst=/mailbox,readonly",
            "--mount",
            f"type=bind,src={self.mailbox_root / 'requests'},dst=/mailbox/requests",
            "--mount",
            f"type=bind,src={self.mailbox_root / 'responses'},dst=/mailbox/responses,readonly",
            "--mount",
            f"type=bind,src={self.mailbox_root / 'acks'},dst=/mailbox/acks",
            *(entry for key, value in env.items() for entry in ("--env", f"{key}={value}")),
            self.config.image,
        )
        self._run_attempted = True
        acquired_id = (await self._command(argv, timeout=30, stage="docker run")).decode().strip()
        if not acquired_id.startswith("sha256:") and len(acquired_id) < 12:
            raise RuntimeError("coding container identity unavailable")
        self.container_id = acquired_id
        # The daemon may use 10 seconds to check its version, then 30 seconds
        # to make /doc ready. Keep host polling within the task's own deadline.
        deadline = min(time.monotonic() + 45, task_deadline)
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                await self._command(
                    (
                        "docker",
                        "exec",
                        self.container_id,
                        "node",
                        "/opt/tapper/opencode-client.mjs",
                        "inspect",
                    ),
                    timeout=min(3, remaining),
                    stage="exec inspect",
                )
                if time.monotonic() < deadline:
                    return
            except RuntimeError:
                await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise TimeoutError("OpenCode serve readiness deadline")

    async def set_goal(self, text: str) -> None:
        if self.container_id is None or not text or len(text.encode()) > 20000:
            raise ValueError("invalid coding goal")
        await self._command(
            (
                "docker",
                "exec",
                "-i",
                self.container_id,
                "node",
                "/opt/tapper/opencode-client.mjs",
                "set-goal",
            ),
            data=text.encode(),
            timeout=5,
            max_stdout=100,
            stage="exec set-goal",
        )

    async def set_frozen_input(self, data: bytes, digest: str) -> None:
        if (
            self.container_id is None
            or not 0 < len(data) <= 32768
            or hashlib.sha256(data).hexdigest() != digest
        ):
            raise ValueError("invalid frozen coding input")
        await self._command(
            (
                "docker",
                "exec",
                "-i",
                self.container_id,
                "node",
                "/opt/tapper/opencode-client.mjs",
                "set-input",
                digest,
            ),
            data=data,
            timeout=5,
            max_stdout=100,
            stage="exec set-input",
        )

    async def run_prompt(self) -> dict[str, Any]:
        if self.container_id is None:
            raise RuntimeError("coding container not started")
        raw = await self._command(
            ("docker", "exec", self.container_id, "node", "/opt/tapper/opencode-client.mjs", "run"),
            timeout=600,
            max_stdout=4000,
            stage="exec run",
        )
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("terminal") not in {
            "complete",
            "failed",
            "incomplete",
        }:
            raise ValueError("OpenCode terminal receipt invalid")
        return result

    async def collect(self) -> dict[str, Any]:
        if self.container_id is None:
            raise RuntimeError("coding container not started")
        raw = await self._command(
            (
                "docker",
                "exec",
                self.container_id,
                "node",
                "/opt/tapper/opencode-client.mjs",
                "collect",
            ),
            timeout=10,
            max_stdout=400_000,
            stage="exec collect",
        )
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("OpenCode artifact receipt invalid")
        return result

    async def stop(self) -> bool:
        if self._stopped or not self._run_attempted:
            return True
        identity = self.container_id
        if identity is None:
            # The run CLI may lose its reply after Docker accepted the create.
            # Never stop an object found by name without matching both owner labels.
            try:
                probe = await asyncio.wait_for(
                    asyncio.create_subprocess_exec(
                        "docker",
                        "inspect",
                        self.name,
                        "--format",
                        "{{json .}}",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    ),
                    self._INSPECT_TIMEOUT,
                )
                try:
                    stdout, _ = await asyncio.wait_for(probe.communicate(), self._INSPECT_TIMEOUT)
                except TimeoutError:
                    probe.kill()
                    await asyncio.wait_for(probe.communicate(), 1)
                    return False
                found = json.loads(stdout) if probe.returncode == 0 else None
            except (OSError, TimeoutError, ValueError, asyncio.CancelledError):
                return False
            if not isinstance(found, dict):
                # An absent name can race an in-flight daemon create. Without
                # the run reply there is no affirmative settled absence proof.
                return False
            labels = found.get("Config", {}).get("Labels", {})
            if (
                found.get("Name") != "/" + self.name
                or labels.get("tap.owner") != "workflow-coding"
                or labels.get("tap.attempt") != self.attempt_id
                or not isinstance(found.get("Id"), str)
            ):
                return False
            identity = found["Id"]
            self.container_id = identity
        try:
            await self._command(
                ("docker", "stop", "-t", "2", identity),
                timeout=10,
                max_stdout=200,
                stage="docker stop",
            )
        except Exception:
            pass
        try:
            await self._command(
                ("docker", "rm", "-f", identity),
                timeout=10,
                max_stdout=200,
                stage="docker rm",
            )
        except Exception:
            pass
        try:
            probe = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "docker",
                    "inspect",
                    identity,
                    "--format",
                    "{{.Id}} {{.State.Running}}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                self._INSPECT_TIMEOUT,
            )
            try:
                stdout, stderr = await asyncio.wait_for(probe.communicate(), self._INSPECT_TIMEOUT)
            except TimeoutError:
                probe.kill()
                await asyncio.wait_for(probe.communicate(), 1)
                return False
        except (OSError, TimeoutError):
            return False
        if probe.returncode == 0:
            self._stopped = stdout.decode(errors="replace").strip() == f"{identity} false"
        else:
            self._stopped = (
                stderr.decode(errors="replace").strip().endswith(f"No such object: {identity}")
            )
        return self._stopped


class CodingRuntime:
    def __init__(
        self,
        config: CodingConfig,
        *,
        browser: BrowserApiClient | Any | None = None,
        store: CodingStore | None = None,
        container_factory: Callable[[CodingConfig, Path], CodingContainer] = DockerCodingContainer,
    ) -> None:
        self.config = config
        self.browser = browser or BrowserApiClient(
            config.browser_origin, config.browser_project_id, config.browser_token
        )
        self.store = store
        self.container_factory = container_factory

    @staticmethod
    def _receipt_result(
        receipt: dict[str, Any],
        *,
        provider_session_id: str | None = None,
        bundle_ref: str | None = None,
        usage: dict[str, object] | None = None,
        replay_model_calls: int = 0,
    ) -> CodingResult:
        status = str(receipt.get("status"))
        if status not in {"passed", "failed", "canceled", "unknown"}:
            raise ValueError("browser terminal receipt invalid")
        return CodingResult(
            cast(Literal["passed", "failed", "canceled", "unknown"], status),
            provider_session_id,
            bundle_ref or receipt.get("bundle_id"),  # type: ignore[arg-type]
            receipt.get("execution_id"),
            usage or {},
            receipt,
            None,
            replay_model_calls,
        )

    @staticmethod
    def _forward_gate(
        gate: BudgetGate, body: bytes, session_id: str | None, *, deadline: float
    ) -> dict[str, Any]:
        if len(body) > 131072:
            return {"status": 413, "contentType": "application/json", "bodyBase64": ""}
        parsed = urlsplit(gate.url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("model forwarding task deadline")
        connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=remaining)
        try:
            headers = {"Authorization": f"Bearer {gate.token}", "Content-Type": "application/json"}
            if session_id is not None:
                headers["x-opencode-session-id"] = session_id
            connection.request("POST", "/v1/chat/completions", body, headers)
            response = connection.getresponse()
            content = response.read(1048577)
            if len(content) > 1048576:
                raise ValueError("model response exceeds cap")
            content_type = response.getheader("Content-Type", "application/json")
            return {
                "status": response.status,
                "contentType": content_type,
                "bodyBase64": base64.b64encode(content).decode(),
            }
        finally:
            connection.close()

    async def run(
        self,
        task_id: str,
        request: dict[str, Any],
        snapshot: dict[str, Any],
        scope: ProjectScopeContext,
        lease_token: str,
        node_id: str,
        attempt: int,
        stop_requested: Callable[[], Any] | None = None,
    ) -> CodingResult:
        if request.get("kind") != "browser" or not isinstance(request.get("browser"), dict):
            raise ValueError("frozen browser task required")
        if request.get("model_alias") != self.config.model_alias:
            raise ValueError("browser model alias differs from frozen pricing")
        browser = request["browser"]
        limit = request.get("budget_limit", snapshot.get("budget_limit", {}))
        if (
            not isinstance(limit, dict)
            or type(limit.get("cost_micros")) is not int
            or limit["cost_micros"] <= 0
        ):
            raise ValueError("browser task requires finite positive cost cap")
        if type(limit.get("model_calls")) is not int or limit["model_calls"] <= 0:
            raise ValueError("browser task requires finite model call cap")
        deadline_value = datetime.fromisoformat(str(request["deadline_at"]))
        if deadline_value.tzinfo is None:
            raise ValueError("browser deadline must have timezone")
        deadline_monotonic = (
            time.monotonic() + (deadline_value.astimezone(UTC) - datetime.now(UTC)).total_seconds()
        )
        # Browser sessions have a hard 600-second server cap. The session and
        # every subsequent stage share this shorter absolute task boundary.
        deadline_monotonic = min(deadline_monotonic, time.monotonic() + 600)

        def seconds_left() -> int:
            remaining = int(deadline_monotonic - time.monotonic())
            if remaining <= 0:
                raise TimeoutError("browser task deadline exceeded")
            return remaining

        async def task_stop_requested() -> bool:
            if stop_requested is None:
                return False
            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("browser task deadline exceeded")
            return bool(await asyncio.wait_for(stop_requested(), timeout=remaining))

        async def bounded_stage(work: Any, *, accept_stopped_result: bool = False) -> Any:
            pending = asyncio.create_task(work)
            try:
                seconds_left()
                while not pending.done():
                    if await task_stop_requested():
                        raise asyncio.CancelledError("workflow cancel requested")
                    remaining = deadline_monotonic - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("browser task deadline exceeded")
                    await asyncio.wait({pending}, timeout=min(0.1, remaining))
                return await pending
            except BaseException:
                if not pending.done():
                    pending.cancel()
                settled = await asyncio.gather(pending, return_exceptions=True)
                if accept_stopped_result and isinstance(settled[0], dict):
                    return settled[0]
                raise

        remaining_seconds = seconds_left()
        replay_of = browser.get("replay_execution_id")
        if isinstance(replay_of, str):
            receipt = await bounded_stage(
                self.browser.replay(
                    task_id,
                    replay_of,
                    f"replay-{task_id}",
                    deadline_seconds=seconds_left(),
                    stop_requested=stop_requested,
                ),
                accept_stopped_result=True,
            )
            if receipt.get("status") == "canceled":
                receipt = {**receipt, "stop_confirmed": True}
            return self._receipt_result(receipt, replay_model_calls=0)
        if self.store is None:
            raise ValueError("durable workflow store unavailable")
        store = self.store
        origins = browser.get("allowed_origins")
        input_data = browser.get("input_data")
        if not isinstance(origins, list) or not origins or not isinstance(input_data, dict):
            raise ValueError("frozen browser origins/input unavailable")
        digest = hashlib.sha256(canonical_json_bytes(input_data)).hexdigest()
        if browser.get("input_sha256") != digest:
            raise ValueError("frozen browser input digest mismatch")
        goal = self._generation_prompt(
            str(request["goal"]),
            browser.get("start_url"),
            browser.get("expected_result"),
            origins,
            input_data,
        )
        used = snapshot.get("budget_used", {})
        if not isinstance(used, dict):
            raise ValueError("workflow budget unavailable")
        remaining_calls = limit["model_calls"] - int(used.get("model_calls", 0))
        remaining_cost = (limit["cost_micros"] - int(used.get("cost_micros", 0))) / 1_000_000
        if remaining_calls <= 0 or remaining_cost <= 0:
            raise ValueError("browser task budget exhausted")
        try:
            session = await bounded_stage(
                self.browser.session(
                    task_id, origins, min(600, remaining_seconds), f"session-{task_id}"
                ),
                accept_stopped_result=True,
            )
        except (asyncio.CancelledError, TimeoutError):
            return CodingResult(
                "unknown", terminal_evidence={"failure": "session create unconfirmed"}
            )
        if session.get("status") != "active":
            if session.get("status") == "canceled":
                stop_receipt = session.get("stop_receipt")
                return CodingResult(
                    "canceled",
                    terminal_evidence={
                        **session,
                        "stop_confirmed": (
                            isinstance(stop_receipt, dict) and stop_receipt.get("stopped") is True
                        ),
                    },
                )
            return CodingResult("unknown", terminal_evidence=session)
        session_id = str(session["session_id"])
        loop = asyncio.get_running_loop()
        callback_timeout = max(1, min(10, int(deadline_monotonic - time.monotonic())))
        callback_lock = threading.Lock()
        callbacks: list[tuple[str, str, Future[Any]]] = []
        late_reservations: set[str] = set()

        def scheduled(kind: str, action_id: str, work: Any) -> Future[Any]:
            future = asyncio.run_coroutine_threadsafe(work, loop)
            with callback_lock:
                callbacks.append((kind, action_id, future))
            return future

        def reserve(action_id: str, _maximum: float) -> None:
            future = scheduled(
                "reserve",
                action_id,
                store.reserve_action(
                    scope,
                    task_id,
                    lease_token,
                    action_id,
                    "model",
                    node_id=node_id,
                    attempt=attempt,
                ),
            )
            try:
                status = future.result(timeout=callback_timeout)
            except FutureTimeoutError:
                with callback_lock:
                    late_reservations.add(action_id)
                raise
            if status not in {"reserved", "existing"}:
                raise ValueError("durable model reservation refused")

        def settle(record: CallRecord) -> None:
            cost = record.estimated_cost_usd if record.usage_verified else record.reserved_cost_usd
            result: dict[str, object] = {
                "status": record.status,
                "usage_verified": record.usage_verified,
                "prompt_tokens": record.prompt_tokens,
                "completion_tokens": record.completion_tokens,
                "cost_usd": cost,
            }
            future = scheduled(
                "settle",
                record.gate_request_id,
                store.settle_action(
                    scope,
                    task_id,
                    lease_token,
                    record.gate_request_id,
                    result,
                    node_id=node_id,
                    attempt=attempt,
                ),
            )
            future.result(timeout=callback_timeout)

        async def drain_callbacks() -> None:
            with callback_lock:
                scheduled_callbacks = list(callbacks)
                delayed = set(late_reservations)
            unconfirmed: list[BaseException] = []
            for kind, action_id, future in scheduled_callbacks:
                try:
                    status = await asyncio.wait_for(
                        asyncio.shield(asyncio.wrap_future(future)), timeout=30
                    )
                except (asyncio.CancelledError, Exception) as error:
                    future.cancel()
                    unconfirmed.append(error)
                    continue
                if (
                    kind == "reserve"
                    and action_id in delayed
                    and status
                    in {
                        "reserved",
                        "existing",
                    }
                ):
                    try:
                        await store.settle_action(
                            scope,
                            task_id,
                            lease_token,
                            action_id,
                            {
                                "status": 429,
                                "usage_verified": False,
                                "prompt_tokens": None,
                                "completion_tokens": None,
                                "cost_usd": 0.0,
                            },
                            node_id=node_id,
                            attempt=attempt,
                        )
                    except Exception as error:
                        unconfirmed.append(error)
            if unconfirmed:
                raise RuntimeError("durable model callback unconfirmed") from unconfirmed[0]

        provider_session: str | None = None
        latest_bundle: str | None = None
        latest_execution: dict[str, Any] | None = None
        result: CodingResult | None = None
        stopped = False
        primary_error: BaseException | None = None
        gate: BudgetGate | None = None
        with tempfile.TemporaryDirectory(prefix="tapper-coding-") as temporary:
            mailbox = CodingMailbox.create(Path(temporary) / "mailbox")
            container: CodingContainer | None = None
            try:
                async with _entered_gate(
                    BudgetGate(
                        self.config.model_origin,
                        self.config.model_key,
                        max_calls=remaining_calls,
                        max_seconds=seconds_left(),
                        max_cost_usd=remaining_cost,
                        input_price_per_million=self.config.input_price_per_million,
                        output_price_per_million=self.config.output_price_per_million,
                        max_input_tokens=self.config.max_input_tokens,
                        max_input_bytes=131072,
                        max_output_bytes=1048576,
                        max_output_tokens=self.config.max_output_tokens,
                        model_alias=str(request["model_alias"]),
                        on_reserve=reserve,
                        on_settle=settle,
                        preserve_upstream_errors=True,
                    ),
                    drain_callbacks,
                ) as gate:
                    shim_token = secrets.token_urlsafe(24)
                    config = build_config(
                        request,
                        "http://127.0.0.1:39001",
                        shim_token,
                        output_tokens=self.config.max_output_tokens,
                    )
                    container = self.container_factory(self.config, mailbox.root)
                    await bounded_stage(container.start(config, shim_token, seconds_left()))
                    await bounded_stage(
                        container.set_frozen_input(canonical_json_bytes(input_data), digest)
                    )

                    async def progress_event(kind: str, payload: dict[str, Any]) -> None:
                        identity = str(payload.get("action_id") or payload.get("revision"))
                        event_id = hashlib.sha256(
                            f"{task_id}:{node_id}:{attempt}:{kind}:{identity}".encode()
                        ).hexdigest()
                        await store.append_event(
                            scope,
                            task_id,
                            lease_token,
                            event_id,
                            kind,
                            {"node_id": node_id, "attempt": attempt, **payload},
                        )

                    progress = BrowserProgress(progress_event)

                    async def handle(item: MailItem) -> dict[str, Any]:
                        if item.kind == "model":
                            encoded = item.payload.get("bodyBase64")
                            if not isinstance(encoded, str) or len(encoded) > 174764:
                                return {
                                    "status": 413,
                                    "contentType": "application/json",
                                    "bodyBase64": "",
                                }
                            try:
                                body = base64.b64decode(encoded, validate=True)
                            except ValueError:
                                return {
                                    "status": 400,
                                    "contentType": "application/json",
                                    "bodyBase64": "",
                                }
                            claimed = item.payload.get("claimedSessionId")
                            if claimed is not None and not isinstance(claimed, str):
                                return {
                                    "status": 400,
                                    "contentType": "application/json",
                                    "bodyBase64": "",
                                }
                            return await asyncio.to_thread(
                                self._forward_gate, gate, body, claimed, deadline=deadline_monotonic
                            )

                        async def execute(action: dict[str, Any]) -> dict[str, Any]:
                            return await self.browser.action(
                                session_id,
                                action,
                                item.identifier,
                                deadline_seconds=min(30, seconds_left()),
                            )

                        return await progress.handle(item.payload, item.identifier, execute)

                    async def prompt_once(text: str) -> tuple[dict[str, Any], dict[str, Any]]:
                        await bounded_stage(container.set_goal(text))
                        running = asyncio.create_task(container.run_prompt())
                        try:
                            while not running.done():
                                if await task_stop_requested():
                                    running.cancel()
                                    raise asyncio.CancelledError("workflow cancel requested")
                                if time.monotonic() >= deadline_monotonic:
                                    running.cancel()
                                    raise TimeoutError("coding deadline exceeded")
                                if not await bounded_stage(mailbox.serve_once(handle)):
                                    await asyncio.sleep(0.02)
                            terminal = await running
                            collected = await bounded_stage(container.collect())
                            return terminal, collected
                        finally:
                            if not running.done():
                                running.cancel()
                            await asyncio.gather(running, return_exceptions=True)

                    for repair in range(self.config.max_repairs + 1):
                        terminal, collected = await prompt_once(goal)
                        provider_session = (
                            str(terminal.get("session_id")) if terminal.get("session_id") else None
                        )
                        input_request = collected.get("input_request")
                        if isinstance(input_request, dict):
                            return CodingResult(
                                "blocked",
                                provider_session_id=provider_session,
                                usage=gate.snapshot(),
                                input_request=input_request,
                            )
                        if terminal.get("terminal") != "complete":
                            return CodingResult(
                                "failed",
                                provider_session_id=provider_session,
                                usage=gate.snapshot(),
                                terminal_evidence={"model_terminal": terminal},
                            )
                        encoded = collected.get("script_base64")
                        if not isinstance(encoded, str):
                            return CodingResult(
                                "failed",
                                provider_session_id=provider_session,
                                usage=gate.snapshot(),
                                terminal_evidence={"failure": "missing flow.spec.ts"},
                            )
                        if not progress.steps:
                            raise ValueError("BDD plan required before executing generated script")
                        script_bytes = base64.b64decode(encoded, validate=True)
                        if not 0 < len(script_bytes) <= 256000 or hashlib.sha256(
                            script_bytes
                        ).hexdigest() != collected.get("script_sha256"):
                            raise ValueError("generated script digest invalid")
                        script = script_bytes.decode("utf8")
                        bundle = await bounded_stage(
                            self.browser.bundle(
                                task_id, script, input_data, origins, f"bundle-{task_id}-{repair}"
                            )
                        )
                        latest_bundle = str(bundle["bundle_id"])
                        limits = browser.get(
                            "execution_limits",
                            {
                                "deadline_seconds": min(120, seconds_left()),
                                "memory_mb": 1024,
                                "cpus": 1.0,
                                "pids": 128,
                                "max_output_bytes": 5_000_000,
                            },
                        )
                        if not isinstance(limits, dict):
                            raise ValueError("browser execution limits invalid")
                        limits = {
                            **limits,
                            "deadline_seconds": min(
                                int(limits.get("deadline_seconds", 120)), seconds_left()
                            ),
                        }
                        latest_execution = await bounded_stage(
                            self.browser.execute(
                                task_id,
                                latest_bundle,
                                str(bundle["input_sha256"]),
                                limits,
                                f"execution-{task_id}-{repair}",
                                deadline_seconds=min(120, seconds_left()),
                                stop_requested=stop_requested,
                            ),
                            accept_stopped_result=True,
                        )
                        if latest_execution.get("status") in {"passed", "canceled", "unknown"}:
                            break
                        actual = latest_execution.get("result")
                        if (
                            repair < self.config.max_repairs
                            and latest_execution.get("failure_code") == "execution_failed"
                            and isinstance(actual, dict)
                            and type(actual.get("failed")) is int
                            and actual["failed"] > 0
                        ):
                            feedback = json.dumps(actual, ensure_ascii=True)[:4000]
                            goal = (
                                "Repair flow.spec.ts in the same session using this actual "
                                "failed browser execution receipt: " + feedback
                            )
                        else:
                            break
                    assert latest_execution is not None
                    result = self._receipt_result(
                        latest_execution,
                        provider_session_id=provider_session,
                        bundle_ref=latest_bundle,
                        usage=gate.snapshot(),
                    )
                stopped = await _finish_cleanup(container.stop())
                if not stopped:
                    return CodingResult(
                        "unknown",
                        provider_session_id=provider_session,
                        bundle_ref=latest_bundle,
                        execution_ref=result.execution_ref,
                        usage=result.usage,
                        terminal_evidence={"failure": "coding container stop unconfirmed"},
                    )
                return result
            except BaseException as error:
                primary_error = error
                raise
            finally:
                try:
                    if container is not None and not stopped:
                        stopped = await _finish_cleanup(container.stop())
                finally:
                    try:
                        try:
                            session_stop = await _finish_cleanup(
                                self.browser.cancel_session(session_id, deadline_seconds=30)
                            )
                            inspector_status = str(session_stop.get("status"))
                        except BaseException:
                            inspector_status = "unavailable"
                        if inspector_status != "canceled" or (
                            container is not None and not stopped
                        ):
                            return CodingResult(
                                "unknown",
                                provider_session_id=provider_session,
                                bundle_ref=latest_bundle,
                                execution_ref=(
                                    str(latest_execution.get("execution_id"))
                                    if latest_execution is not None
                                    else None
                                ),
                                usage=gate.snapshot() if gate is not None else {},
                                terminal_evidence={
                                    "failure": "coding or inspector stop unconfirmed",
                                    "primary_error_type": (
                                        type(primary_error).__name__ if primary_error else None
                                    ),
                                    "coding_stopped": stopped,
                                    "inspector_status": inspector_status,
                                },
                            )
                        if isinstance(primary_error, asyncio.CancelledError) or (
                            result is not None and result.status == "canceled"
                        ):
                            return CodingResult(
                                "canceled",
                                provider_session_id=provider_session,
                                bundle_ref=latest_bundle,
                                execution_ref=(
                                    str(latest_execution.get("execution_id"))
                                    if latest_execution is not None
                                    else None
                                ),
                                usage=gate.snapshot() if gate is not None else {},
                                terminal_evidence={
                                    **(result.terminal_evidence if result is not None else {}),
                                    "coding_stopped": True,
                                    "inspector_status": "canceled",
                                    "stop_confirmed": True,
                                },
                            )
                    finally:
                        mailbox.close()

    @staticmethod
    def _generation_prompt(
        goal: str,
        start_url: object,
        expected_result: object,
        origins: list[str],
        input_data: dict[str, Any],
    ) -> str:
        if not goal or len(goal.encode()) > 10000:
            raise ValueError("browser goal invalid")
        if not isinstance(start_url, str) or len(start_url.encode()) > 4096:
            raise ValueError("browser start URL invalid")
        parsed = urlsplit(start_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or origin not in origins
        ):
            raise ValueError("browser start URL outside approved origins")
        if (
            not isinstance(expected_result, str)
            or not expected_result.strip()
            or len(expected_result.encode()) > 4000
        ):
            raise ValueError("browser expected result invalid")
        return (
            "You are implementing a generic Playwright browser task. "
            "Read the exact frozen user input JSON from /workspace/frozen-inputs.json "
            "during generation; this is the only supplied input value source. "
            "Before any browser action, decompose the user goal into business-readable BDD steps "
            "and persist the plan using browserctl with "
            "{type:plan,steps:[{id,keyword,title,expected_result}]}. "
            "Use JSON quoted keys; keyword is Given, When, Then, or And; never report "
            "a step passed "
            "from your own prose. Every subsequent helper action MUST include step "
            "with its planned id. "
            "Plan amendments require reason and must retain already executed steps unchanged. "
            "Then inspect the actual approved page with "
            '`node /opt/tapper/browserctl.mjs \'{"type":"navigate","url":"...","step":"open"}\'` '
            "and snapshot/click/fill/select/press/wait/screenshot using the same fixed helper; "
            "only supplied origins are allowed. Then write /workspace/flow.spec.ts with a real "
            "Playwright test that uses actual page observations. Wrap each planned business step "
            "in test.step using the EXACT keyword + space + title from the saved plan, retaining "
            "the plan order; assert the expected outcomes in Then steps. Keep "
            "inspection and actual "
            "test execution distinct. Snapshot uses the official Playwright CLI AI "
            "accessibility tree "
            "with [ref=...] element references. Copy the complete reference exactly "
            "(including any frame prefix, for example aria-ref=f1e61) from the "
            "latest snapshot "
            "for inspection actions; re-observe after navigation or a stale reference error. "
            "For a long page, scope snapshot to a returned element using its aria-ref selector. "
            "Use observed labels/roles or returned CSS selectors in the frozen test, because "
            "snapshot refs are session-local and must never be replayed in a fresh browser. "
            "Never guess placeholders or repeatedly retry an ambiguous text selector. "
            "A navigation timeout may still include a usable DOM; inspect the returned "
            "content and error. "
            "Read frozen input JSON from "
            "/opt/browser/frozen/inputs.json during execution. To return business values, "
            "Use Playwright callback `test('task', async ({ page }, testInfo) => { ... })`; "
            "testInfo is the second argument, never a page fixture. "
            "attach a structured JSON object via "
            "`await testInfo.attach('tapper-result', {body: "
            "Buffer.from(JSON.stringify(values)), contentType: 'application/json'})`; "
            "all business outputs together must be <=32768 bytes. "
            "Verify actual page state with Playwright assertions and return business values "
            "extracted from the actual DOM, not constants copied from the expected result. "
            "If required data or external login/captcha blocks progress, write "
            "/workspace/tapper-input-request.json with "
            '`{"reason":"missing_input"|"external_block","question":"..."}` and stop. '
            "Do not claim success from prose. Approved origins: "
            + json.dumps(origins)
            + ". Frozen input keys: "
            + json.dumps(sorted(input_data))
            + ". Start URL: "
            + start_url
            + ". Expected result: "
            + expected_result
            + ". User goal: "
            + goal
        )
