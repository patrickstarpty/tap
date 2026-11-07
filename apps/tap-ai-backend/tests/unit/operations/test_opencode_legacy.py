"""Legacy V1 serve protocol evidence and terminal reconciliation."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from scripts.opencode_probe.legacy import LegacyClient, classify_session, parse_sse


class LegacyStub(BaseHTTPRequestHandler):
    calls: list[tuple[str, str]] = []
    last_message_id = "msg_fixture"
    include_task_part = True
    include_task_link = True
    extra_child = False

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
            children = [{"id": "ses_child", "parentID": "ses_root"}]
            if type(self).extra_child:
                children.append({"id": "ses_unlinked", "parentID": "ses_root"})
            self._json(200, children)
        elif self.path == "/session/ses_root/message":
            message_id = type(self).last_message_id
            self._json(
                200,
                [
                    {"info": {"id": message_id, "role": "user"}, "parts": []},
                    {
                        "info": {
                            "role": "assistant",
                            "sessionID": "ses_root",
                            "parentID": message_id,
                            "finish": "stop",
                        },
                        "parts": [
                            {
                                "type": "tool",
                                "tool": "task",
                                "state": {
                                    "status": "completed",
                                    "metadata": {"sessionId": "ses_child"}
                                    if type(self).include_task_link
                                    else {},
                                },
                            }
                        ]
                        if type(self).include_task_part
                        else [],
                    },
                ],
            )
        elif self.path == "/session/ses_child/message":
            self._json(
                200,
                [
                    {"info": {"id": "msg_child", "role": "user"}, "parts": []},
                    {
                        "info": {"role": "assistant", "parentID": "msg_child", "finish": "stop"},
                        "parts": [],
                    },
                ],
            )
        else:
            self._json(404, {})

    def do_POST(self) -> None:
        type(self).calls.append(("POST", self.path))
        if self.path == "/session":
            self._json(200, {"id": "ses_root"})
        elif self.path == "/session/ses_root/prompt_async":
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            type(self).last_message_id = body["messageID"]
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
    LegacyStub.last_message_id = "msg_fixture"
    LegacyStub.include_task_part = True
    LegacyStub.include_task_link = True
    LegacyStub.extra_child = False
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
    assert client.prompt_async(root, [{"type": "text", "text": "fixture"}]).startswith("msg_")
    assert client.abort(root) == "cancel_requested"
    assert classify_session(False, [], []) == "incomplete"


def test_reconcile_requires_idle_and_child_terminal(legacy_server: ThreadingHTTPServer) -> None:
    client = LegacyClient(f"http://127.0.0.1:{legacy_server.server_port}", "pass")
    message_id = client.prompt_async("ses_root", [{"type": "text", "text": "fixture"}])
    client.observe_event(
        "ses_root",
        {
            "type": "message.updated",
            "properties": {
                "info": {"role": "assistant", "sessionID": "ses_root", "parentID": message_id}
            },
        },
    )
    client.observe_event(
        "ses_root", {"type": "session.idle", "properties": {"sessionID": "ses_root"}}
    )
    assert client.reconcile("ses_root") == "incomplete"
    client.observe_event(
        "ses_root", {"type": "session.idle", "properties": {"sessionID": "ses_child"}}
    )
    assert client.reconcile("ses_root") == "complete"
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


@pytest.mark.parametrize(
    "messages",
    [
        [
            {"info": {"id": "msg_new", "role": "user"}, "parts": []},
            {
                "info": {"role": "assistant", "parentID": "msg_new", "finish": "stop"},
                "parts": [{"type": "tool", "state": {"status": "running"}}],
            },
        ],
        [
            {"info": {"id": "msg_old", "role": "user"}, "parts": []},
            {"info": {"role": "assistant", "parentID": "msg_old", "finish": "stop"}, "parts": []},
            {"info": {"id": "msg_new", "role": "user"}, "parts": []},
        ],
        [
            {"info": {"id": "msg_new", "role": "user"}, "parts": []},
            {"info": {"role": "assistant", "parentID": "msg_new", "finish": "length"}, "parts": []},
        ],
        [
            {"info": {"id": "msg_new", "role": "user"}, "parts": []},
            {
                "info": {"role": "assistant", "parentID": "msg_new", "finish": "tool-calls"},
                "parts": [],
            },
        ],
    ],
)
def test_current_operation_rejects_unfinished_or_stale_evidence(messages: list[object]) -> None:
    assert classify_session(True, messages, [], expected_user_id="msg_new") == "incomplete"


def test_new_prompt_invalidates_old_idle_and_assistant_events(
    legacy_server: ThreadingHTTPServer,
) -> None:
    client = LegacyClient(f"http://127.0.0.1:{legacy_server.server_port}", "pass")
    old_id = client.prompt_async("ses_root", [{"type": "text", "text": "old"}])
    client.observe_event(
        "ses_root",
        {
            "type": "message.updated",
            "properties": {
                "info": {"role": "assistant", "sessionID": "ses_root", "parentID": old_id}
            },
        },
    )
    client.observe_event(
        "ses_root", {"type": "session.idle", "properties": {"sessionID": "ses_root"}}
    )
    new_id = client.prompt_async("ses_root", [{"type": "text", "text": "new"}])
    assert new_id != old_id
    client.observe_event(
        "ses_root", {"type": "session.idle", "properties": {"sessionID": "ses_root"}}
    )
    assert client.reconcile("ses_root") == "incomplete"
    client.observe_event(
        "ses_root",
        {
            "type": "message.updated",
            "properties": {
                "info": {"role": "assistant", "sessionID": "ses_root", "parentID": old_id}
            },
        },
    )
    assert client.reconcile("ses_root") == "incomplete"


def test_missing_current_task_child_link_cannot_complete(
    legacy_server: ThreadingHTTPServer,
) -> None:
    client = LegacyClient(f"http://127.0.0.1:{legacy_server.server_port}", "pass")
    message_id = client.prompt_async("ses_root", [{"type": "text", "text": "fixture"}])
    LegacyStub.include_task_link = False
    client.observe_event(
        "ses_root",
        {
            "type": "message.updated",
            "properties": {
                "info": {"role": "assistant", "sessionID": "ses_root", "parentID": message_id}
            },
        },
    )
    for session_id in ("ses_root", "ses_child"):
        client.observe_event(
            "ses_root", {"type": "session.idle", "properties": {"sessionID": session_id}}
        )
    assert client.reconcile("ses_root") == "incomplete"


def test_new_unlinked_child_cannot_complete_but_baseline_child_is_preserved(
    legacy_server: ThreadingHTTPServer,
) -> None:
    client = LegacyClient(f"http://127.0.0.1:{legacy_server.server_port}", "pass")
    message_id = client.prompt_async("ses_root", [{"type": "text", "text": "fixture"}])
    client.observe_event(
        "ses_root",
        {
            "type": "message.updated",
            "properties": {
                "info": {"role": "assistant", "sessionID": "ses_root", "parentID": message_id}
            },
        },
    )
    for session_id in ("ses_root", "ses_child"):
        client.observe_event(
            "ses_root", {"type": "session.idle", "properties": {"sessionID": session_id}}
        )
    assert client.reconcile("ses_root") == "complete"
    LegacyStub.extra_child = True
    assert client.reconcile("ses_root") == "incomplete"


def test_preexisting_unlinked_child_is_excluded_by_operation_baseline(
    legacy_server: ThreadingHTTPServer,
) -> None:
    client = LegacyClient(f"http://127.0.0.1:{legacy_server.server_port}", "pass")
    message_id = client.prompt_async("ses_root", [{"type": "text", "text": "fixture"}])
    LegacyStub.include_task_part = False
    client.observe_event(
        "ses_root",
        {
            "type": "message.updated",
            "properties": {
                "info": {"role": "assistant", "sessionID": "ses_root", "parentID": message_id}
            },
        },
    )
    client.observe_event(
        "ses_root", {"type": "session.idle", "properties": {"sessionID": "ses_root"}}
    )
    assert client.reconcile("ses_root") == "complete"
    assert ("GET", "/session/ses_child/message") not in LegacyStub.calls
