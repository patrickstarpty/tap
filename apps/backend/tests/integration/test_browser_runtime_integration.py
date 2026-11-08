import asyncio
import base64
import json
import re
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
import time
import threading

import pytest

from tap_platform.browser_automation.contracts import (
    BrowserBundle,
    BrowserLimits,
    Evidence,
)
from tap_platform.browser_automation.docker import (
    BrowserRuntime,
    CommandResult,
    SubprocessDockerClient,
)


@dataclass
class FakeDocker:
    commands: list[tuple[tuple[str, ...], bytes | None]]
    fail_stop: bool = False

    async def run(
        self,
        argv: tuple[str, ...],
        stdin: bytes | None = None,
        *,
        max_stdout: int = 1_000_000,
    ) -> CommandResult:
        self.commands.append((argv, stdin))
        created = next(
            (
                command
                for command, _ in self.commands
                if command[:3] == ("docker", "network", "create")
            ),
            None,
        )
        network = created[-1] if created else ""
        labels = {
            created[index + 1].split("=", 1)[0]: created[index + 1].split("=", 1)[1]
            for index, value in enumerate(created or ())
            if value == "--label"
        }
        if argv[:2] == ("docker", "network") and "create" in argv:
            return CommandResult(0, "network-id", "")
        if "/tmp/tapper-task-ca/ca.crt" in argv:
            return CommandResult(
                0, "-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----\n", ""
            )
        if argv[:3] == ("docker", "network", "inspect"):
            return CommandResult(
                0, json.dumps([{"Id": "network-id", "Labels": labels}]), ""
            )
        if argv[:2] == ("docker", "inspect"):
            if "--format" in argv:
                return CommandResult(0, "true", "")
            return CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "Id": "ok",
                            "Config": {"Labels": labels},
                            "NetworkSettings": {
                                "Networks": {network: {"IPAddress": "172.30.0.2"}}
                            },
                        }
                    ]
                ),
                "",
            )
        if argv[:2] == ("docker", "stop") and self.fail_stop:
            return CommandResult(1, "", "unavailable")
        if argv[:3] == ("docker", "exec", "-i"):
            return CommandResult(
                0,
                json.dumps(
                    {
                        "session_id": "t1",
                        "sequence": 1,
                        "url": "https://example.com/",
                        "title": "Example",
                        "bounded_snapshot": "ok",
                        "fields": [],
                        "screenshot_ref": None,
                        "error": None,
                    }
                ),
                "",
            )
        return CommandResult(0, "ok", "")


@pytest.mark.asyncio
async def test_inspector_applies_network_gate_before_browser_process() -> None:
    client = FakeDocker([])
    runtime = BrowserRuntime(client, image="browser:test")
    handle = await runtime.start_inspector("t1", ("https://example.com",), 60)
    commands = [argv for argv, _ in client.commands]
    gate_index = next(
        i for i, argv in enumerate(commands) if "/opt/browser/network-policy.sh" in argv
    )
    launch_index = next(
        i for i, argv in enumerate(commands) if "/opt/browser/inspector.mjs" in argv
    )
    assert gate_index < launch_index
    observation = await runtime.observe(
        handle, {"type": "navigate", "url": "https://example.com/"}
    )
    assert observation.title == "Example"
    with pytest.raises(ValueError):
        await runtime.observe(handle, {"type": "navigate", "url": "http://127.0.0.1/"})


@pytest.mark.asyncio
async def test_cancel_requires_confirmed_stop() -> None:
    client = FakeDocker([], fail_stop=True)
    runtime = BrowserRuntime(client, image="browser:test")
    handle = await runtime.start_inspector("t1", ("https://example.com",), 60)
    receipt = await runtime.cancel(handle)
    assert receipt["stopped"] is False
    assert receipt["status"] == "unknown"


@pytest.mark.asyncio
async def test_cross_task_handle_rejected() -> None:
    client = FakeDocker([])
    runtime = BrowserRuntime(client, image="browser:test")
    handle = await runtime.start_inspector("t1", ("https://example.com",), 60)
    tampered = type(handle)(
        task_id="t2",
        container=handle.container,
        network=handle.network,
        proxy=handle.proxy,
        allowed_origins=handle.allowed_origins,
    )
    with pytest.raises(ValueError):
        await runtime.observe(tampered, {"type": "snapshot"})


@pytest.mark.asyncio
async def test_subprocess_client_bounds_command_output() -> None:
    client = SubprocessDockerClient()
    result = await client.run(("python3", "-c", "print('x' * 2000000)"))
    assert result.exit_code != 0
    assert len(result.stdout) <= 1_000_000


