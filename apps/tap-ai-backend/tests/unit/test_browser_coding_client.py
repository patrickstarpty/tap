import asyncio
import json
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from tap.modules.workflows.adapters.browser_client import BrowserApiClient, canonical_json_bytes


def test_canonical_input_digest_matches_server_for_chinese_and_reordered_keys():
    left = {"北京": "值", "nested": {"b": 2, "a": 1}}
    right = {"nested": {"a": 1, "b": 2}, "北京": "值"}
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert b"\\u5317\\u4eac" in canonical_json_bytes(left)
    assert (
        sha256(canonical_json_bytes(left)).hexdigest()
        == sha256(canonical_json_bytes(right)).hexdigest()
    )


@pytest.mark.asyncio
async def test_browser_client_posts_project_scoped_idempotent_action_and_polls():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers["Content-Length"])
            seen.append(
                (
                    self.path,
                    self.headers["Authorization"],
                    self.headers["Idempotency-Key"],
                    json.loads(self.rfile.read(size)),
                )
            )
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"action_id":"action-a","status":"pending"}')

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"action_id":"action-a","status":"completed",'
                b'"observation":{"snapshot":"real page"}}'
            )

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = BrowserApiClient(f"http://127.0.0.1:{server.server_port}", "project-a", "x" * 16)
        result = await client.action("session-a", {"type": "snapshot"}, "stable-action")
        assert result["observation"]["snapshot"] == "real page"
        assert seen == [
            (
                "/api/projects/project-a/browser/sessions/session-a/actions",
                "Bearer " + "x" * 16,
                "stable-action",
                {"type": "snapshot"},
            )
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_browser_client_rejects_nonloopback_and_bad_identity():
    with pytest.raises(ValueError):
        BrowserApiClient("https://example.com", "project-a", "x" * 16)
    with pytest.raises(ValueError):
        BrowserApiClient("http://127.0.0.1:8002", "../project", "x" * 16)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["canceled", "unknown", "passed", "failed"])
@pytest.mark.parametrize("trigger", ["callback", "timeout"])
async def test_execute_stop_uses_authoritative_task2_cancel_endpoint(terminal, trigger):
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            paths.append(self.path)
            body = (
                b'{"execution_id":"execution-a","status":"pending"}'
                if self.path.endswith("/executions")
                else json.dumps({"execution_id": "execution-a", "status": terminal}).encode()
            )
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"execution_id": "execution-a", "status": terminal}).encode()
            )

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = BrowserApiClient(f"http://127.0.0.1:{server.server_port}", "project-a", "x" * 16)

        async def canceled():
            return True

        result = await client.execute(
            "task-a",
            "bundle-a",
            "a" * 64,
            {},
            "execution-key",
            deadline_seconds=2 if trigger == "callback" else 0,
            stop_requested=canceled if trigger == "callback" else None,
        )
        assert result["status"] == terminal
        assert paths[-1].endswith("/executions/execution-a/cancel")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["canceled", "unknown", "passed", "failed"])
@pytest.mark.parametrize("trigger", ["callback", "timeout"])
async def test_replay_stop_uses_authoritative_task2_cancel_endpoint(terminal, trigger):
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            paths.append(self.path)
            body = (
                b'{"execution_id":"replay-a","status":"pending"}'
                if self.path.endswith("/executions")
                else json.dumps({"execution_id": "replay-a", "status": terminal}).encode()
            )
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"execution_id": "replay-a", "status": terminal}).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = BrowserApiClient(f"http://127.0.0.1:{server.server_port}", "project-a", "x" * 16)

        async def canceled():
            return True

        result = await client.replay(
            "task-a",
            "execution-old",
            "replay-key",
            deadline_seconds=2 if trigger == "callback" else 0,
            stop_requested=canceled if trigger == "callback" else None,
        )
        assert result["status"] == terminal
        assert paths[-1].endswith("/executions/replay-a/cancel")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
