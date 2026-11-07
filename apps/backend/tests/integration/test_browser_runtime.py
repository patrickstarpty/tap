import asyncio
import json
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
import time

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
        if "/tmp/tapper-task-ca/ca.crt" in argv:
            return CommandResult(
                0, "-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----\n", ""
            )
        if argv[:3] == ("docker", "network", "inspect"):
            return CommandResult(
                0, json.dumps([{"Labels": {"tap.browser.task": "t1"}}]), ""
            )
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


class LifecycleDocker:
    def __init__(self, block: str = "") -> None:
        self.block = block
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.commands: list[tuple[str, ...]] = []
        self.containers: set[str] = set()
        self.networks: set[str] = set()

    async def run(
        self,
        argv: tuple[str, ...],
        stdin: bytes | None = None,
        *,
        max_stdout: int = 1_000_000,
    ) -> CommandResult:
        self.commands.append(argv)
        if argv[:3] == ("docker", "network", "create"):
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
                                "Labels": {
                                    "tap.browser.task": argv[-1].removeprefix(
                                        "tapper-browser-"
                                    )
                                }
                            }
                        ]
                    ),
                    "",
                )
                if argv[-1] in self.networks
                else CommandResult(1, "", "missing")
            )
        if argv[:3] == ("docker", "run", "-d"):
            self.containers.add(argv[argv.index("--name") + 1])
            return CommandResult(0, "container-id", "")
        if argv[:2] == ("docker", "inspect"):
            name = argv[-1]
            if name not in self.containers:
                return CommandResult(1, "", "missing")
            task = (
                name.removeprefix("tapper-browser-")
                .removesuffix("-proxy")
                .removesuffix("-runtime")
            )
            return CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "Config": {"Labels": {"tap.browser.task": task}},
                            "NetworkSettings": {
                                "Networks": {
                                    f"tapper-browser-{task}": {
                                        "IPAddress": "172.30.0.2"
                                    }
                                }
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
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=16m,uid=10001,gid=10001",
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
