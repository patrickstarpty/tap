"""Opt-in, zero-provider-cost OpenCode/native-tool/HTTP/Chromium protocol proof.

Runs only owned loopback fixtures, with SQLite persistence and no Docker services.
The scripted model is a protocol fixture, never a claim of model autonomy.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import shutil
import socket
import tempfile
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import uvicorn
from fastapi import FastAPI
from sqlalchemy import create_engine
from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.http import create_browser_router
from tap_platform.browser_automation.local import LocalBrowserRuntime
from tap_platform.browser_automation.objects import (
    BrowserEvidenceSink,
    BrowserObjectStore,
)
from tap_platform.browser_automation.service import BrowserService
from tap_platform.browser_automation.store import BrowserStore, metadata
from tap_platform.browser_automation.worker import BrowserWorker

from tap.modules.workflows.adapters.browser_client import BrowserApiClient
from tap.modules.workflows.adapters.coding_mailbox import CodingMailbox
from tap.modules.workflows.adapters.local_opencode import LocalCodingContainer
from tap.modules.workflows.adapters.opencode_profiles import build_config
from tap.modules.workflows.adapters.opencode_runtime import CodingConfig
from tap.modules.workflows.application.browser_steps import BrowserProgress

ROOT = Path(__file__).resolve().parents[1]
PLAN = {
    "type": "plan",
    "steps": [
        {"id": "open", "keyword": "Given", "title": "Open owned fixture"},
        {"id": "click", "keyword": "When", "title": "Click owned button"},
        {"id": "result", "keyword": "Then", "title": "Read changed result"},
    ],
}
ACTIONS = [
    PLAN,
    {"type": "navigate", "step": "open", "url": "http://example.test/"},
    {"type": "click", "step": "click", "selector": "#go"},
    {
        "type": "read",
        "step": "result",
        "selector": "#result",
        "name": "result",
        "equals": "CLICKED",
    },
]
REJECTED_PLAN = {"type": "plan", "steps": [PLAN["steps"][0], PLAN["steps"][0]]}
SCENARIOS = {
    "normal": ACTIONS,
    "plain_text": [],
    "schema_error": [{"type": "click", "step": "click"}],
    "server_rejected": [REJECTED_PLAN],
    "corrected": [REJECTED_PLAN, *ACTIONS],
}


class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class Site(QuietHandler):
    def do_GET(self):
        body = b"""<title>Owned protocol fixture</title><button id="go" onclick="document.querySelector('#result').textContent='CLICKED'">Continue</button><p id="result">READY</p>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)


