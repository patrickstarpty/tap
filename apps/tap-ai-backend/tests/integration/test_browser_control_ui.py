"""Opt-in real UI → workflow gateway → ledger/worker → owned Chromium journey.

Only task authorization is a synthetic fixture. Browser control, frames, actions,
HTTP routes, capability checks and the rendered UI use the production code.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine

from tap.interfaces.http.browser_control_gateway import BrowserControlGateway
from tap.interfaces.http.middleware.origin import OriginPolicyMiddleware
from tap.interfaces.http.routes import workflows
from tap.modules.workflows.adapters.browser_client import BrowserApiClient

pytestmark = pytest.mark.skipif(
    os.environ.get("TAP_RUN_BROWSER_CONTROL_UI") != "1",
    reason="explicit owned real Chromium UI/gateway journey",
)
ROOT = Path(__file__).resolve().parents[4]


def port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.mark.asyncio
async def test_real_ui_takeover_fill_single_submit_and_handback(tmp_path, monkeypatch):
    from tap_platform.access import AccessPrincipal
    from tap_platform.browser_automation.http import create_browser_router
    from tap_platform.browser_automation.local import LocalBrowserRuntime
    from tap_platform.browser_automation.objects import BrowserEvidenceSink, BrowserObjectStore
    from tap_platform.browser_automation.service import BrowserService
    from tap_platform.browser_automation.store import BrowserStore, metadata
    from tap_platform.browser_automation.worker import BrowserWorker

    class Fixture(BaseHTTPRequestHandler):
        def do_GET(self):
            content = b"""<title>Owned control fixture</title><h1>Quote</h1>
            <label style="position:absolute;left:80px;top:75px">Age</label>
            <input id="age" aria-label="Age" value="25"
              style="position:absolute;left:80px;top:100px;width:200px;height:40px">
            <button style="position:absolute;left:80px;top:170px;width:200px;height:40px"
              onclick="const output=document.querySelector('#submit-count');
              output.textContent=String(Number(output.textContent)+1)">Calculate</button>
            <output id="submit-count" style="position:absolute;top:250px">0</output>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
            pass

    fixture = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    thread.start()
    engine = create_engine(f"sqlite:///{tmp_path / 'ui.db'}")
    metadata.create_all(engine)
    store = BrowserStore(engine)
    objects = BrowserObjectStore(tmp_path / "objects")
    service = BrowserService(store, objects)
    principal = AccessPrincipal(
        project_id="p",
        principal_id="agent",
        principal_type="service",
        audience="tap-browser",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        enabled=True,
        actions=frozenset(
            {
                "browser.sessions.create",
                "browser.sessions.read",
                "browser.sessions.cancel",
                "browser.sessions.control",
                "browser.actions.create",
                "browser.actions.read",
            }
        ),
    )
    runtime = LocalBrowserRuntime(
        evidence_sink=BrowserEvidenceSink(store, objects),
        project_id="p",
        test_target=f"http://127.0.0.1:{fixture.server_port}",
    )
    worker = BrowserWorker(service, runtime, "p", "ui-worker")
    app = FastAPI()
    api_port, vite_port = port(), port()
    origin = f"http://127.0.0.1:{api_port}"
    vite_origin = f"http://127.0.0.1:{vite_port}"

    class GatewayOriginPolicy(OriginPolicyMiddleware):
        async def __call__(self, scope, receive, send):
            # The standalone core service uses bearer auth, while the browser-facing
            # gateway also requires the production same-origin/CSRF policy.
            if scope.get("path", "").startswith("/api/v1/"):
                return await super().__call__(scope, receive, send)
            return await self.app(scope, receive, send)

    app.add_middleware(GatewayOriginPolicy, allowed_origins=(origin,))
    app.include_router(create_browser_router(service, principal, "a" * 32, gateway_token="g" * 32))
    client = BrowserApiClient(origin, "p", "a" * 32, gateway_token="g" * 32)

    class TaskQuery:
        async def snapshot(self, scope, task_id):
            assert scope.project_id == "p" and scope.actor_id == "fixture-owner"
            if task_id != "ui-journey":
                raise LookupError("not authorized")
            return {"status": "running"}

    gateway = BrowserControlGateway(TaskQuery(), client)
    monkeypatch.setattr(workflows, "browser_control_gateway", lambda _request: gateway)

    @app.middleware("http")
    async def scope(request: Request, call_next):
        request.state.correlation_id = "synthetic-ui-journey"
        request.state.project_scope = SimpleNamespace(project_id="p", actor_id="fixture-owner")
        return await call_next(request)

    prefix = "/api/v1/projects/p/workflows/{task_id}/browser-control/{session_id}"
    for suffix, method, handler in (
        ("panel", "POST", workflows.browser_panel),
        ("state", "GET", workflows.browser_control_state),
        ("commands", "POST", workflows.browser_control_command),
        ("frame", "GET", workflows.browser_live_frame),
        ("actions", "POST", workflows.browser_human_action),
    ):
        app.add_api_route(prefix + "/" + suffix, handler, methods=[method])

    @app.get("/ui", response_class=HTMLResponse)
    async def ui():
        return f"""<main style="max-width:900px;margin:20px auto;padding:16px" id="root"></main>
        <script type="module">
        import R from '{vite_origin}/@react-refresh';R.injectIntoGlobalHook(window);
        window.$RefreshReg$=()=>{{}};window.$RefreshSig$=()=>(t)=>t;
        window.__vite_plugin_react_preamble_installed__=true;
        await import('{vite_origin}/src/app/styles.css');
        await import('{vite_origin}/src/features/workflows/components/workflow.css');
        const React=(await import('{vite_origin}/node_modules/.vite/deps/react.js')).default;
        const modules='{vite_origin}/node_modules/.vite/deps/';
        const components='{vite_origin}/src/features/workflows/components/';
        const {{createRoot}}=(await import(modules+'react-dom_client.js')).default;
        const {{BrowserControl}}=await import(components+'BrowserControl.tsx');
        const {{createBrowserControlTransport}}=
          await import(components+'browserControlTransport.ts');
        const transport=createBrowserControlTransport('p','ui-journey','{app.state.sid}');
        createRoot(document.getElementById('root')).render(React.createElement(BrowserControl,{{transport,pollIntervalMs:200}}));
        </script>"""

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="error"))
    serving = asyncio.create_task(server.serve())
    stop = asyncio.Event()

    async def pump():
        while not stop.is_set():
            await worker.tick()
            await asyncio.sleep(0.03)

    pumping = asyncio.create_task(pump())
    vite = None
    process = None
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.02)
        session = await client.session(
            "ui-journey",
            ["http://example.test"],
            120,
            "ui-session",
            control_version="browser-control-v1",
        )
        sid = app.state.sid = session["session_id"]
        navigation = await client.action(
            sid,
            {"type": "navigate", "url": "http://example.test/quote", "expected_epoch": 0},
            "navigate",
        )
        assert navigation["status"] == "completed"
        vite = await asyncio.create_subprocess_exec(
            shutil.which("corepack"),
            "pnpm",
            "exec",
            "vite",
            "--host",
            "127.0.0.1",
            "--port",
            str(vite_port),
            "--strictPort",
            cwd=ROOT / "apps/tap-ai-frontend",
            start_new_session=True,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        async with asyncio.timeout(10):
            while True:
                try:
                    reader, writer = await asyncio.open_connection("127.0.0.1", vite_port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.05)
        script = r"""
        import {chromium} from '@playwright/test';
        const browser=await chromium.launch({headless:true});
        try {
          const page=await browser.newPage({viewport:{width:1100,height:900}});
          const errors=[];page.on('pageerror',error=>errors.push(String(error)));
          await page.goto(process.env.UI_ORIGIN+'/ui');
          await page.getByRole('button',{name:'Request control'}).click({timeout:15000});
          await page.getByText('You have browser control').waitFor();
          await page.getByRole('img',{name:'Browser page, last received frame'}).waitFor();
          await page.screenshot({path:process.env.UI_SCREENSHOT+'.human.png',fullPage:true});
          await page.setViewportSize({width:390,height:844});
          await page.screenshot({path:process.env.UI_SCREENSHOT+'.mobile.png',fullPage:true});
          if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))
            throw new Error('mobile horizontal overflow');
          await page.setViewportSize({width:1100,height:900});
          const fill=page.getByRole('button',{name:'Fill point'});
          await page.getByLabel('Page X').fill('100');
          await page.getByLabel('Page Y').fill('120');
          await page.getByLabel('Text to fill').fill('35');
          await fill.click();
          await page.getByText('Action confirmed.').waitFor();
          await page.getByRole('button',{name:'Scroll down'}).click();
          await page.getByText('Action confirmed.').waitFor();
          const image=page.getByRole('img',{name:'Browser page, last received frame'});
          await image.evaluate(image=>image.decode());
          const size=await image.evaluate(image=>({w:image.naturalWidth,h:image.naturalHeight}));
          const box=await image.boundingBox();
          await page.getByRole('button',{name:'Click browser page'}).click({
            position:{x:100/size.w*box.width,y:190/size.h*box.height}
          });
          await page.getByText('Action confirmed.').waitFor();
          await page.getByRole('button',{name:'Hand back to agent'}).click();
          await page.getByText('Agent has browser control').waitFor();
          await page.screenshot({path:process.env.UI_SCREENSHOT,fullPage:true});
          if(errors.length) throw new Error(errors.join('\n'));
          console.log('real UI handback complete');
        } finally {await browser.close();}
        """
        environment = {
            key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ
        }
        process = await asyncio.create_subprocess_exec(
            shutil.which("node"),
            "--input-type=module",
            "-e",
            script,
            cwd=ROOT / "apps/tap-ai-frontend",
            start_new_session=True,
            env={
                **environment,
                "UI_ORIGIN": origin,
                "UI_SCREENSHOT": str(tmp_path / "control-ui.png"),
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, errors = await asyncio.wait_for(process.communicate(), 60)
        assert process.returncode == 0, errors.decode() + output.decode()
        control = await client.get_control(sid)
        assert control["control_state"] == "agent_active" and control["control_epoch"] == 2
        for selector, attribute, expected in (
            ("#age", "value", "35"),
            ("#submit-count", "text", "1"),
        ):
            result = await client.action(
                sid,
                {
                    "type": "read",
                    "selector": selector,
                    "name": "result",
                    "attribute": attribute,
                    "expected_epoch": 2,
                },
                "read-" + selector[1:],
            )
            assert result["observation"]["extracted"]["value"] == expected
        print(
            json.dumps(
                {
                    "screenshot": str(tmp_path / "control-ui.png"),
                    "submit_count": 1,
                    "human_value": "35",
                    "control_epoch": 2,
                }
            )
        )
    finally:
        stop.set()
        await pumping
        await worker.shutdown()
        server.should_exit = True
        await serving
        for child in (process, vite):
            if child is not None and child.returncode is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(child.wait(), 3)
                except TimeoutError:
                    os.killpg(child.pid, signal.SIGKILL)
                    await child.wait()
        fixture.shutdown()
        fixture.server_close()
        thread.join()
        engine.dispose()