async def test_unconfirmed_execution_cancel_returns_unknown_receipt():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(500 if self.path.endswith("/cancel") else 202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"detail":"cancel unavailable"}'
                if self.path.endswith("/cancel")
                else b'{"execution_id":"execution-a","status":"pending"}'
            )

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = BrowserApiClient(f"http://127.0.0.1:{server.server_port}", "project-a", "x" * 16)

        async def canceled():
            return True

        result = await client.execute(
            "task-a",
            "bundle-a",
            "a" * 64,
            {},
            "execution-key",
            deadline_seconds=2,
            stop_requested=canceled,
        )
        assert result["status"] == "unknown"
        assert result["failure_code"] == "stop_unconfirmed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["execute", "replay", "session"])
async def test_cancel_during_accepted_create_reconciles_same_key_and_stops(kind, monkeypatch):
    client = BrowserApiClient("http://127.0.0.1:8002", "project-a", "x" * 16)
    accepted = asyncio.Event()
    release = asyncio.Event()
    posts = []
    canceled = []

    async def request(method, path, body=None, key=None):
        if method == "POST" and path in {"/sessions", "/executions"}:
            posts.append((path, body, key))
            if len(posts) == 1:
                accepted.set()
                await release.wait()
                raise OSError("response lost after commit")
            return {"session_id": "session-a", "execution_id": "execution-a", "status": "pending"}
        if method == "POST" and path.endswith("/cancel"):
            canceled.append(path)
            return {"status": "pending"}
        if method == "GET":
            return {"session_id": "session-a", "execution_id": "execution-a", "status": "canceled"}
        raise AssertionError((method, path))

    monkeypatch.setattr(client, "request", request)
    if kind == "session":
        work = client.session("task-a", ["https://example.com"], 30, "same-key")
    elif kind == "replay":
        work = client.replay("task-a", "execution-old", "same-key", deadline_seconds=30)
    else:
        work = client.execute("task-a", "bundle-a", "a" * 64, {}, "same-key", deadline_seconds=30)
    running = asyncio.create_task(work)
    await accepted.wait()
    running.cancel()
    release.set()
    result = await running
    assert result["status"] == "canceled"
    assert len(posts) == 2 and posts[0] == posts[1]
    assert canceled == [
        "/sessions/session-a/cancel" if kind == "session" else "/executions/execution-a/cancel"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["execute", "session"])
async def test_unresolved_accepted_create_is_unknown(kind, monkeypatch):
    client = BrowserApiClient("http://127.0.0.1:8002", "project-a", "x" * 16)
    attempts = []

    async def request(method, path, body=None, key=None):
        attempts.append((method, path, body, key))
        raise OSError("receipt unavailable")

    monkeypatch.setattr(client, "request", request)
    if kind == "session":
        result = await client.session("task-a", ["https://example.com"], 30, "same-key")
    else:
        result = await client.execute(
            "task-a", "bundle-a", "a" * 64, {}, "same-key", deadline_seconds=30
        )
    assert result["status"] == "unknown"
    assert result["failure_code"] == "create_unconfirmed"
    assert len(attempts) == 2 and attempts[0] == attempts[1]


@pytest.mark.asyncio
async def test_session_readiness_cancellation_stops_known_session(monkeypatch):
    client = BrowserApiClient("http://127.0.0.1:8002", "project-a", "x" * 16)
    polling = asyncio.Event()
    canceled = []

    async def request(method, path, body=None, key=None):
        if method == "POST" and path == "/sessions":
            return {"session_id": "session-a", "status": "pending"}
        if method == "POST" and path == "/sessions/session-a/cancel":
            canceled.append(path)
            return {"session_id": "session-a", "status": "pending"}
        if method == "GET":
            if not canceled:
                polling.set()
                await asyncio.Event().wait()
            return {"session_id": "session-a", "status": "canceled"}
        raise AssertionError((method, path))

    monkeypatch.setattr(client, "request", request)
    running = asyncio.create_task(
        client.session("task-a", ["https://example.com"], 30, "session-key")
    )
    await polling.wait()
    running.cancel()
    receipt = await running
    assert receipt["status"] == "canceled"
    assert canceled == ["/sessions/session-a/cancel"]


