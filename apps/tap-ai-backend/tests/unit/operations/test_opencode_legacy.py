"""Legacy V1 serve protocol evidence and terminal reconciliation."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from scripts.opencode_probe.legacy import LegacyClient, classify_session, parse_sse


class LegacyStub(BaseHTTPRequestHandler):
    calls: list[tuple[str, str]] = []

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        type(self).calls.append(("GET", self.path))
        if self.headers.get("Authorization") != "Basic b3BlbmNvZGU6cGFzcw==":
            self._json(401, {})
        elif self.path == "/doc":
            self._json(
                200,
                {
                    "openapi": "3.1.0",
                    "paths": {
                        p: {}
                        for p in (
                            "/session",
                            "/session/{sessionID}/message",
                            "/session/{sessionID}/children",
                            "/session/{sessionID}/prompt_async",
                            "/session/{sessionID}/abort",
                            "/event",
                        )
                    },
                },
            )
        elif self.path == "/session/ses_root/children":
            self._json(200, [{"id": "ses_child", "parentID": "ses_root"}])
        elif self.path == "/session/ses_root/message":
            self._json(200, [{"info": {"role": "assistant", "finish": "stop"}, "parts": []}])
        elif self.path == "/session/ses_child/message":
            self._json(200, [{"info": {"role": "assistant", "finish": "stop"}, "parts": []}])
        else:
            self._json(404, {})

    def do_POST(self) -> None:
        type(self).calls.append(("POST", self.path))
        if self.path == "/session":
            self._json(200, {"id": "ses_root"})
        elif self.path == "/session/ses_root/prompt_async":
            self.send_response(204)
            self.end_headers()
        elif self.path == "/session/ses_root/abort":
            self._json(200, True)
        else:
            self._json(404, {})

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def legacy_server() -> ThreadingHTTPServer:
    LegacyStub.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), LegacyStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_schema_preflight_and_admission_are_distinct(legacy_server: ThreadingHTTPServer) -> None:
    client = LegacyClient(f"http://127.0.0.1:{legacy_server.server_port}", "pass")
    client.verify_schema()
    root = client.create_session()
    assert root == "ses_root"
    assert client.prompt_async(root, [{"type": "text", "text": "fixture"}]) == "admitted"
    assert client.abort(root) == "cancel_requested"
    assert classify_session(False, [], []) == "incomplete"


def test_reconcile_requires_idle_and_child_terminal(legacy_server: ThreadingHTTPServer) -> None:
    client = LegacyClient(f"http://127.0.0.1:{legacy_server.server_port}", "pass")
    assert client.reconcile("ses_root", idle_sessions={"ses_root", "ses_child"}) == "complete"
    assert client.reconcile("ses_root", idle_sessions={"ses_root"}) == "incomplete"
    assert ("GET", "/session/ses_child/message") in LegacyStub.calls


def test_sse_parser_handles_split_frames_and_malformed_json() -> None:
    chunks = [
        b'data: {"id":"evt_1","type":"session.',
        b'idle","properties":{"sessionID":"ses_root"}}\n\n',
        b"data: {bad}\n\n",
    ]
    events, invalid = parse_sse(chunks)
    assert events == (
        {"id": "evt_1", "type": "session.idle", "properties": {"sessionID": "ses_root"}},
    )
    assert invalid == 1
