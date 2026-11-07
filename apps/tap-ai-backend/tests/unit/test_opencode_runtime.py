import asyncio
import base64
import json
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from tap.modules.workflows.adapters.opencode_runtime import CodingConfig, CodingRuntime


class ReplayBrowser:
    def __init__(self):
        self.calls = []

    async def replay(self, task_id, execution_id, key, *, deadline_seconds, stop_requested):
        self.calls.append((task_id, execution_id, key, deadline_seconds, stop_requested))
        return {
            "execution_id": "execution-new",
            "bundle_id": "bundle-old",
            "status": "passed",
            "result": {"outputs": [{"quote": "42"}]},
            "evidence": [{"kind": "result", "object_ref": "evidence-a"}],
        }


def request(*, cost_micros=100000, replay=True):
    return {
        "kind": "browser",
        "goal": "Read the page",
        "model_alias": "qwen-plus",
        "root": {
            "agent_id": "orchestrator",
            "revision_id": "r1",
            "description": "root",
            "instruction": "work",
        },
        "candidates": [],
        "requested_ids": [],
        "browser": {
            "start_url": "https://example.com/",
            "allowed_origins": ["https://example.com"],
            "expected_result": "Read the page",
            "input_data": {},
            "input_sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
            **({"replay_execution_id": "execution-old"} if replay else {}),
        },
        "budget_limit": {"model_calls": 3, "cost_micros": cost_micros},
        "deadline_at": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
    }


@pytest.mark.asyncio
async def test_replay_bypasses_model_and_container_entirely():
    browser = ReplayBrowser()
    config = CodingConfig.for_test()
    runtime = CodingRuntime(
        config,
        browser=browser,
        container_factory=lambda *_: (_ for _ in ()).throw(AssertionError("container started")),
    )

    async def canceled():
        return False

    result = await runtime.run(
        "task-a", request(), {}, None, "lease", "node", 1, stop_requested=canceled
    )
    assert result.status == "passed"
    assert result.replay_model_calls == 0
    assert result.provider_session_id is None
    assert result.execution_ref == "execution-new"
    assert len(browser.calls) == 1
    assert browser.calls[0][-1] is canceled


@pytest.mark.asyncio
async def test_browser_task_rejects_missing_finite_cost_cap_before_model():
    runtime = CodingRuntime(CodingConfig.for_test(), browser=ReplayBrowser())
    with pytest.raises(ValueError, match="cost cap"):
        await runtime.run("task-a", request(cost_micros=0), {}, None, "lease", "node", 1)


class SessionBrowser:
    def __init__(self):
        self.canceled = False

    async def session(self, *_args):
        return {"status": "active", "session_id": "session-a"}

    async def cancel_session(self, *_args, **_kwargs):
        self.canceled = True
        return {"status": "canceled"}


class Journal:
    def __init__(self):
        self.events = []

    async def reserve_action(self, _scope, _task, token, action_id, kind, **_kwargs):
        assert token == "lease" and kind == "model"
        self.events.append(("reserved", action_id))
        return "reserved"

    async def settle_action(self, _scope, _task, token, action_id, result, **_kwargs):
        assert token == "lease"
        self.events.append(("settled", action_id, result))
        return result


class MailContainer:
    def __init__(self, root, *, wait_forever=False):
        self.root = root
        self.wait_forever = wait_forever
        self.stopped = False
        self.response_status = None

    async def start(self, *_args):
        pass

    async def set_goal(self, _text):
        pass

    async def run_prompt(self):
        if self.wait_forever:
            await asyncio.Event().wait()
        body = json.dumps(
            {"model": "qwen-plus", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8}
        ).encode()
        (self.root / "requests" / "model-a.json").write_text(
            json.dumps(
                {
                    "id": "model-a",
                    "kind": "model",
                    "deadline_at": time.time() + 10,
                    "payload": {
                        "bodyBase64": base64.b64encode(body).decode(),
                        "claimedSessionId": None,
                    },
                }
            )
        )
        (self.root / "requests" / "model-a.json").chmod(0o600)
        while not (self.root / "responses" / "model-a.json").exists():
            await asyncio.sleep(0.01)
        self.response_status = json.loads((self.root / "responses" / "model-a.json").read_text())[
            "status"
        ]
        return {"terminal": "failed", "session_id": "ses_test"}

    async def collect(self):
        return {}

    async def stop(self):
        self.stopped = True
        return True


