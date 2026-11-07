import asyncio
import json
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

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
        if argv[:2] == ("docker", "network") and "create" in argv:
            return CommandResult(0, "network-id", "")
        if argv[:2] == ("docker", "inspect"):
            if "--format" in argv:
                return CommandResult(0, "true", "")
            return CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "Config": {"Labels": {"tap.browser.task": "t1"}},
                            "NetworkSettings": {
                                "Networks": {
                                    "tapper-browser-t1": {"IPAddress": "172.30.0.2"}
                                }
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
        if argv[:3] == ("docker", "run", "-d") and "tapper-browser-t1-runtime" in argv
    )
    assert ("--memory", "256m") == (
        browser[browser.index("--memory")],
        browser[browser.index("--memory") + 1],
    )
    assert ("--pids-limit", "64") == (
        browser[browser.index("--pids-limit")],
        browser[browser.index("--pids-limit") + 1],
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("TAP_RUN_BROWSER_RUNTIME_INTEGRATION") != "1",
    reason="requires owned Docker browser image",
)
async def test_real_chromium_isolated_flow(tmp_path: Path) -> None:
    image = "tapper-browser-runtime:1.62.1-arm64"
    client = SubprocessDockerClient()
    site = "tapper-browser-synthetic-site"

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
            image,
            "node",
            "/opt/browser/synthetic-site.mjs",
        )
    )
    assert started.exit_code == 0, started.stderr
    try:
        inspected = await client.run(("docker", "inspect", site))
        site_ip = json.loads(inspected.stdout)[0]["NetworkSettings"]["Networks"][
            "bridge"
        ]["IPAddress"]
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
            await runtime.observe(
                inspector,
                {"type": "fill", "selector": "[name=customer]", "value": "Ada"},
            )
            await runtime.observe(
                inspector,
                {"type": "select", "selector": "[name=region]", "value": "west"},
            )
            await runtime.observe(inspector, {"type": "click", "selector": "button"})
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
        script = b"""import { test, expect } from '@playwright/test';
test('lookup', async ({ page }) => {
  await page.goto('http://example.test/');
  await page.getByLabel('Customer').fill('Ada');
  await page.getByLabel('Region').selectOption('west');
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
