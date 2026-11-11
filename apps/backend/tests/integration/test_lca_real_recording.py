"""Owned real Chromium recorder → persisted draft → frozen debug replay.

Opt-in, deterministic and synthetic only. This exercises the backend HTTP and
worker boundaries, not the frontend or its same-origin capability gateway.
"""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
import threading
from urllib.parse import parse_qs, urlsplit
import uuid
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select

from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.http import create_browser_router
from tap_platform.browser_automation.local import LocalBrowserRuntime
from tap_platform.browser_automation.objects import (
    BrowserEvidenceSink,
    BrowserObjectStore,
)
from tap_platform.browser_automation.service import BrowserService
from tap_platform.browser_automation.store import (
    BrowserStore,
    metadata as browser_metadata,
)
from tap_platform.browser_automation.worker import BrowserWorker
from tap_platform.lca.http import create_lca_router, DelegatedLcaAuthorizer
from tap_platform.lca.service import LcaService
from tap_platform.lca.store import LcaStore, metadata as lca_metadata


pytestmark = pytest.mark.skipif(
    os.environ.get("TAP_RUN_BROWSER_LOCAL_INTEGRATION") != "1",
    reason="explicit owned local Chromium LCA recording/replay acceptance",
)


@pytest.fixture
def recording_site():
    """Count actual requests and hold the first submit before its acknowledgement."""
    submissions = []
    first_submit = threading.Event()
    release_submit = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            context = (
                cookie["context"].value if "context" in cookie else uuid.uuid4().hex
            )
            if parsed.path == "/form":
                content = b"""<!doctype html><title>Owned LCA fixture</title>
                <form action="/submit" method="get">
                  <label>Customer<input data-testid="customer" name="customer" required></label>
                  <label>Plan<select data-testid="plan" name="plan">
                    <option value="basic">Basic</option><option value="premium">Premium</option>
                  </select></label>
                  <button data-testid="submit" type="submit">Submit request</button>
                </form>"""
            elif parsed.path == "/submit":
                assert "context" in cookie, "submit must retain its navigation cookie"
                values = parse_qs(parsed.query)
                submission = {
                    "customer": values.get("customer", [""])[0],
                    "plan": values.get("plan", [""])[0],
                    "context": context,
                }
                submissions.append(submission)
                if len(submissions) == 1:
                    first_submit.set()
                    assert release_submit.wait(15), "test did not release owned submit"
                content = (
                    "<!doctype html><title>Submitted request</title>"
                    '<output data-testid="result">'
                    f"{submission['customer']} / {submission['plan']}</output>"
                ).encode()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Set-Cookie", f"context={context}; Path=/; SameSite=Lax")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"http://127.0.0.1:{server.server_port}",
            submissions,
            first_submit,
            release_submit,
        )
    finally:
        release_submit.set()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