def http_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def scenario(name, root, browser, worker, binary):
    state = {
        "provider_requests": 0,
        "text_responses": 0,
        "tool_responses": 0,
        "json_arguments": 0,
        "json_text_responses": 0,
        "accepted_plans": 0,
        "completed_actions": 0,
        "server_rejections": 0,
        "dom_clicked": False,
    }
    script = SCENARIOS[name]

    class Model(QuietHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["provider_requests"] += 1
            if state["provider_requests"] > 10:
                self.send_response(429)
                self.end_headers()
                self.wfile.write(
                    b'{"error":{"type":"fixture_limit","retryable":false}}'
                )
                return
            index = state["tool_responses"]
            tool = None
            if request.get("tools") and index < len(script):
                state["tool_responses"] += 1
                state["json_arguments"] += 1
                tool = {
                    "index": 0,
                    "id": f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": "tap_browser",
                        "arguments": json.dumps({"action": script[index]}),
                    },
                }
            else:
                state["text_responses"] += 1
                if name == "plain_text":
                    state["json_text_responses"] += 1
            content = (
                json.dumps({"action": PLAN})
                if name == "plain_text"
                else "Fixture complete."
            )
            frame = {
                "id": "chatcmpl-owned",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "qwen-plus",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            **(
                                {"tool_calls": [tool]} if tool else {"content": content}
                            ),
                        },
                        "finish_reason": "tool_calls" if tool else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            }
            body = ("data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    model = http_server(Model)
    (root / name).mkdir()
    mailbox = CodingMailbox.create(root / name / "mailbox")
    container = LocalCodingContainer(
        CodingConfig.for_test(),
        mailbox.root,
        opencode_binary=binary,
        node_binary=Path(shutil.which("node")),
        client_path=ROOT / "deploy/browser/opencode-client.mjs",
    )
    session = await browser.session(
        name, ["http://example.test"], 90, f"session-{name}"
    )
    assert session["status"] == "active"

    async def emit(kind, _payload):
        if kind == "browser.plan_created":
            state["accepted_plans"] += 1

    progress = BrowserProgress(emit)

    async def execute(action, key):
        result = await browser.action(
            session["session_id"], action, key, deadline_seconds=20
        )
        if result.get("status") == "completed":
            state["completed_actions"] += 1
            if action["type"] == "read":
                state["dom_clicked"] = (
                    result.get("observation", {}).get("extracted", {}).get("value")
                    == "CLICKED"
                )
        return result

    async def handle(item):
        if item.kind == "model":

            def forward():
                req = Request(
                    f"http://127.0.0.1:{model.server_port}/v1/chat/completions",
                    data=base64.b64decode(item.payload["bodyBase64"]),
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(req, timeout=10) as response:
                    return {
                        "status": 200,
                        "contentType": "text/event-stream",
                        "bodyBase64": base64.b64encode(response.read()).decode(),
                    }

            return await asyncio.to_thread(forward)
        result = await progress.handle(
            item.payload,
            item.identifier,
            lambda action: execute(action, item.identifier),
        )
        if result.get("error"):
            state["server_rejections"] += 1
        return result

    request = {
        "model_alias": "qwen-plus",
        "root": {
            "agent_id": "orchestrator",
            "revision_id": "test",
            "description": "Owned protocol fixture",
            "instruction": "Use the native browser tool for the owned fixture.",
        },
    }
    config = build_config(
        request,
        "http://127.0.0.1:39001",
        "owned-synthetic-token",
        input_tokens=30720,
        output_tokens=2048,
    )
    running = None
    try:
        await container.start(config, "owned-synthetic-token", 90)
        await container.set_frozen_input(b"{}", hashlib.sha256(b"{}").hexdigest())
        await container.set_goal(
            "Execute the owned protocol fixture only. No external sites or purchases."
        )
        running = asyncio.create_task(container.run_prompt())
        async with asyncio.timeout(75):
            while not running.done():
                if not await mailbox.serve_once(handle):
                    await asyncio.sleep(0.01)
            terminal = await running
        state["terminal"] = terminal.get("terminal")
        state["diagnostics"] = terminal.get("diagnostics", [])
        state["unknown"] = progress.unknown_action
        (root / name / "safe-summary.json").write_text(json.dumps(state, indent=2))
        assert state["provider_requests"] <= 10, state
        assert state["json_arguments"] == state["tool_responses"], state
        assert state["tool_responses"] == len(script), state
        if name in {"normal", "corrected"}:
            assert state["accepted_plans"] == 1, state
            assert state["dom_clicked"] and state["completed_actions"] == 3, state
        if name == "plain_text":
            assert (
                state["tool_responses"]
                == state["completed_actions"]
                == state["accepted_plans"]
                == 0
            ), state
            assert state["json_text_responses"] > 0, state
        if name == "schema_error":
            assert state["completed_actions"] == state["accepted_plans"] == 0, state
            assert any(
                d["category"] == "validation_error" for d in state["diagnostics"]
            ), state
        if name == "server_rejected":
            assert state["accepted_plans"] == state["completed_actions"] == 0, state
        if name in {"server_rejected", "corrected"}:
            assert state["server_rejections"] == 1, state
            assert any(
                d["category"] == "server_rejected" for d in state["diagnostics"]
            ), state
        return state
    finally:
        if running and not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        try:
            assert await container.stop()
        finally:
            try:
                await browser.cancel_session(session["session_id"])
            finally:
                model.shutdown()
                model.server_close()


async def main(args):
    binary = args.opencode_binary.resolve(strict=True)
    version = await asyncio.create_subprocess_exec(
        str(binary), "--version", stdout=asyncio.subprocess.PIPE
    )
    output, _ = await version.communicate()
    assert output.decode().strip() == "1.19.5"
    root = Path(tempfile.mkdtemp(prefix="tap-opencode-protocol-"))
    engine = create_engine(f"sqlite:///{root / 'browser.db'}")
    metadata.create_all(engine)
    store = BrowserStore(engine)
    objects = BrowserObjectStore(root / "objects")
    service = BrowserService(store, objects)
    principal = AccessPrincipal(
        project_id="p",
        principal_id="owned",
        principal_type="service",
        audience="tap-browser",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        enabled=True,
        actions=frozenset(
            {
                "browser.sessions.create",
                "browser.sessions.read",
                "browser.sessions.cancel",
                "browser.actions.create",
                "browser.actions.read",
            }
        ),
    )
    app = FastAPI()
    app.include_router(create_browser_router(service, principal, "a" * 32))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        await asyncio.sleep(0.01)
    site = http_server(Site)
    runtime = LocalBrowserRuntime(
        evidence_sink=BrowserEvidenceSink(store, objects),
        project_id="p",
        test_target=f"http://127.0.0.1:{site.server_port}",
    )
    worker = BrowserWorker(service, runtime, "p", "owned-protocol-worker")
    stop = asyncio.Event()

    async def pump():
        while not stop.is_set():
            await worker.tick()
            await asyncio.sleep(0.02)

    pumping = asyncio.create_task(pump())
    report = {
        "opencode_version": "1.19.5",
        "provider": "owned_synthetic",
        "paid_calls": 0,
        "browser_chain": "HTTP/SQLite/BrowserWorker/LocalBrowserRuntime/Chromium",
        "model_forwarding": "test handler to owned HTTP synthetic provider; not CodingRuntime/BudgetGate",
        "financial_guard_tested": False,
        "cases": {},
    }
    try:
        client = BrowserApiClient(f"http://127.0.0.1:{port}", "p", "a" * 32)
        for name in args.cases:
            report["cases"][name] = await scenario(name, root, client, worker, binary)
            args.evidence.write_text(json.dumps(report, indent=2))
            print(json.dumps({"case": name, **report["cases"][name]}), flush=True)
    finally:
        stop.set()
        await pumping
        await worker.shutdown()
        server.should_exit = True
        await serving
        site.shutdown()
        site.server_close()
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencode-binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--cases", nargs="+", choices=SCENARIOS, default=list(SCENARIOS)
    )
    asyncio.run(main(parser.parse_args()))
