"""Real Chromium through HTTP, durable ledger and worker; no model or Docker."""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from test_browser_local_runtime_integration import fixture_site  # noqa: F401
from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.http import create_browser_router
from tap_platform.browser_automation.local import LocalBrowserRuntime
from tap_platform.browser_automation.objects import (
    BrowserEvidenceSink,
    BrowserObjectStore,
)
from tap_platform.browser_automation.service import BrowserService
from tap_platform.browser_automation.store import BrowserStore, metadata, actions
from tap_platform.browser_automation.worker import BrowserWorker

pytestmark = pytest.mark.skipif(
    os.environ.get("TAP_RUN_BROWSER_LOCAL_INTEGRATION") != "1",
    reason="explicit owned local Chromium HTTP/worker journey",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_inputs", [False, True])
async def test_http_worker_takeover_human_preflight_handback_and_single_submit(
    tmp_path,
    fixture_site,  # noqa: F811
    remote_inputs,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'journey.db'}")
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
    app = FastAPI()
    app.include_router(
        create_browser_router(
            service, principal, "a" * 32, gateway_token="g" * 32, worker_token="w" * 32
        )
    )
    runtime = LocalBrowserRuntime(
        evidence_sink=BrowserEvidenceSink(store, objects),
        project_id="p",
        test_target=fixture_site,
    )
    worker_service = service
    input_fetcher = None
    server = thread = listen = None
    if remote_inputs:
        import socket
        import threading
        import uvicorn
        from tap_platform.browser_automation.input_transport import WorkerInputClient

        listen = socket.socket()
        listen.bind(("127.0.0.1", 0))
        listen.listen()
        port = listen.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="error", access_log=False)
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listen]}, daemon=True
        )
        thread.start()
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        # Separate service instance models the production worker's empty memory vault.
        worker_service = BrowserService(BrowserStore(engine), objects)
        input_fetcher = WorkerInputClient(f"http://127.0.0.1:{port}", "w" * 32)
        assert worker_service.inputs is not service.inputs
    worker = BrowserWorker(
        worker_service, runtime, "p", "worker", input_fetcher=input_fetcher
    )
    client = TestClient(app)
    agent = {"Authorization": "Bearer " + "a" * 32}
    human = {
        "Authorization": "Bearer " + "g" * 32,
        "X-Tap-Controller": "c" * 32,
        "X-Tap-Controller-Expires": str(
            (datetime.now(UTC) + timedelta(seconds=120)).timestamp()
        ),
    }
    stop = asyncio.Event()

    async def pump():
        while not stop.is_set():
            await worker.tick()
            await asyncio.sleep(0.03)

    task = asyncio.create_task(pump())

    async def request(method, path, *, body=None, key=None, human_actor=False):
        headers = dict(human if human_actor else agent)
        if key:
            headers["Idempotency-Key"] = key
        return await asyncio.to_thread(
            client.request, method, path, headers=headers, json=body
        )

    async def wait_for(path, field, value):
        async with asyncio.timeout(20):
            while True:
                response = await request("GET", path)
                assert response.status_code == 200, response.text
                result = response.json()
                if result.get(field) == value:
                    return result
                assert result.get(field) not in {
                    "blocked",
                    "closed",
                    "unknown",
                    "failed",
                    "rejected",
                }, result
                await asyncio.sleep(0.03)

    root = "/api/projects/p/browser"
    session_path = None
    try:
        created = await request(
            "POST",
            root + "/sessions",
            key="session",
            body={
                "task_id": "journey",
                "allowed_origins": ["http://example.test"],
                "deadline_seconds": 120,
                "control_version": "browser-control-v1",
            },
        )
        assert created.status_code == 202, created.text
        sid = created.json()["session_id"]
        session_path = f"{root}/sessions/{sid}"
        await wait_for(session_path, "status", "active")
        handle = worker._handles[sid]
        assert handle.cdp_url is None

        async def perform(body, key, *, human_actor=False):
            path = session_path + ("/human-actions" if human_actor else "/actions")
            response = await request(
                "POST", path, body=body, key=key, human_actor=human_actor
            )
            assert response.status_code == 202, response.text
            receipt = await wait_for(
                session_path + "/actions/" + response.json()["action_id"],
                "status",
                "completed",
            )
            assert not receipt["observation"].get("error"), receipt
            return receipt

        await perform(
            {
                "type": "navigate",
                "url": "http://example.test/quote",
                "expected_epoch": 0,
            },
            "navigate",
        )
        reserved = await request(
            "POST",
            session_path + "/model-reservations",
            body={"expected_epoch": 0, "request_id": "synthetic-model-reservation"},
        )
        assert reserved.json()["status"] == "reserved"
        takeover = await request(
            "POST",
            session_path + "/control",
            body={"operation": "takeover", "expected_epoch": 0},
            key="take",
            human_actor=True,
        )
        assert takeover.status_code == 202, takeover.text
        assert takeover.json()["control_state"] == "takeover_pending"
        await asyncio.sleep(0.08)
        assert (await request("GET", session_path + "/control")).json()[
            "control_state"
        ] == "takeover_pending"
        settled = await request(
            "POST",
            session_path + "/model-reservations/synthetic-model-reservation/settle",
            body={"outcome": "completed"},
        )
        assert settled.json()["status"] == "completed"
        control = await wait_for(
            session_path + "/control", "control_state", "human_active"
        )
        assert control["control_epoch"] == 1
        duplicate = await request(
            "POST",
            session_path + "/control",
            body={"operation": "takeover", "expected_epoch": 0},
            key="take",
            human_actor=True,
        )
        assert duplicate.json() == takeover.json()
        denied = await request(
            "POST",
            session_path + "/actions",
            body={"type": "click", "selector": "button", "expected_epoch": 0},
            key="stale",
        )
        assert denied.status_code == 409, denied.text

        async def get_frame(after=0):
            async with asyncio.timeout(10):
                while True:
                    response = await request("GET", session_path + "/frame")
                    if (
                        response.status_code == 200
                        and response.json()["frame_sequence"] > after
                    ):
                        return response.json()
                    assert response.status_code in {200, 409}, response.text
                    await asyncio.sleep(0.05)

        frame = await get_frame()
        navigation = {
            "type": "navigate",
            "url": "http://example.test/quote?human=1",
            "expected_epoch": 1,
            "frame_sequence": frame["frame_sequence"],
            "viewport": frame["viewport"],
        }
        navigated = await perform(navigation, "human-navigate", human_actor=True)
        assert navigated["target"] is None
        assert navigated["args"] == {"url": navigation["url"]}
        duplicate_navigation = await request(
            "POST",
            session_path + "/human-actions",
            body=navigation,
            key="human-navigate",
            human_actor=True,
        )
        assert duplicate_navigation.json()["action_id"] == navigated["action_id"]
        frame = await get_frame(frame["frame_sequence"])
        select_target = {
            "selector": "#plan",
            "expected_epoch": 1,
            "frame_sequence": frame["frame_sequence"],
            "viewport": frame["viewport"],
        }
        select_proof = await perform(
            {"type": "preflight", "operation": "select", **select_target},
            "select-proof",
            human_actor=True,
        )
        selection = {
            "type": "select",
            **select_target,
            "value": "Premium",
            "preflight_action_id": select_proof["action_id"],
        }
        selected = await perform(selection, "human-select", human_actor=True)
        assert selected["args"] == {"value": "premium"}
        duplicate_selection = await request(
            "POST",
            session_path + "/human-actions",
            body=selection,
            key="human-select",
            human_actor=True,
        )
        assert duplicate_selection.json()["action_id"] == selected["action_id"]
        frame = await get_frame(frame["frame_sequence"])
        assert frame["control_epoch"] == 1
        assert frame["image_base64"]
        target = {
            "selector": "#age",
            "frame_sequence": frame["frame_sequence"],
            "viewport": frame["viewport"],
            "expected_epoch": 1,
        }
        preflight = await perform(
            {"type": "preflight", "operation": "fill", **target},
            "preflight",
            human_actor=True,
        )
        filled = await perform(
            {
                "type": "fill",
                **target,
                "value": "35",
                "preflight_action_id": preflight["action_id"],
            },
            "human-fill",
            human_actor=True,
        )
        assert filled["actor_kind"] == "human"
        assert filled["target"]["kind"] in {"test_id", "role_name", "css"}
        assert filled["args"] == {"value": "35"}
        handback = await request(
            "POST",
            session_path + "/control",
            body={"operation": "handback", "expected_epoch": 1},
            key="back",
            human_actor=True,
        )
        assert handback.status_code == 202, handback.text
        resumed = await wait_for(
            session_path + "/control", "control_state", "agent_active"
        )
        assert resumed["control_epoch"] == 2
        assert worker._handles[sid] is handle
        stale_human = await request(
            "POST",
            session_path + "/human-actions",
            body={
                "type": "click",
                "selector": "button",
                "expected_epoch": 1,
                "frame_sequence": frame["frame_sequence"],
                "viewport": frame["viewport"],
            },
            key="late-human",
            human_actor=True,
        )
        assert stale_human.status_code == 409, stale_human.text
        click = {"type": "click", "selector": "button", "expected_epoch": 2}
        submitted = await perform(click, "submit")
        duplicate = await request(
            "POST", session_path + "/actions", body=click, key="submit"
        )
        assert duplicate.json()["action_id"] == submitted["action_id"]
        count = await perform(
            {
                "type": "read",
                "selector": "#submit-count",
                "name": "submit_count",
                "expected_epoch": 2,
            },
            "count",
        )
        assert count["observation"]["extracted"]["value"] == "1"
        selections = await perform(
            {
                "type": "read",
                "selector": "#selection-count",
                "name": "selection_count",
                "expected_epoch": 2,
            },
            "selection-count",
        )
        assert selections["observation"]["extracted"]["value"] == "1"
        navigations = await perform(
            {
                "type": "read",
                "selector": "#navigation-count",
                "name": "navigation_count",
                "expected_epoch": 2,
            },
            "navigation-count",
        )
        assert navigations["observation"]["extracted"]["value"] == "2"
        plan = await perform(
            {
                "type": "read",
                "selector": "#plan",
                "attribute": "value",
                "name": "plan",
                "expected_epoch": 2,
            },
            "plan-value",
        )
        assert plan["observation"]["extracted"]["value"] == "premium"

        age = await perform(
            {
                "type": "read",
                "selector": "#age",
                "name": "age",
                "attribute": "value",
                "expected_epoch": 2,
            },
            "age",
        )
        assert age["observation"]["extracted"]["value"] == "35"
        finished = await perform({"type": "finish", "expected_epoch": 2}, "finish")
        assert {x["kind"] for x in finished["evidence_refs"]} == {
            "screenshot",
            "trace",
            "result",
        }
        with engine.connect() as connection:
            ledger = repr(connection.execute(select(actions)).all())
        assert frame["image_base64"] not in ledger
        canceled = await request("POST", session_path + "/cancel")
        assert canceled.status_code == 202, canceled.text
        await wait_for(session_path, "status", "canceled")
        assert (await request("GET", session_path + "/frame")).status_code == 409
    finally:
        stop.set()
        await task
        await worker.shutdown()
        client.close()
        if server is not None:
            server.should_exit = True
            await asyncio.to_thread(thread.join, 5)
            assert not thread.is_alive()
            listen.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_real_dom_password_flip_keeps_input_out_of_ledger_and_artifacts(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    flipped = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/state":
                content = b"password" if flipped.is_set() else b"text"
            else:
                content = b"""<label>Name<input id="name"></label><script>
                setInterval(async()=>{document.querySelector('#name').type=await(await fetch('/state')).text()},20)
                </script>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    engine = create_engine(f"sqlite:///{tmp_path / 'flip.db'}")
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
        actions=frozenset({"browser.sessions.create", "browser.actions.create"}),
    )
    runtime = LocalBrowserRuntime(
        evidence_sink=BrowserEvidenceSink(store, objects),
        project_id="p",
        test_target=f"http://127.0.0.1:{server.server_port}",
    )
    worker = BrowserWorker(service, runtime, "p", "worker")
    secret = "synthetic-password-must-not-be-persisted"
    try:
        sid = service.create_session(
            principal,
            {
                "task_id": "flip",
                "allowed_origins": ["http://example.test"],
                "control_version": "browser-control-v1",
            },
            "session",
        )["session_id"]
        await worker.tick()
        await worker.drain()

        async def perform(body, key):
            receipt = service.create_action(
                principal, sid, {**body, "expected_epoch": 0}, key
            )
            await worker.tick()
            await worker.drain()
            row = store.get("action", "p", receipt["action_id"])
            assert row["status"] == "completed", row
            return row

        await perform({"type": "navigate", "url": "http://example.test/"}, "navigate")
        proof = await perform(
            {"type": "preflight", "operation": "fill", "selector": "#name"}, "proof"
        )
        pending = service.create_action(
            principal,
            sid,
            {
                "type": "fill",
                "selector": "#name",
                "value": secret,
                "expected_epoch": 0,
                "preflight_action_id": proof["id"],
            },
            "input",
        )
        flipped.set()
        # Observe the independently changed DOM without admitting another ledger action.
        async with asyncio.timeout(5):
            while True:
                checked = await runtime.observe(
                    worker._handles[sid],
                    {
                        "type": "preflight",
                        "operation": "fill",
                        "selector": "#name",
                        "actor_kind": "agent",
                        "control_epoch": 0,
                    },
                )
                if checked.error:
                    break
                await asyncio.sleep(0.03)
        await worker.tick()
        await worker.drain()
        row = store.get("action", "p", pending["action_id"])
        assert row["status"] == "failed", row
        with engine.connect() as connection:
            assert secret not in repr(connection.execute(select(actions)).all())
        for file in tmp_path.rglob("*"):
            if file.is_file():
                assert secret.encode() not in file.read_bytes(), file
    finally:
        await worker.shutdown()
        engine.dispose()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join()
