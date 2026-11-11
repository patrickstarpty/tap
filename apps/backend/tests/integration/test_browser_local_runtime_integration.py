"""Explicit zero-model Mac browser probe, isolated from the default test gate."""

import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tap_platform.browser_automation.contracts import (
    BrowserBundle,
    BrowserLimits,
    Evidence,
)
from tap_platform.browser_automation.local import LocalBrowserRuntime

pytestmark = pytest.mark.skipif(
    os.environ.get("TAP_RUN_BROWSER_LOCAL_INTEGRATION") != "1",
    reason="requires installed local Node/Playwright/Chromium",
)


class Sink:
    def __init__(self):
        self.items = {}

    def persist(
        self, project_id, task_id, execution_id, kind, data, max_bytes, **kwargs
    ):
        key = f"objects/{task_id}/{execution_id}/{kind}/{len(self.items)}"
        self.items[key] = data
        return Evidence(kind, key, hashlib.sha256(data).hexdigest(), len(data))


@pytest.fixture
def fixture_site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] == "/quote":
                self.server.document_visits += 1
            content = b"""<title>Local quote fixture</title><h1>Quote</h1>
            <label>Age<input id="age" value="25"></label>
            <label>Plan<select id="plan" onchange="document.querySelector('#selection-count').textContent=String(Number(document.querySelector('#selection-count').textContent)+1)"><option value="basic">Basic</option><option value="premium">Premium</option></select></label>
            <output id="selection-count">0</output><output id="navigation-count">NAVIGATION_COUNT</output>
            <button onclick="document.querySelector('#quote').textContent='HK$10.62/month';document.querySelector('#submit-count').textContent=String(Number(document.querySelector('#submit-count').textContent)+1)">Calculate</button>
            <output id="quote"></output><output id="submit-count">0</output>"""
            content = content.replace(
                b"NAVIGATION_COUNT", str(self.server.document_visits).encode()
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.document_visits = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
async def test_real_local_inspector_and_independent_replay_preserve_dom_evidence(
    fixture_site,
):
    sink = Sink()
    runtime = LocalBrowserRuntime(
        evidence_sink=sink, project_id="p1", test_target=fixture_site
    )
    handle = await runtime.start_inspector(
        "task1", ("http://example.test",), 60, session_id="s1"
    )
    try:
        for action in (
            {"type": "navigate", "url": "http://example.test/quote"},
            {"type": "fill", "selector": "#age", "value": "30"},
            {"type": "click", "selector": "button"},
            {
                "type": "read",
                "selector": "#quote",
                "name": "premium",
                "attribute": "text",
            },
        ):
            observation = await runtime.observe(handle, action)
            assert observation.error is None
        assert observation.extracted["value"] == "HK$10.62/month"
        finished = await runtime.observe(handle, {"type": "finish"})
        assert finished.error is None
        assert {item["kind"] for item in finished.evidence} == {
            "screenshot",
            "trace",
            "video",
            "result",
        }
        assert finished.result["outputs"][0]["premium"] == "HK$10.62/month"
    finally:
        assert (await runtime.cancel(handle))["stopped"] is True

    script = b"""import {test, expect} from '@playwright/test';
import fs from 'node:fs';
test('independent quote', async ({page}, testInfo) => {
 const inputs = JSON.parse(fs.readFileSync(process.env.TAP_BROWSER_INPUTS_PATH, 'utf8'));
 await page.goto('http://example.test/quote');
 await page.locator('#age').fill(inputs.age);
 await page.locator('button').click();
 await expect(page.locator('#quote')).toHaveText('HK$10.62/month');
 await testInfo.attach('tapper-result', {contentType:'application/json',
   body:Buffer.from(JSON.stringify({premium:await page.locator('#quote').innerText()}))});
});"""
    inputs = {"age": "30"}
    bundle = BrowserBundle(
        "b1",
        "p1",
        hashlib.sha256(script).hexdigest(),
        script,
        hashlib.sha256(
            json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        ("http://example.test",),
        "2026-11-09",
    )
    receipt = await runtime.execute(
        bundle,
        {"task_id": "task1", "input_data": inputs},
        BrowserLimits(deadline_seconds=60),
        execution_id="e1",
    )
    assert receipt.status == "passed", receipt
    assert receipt.result["outputs"] == [{"premium": "HK$10.62/month"}]
    assert {item.kind for item in receipt.evidence} == {
        "screenshot",
        "trace",
        "video",
        "result",
    }
    assert not runtime._local_processes


@pytest.mark.asyncio
async def test_cancel_stops_detached_hung_chromium_and_removes_private_profile():
    import asyncio
    import signal
    import subprocess
    from pathlib import Path

    runtime = LocalBrowserRuntime(evidence_sink=Sink(), project_id="p1")
    script = b"""import {test} from '@playwright/test'; import fs from 'node:fs'; import os from 'node:os';
    test('waiting browser', async({page})=>{await page.goto('about:blank');
      fs.writeFileSync(process.env.TAP_BROWSER_LOCAL_DIR+'/ready',os.tmpdir());
      await page.waitForTimeout(120000);});"""
    bundle = BrowserBundle(
        "b1",
        "p1",
        hashlib.sha256(script).hexdigest(),
        script,
        hashlib.sha256(b"{}").hexdigest(),
        ("http://example.test",),
        "2026-11-09",
    )
    task = asyncio.create_task(
        runtime.execute(
            bundle,
            {"task_id": "t1", "input_data": {}},
            BrowserLimits(deadline_seconds=60),
            execution_id="e1",
        )
    )
    browser_pid = None
    try:
        async with asyncio.timeout(20):
            while not any(
                (root / "ready").exists() for root in runtime._local_roots.values()
            ):
                assert not task.done(), await task if task.done() else None
                await asyncio.sleep(0.05)
        root = next(iter(runtime._local_roots.values()))
        temporary = Path((root / "ready").read_text())
        rows = [
            line.split(None, 3)
            for line in subprocess.check_output(
                ["ps", "-axo", "pid=,ppid=,pgid=,command="], text=True
            ).splitlines()
        ]
        owned = {
            process.pid
            for processes in runtime._local_processes.values()
            for process in processes
        }
        for _ in range(len(rows)):
            children = {int(row[0]) for row in rows if int(row[1]) in owned}
            if children.issubset(owned):
                break
            owned.update(children)
        browser_pid = next(
            int(row[0])
            for row in rows
            if int(row[0]) in owned and "--user-data-dir=" in row[3]
        )
        os.kill(browser_pid, signal.SIGSTOP)
        receipt = await runtime.cancel_execution("e1", "t1")
        await task
        assert receipt["stopped"] is True
        await asyncio.sleep(0.2)
        with pytest.raises(ProcessLookupError):
            os.kill(browser_pid, 0)
        assert temporary.resolve() == root.resolve()
        assert not root.exists()
    finally:
        if browser_pid is not None:
            try:
                os.killpg(browser_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if not task.done():
            await runtime.cancel_execution("e1", "t1")
            await task