@pytest.mark.asyncio
async def test_coding_runtime_journals_reservation_before_forward_and_settles_usage():
    journal = Journal()

    class Upstream(BaseHTTPRequestHandler):
        calls = 0

        def do_POST(self):
            assert journal.events and journal.events[0][0] == "reserved"
            type(self).calls += 1
            self.rfile.read(int(self.headers["Content-Length"]))
            body = b'{"usage":{"prompt_tokens":2,"completion_tokens":3},"choices":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = SessionBrowser()
    built = []
    try:
        original = CodingConfig.for_test()
        config = CodingConfig(
            original.browser_origin,
            original.browser_project_id,
            original.browser_token,
            original.image,
            original.image_id,
            f"http://127.0.0.1:{server.server_port}",
            "synthetic",
            1000,
            2048,
            1.0,
            2.0,
        )

        def make_container(_config, root):
            container = MailContainer(root)
            built.append(container)
            return container

        runtime = CodingRuntime(
            config, browser=browser, store=journal, container_factory=make_container
        )
        result = await runtime.run("task-a", request(replay=False), {}, None, "lease", "node", 1)
        assert result.status == "failed"
        assert Upstream.calls == 1
        assert journal.events[0][0] == "reserved"
        assert journal.events[1][0] == "settled"
        assert journal.events[1][2]["usage_verified"] is True
        assert built[0].response_status == 200
        assert built[0].stopped and browser.canceled
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
async def test_coding_runtime_cancel_confirms_container_and_inspector_stop():
    browser = SessionBrowser()
    built = []

    def make_container(_config, root):
        container = MailContainer(root, wait_forever=True)
        built.append(container)
        return container

    runtime = CodingRuntime(
        CodingConfig.for_test(),
        browser=browser,
        store=Journal(),
        container_factory=make_container,
    )

    async def cancel():
        return True

    with pytest.raises(asyncio.CancelledError):
        await runtime.run(
            "task-a",
            request(replay=False),
            {},
            None,
            "lease",
            "node",
            1,
            stop_requested=cancel,
        )
    assert built[0].stopped and browser.canceled


@pytest.mark.asyncio
async def test_unconfirmed_inspector_stop_preserves_primary_failure_cause():
    class UnknownBrowser(SessionBrowser):
        async def cancel_session(self, *_args, **_kwargs):
            return {"status": "unknown", "stop_receipt": {"stopped": False}}

    class CrashContainer(MailContainer):
        async def run_prompt(self):
            raise ValueError("synthetic prompt failure")

    runtime = CodingRuntime(
        CodingConfig.for_test(),
        browser=UnknownBrowser(),
        store=Journal(),
        container_factory=lambda _config, root: CrashContainer(root),
    )
    result = await runtime.run("task-a", request(replay=False), {}, None, "lease", "node", 1)
    assert result.status == "unknown"
    assert result.terminal_evidence["primary_error_type"] == "ValueError"
    assert result.terminal_evidence["inspector_status"] == "unknown"
    assert result.terminal_evidence["coding_stopped"] is True


def test_generation_prompt_keeps_frozen_start_url_and_expected_result():
    prompt = CodingRuntime._generation_prompt(
        "Search the customer",
        "https://example.com/customers?q=Ada",
        "Show Ada's matched record",
        ["https://example.com"],
        {"customer": "Ada"},
    )
    assert "https://example.com/customers?q=Ada" in prompt
    assert "Show Ada's matched record" in prompt
    assert "async ({ page }, testInfo)" in prompt
    assert "actual DOM" in prompt
    with pytest.raises(ValueError, match="start URL"):
        CodingRuntime._generation_prompt(
            "Search", "https://other.example/", "Show Ada", ["https://example.com"], {}
        )


@pytest.mark.asyncio
async def test_session_start_time_does_not_extend_absolute_coding_deadline():
    class SlowBrowser(SessionBrowser):
        async def session(self, *_args):
            await asyncio.sleep(1.5)
            return {"status": "active", "session_id": "session-a"}

    runtime = CodingRuntime(
        CodingConfig.for_test(),
        browser=SlowBrowser(),
        store=Journal(),
        container_factory=lambda _config, root: MailContainer(root, wait_forever=True),
    )
    frozen = request(replay=False)
    frozen["deadline_at"] = (datetime.now(UTC) + timedelta(seconds=3)).isoformat()
    start = time.monotonic()
    with pytest.raises(TimeoutError, match="deadline"):
        await runtime.run("task-a", frozen, {}, None, "lease", "node", 1)
    assert time.monotonic() - start < 3.6