@pytest.mark.asyncio
async def test_execution_limits_reach_container_creation() -> None:
    client = FakeDocker([])
    runtime = BrowserRuntime(client, image="browser:test")
    await runtime._create_environment(
        "t1",
        ("https://example.com",),
        30,
        limits=BrowserLimits(memory_mb=256, cpus=0.5, pids=64),
    )
    browser = next(
        argv
        for argv, _ in client.commands
        if argv[:3] == ("docker", "run", "-d")
        and argv[argv.index("--name") + 1].endswith("-runtime")
    )
    assert ("--memory", "256m") == (
        browser[browser.index("--memory")],
        browser[browser.index("--memory") + 1],
    )
    assert ("--pids-limit", "64") == (
        browser[browser.index("--pids-limit")],
        browser[browser.index("--pids-limit") + 1],
    )


class LifecycleDocker:
    def __init__(self, block: str = "") -> None:
        self.block = block
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.commands: list[tuple[str, ...]] = []
        self.containers: set[str] = set()
        self.networks: set[str] = set()
        self.labels: dict[str, dict[str, str]] = {}

    async def run(
        self,
        argv: tuple[str, ...],
        stdin: bytes | None = None,
        *,
        max_stdout: int = 1_000_000,
    ) -> CommandResult:
        self.commands.append(argv)
        if argv[:3] == ("docker", "network", "create"):
            self.labels[argv[-1]] = {
                argv[index + 1].split("=", 1)[0]: argv[index + 1].split("=", 1)[1]
                for index, value in enumerate(argv)
                if value == "--label"
            }
            self.networks.add(argv[-1])
            if self.block == "create":
                self.entered.set()
                await self.release.wait()
            return CommandResult(0, "network-id", "")
        if self.block and self.block in argv:
            self.entered.set()
            await self.release.wait()
        if argv[:3] == ("docker", "network", "rm"):
            self.networks.discard(argv[-1])
            return CommandResult(0, argv[-1], "")
        if argv[:3] == ("docker", "network", "inspect"):
            return (
                CommandResult(
                    0,
                    json.dumps(
                        [
                            {
                                "Id": "network-id",
                                "Labels": self.labels.get(argv[-1], {}),
                            }
                        ]
                    ),
                    "",
                )
                if argv[-1] in self.networks
                else CommandResult(1, "", "missing")
            )
        if argv[:3] == ("docker", "run", "-d"):
            name = argv[argv.index("--name") + 1]
            self.containers.add(name)
            self.labels[name] = {
                argv[index + 1].split("=", 1)[0]: argv[index + 1].split("=", 1)[1]
                for index, value in enumerate(argv)
                if value == "--label"
            }
            return CommandResult(0, "container-id", "")
        if argv[:2] == ("docker", "inspect"):
            name = argv[-1]
            if name not in self.containers:
                return CommandResult(1, "", "missing")
            network = name.removesuffix("-proxy").removesuffix("-runtime")
            return CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "Id": "container-id",
                            "Config": {"Labels": self.labels.get(name, {})},
                            "NetworkSettings": {
                                "Networks": {network: {"IPAddress": "172.30.0.2"}}
                            },
                        }
                    ]
                ),
                "",
            )
        if argv[:2] == ("docker", "stop"):
            return CommandResult(0, argv[-1], "")
        if argv[:2] == ("docker", "rm"):
            self.containers.discard(argv[-1])
            return CommandResult(0, argv[-1], "")
        if argv[:3] == ("docker", "exec", "-i") and "/opt/browser/runner.mjs" in argv:
            return CommandResult(1, "", "stopped")
        if argv[:3] == ("docker", "exec", "-i") and "/opt/browser/export.mjs" in argv:
            return CommandResult(1, "", "stopped")
        if argv[:2] == ("docker", "exec") and "test" in argv:
            return CommandResult(0, "", "")
        return CommandResult(0, "ok", "")