@pytest.mark.asyncio
async def test_new_sessions_and_frozen_bundles_use_public_web_without_domain_setup(monkeypatch):
    client = BrowserApiClient("http://127.0.0.1:8002", "project-a", "x" * 16)
    posted = []

    async def request(method, path, body=None, key=None):
        if method == "POST":
            posted.append((path, body))
        if path == "/bundles":
            return {"bundle_id": "bundle-a"}
        return {"session_id": "session-a", "status": "active"}

    monkeypatch.setattr(client, "request", request)
    await client.session("task-a", ["https://example.com"], 30, "session-key")
    await client.bundle("task-a", "test", {}, ["https://example.com"], "bundle-key")
    assert [path for path, _ in posted] == ["/sessions", "/bundles"]
    assert all(body["network_mode"] == "public_web" for _, body in posted)
    assert all(body["allowed_origins"] == ["https://example.com"] for _, body in posted)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["fill", "select", "press"])
@pytest.mark.parametrize(
    "mutation",
    [
        "valid",
        "unknown",
        "write",
        "error",
        "session",
        "epoch",
        "id",
        "actor",
        "operation",
        "proof",
        "sequence",
    ],
)
async def test_controlled_preflight_rejection_never_admits_business_action(operation, mutation):
    from tap.modules.workflows.adapters.browser_client import BrowserApiError

    proof = {
        "action_id": "preflight-a",
        "operation": "preflight",
        "actor_kind": "agent",
        "session_id": "session-a",
        "control_epoch": 0,
        "status": "failed",
        "observation": {
            "session_id": "session-a",
            "sequence": 1,
            "write_started": False,
            "error": "controlled target missing before actuation",
            "extracted": {
                "pre_actuation_failure": {
                    "phase": "target_resolution",
                    "category": "target_missing",
                }
            },
        },
    }
    mutations = {
        "unknown": ("status", "unknown"),
        "session": ("session_id", "other"),
        "epoch": ("control_epoch", 1),
        "id": ("action_id", "other"),
        "actor": ("actor_kind", "human"),
        "operation": ("operation", "fill"),
    }
    if mutation in mutations:
        field, value = mutations[mutation]
        proof[field] = value
    if mutation == "write":
        proof["observation"]["write_started"] = True
    if mutation == "error":
        proof["observation"]["error"] = "private error"
    if mutation == "proof":
        proof["observation"]["extracted"] = {}
    if mutation == "sequence":
        proof["observation"]["sequence"] = True
    seen = []

    class Client(BrowserApiClient):
        async def request(self, method, route, body=None, key=None):
            seen.append(body)
            return {"action_id": "preflight-a"}

        async def poll(self, *args, **kwargs):
            return proof

    client = Client("http://127.0.0.1:8002", "project-a", "x" * 16)
    action = {
        "type": operation,
        "selector": "#private",
        "expected_epoch": 0,
        "key" if operation == "press" else "value": "private-value",
    }
    if mutation == "valid":
        result = await client.action("session-a", action, "key")
        assert result["status"] == "preflight_rejected"
        assert result["business_action_admitted"] is False
        assert "private" not in json.dumps(result)
    else:
        with pytest.raises(BrowserApiError):
            await client.action("session-a", action, "key")
    assert len(seen) == 1 and seen[0]["type"] == "preflight"
    assert "value" not in seen[0] and "key" not in seen[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["conflict", "timeout"])
async def test_preflight_transport_failure_is_never_recoverable_rejection(failure):
    from tap.modules.workflows.adapters.browser_client import BrowserApiError

    class Client(BrowserApiClient):
        async def request(self, *args, **kwargs):
            if failure == "conflict":
                raise BrowserApiError(409, "conflict")
            raise TimeoutError("timeout")

    client = Client("http://127.0.0.1:8002", "project-a", "x" * 16)
    with pytest.raises(BrowserApiError if failure == "conflict" else TimeoutError):
        await client.action(
            "session-a",
            {"type": "fill", "selector": "#x", "value": "secret", "expected_epoch": 0},
            "key",
        )