async def test_real_record_stop_save_generate_and_explicit_fresh_replay(
    tmp_path, recording_site
):
    site, submissions, first_submit, release_submit = recording_site
    engine = create_engine(f"sqlite:///{tmp_path / 'recording.db'}")
    browser_metadata.create_all(engine)
    lca_metadata.create_all(engine)
    store = BrowserStore(engine)
    objects = BrowserObjectStore(tmp_path / "objects")
    # One service instance owns the ephemeral input vault for API and worker.
    browser = BrowserService(store, objects)
    service = LcaService(LcaStore(engine), browser)
    principal = AccessPrincipal(
        project_id="p",
        principal_id="owned-browser-service",
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
    actor = AccessPrincipal(
        project_id="p",
        principal_id="alice",
        principal_type="user",
        audience="tap-lca",
        expires_at=principal.expires_at,
        enabled=True,
        actions=frozenset({"lca.drafts.generate"}),
    )

    def application(lca_service):
        app = FastAPI()
        app.include_router(
            create_browser_router(browser, principal, "a" * 32, gateway_token="b" * 32)
        )
        app.include_router(
            create_lca_router(lca_service, DelegatedLcaAuthorizer("d" * 32, "p"))
        )
        return app

    client = TestClient(application(service))
    runtime = LocalBrowserRuntime(
        evidence_sink=BrowserEvidenceSink(store, objects),
        project_id="p",
        test_target=site,
    )
    worker = BrowserWorker(browser, runtime, "p", "owned-lca-worker")
    browser_headers = {"Authorization": "Bearer " + "a" * 32}
    human_headers = {
        "Authorization": "Bearer " + "b" * 32,
        "X-Tap-Controller": "c" * 32,
        "X-Tap-Controller-Expires": str(
            (datetime.now(UTC) + timedelta(seconds=120)).timestamp()
        ),
    }
    lca_headers = {"Authorization": "Bearer " + "d" * 32, "X-Lca-Actor": "alice"}
    stop = asyncio.Event()

    async def pump():
        while not stop.is_set():
            await worker.tick()
            await asyncio.sleep(0.03)

    pumping = asyncio.create_task(pump())

    async def request(method, path, *, body=None, key=None, human=False, owner="alice"):
        headers = dict(
            lca_headers
            if "/lca/" in path
            else human_headers
            if human
            else browser_headers
        )
        if "/lca/" in path:
            headers["X-Lca-Actor"] = owner
        if key:
            headers["Idempotency-Key"] = key
        return await asyncio.to_thread(
            client.request, method, path, headers=headers, json=body
        )

    async def read_until(path, ready, *, timeout=30):
        async with asyncio.timeout(timeout):
            while True:
                assert not pumping.done(), (
                    pumping.exception() if pumping.done() else None
                )
                response = await request("GET", path)
                assert response.status_code == 200, response.text
                value = response.json()
                if ready(value):
                    return value
                assert value.get("status") not in {"unknown", "failed", "rejected"}, (
                    value
                )
                assert value.get("control_state") not in {"blocked", "closed"}, value
                await asyncio.sleep(0.03)

    def count(table):
        with engine.connect() as connection:
            return connection.scalar(
                select(func.count()).select_from(
                    browser_metadata.tables[f"tap_browser_{table}"]
                )
            )

    lca_root = "/api/projects/p/lca"
    try:
        seed = {
            "schema_version": "lca-debug-v1",
            "allowed_origins": ["http://example.test"],
            "targets": [],
            "parameters": [],
            "steps": [
                {
                    "id": "open",
                    "operation": "navigate",
                    "arguments": {"url": "http://example.test/form"},
                    "timeout_ms": 5000,
                }
            ],
        }
        created = await request(
            "POST",
            lca_root + "/flows",
            body={"title": "Owned recording", "definition": seed},
            key="flow",
        )
        assert created.status_code == 201, created.text
        flow = created.json()
        flow_path = f"{lca_root}/flows/{flow['id']}"
        started = await request(
            "POST",
            flow_path + "/recordings",
            body={"expected_version": 1},
            key="record",
        )
        assert started.status_code == 202, started.text
        sid = started.json()["session_id"]
        session_path = f"/api/projects/p/browser/sessions/{sid}"
        recording_path = f"{flow_path}/recordings/{sid}"
        await read_until(session_path, lambda row: row["status"] == "active")
        takeover = await request(
            "POST",
            session_path + "/control",
            body={"operation": "takeover", "expected_epoch": 0},
            key="takeover",
            human=True,
        )
        assert takeover.status_code == 202, takeover.text
        await read_until(
            session_path + "/control",
            lambda row: row["control_state"] == "human_active",
        )

        async def frame(after=0):
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

        async def admit(body, key):
            response = await request(
                "POST", session_path + "/human-actions", body=body, key=key, human=True
            )
            assert response.status_code == 202, response.text
            return response.json()

        async def completed(action_id):
            row = await read_until(
                session_path + "/actions/" + action_id,
                lambda value: value["status"]
                in {"completed", "failed", "unknown", "rejected", "canceled"},
            )
            assert row["status"] == "completed", row
            assert not row["observation"].get("error"), row
            return row

        async def perform(body, key):
            return await completed((await admit(body, key))["action_id"])

        current = await frame()
        navigation = {
            "type": "navigate",
            "url": "http://example.test/form",
            "expected_epoch": 1,
            "frame_sequence": current["frame_sequence"],
            "viewport": current["viewport"],
        }
        recorded = [await perform(navigation, "navigate")]
        for operation, selector, value in [
            ("fill", '[data-testid="customer"]', "recorded-user"),
            ("select", '[data-testid="plan"]', "Premium"),
        ]:
            current = await frame(current["frame_sequence"])
            target = {
                "selector": selector,
                "expected_epoch": 1,
                "frame_sequence": current["frame_sequence"],
                "viewport": current["viewport"],
            }
            proof = await perform(
                {"type": "preflight", "operation": operation, **target},
                operation + "-proof",
            )
            recorded.append(
                await perform(
                    {
                        "type": operation,
                        "value": value,
                        "preflight_action_id": proof["action_id"],
                        **target,
                    },
                    operation,
                )
            )
        assert recorded[-1]["args"] == {"value": "premium"}
        current = await frame(current["frame_sequence"])
        submit = {
            "type": "click",
            "selector": '[data-testid="submit"]',
            "expected_epoch": 1,
            "frame_sequence": current["frame_sequence"],
            "viewport": current["viewport"],
        }
        admitted = await admit(submit, "submit-once")
        assert await asyncio.to_thread(first_submit.wait, 10), (
            "real submit did not reach fixture"
        )
        assert len(submissions) == 1

        # Simulate a lost response using the original command identity, not a retry action.
        duplicate = await admit(submit, "submit-once")
        assert duplicate["action_id"] == admitted["action_id"]
        stopped = await request(
            "POST",
            session_path + "/recording/stop",
            body={"expected_epoch": 1},
            key="stop",
            human=True,
        )
        assert stopped.status_code == 202, stopped.text
        draining = await request("GET", recording_path)
        assert draining.status_code == 200, draining.text
        assert draining.json()["status"] == "draining"
        assert draining.json()["projection"]["ready"] is False
        assert (
            admitted["sequence"] in draining.json()["projection"]["missing_sequences"]
        )
        release_submit.set()
        recorded.append(await completed(admitted["action_id"]))
        frozen = await read_until(
            recording_path, lambda row: row["status"] == "stopped"
        )
        projection = frozen["projection"]
        assert projection["ready"] and not projection["missing_sequences"]
        definition = projection["definition"]
        assert [step["operation"] for step in definition["steps"]] == [
            "navigate",
            "fill",
            "select",
            "click",
        ]
        assert [step["provenance"]["action_id"] for step in definition["steps"]] == [
            row["action_id"] for row in recorded
        ]
        assert len(projection["excluded"]) == 2  # Read-only preflights remain visible.
        assert {item["reason"] for item in projection["excluded"]} == {
            "unsupported_operation"
        }
        assert (await request("GET", recording_path, owner="bob")).status_code == 404
        late = await request(
            "POST",
            session_path + "/human-actions",
            body=submit,
            key="forbidden-after-stop",
            human=True,
        )
        assert late.status_code == 409, late.text

        imported = await request(
            "PUT",
            flow_path + "/draft",
            body={"expected_version": 1, "definition": definition},
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["version"] == 2
        assert len(submissions) == 1
        service.generate_draft(actor, flow["id"])
        assert count("executions") == count("bundles") == 0
        assert len(submissions) == 1

        edited = deepcopy(definition)
        edited["parameters"] = [
            {"name": "customer", "type": "string", "required": True}
        ]
        edited["steps"][1]["arguments"] = {"value": {"parameter": "customer"}}
        edited["targets"].append({"id": "result", "kind": "test_id", "value": "result"})
        edited["steps"].append(
            {
                "id": "verify_result",
                "operation": "assert_text",
                "target_id": "result",
                "arguments": {"text": "replay-user / premium"},
                "timeout_ms": 1000,
            }
        )
        saved = await request(
            "PUT",
            flow_path + "/draft",
            body={"expected_version": 2, "definition": edited},
        )
        assert saved.status_code == 200, saved.text
        generated = service.generate_draft(
            actor, flow["id"], {"customer": "replay-user"}
        )
        assert generated == service.generate_draft(
            actor, flow["id"], {"customer": "replay-user"}
        )
        assert count("executions") == count("bundles") == 0
        assert len(submissions) == 1
        # Stopping a drained LCA recording already acknowledged browser teardown.
        assert (await request("GET", session_path)).json()["status"] == "completed"

        async def replay(version, key, status):
            body = {
                "expected_version": version,
                "parameters": {"customer": "replay-user"},
            }
            response = await request("POST", flow_path + "/runs", body=body, key=key)
            assert response.status_code == 202, response.text
            run = response.json()
            duplicate = await request("POST", flow_path + "/runs", body=body, key=key)
            assert duplicate.json()["execution_id"] == run["execution_id"]
            result = await read_until(
                f"{lca_root}/runs/{run['id']}",
                lambda row: row["execution"]["status"] not in {"pending", "running"},
                timeout=60,
            )
            assert result["execution"]["status"] == status, result
            assert result["canonical_results_error"] is None, result
            assert result["canonical_results"] == [
                {
                    "step_id": step["id"],
                    "status": status if step["id"] == "verify_result" else "passed",
                }
                for step in edited["steps"]
            ]
            assert {item["kind"] for item in result["execution"]["evidence"]} >= {
                "screenshot",
                "trace",
                "result",
            }
            for evidence in result["execution"]["evidence"]:
                if evidence["kind"] not in {"screenshot", "trace", "result"}:
                    continue
                path = f"{lca_root}/runs/{run['id']}/evidence/{evidence['object_ref']}"
                response = await request("GET", path)
                assert response.status_code == 200, response.text
                assert (
                    hashlib.sha256(response.content).hexdigest() == evidence["sha256"]
                )
                assert (await request("GET", path, owner="bob")).status_code == 404
                if evidence["kind"] == "trace":
                    assert zipfile.is_zipfile(io.BytesIO(response.content))
                elif evidence["kind"] == "screenshot":
                    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
            return result

        passed = await replay(3, "explicit-pass", "passed")
        assert len(submissions) == 2
        assert submissions[1]["customer"] == "replay-user"
        assert submissions[1]["context"] != submissions[0]["context"]
        edited["steps"][-1]["arguments"]["text"] = "deliberately incorrect"
        changed = await request(
            "PUT",
            flow_path + "/draft",
            body={"expected_version": 3, "definition": edited},
        )
        assert changed.status_code == 200, changed.text
        assert len(submissions) == 2
        failed = await replay(4, "explicit-failure", "failed")
        assert len(submissions) == 3
        assert len({row["context"] for row in submissions}) == 3
        assert passed["snapshot_id"] != failed["snapshot_id"]
        assert passed["execution_id"] != failed["execution_id"]
        assert count("executions") == 2
        assert (
            await request("GET", f"{lca_root}/runs/{passed['id']}")
        ).json() == passed

        edited["steps"][-1]["arguments"]["text"] = "replay-user / premium"
        corrected_draft = await request(
            "PUT",
            flow_path + "/draft",
            body={"expected_version": 4, "definition": edited},
        )
        assert corrected_draft.status_code == 200, corrected_draft.text
        assert len(submissions) == 3
        corrected = await replay(5, "explicit-correction", "passed")
        assert corrected["snapshot_id"] != failed["snapshot_id"]
        assert corrected["execution_id"] != failed["execution_id"]
        assert count("executions") == 3
        assert len(submissions) == 4
        assert len({row["context"] for row in submissions}) == 4
        for evidence in failed["execution"]["evidence"]:
            if evidence["kind"] not in {"screenshot", "trace", "result"}:
                continue
            response = await request(
                "GET",
                f"{lca_root}/runs/{failed['id']}/evidence/{evidence['object_ref']}",
            )
            assert response.status_code == 200, response.text
            assert hashlib.sha256(response.content).hexdigest() == evidence["sha256"]

        # Recreate the HTTP application and store adapter to rule out local UI state.
        client.close()
        client = TestClient(application(LcaService(LcaStore(engine), browser)))
        restored = await request("GET", flow_path)
        assert restored.status_code == 200, restored.text
        assert restored.json()["version"] == 5
        assert restored.json()["definition"] == corrected_draft.json()["definition"]
        history = await request("GET", flow_path + "/runs")
        assert {row["id"] for row in history.json()} == {
            passed["id"],
            failed["id"],
            corrected["id"],
        }
        assert (
            await request("GET", f"{lca_root}/runs/{failed['id']}")
        ).json() == failed
        assert (await request("GET", recording_path)).json()["projection"] == projection
        assert len(submissions) == 4

        # A second recording of the saved flow gets a fresh inspector only after
        # the first stop has acknowledged teardown; historic receipts stay intact.
        first_session = (await request("GET", session_path)).json()
        assert first_session["status"] == "completed"
        original_projection = (await request("GET", recording_path)).json()[
            "projection"
        ]
        second = await request(
            "POST",
            flow_path + "/recordings",
            body={"expected_version": 5},
            key="record-again",
        )
        assert second.status_code == 202, second.text
        sid2 = second.json()["session_id"]
        assert sid2 != sid
        session_path = f"/api/projects/p/browser/sessions/{sid2}"
        await read_until(session_path, lambda row: row["status"] == "active")
        took = await request(
            "POST",
            session_path + "/control",
            body={"operation": "takeover", "expected_epoch": 0},
            key="take-again",
            human=True,
        )
        assert took.status_code == 202, took.text
        await read_until(
            session_path + "/control",
            lambda row: row["control_state"] == "human_active",
        )
        fresh = await frame()
        command = await admit(
            {
                "type": "navigate",
                "url": "http://example.test/form",
                "expected_epoch": 1,
                "frame_sequence": fresh["frame_sequence"],
                "viewport": fresh["viewport"],
            },
            "navigate-again",
        )
        await completed(command["action_id"])
        stopped2 = await request(
            "POST",
            session_path + "/recording/stop",
            body={"expected_epoch": 1},
            key="stop-again",
            human=True,
        )
        assert stopped2.status_code == 202, stopped2.text
        await read_until(
            flow_path + f"/recordings/{sid2}", lambda row: row["status"] == "stopped"
        )
        assert (await request("GET", session_path)).json()["status"] == "completed"
        assert (await request("GET", recording_path)).json()[
            "projection"
        ] == original_projection
        assert len(submissions) == 4
    finally:
        release_submit.set()
        stop.set()
        try:
            await pumping
        finally:
            await worker.shutdown()
            client.close()
            engine.dispose()
