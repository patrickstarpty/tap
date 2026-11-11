"""Local transport owns processes/profiles while retaining runtime contracts."""

import asyncio
import hashlib
import shutil
from pathlib import Path

import pytest

from tap_platform.browser_automation.contracts import BrowserBundle, BrowserLimits


@pytest.fixture
def inspector_dir(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[4] / "deploy/browser"
    for name in (
        "site-proxy.mjs",
        "upstream-transport.mjs",
        "proxy-cert.mjs",
        "export.mjs",
    ):
        shutil.copyfile(source / name, tmp_path / name)
    (tmp_path / "runner.mjs").write_text("""
import fs from 'node:fs';
const output = process.env.TAP_BROWSER_OUTPUT_DIR;
fs.mkdirSync(output, {recursive:true});
fs.writeFileSync(output + '/screenshot-0.png', Buffer.from('89504e470d0a1a0a', 'hex'));
fs.writeFileSync(output + '/trace-1.zip', Buffer.from('504b0304', 'hex'));
fs.writeFileSync(output + '/result.json', JSON.stringify({nonce:'owned-run', passed:1,
  skipped:0, outputs:[{quote:'10.62'}], evidence:[{kind:'screenshot',name:'screenshot-0.png'},
  {kind:'trace',name:'trace-1.zip'}]}));
process.stdout.write(JSON.stringify({nonce:'owned-run'}));
""")
    # A real child/socket isolates transport and lifecycle tests from Chromium.
    (tmp_path / "inspector.mjs").write_text("""
import fs from 'node:fs';
import net from 'node:net';
const root = process.env.TAP_BROWSER_LOCAL_DIR;
fs.mkdirSync(root + '/profile', {recursive:true});
fs.writeFileSync(root + '/profile/DevToolsActivePort', '54321\\n/devtools/browser/test');
net.createServer(client => {
  client.once('data', data => {
    const action = JSON.parse(data);
    client.end(JSON.stringify({session_id:process.env.TAP_BROWSER_SESSION,
      sequence:1, url:'about:blank', title:'Local child', bounded_snapshot:'', fields:[],
      screenshot_ref:null, error:null}));
  });
}).listen(process.env.TAP_BROWSER_SOCKET);
""")
    return tmp_path


def local_runtime(directory: Path):
    from tap_platform.browser_automation.local import LocalBrowserRuntime

    return LocalBrowserRuntime(browser_dir=directory)


@pytest.mark.asyncio
async def test_local_inspector_uses_private_socket_and_owned_cleanup(inspector_dir):
    runtime = local_runtime(inspector_dir)
    handle = await runtime.start_inspector(
        "task1", ("https://example.com",), 10, session_id="session1"
    )
    root = Path(handle.container)
    try:
        assert root.stat().st_mode & 0o777 == 0o700
        assert (root / "inspector.sock").is_socket()
        assert handle.cdp_url == "http://127.0.0.1:54321"
        observation = await runtime.observe(handle, {"type": "snapshot"})
        assert observation.title == "Local child"
        assert observation.session_id == "session1"
        foreign = await runtime.cancel_inspector("task1", session_id="other")
        assert foreign["status"] == "unknown"
        assert (root / "inspector.sock").exists()
    finally:
        receipt = await runtime.cancel(handle)
    assert receipt["stopped"] is True
    assert not root.exists()
    with pytest.raises(ValueError, match="stale"):
        await runtime.observe(handle, {"type": "snapshot"})


@pytest.mark.asyncio
async def test_local_navigation_keeps_origin_and_action_guards(inspector_dir):
    runtime = local_runtime(inspector_dir)
    handle = await runtime.start_inspector("task1", ("https://example.com",), 10)
    try:
        for action in (
            {"type": "navigate", "url": "http://127.0.0.1/"},
            {"type": "navigate", "url": "https://other.example/"},
            {"type": "evaluate", "script": "process.exit()"},
        ):
            with pytest.raises(ValueError):
                await runtime.observe(handle, action)
    finally:
        await runtime.cancel(handle)


@pytest.mark.asyncio
async def test_local_expiry_removes_profile_and_process(inspector_dir):
    runtime = local_runtime(inspector_dir)
    handle = await runtime.start_inspector("task1", ("https://example.com",), 1)
    root = Path(handle.container)
    async with asyncio.timeout(5):
        while root.exists():
            await asyncio.sleep(0.05)
    assert "task1" not in runtime._handles


@pytest.mark.asyncio
async def test_local_start_failure_cleans_owned_profile(inspector_dir):
    (inspector_dir / "inspector.mjs").write_text("process.exit(17);")
    runtime = local_runtime(inspector_dir)
    with pytest.raises(RuntimeError, match="inspector exited"):
        await runtime.start_inspector(
            "task1", ("https://example.com",), 5, session_id="session1"
        )
    receipt = await runtime.cancel_inspector("task1", session_id="session1")
    assert receipt["stopped"] is True
    assert not runtime._local_processes


@pytest.mark.asyncio
async def test_local_cancel_during_start_confirms_process_stopped(inspector_dir):
    (inspector_dir / "inspector.mjs").write_text("setInterval(() => {}, 1000);")
    runtime = local_runtime(inspector_dir)
    task = asyncio.create_task(
        runtime.start_inspector(
            "task1", ("https://example.com",), 10, session_id="session1"
        )
    )
    async with asyncio.timeout(5):
        while not runtime._local_processes:
            await asyncio.sleep(0.01)
    receipt = await runtime.cancel_inspector("task1", session_id="session1")
    await asyncio.gather(task, return_exceptions=True)
    assert receipt["stopped"] is True
    assert not runtime._local_processes


@pytest.mark.asyncio
async def test_local_bundle_replay_keeps_result_and_evidence_contract(inspector_dir):
    from tap_platform.browser_automation.contracts import Evidence

    runtime = local_runtime(inspector_dir)

    class Sink:
        def persist(
            self, project_id, task_id, execution_id, kind, data, max_bytes, **kwargs
        ):
            return Evidence(
                kind,
                f"objects/{execution_id}/{kind}",
                hashlib.sha256(data).hexdigest(),
                len(data),
            )

    runtime.evidence_sink = Sink()
    script = b"test('example', () => {});"
    bundle = BrowserBundle(
        "b1",
        "p1",
        hashlib.sha256(script).hexdigest(),
        script,
        hashlib.sha256(b"{}").hexdigest(),
        ("https://example.com",),
        "2026-11-09",
    )
    result = await runtime.execute(
        bundle, {"task_id": "t1", "input_data": {}}, BrowserLimits(), execution_id="e1"
    )
    assert result.status == "passed"
    assert result.result["outputs"] == [{"quote": "10.62"}]
    assert {item.kind for item in result.evidence} == {"screenshot", "trace", "result"}
    assert result.failure_code is None
    assert not runtime._local_processes


def test_worker_local_selection_does_not_require_image(monkeypatch):
    from tap_platform.browser_automation.worker import runtime_from_environment
    from tap_platform.browser_automation.local import LocalBrowserRuntime

    monkeypatch.setenv("TAP_BROWSER_EXECUTION_MODE", "local")
    monkeypatch.delenv("TAP_BROWSER_IMAGE", raising=False)
    runtime = runtime_from_environment(None, "project1")
    assert isinstance(runtime, LocalBrowserRuntime)
    monkeypatch.setenv("TAP_BROWSER_EXECUTION_MODE", "locla")
    with pytest.raises(ValueError, match="execution mode"):
        runtime_from_environment(None, "project1")


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_by_deadline", [False, True])
async def test_local_replay_cancel_and_deadline_stop_owned_children(
    inspector_dir, stop_by_deadline
):
    from tap_platform.browser_automation.contracts import Evidence

    class Sink:
        def persist(
            self, project_id, task_id, execution_id, kind, data, max_bytes, **kwargs
        ):
            return Evidence(kind, "unused", hashlib.sha256(data).hexdigest(), len(data))

    (inspector_dir / "runner.mjs").write_text("""
import {spawn} from 'node:child_process';
spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {stdio:'inherit'});
setInterval(() => {}, 1000);
""")
    runtime = local_runtime(inspector_dir)
    runtime.evidence_sink = Sink()
    script = b"test('example', () => {});"
    bundle = BrowserBundle(
        "b1",
        "p1",
        hashlib.sha256(script).hexdigest(),
        script,
        hashlib.sha256(b"{}").hexdigest(),
        ("http://example.test",),
        "2026-11-09",
    )
    executing = asyncio.create_task(
        runtime.execute(
            bundle,
            {"task_id": "t1", "input_data": {}},
            BrowserLimits(deadline_seconds=1 if stop_by_deadline else 30),
            execution_id="e1",
        )
    )
    async with asyncio.timeout(10):
        while not any(
            len(children) >= 2 for children in runtime._local_processes.values()
        ):
            await asyncio.sleep(0.01)
        roots = list(runtime._local_roots.values())
        if not stop_by_deadline:
            with pytest.raises(ValueError, match="cross-task"):
                await runtime.cancel_execution("e1", "other")
            cancellation = await runtime.cancel_execution("e1", "t1")
            assert cancellation["status"] == "canceled"
            assert cancellation["stopped"] is True
        receipt = await executing
    assert receipt.failure_code == ("timeout" if stop_by_deadline else "canceled")
    assert not runtime._local_processes
    assert all(not root.exists() for root in roots)


@pytest.mark.parametrize(
    "value",
    [
        "http://localhost:7897",
        "http://10.0.0.1:7897",
        "https://127.0.0.1:7897",
        "http://user:secret@127.0.0.1:7897",
        "http://127.0.0.1:7897/path",
        "http://127.0.0.1:7897/?query=1",
        "http://127.0.0.1:7897/#fragment",
        "http://127.0.0.1",
        "http://127.1:7897",
        "http://127.0.0.1:65536",
        "",
    ],
)
def test_local_upstream_configuration_is_explicit_loopback_only(value):
    from tap_platform.browser_automation.local import LocalBrowserRuntime

    with pytest.raises(ValueError, match="upstream proxy"):
        LocalBrowserRuntime(upstream_proxy=value)


def test_local_upstream_and_test_target_cannot_mix(monkeypatch):
    from tap_platform.browser_automation.local import LocalBrowserRuntime
    from tap_platform.browser_automation.worker import runtime_from_environment

    with pytest.raises(ValueError, match="synthetic"):
        LocalBrowserRuntime(
            upstream_proxy="http://127.0.0.1:7897", test_target="http://127.0.0.1:9999"
        )
    monkeypatch.setenv("TAP_BROWSER_EXECUTION_MODE", "local")
    monkeypatch.setenv("TAP_BROWSER_LOCAL_UPSTREAM_PROXY", "http://127.0.0.1:7897")
    assert (
        runtime_from_environment(None, "p1").upstream_proxy == "http://127.0.0.1:7897"
    )


@pytest.mark.asyncio
async def test_local_upstream_only_reaches_owned_policy_proxy(
    inspector_dir, monkeypatch
):
    import json

    from tap_platform.browser_automation.local import LocalBrowserRuntime

    monkeypatch.setenv("HTTP_PROXY", "http://user:secret@127.0.0.1:7897")
    monkeypatch.setenv("NODE_TLS_REJECT_UNAUTHORIZED", "0")
    monkeypatch.setenv("TAP_BROWSER_LOCAL_UPSTREAM_PROXY", "http://127.0.0.1:7897")
    runtime = LocalBrowserRuntime(
        browser_dir=inspector_dir, upstream_proxy="http://127.0.0.1:7897"
    )
    launched = {}
    spawn = runtime._spawn

    async def capture(attempt, name, env, *args, **kwargs):
        launched[name] = env.copy()
        return await spawn(attempt, name, env, *args, **kwargs)

    monkeypatch.setattr(runtime, "_spawn", capture)
    handle = await runtime.start_inspector("upstream1", ("https://example.com",), 10)
    try:
        policy = json.loads(launched["site-proxy.mjs"]["TAP_BROWSER_PROXY_POLICY"])
        assert policy["upstream_proxy"] == "http://127.0.0.1:7897"
        assert policy["public_web"] is False
        assert "TAP_BROWSER_PROXY_POLICY" not in launched["inspector.mjs"]
        assert (
            launched["inspector.mjs"]["TAP_BROWSER_PROXY"] != policy["upstream_proxy"]
        )
        for env in launched.values():
            assert "TAP_BROWSER_LOCAL_UPSTREAM_PROXY" not in env
            assert "HTTP_PROXY" not in env
            assert "NODE_TLS_REJECT_UNAUTHORIZED" not in env
    finally:
        await runtime.cancel(handle)