def _lifecycle_bundle() -> BrowserBundle:
    script = b"import { test } from '@playwright/test'; test('x', async () => {});"
    return BrowserBundle(
        "b",
        "p",
        hashlib.sha256(script).hexdigest(),
        script,
        hashlib.sha256(b"{}").hexdigest(),
        ("http://example.test",),
        "2026-11-07T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_cancel_during_setup_prevents_runner_launch() -> None:
    client = LifecycleDocker("create")
    runtime = BrowserRuntime(client, "browser:test", evidence_sink=object())
    running = asyncio.create_task(
        runtime.execute(
            _lifecycle_bundle(),
            {"task_id": "pending", "input_data": {}},
            BrowserLimits(),
            execution_id="known",
        )
    )
    await asyncio.wait_for(client.entered.wait(), 1)
    receipt = await asyncio.wait_for(runtime.cancel_execution("known", "pending"), 2)
    result = await asyncio.wait_for(running, 2)
    assert receipt["status"] == "canceled"
    assert result.status == "canceled"
    assert not any("/opt/browser/runner.mjs" in cmd for cmd in client.commands)
    assert not client.networks


@pytest.mark.asyncio
async def test_deadline_bounds_setup_and_inspector_expiry_cleans_proxy() -> None:
    client = LifecycleDocker("create")
    runtime = BrowserRuntime(client, "browser:test", evidence_sink=object())
    started = time.monotonic()
    result = await asyncio.wait_for(
        runtime.execute(
            _lifecycle_bundle(),
            {"task_id": "deadline", "input_data": {}},
            BrowserLimits(deadline_seconds=1),
            execution_id="deadline-run",
        ),
        3,
    )
    assert result.status != "passed"
    assert time.monotonic() - started < 3
    assert not client.networks
    client.block = ""
    handle = await runtime.start_inspector("expire", ("http://example.test",), 1)
    await asyncio.sleep(1.2)
    assert handle.task_id not in runtime._handles
    assert not client.containers
    assert not client.networks


@pytest.mark.asyncio
async def test_cancel_during_export_has_one_confirmed_teardown() -> None:
    client = LifecycleDocker("/opt/browser/export.mjs")
    runtime = BrowserRuntime(client, "browser:test", evidence_sink=object())
    running = asyncio.create_task(
        runtime.execute(
            _lifecycle_bundle(),
            {"task_id": "export", "input_data": {}},
            BrowserLimits(),
            execution_id="export-run",
        )
    )
    await asyncio.wait_for(client.entered.wait(), 1)
    receipt = await asyncio.wait_for(
        runtime.cancel_execution("export-run", "export"), 2
    )
    result = await asyncio.wait_for(running, 2)
    assert receipt["status"] == "canceled"
    assert result.status == "canceled"
    assert not client.containers
    assert not client.networks


class SuccessfulOutputDocker:
    async def run(
        self,
        argv: tuple[str, ...],
        stdin: bytes | None = None,
        *,
        max_stdout: int = 1_000_000,
    ) -> CommandResult:
        if "/opt/browser/runner.mjs" in argv:
            return CommandResult(0, '{"nonce":"n"}', "")
        if "/opt/browser/export.mjs" in argv:
            result = {
                "nonce": "n",
                "passed": 1,
                "skipped": 0,
                "steps": [],
                "evidence": [
                    {"kind": "screenshot", "name": "shot.png"},
                    {"kind": "trace", "name": "trace.zip"},
                ],
            }
            files = (
                ("result.json", json.dumps(result).encode()),
                ("shot.png", b"\x89PNG\r\n\x1a\nshot"),
                ("trace.zip", b"PK\x03\x04trace"),
            )
            return CommandResult(
                0,
                json.dumps(
                    [
                        {"name": name, "data": base64.b64encode(data).decode()}
                        for name, data in files
                    ]
                ),
                "",
            )
        return CommandResult(0, "ok", "")


def _fast_owned_environment(
    monkeypatch: pytest.MonkeyPatch, runtime: BrowserRuntime
) -> None:
    async def create(*args: object, **kwargs: object) -> tuple[str, str, str]:
        return (
            "tapper-browser-fast",
            "tapper-browser-fast-proxy",
            "tapper-browser-fast-runtime",
        )

    async def cleanup(*args: object, **kwargs: object) -> bool:
        return True

    monkeypatch.setattr(runtime, "_create_environment", create)
    monkeypatch.setattr(runtime, "_cleanup", cleanup)


@pytest.mark.asyncio
async def test_slow_sink_cannot_publish_passed_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowSink:
        def persist(
            self,
            project_id: str,
            task_id: str,
            execution_id: str,
            kind: str,
            data: bytes,
            max_bytes: int,
        ) -> Evidence:
            time.sleep(0.4)
            return Evidence(
                kind,
                f"{project_id}/{task_id}/{kind}",
                hashlib.sha256(data).hexdigest(),
                len(data),
            )

    runtime = BrowserRuntime(
        SuccessfulOutputDocker(), "browser:test", evidence_sink=SlowSink()
    )
    _fast_owned_environment(monkeypatch, runtime)
    started = time.monotonic()
    result = await asyncio.wait_for(
        runtime.execute(
            _lifecycle_bundle(),
            {"task_id": "slow", "input_data": {}},
            BrowserLimits(deadline_seconds=1),
            execution_id="slow-run",
        ),
        2,
    )
    assert result.status != "passed"
    assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_cancel_remains_responsive_during_synchronous_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()

    class SlowSink:
        def persist(
            self,
            project_id: str,
            task_id: str,
            execution_id: str,
            kind: str,
            data: bytes,
            max_bytes: int,
        ) -> Evidence:
            entered.set()
            time.sleep(1.5)
            return Evidence(
                kind,
                f"{project_id}/{task_id}/{kind}",
                hashlib.sha256(data).hexdigest(),
                len(data),
            )

    runtime = BrowserRuntime(
        SuccessfulOutputDocker(), "browser:test", evidence_sink=SlowSink()
    )
    _fast_owned_environment(monkeypatch, runtime)
    running = asyncio.create_task(
        runtime.execute(
            _lifecycle_bundle(),
            {"task_id": "sink", "input_data": {}},
            BrowserLimits(deadline_seconds=5),
            execution_id="sink-run",
        )
    )
    assert await asyncio.to_thread(entered.wait, 1)
    started = time.monotonic()
    receipt = await asyncio.wait_for(runtime.cancel_execution("sink-run", "sink"), 1)
    result = await asyncio.wait_for(running, 1)
    assert time.monotonic() - started < 1
    assert receipt["status"] == result.status == "canceled"


@pytest.mark.asyncio
async def test_failed_initial_network_create_does_not_clean_existing_resources() -> (
    None
):
    class CollisionDocker(LifecycleDocker):
        async def run(
            self,
            argv: tuple[str, ...],
            stdin: bytes | None = None,
            *,
            max_stdout: int = 1_000_000,
        ) -> CommandResult:
            if argv[:3] == ("docker", "network", "create"):
                self.commands.append(argv)
                self.networks.add(argv[-1])
                self.containers.update((f"{argv[-1]}-proxy", f"{argv[-1]}-runtime"))
                return CommandResult(1, "", "network already exists")
            return await super().run(argv, stdin, max_stdout=max_stdout)

    client = CollisionDocker()
    runtime = BrowserRuntime(client, "browser:test", evidence_sink=object())
    result = await runtime.execute(
        _lifecycle_bundle(),
        {"task_id": "collision", "input_data": {}},
        BrowserLimits(),
        execution_id="known",
    )
    assert result.status != "passed"
    assert not any(argv[:2] == ("docker", "stop") for argv in client.commands)
    assert not any(argv[:3] == ("docker", "network", "rm") for argv in client.commands)
    assert client.networks and client.containers


@pytest.mark.asyncio
async def test_nonzero_proxy_start_cleans_created_owned_container() -> None:
    class PartialStartDocker(LifecycleDocker):
        async def run(
            self,
            argv: tuple[str, ...],
            stdin: bytes | None = None,
            *,
            max_stdout: int = 1_000_000,
        ) -> CommandResult:
            if argv[:3] == ("docker", "run", "-d") and argv[
                argv.index("--name") + 1
            ].endswith("-proxy"):
                await super().run(argv, stdin, max_stdout=max_stdout)
                return CommandResult(125, "", "container created but start failed")
            if argv[:2] == ("docker", "stop"):
                self.commands.append(argv)
                return CommandResult(1, "", "container not running")
            if argv[:3] == ("docker", "inspect", "--format"):
                self.commands.append(argv)
                return CommandResult(0, "false", "")
            return await super().run(argv, stdin, max_stdout=max_stdout)

    client = PartialStartDocker()
    runtime = BrowserRuntime(client, "browser:test", evidence_sink=object())
    result = await runtime.execute(
        _lifecycle_bundle(),
        {"task_id": "partial", "input_data": {}},
        BrowserLimits(),
        execution_id="partial-run",
    )
    assert result.status == "failed"
    assert not client.containers
    assert not client.networks
    assert any(argv[:2] == ("docker", "rm") for argv in client.commands)


@pytest.mark.asyncio
async def test_concurrent_same_task_inspector_start_reserves_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeDocker([])
    runtime = BrowserRuntime(client, "browser:test")
    entered = asyncio.Event()
    release = asyncio.Event()
    creations = 0

    async def create(*args: object, **kwargs: object) -> tuple[str, str, str]:
        nonlocal creations
        creations += 1
        entered.set()
        await release.wait()
        return "network", "proxy", "browser"

    monkeypatch.setattr(runtime, "_create_environment", create)
    first = asyncio.create_task(
        runtime.start_inspector("same", ("http://example.test",), 60)
    )
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(
        runtime.start_inspector("same", ("http://example.test",), 60)
    )
    await asyncio.sleep(0)
    observed_creations = creations
    release.set()
    handle = await asyncio.wait_for(first, 1)
    try:
        with pytest.raises(ValueError, match="already active"):
            await asyncio.wait_for(second, 1)
        assert observed_creations == 1
        assert runtime._handles["same"] is handle
    finally:
        if runtime._handles.get("same") is handle:
            await runtime.cancel(handle)


@pytest.mark.asyncio
async def test_failed_inspector_setup_releases_task_reservation() -> None:
    client = LifecycleDocker("create")
    runtime = BrowserRuntime(client, "browser:test")
    pending = asyncio.create_task(
        runtime.start_inspector("retry", ("http://example.test",), 30)
    )
    await asyncio.wait_for(client.entered.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert "retry" not in runtime._inspector_pending
    client.block = ""
    handle = await runtime.start_inspector("retry", ("http://example.test",), 30)
    assert (await runtime.cancel(handle))["stopped"]


@pytest.mark.asyncio
async def test_pending_inspector_cancel_returns_cleanup_receipt() -> None:
    client = LifecycleDocker("create")
    runtime = BrowserRuntime(client, "browser:test")
    pending = asyncio.create_task(
        runtime.start_inspector("pending", ("http://example.test",), 30)
    )
    await asyncio.wait_for(client.entered.wait(), 1)
    receipt = await asyncio.wait_for(runtime.cancel_inspector("pending"), 2)
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert receipt == {"task_id": "pending", "stopped": True, "status": "canceled"}
    assert not client.networks
    assert not client.containers


@pytest.mark.asyncio
async def test_pending_inspector_cancel_requires_exact_session_owner() -> None:
    client = LifecycleDocker("create")
    runtime = BrowserRuntime(client, "browser:test")
    pending = asyncio.create_task(
        runtime.start_inspector(
            "same-task", ("http://example.test",), 30, session_id="session-a"
        )
    )
    await asyncio.wait_for(client.entered.wait(), 1)
    wrong = await runtime.cancel_inspector("same-task", session_id="session-b")
    assert wrong["stopped"] is False
    assert not pending.done()
    correct = await runtime.cancel_inspector("same-task", session_id="session-a")
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert correct["stopped"] is True
    assert (await runtime.cancel_inspector("same-task", session_id="session-b"))[
        "stopped"
    ] is False


@pytest.mark.asyncio
async def test_active_inspector_cancel_requires_exact_session_owner() -> None:
    client = FakeDocker([])
    runtime = BrowserRuntime(client, "browser:test")
    handle = await runtime.start_inspector(
        "same-task", ("https://example.com",), 60, session_id="session-a"
    )
    wrong = await runtime.cancel_inspector("same-task", session_id="session-b")
    assert wrong["stopped"] is False
    assert runtime._handles["same-task"] is handle
    assert (await runtime.cancel_inspector("same-task", session_id="session-a"))[
        "stopped"
    ] is True


@pytest.mark.asyncio
async def test_inspector_screenshot_passes_immutable_session_before_sink_thread() -> (
    None
):
    class ScreenshotDocker(FakeDocker):
        async def run(self, argv, stdin=None, *, max_stdout=1_000_000):
            if argv[:3] == ("docker", "exec", "-i"):
                return CommandResult(
                    0,
                    json.dumps(
                        {
                            "session_id": "internal",
                            "sequence": 1,
                            "url": "https://example.com/",
                            "title": "Example",
                            "bounded_snapshot": "snapshot",
                            "fields": [],
                            "screenshot_data": base64.b64encode(
                                b"\x89PNG\r\n\x1a\nimage"
                            ).decode(),
                            "error": None,
                        }
                    ),
                    "",
                )
            return await super().run(argv, stdin, max_stdout=max_stdout)

    class CapturingSink:
        def __init__(self):
            self.sessions = []

        def persist(
            self,
            project_id,
            task_id,
            execution_id,
            kind,
            data,
            max_bytes,
            *,
            session_id=None,
        ):
            self.sessions.append(session_id)
            return Evidence(
                kind, "screenshot-ref", hashlib.sha256(data).hexdigest(), len(data)
            )

    sink = CapturingSink()
    runtime = BrowserRuntime(
        ScreenshotDocker([]), "browser:test", evidence_sink=sink, project_id="project-a"
    )
    handle = await runtime.start_inspector(
        "task-a", ("https://example.com",), 60, session_id="session-a"
    )
    try:
        observation = await runtime.observe(handle, {"type": "screenshot"})
        assert observation.screenshot_ref == "screenshot-ref"
        assert sink.sessions == ["session-a"]
    finally:
        await runtime.cancel(handle)


@pytest.mark.asyncio
async def test_expiry_and_user_cancel_share_inspector_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LifecycleDocker()
    runtime = BrowserRuntime(client, "browser:test")
    handle = await runtime.start_inspector("joined", ("http://example.test",), 1)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def cleanup(*args: object, **kwargs: object) -> bool:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return True

    monkeypatch.setattr(runtime, "_cleanup", cleanup)
    await asyncio.wait_for(entered.wait(), 2)  # expiry owns the first cleanup
    user_cancel = asyncio.create_task(runtime.cancel(handle))
    await asyncio.sleep(0)
    release.set()
    receipt = await asyncio.wait_for(user_cancel, 1)
    assert receipt["status"] == "canceled"
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("TAP_RUN_BROWSER_RUNTIME_INTEGRATION") != "1",
    reason="requires owned Docker browser image",
)
async def test_real_chromium_isolated_flow(tmp_path: Path) -> None:
    image = "tapper-browser-runtime:1.62.1-arm64"
    client = SubprocessDockerClient()
    site = "tapper-browser-synthetic-site"
    fixture = Path(__file__).resolve().parents[4] / "deploy/browser/synthetic-site.mjs"

    @dataclass
    class Sink:
        def persist(
            self,
            project_id: str,
            task_id: str,
            execution_id: str,
            kind: str,
            data: bytes,
            max_bytes: int,
        ) -> Evidence:
            assert len(data) <= max_bytes
            digest = hashlib.sha256(data).hexdigest()
            name = f"{execution_id}-{kind}-{digest}"
            (tmp_path / name).write_bytes(data)
            return Evidence(kind, f"{project_id}/{task_id}/{name}", digest, len(data))

    started = await client.run(
        (
            "docker",
            "run",
            "-d",
            "--name",
            site,
            "--label",
            "tap.browser.test=owned",
            "--network",
            "bridge",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "10001:10001",
            "--read-only",
            "--mount",
            f"type=bind,source={fixture},target=/opt/browser/synthetic-site-fixture.mjs,readonly",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=16m,uid=10001,gid=10001",
            image,
            "node",
            "/opt/browser/synthetic-site-fixture.mjs",
        )
    )
    assert started.exit_code == 0, started.stderr
    try:
        inspected = await client.run(("docker", "inspect", site))
        site_ip = json.loads(inspected.stdout)[0]["NetworkSettings"]["Networks"][
            "bridge"
        ]["IPAddress"]
        for _ in range(50):
            site_cert = await client.run(
                ("docker", "exec", site, "cat", "/tmp/site.crt")
            )
            if site_cert.exit_code == 0:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("owned HTTPS fixture certificate unavailable")
        runtime = BrowserRuntime(
            client,
            image,
            evidence_sink=Sink(),
            project_id="p1",
            test_target=f"http://{site_ip}:8080",
        )
        inspector = await runtime.start_inspector(
            "integration-inspect", ("http://example.test",), 90
        )
        try:
            first = await runtime.observe(
                inspector, {"type": "navigate", "url": "http://example.test/"}
            )
            assert first.title == "Synthetic Tapper Site"
            assert any(field["name"] == "customer" for field in first.fields)
            custom = next(
                field for field in first.fields if field.get("id") == "custom-region"
            )
            assert custom["role"] == "combobox"
            assert custom["selector"] == "#custom-region"
            assert custom["label"] == "Delivery region"
            customer_ref = re.search(
                r'textbox "Customer" \[ref=(e\d+)\]', first.bounded_snapshot
            )
            assert customer_ref, first.bounded_snapshot
            by_ref = await runtime.observe(
                inspector,
                {
                    "type": "fill",
                    "selector": f"aria-ref={customer_ref[1]}",
                    "value": "Observed reference",
                },
            )
            assert by_ref.error is None
            assert any(
                field.get("value") == "Observed reference" for field in by_ref.fields
            )
            failed_click = await runtime.observe(
                inspector, {"type": "click", "selector": "button"}
            )
            assert "strict mode" in failed_click.error
            assert failed_click.title == "Synthetic Tapper Site"
            assert "Order lookup" in failed_click.bounded_snapshot
            delayed_started = time.monotonic()
            delayed = await runtime.observe(
                inspector,
                {"type": "navigate", "url": "http://example.test/delayed-load"},
            )
            delayed_snapshot = await runtime.observe(inspector, {"type": "snapshot"})
            assert "Order lookup" in delayed_snapshot.bounded_snapshot
            assert delayed.error is None
            assert delayed.title == "Synthetic Tapper Site"
            assert "Order lookup" in delayed.bounded_snapshot
            assert time.monotonic() - delayed_started < 15
            partial = await runtime.observe(
                inspector,
                {"type": "navigate", "url": "http://example.test/delayed-dom"},
            )
            assert partial.error is not None and "Timeout" in partial.error
            assert partial.title == "Synthetic Tapper Site"
            assert "Order lookup" in partial.bounded_snapshot
            assert any(field["name"] == "customer" for field in partial.fields)
            await runtime.observe(
                inspector,
                {"type": "fill", "selector": "[name=customer]", "value": "Ada"},
            )
            await runtime.observe(
                inspector,
                {"type": "select", "selector": "[name=region]", "value": "west"},
            )
            await runtime.observe(
                inspector, {"type": "click", "selector": "button[type=submit]"}
            )
            snapshot = await runtime.observe(inspector, {"type": "screenshot"})
            assert "Ada / west / FOUND" in snapshot.bounded_snapshot
            assert snapshot.screenshot_ref is not None
            blocked = await client.run(
                (
                    "docker",
                    "exec",
                    inspector.container,
                    "node",
                    "-e",
                    "const s=require('net').connect(80,'1.1.1.1');s.setTimeout(1000);s.on('connect',()=>process.exit(0));s.on('error',()=>process.exit(1));s.on('timeout',()=>process.exit(1))",
                )
            )
            assert blocked.exit_code != 0
            inspected_browser = await client.run(
                ("docker", "inspect", inspector.container)
            )
            gateway = json.loads(inspected_browser.stdout)[0]["NetworkSettings"][
                "Networks"
            ][inspector.network]["Gateway"]
            assert gateway
            blocked_gateway = await client.run(
                (
                    "docker",
                    "exec",
                    inspector.container,
                    "node",
                    "-e",
                    "const s=require('net').connect(80,process.argv[1]);s.setTimeout(1000);s.on('connect',()=>process.exit(0));s.on('error',()=>process.exit(1));s.on('timeout',()=>process.exit(1))",
                    gateway,
                )
            )
            assert blocked_gateway.exit_code != 0
            second = await runtime.start_inspector(
                "integration-second", ("http://example.test",), 90
            )
            try:
                inspected_second = await client.run(("docker", "inspect", second.proxy))
                other_ip = json.loads(inspected_second.stdout)[0]["NetworkSettings"][
                    "Networks"
                ][second.network]["IPAddress"]
                blocked_other = await client.run(
                    (
                        "docker",
                        "exec",
                        inspector.container,
                        "node",
                        "-e",
                        "const s=require('net').connect(3128,process.argv[1]);s.setTimeout(1000);s.on('connect',()=>process.exit(0));s.on('error',()=>process.exit(1));s.on('timeout',()=>process.exit(1))",
                        other_ip,
                    )
                )
                assert blocked_other.exit_code != 0
            finally:
                assert (await runtime.cancel(second))["stopped"]
        finally:
            assert (await runtime.cancel(inspector))["stopped"]
        unreachable_runtime = BrowserRuntime(
            client, image, evidence_sink=Sink(), project_id="p1"
        )
        unreachable = await unreachable_runtime.start_inspector(
            "integration-unreachable", ("http://example.invalid",), 45
        )
        try:
            missing = await unreachable_runtime.observe(
                unreachable, {"type": "navigate", "url": "http://example.invalid/"}
            )
            assert "destination denied" in missing.bounded_snapshot
            assert not missing.title
        finally:
            assert (await unreachable_runtime.cancel(unreachable))["stopped"]
        unreachable_script = b"""import { test, expect } from '@playwright/test';
test('unreachable target is not a pass', async ({ page }) => {
  await page.goto('http://example.invalid/');
  await expect(page.getByRole('heading', { name: 'Order result' })).toBeVisible({ timeout: 1000 });
});
"""
        unreachable_bundle = BrowserBundle(
            "unreachable-bundle",
            "p1",
            hashlib.sha256(unreachable_script).hexdigest(),
            unreachable_script,
            hashlib.sha256(b"{}").hexdigest(),
            ("http://example.invalid",),
            "2026-11-07T00:00:00Z",
        )
        unreachable_result = await unreachable_runtime.execute(
            unreachable_bundle,
            {"task_id": "integration-unreachable-run", "input_data": {}},
            BrowserLimits(deadline_seconds=45),
        )
        assert unreachable_result.status == "failed"
        https_runtime = BrowserRuntime(
            client,
            image,
            evidence_sink=Sink(),
            project_id="p1",
            test_target=f"https://{site_ip}:8443",
            test_ca_pem=site_cert.stdout,
        )
        https_inspector = await https_runtime.start_inspector(
            "integration-https", ("https://example.test",), 90
        )
        try:
            secure = await https_runtime.observe(
                https_inspector,
                {"type": "navigate", "url": "https://example.test/"},
            )
            assert secure.title == "Synthetic Tapper Site", secure.error
            host_probe = r"""
const net=require('net'),tls=require('tls'),fs=require('fs');
setTimeout(()=>process.exit(9),10000).unref();
const p=new URL(process.env.TAP_BROWSER_PROXY);const raw=net.connect(Number(p.port),p.hostname);
raw.on('connect',()=>raw.write('CONNECT example.test:443 HTTP/1.1\r\nHost: example.test:443\r\n\r\n'));
let header='';raw.on('data',function collect(b){header+=b.toString();if(!header.includes('\r\n\r\n'))return;raw.off('data',collect);
if(!header.startsWith('HTTP/1.1 200'))process.exit(2);
const secure=tls.connect({socket:raw,servername:'example.test',ca:fs.readFileSync('/home/browser/ca.crt')});
let response='';secure.on('secureConnect',()=>secure.write('GET / HTTP/1.1\r\nHost: other.test\r\nConnection: close\r\n\r\n'));
secure.on('data',b=>response+=b.toString());secure.on('end',()=>process.exit(response.startsWith('HTTP/1.1 403')?0:3));
secure.on('error',()=>process.exit(4));});
"""
            denied_host = await client.run(
                ("docker", "exec", https_inspector.container, "node", "-e", host_probe)
            )
            assert denied_host.exit_code == 0, denied_host.stderr
            sni_probe = r"""
const net=require('net'),tls=require('tls'),fs=require('fs');
setTimeout(()=>process.exit(9),10000).unref();
const p=new URL(process.env.TAP_BROWSER_PROXY);const raw=net.connect(Number(p.port),p.hostname);
raw.on('connect',()=>raw.write('CONNECT example.test:443 HTTP/1.1\r\nHost: example.test:443\r\n\r\n'));
let header='';raw.on('data',function collect(b){header+=b.toString();if(!header.includes('\r\n\r\n'))return;raw.off('data',collect);
if(!header.startsWith('HTTP/1.1 200'))process.exit(2);
const secure=tls.connect({socket:raw,servername:'other.test',ca:fs.readFileSync('/home/browser/ca.crt'),
checkServerIdentity:(_name,cert)=>tls.checkServerIdentity('example.test',cert)});
let response='';secure.on('secureConnect',()=>secure.write('GET / HTTP/1.1\r\nHost: example.test\r\nConnection: close\r\n\r\n'));
secure.on('data',b=>response+=b.toString());secure.on('close',()=>process.exit(response.includes('Synthetic Tapper Site')?3:0));
secure.on('error',()=>process.exit(0));});
"""
            denied_sni = await client.run(
                ("docker", "exec", https_inspector.container, "node", "-e", sni_probe)
            )
            assert denied_sni.exit_code == 0, denied_sni.stderr
        finally:
            assert (await https_runtime.cancel(https_inspector))["stopped"]
        script = b"""import { test, expect } from '@playwright/test';
test('lookup', async ({ page }) => {
  await page.goto('http://example.test/');
  await page.getByLabel('Customer').fill('Ada');
  await page.getByLabel('Region', { exact: true }).selectOption('west');
  await page.getByRole('button', { name: 'Find' }).click();
  await expect(page.locator('#result')).toHaveText('Ada / west / FOUND');
});
"""
        input_data: dict[str, str] = {}
        bundle = BrowserBundle(
            "b1",
            "p1",
            hashlib.sha256(script).hexdigest(),
            script,
            hashlib.sha256(b"{}").hexdigest(),
            ("http://example.test",),
            "2026-11-07T00:00:00Z",
        )
        execution = await runtime.execute(
            bundle,
            {"task_id": "integration-run", "input_data": input_data},
            BrowserLimits(deadline_seconds=90, max_output_bytes=5_000_000),
        )
        assert execution.status == "passed", execution.result
        assert {item.kind for item in execution.evidence} >= {"screenshot", "trace"}
        assert all(
            (tmp_path / item.object_ref.split("/")[-1]).exists()
            for item in execution.evidence
        )
        slow_script = b"import { test } from '@playwright/test'; test('slow', async ({ page }) => { await page.goto('http://example.test/'); await page.waitForTimeout(60000); });"
        slow_bundle = BrowserBundle(
            "b2",
            "p1",
            hashlib.sha256(slow_script).hexdigest(),
            slow_script,
            hashlib.sha256(b"{}").hexdigest(),
            ("http://example.test",),
            "2026-11-07T00:00:00Z",
        )
        running = asyncio.create_task(
            runtime.execute(
                slow_bundle,
                {"task_id": "integration-cancel-task", "input_data": {}},
                BrowserLimits(deadline_seconds=90),
                execution_id="integration-cancel",
            )
        )
        for _ in range(100):
            if "integration-cancel" in runtime._executions:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("execution did not become active")
        canceled = await runtime.cancel_execution(
            "integration-cancel", "integration-cancel-task"
        )
        assert canceled["stopped"] is True
        assert (await running).status == "canceled"
    finally:
        await client.run(("docker", "stop", "--time", "2", site))
        await client.run(("docker", "rm", site))
