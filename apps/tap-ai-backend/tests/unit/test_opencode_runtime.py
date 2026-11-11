import asyncio
import base64
import hashlib
import json
import time
import traceback
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import pytest

from tap.entrypoints.tapper_runtime import TapperSettings
from tap.modules.workflows.adapters.opencode_runtime import (
    CodingConfig,
    CodingRuntime,
    DockerCodingContainer,
)
from tests.object_settings import S3_SETTINGS


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

    async def append_event(self, _scope, _task, _token, _id, kind, payload):
        self.events.append((kind, payload))

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
        self.frozen_input = None

    async def start(self, *_args):
        pass

    async def set_goal(self, _text):
        pass

    async def set_frozen_input(self, data, digest):
        self.frozen_input = (data, digest)

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
        return {
            "terminal": "failed",
            "session_id": "ses_test",
            "failure": {
                "category": "tool_error",
                "tool": "bash",
                "summary": "OpenCode tool reported an error.",
            },
        }

    async def collect(self):
        return {}

    async def stop(self):
        self.stopped = True
        return True


class CompletedScriptContainer(MailContainer):
    def __init__(self, root):
        super().__init__(root)
        self.goals = []

    async def set_goal(self, text):
        self.goals.append(text)

    async def run_prompt(self):
        if len(self.goals) == 1:
            path = self.root / "requests" / "plan-script.json"
            path.write_text(
                json.dumps(
                    {
                        "id": "plan-script",
                        "kind": "browser",
                        "deadline_at": time.time() + 10,
                        "payload": {
                            "type": "plan",
                            "steps": [
                                {"id": "verify", "keyword": "Then", "title": "Verify result"}
                            ],
                        },
                    }
                )
            )
            path.chmod(0o600)
            while not (self.root / "responses" / "plan-script.json").exists():
                await asyncio.sleep(0.01)
        return {"terminal": "complete", "session_id": "ses_test"}

    async def collect(self):
        script = b"import { test } from '@playwright/test'; test('task', () => {});"
        return {
            "script_base64": base64.b64encode(script).decode(),
            "script_sha256": hashlib.sha256(script).hexdigest(),
        }


@pytest.mark.asyncio
async def test_script_synthesis_uses_evidence_without_opening_or_replaying_browser():
    containers = []

    def factory(_config, root):
        container = CompletedScriptContainer(root)
        containers.append(container)
        return container

    # No browser methods: synthesis must not acquire or write a browser.
    runtime = CodingRuntime(
        CodingConfig.for_test(), browser=object(), store=Journal(), container_factory=factory
    )
    result = await runtime.run(
        "task-a",
        request(replay=False),
        {},
        None,
        "lease",
        "node",
        1,
        script_source={
            "steps": [],
            "actions": [{"operation": "click"}],
            "outputs": {"quote": "42"},
        },
    )
    assert result.status == "passed"
    assert "test('task'" in result.terminal_evidence["script"]
    assert containers[0].stopped
    assert "successful operations" in containers[0].goals[0]


class ReceiptBrowser(SessionBrowser):
    def __init__(self, receipts):
        super().__init__()
        self.receipts = list(receipts)
        self.limits = []

    async def bundle(self, _task_id, _script, _inputs, _origins, _key):
        return {"bundle_id": "bundle-a", "input_sha256": hashlib.sha256(b"{}").hexdigest()}

    async def execute(self, _task_id, _bundle_id, _input_sha256, limits, _key, **_kwargs):
        self.limits.append(limits)
        return self.receipts.pop(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_code", "failed_tests", "explicit_cap"),
    [
        ("runtime_error", 0, False),
        ("evidence_limit", 0, True),
        ("execution_failed", 0, False),
    ],
)
async def test_coding_runtime_does_not_repair_without_actual_test_failure(
    failure_code, failed_tests, explicit_cap
):
    browser = ReceiptBrowser(
        [
            {
                "status": "failed",
                "failure_code": failure_code,
                "execution_id": "execution-a",
                "bundle_id": "bundle-a",
                "result": {"failed": failed_tests} if failed_tests else {},
                "steps": [],
                "evidence": [],
            }
        ]
    )
    built = []

    def make_container(_config, root):
        container = CompletedScriptContainer(root)
        built.append(container)
        return container

    frozen = request(replay=False)
    if explicit_cap:
        frozen["browser"]["execution_limits"] = {"max_output_bytes": 1_000_000}
    runtime = CodingRuntime(
        CodingConfig.for_test(),
        browser=browser,
        store=Journal(),
        container_factory=make_container,
    )
    result = await runtime.run("task-a", frozen, {}, None, "lease", "node", 1)
    assert result.status == "failed"
    assert result.terminal_evidence["failure_code"] == failure_code
    assert len(built[0].goals) == len(browser.limits) == 1
    assert browser.limits[0]["max_output_bytes"] == (1_000_000 if explicit_cap else 5_000_000)
    assert built[0].stopped and browser.canceled


@pytest.mark.asyncio
async def test_coding_runtime_repairs_actual_assertion_failure():
    browser = ReceiptBrowser(
        [
            {
                "status": "failed",
                "failure_code": "execution_failed",
                "execution_id": "execution-a",
                "bundle_id": "bundle-a",
                "result": {"failed": 1, "tests": [{"status": "failed"}]},
                "steps": [{"title": "Expect toHaveText", "status": "failed"}],
                "evidence": [],
            },
            {
                "status": "passed",
                "failure_code": None,
                "execution_id": "execution-b",
                "bundle_id": "bundle-a",
                "result": {"passed": 1, "failed": 0},
                "steps": [],
                "evidence": [],
            },
        ]
    )
    built = []

    def make_container(_config, root):
        container = CompletedScriptContainer(root)
        built.append(container)
        return container

    runtime = CodingRuntime(
        CodingConfig.for_test(),
        browser=browser,
        store=Journal(),
        container_factory=make_container,
    )
    result = await runtime.run("task-a", request(replay=False), {}, None, "lease", "node", 1)
    assert result.status == "passed"
    assert result.execution_ref == "execution-b"
    assert len(browser.limits) == len(built[0].goals) == 2
    assert built[0].goals[1].startswith("Repair flow.spec.ts")
    assert built[0].stopped and browser.canceled


@pytest.mark.asyncio
async def test_coding_generation_receives_exact_frozen_input_before_prompt():
    payload = {"notes": "customer=Ada; region=west; unique=only-in-input"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    frozen = request(replay=False)
    frozen["browser"]["input_data"] = payload
    frozen["browser"]["input_sha256"] = hashlib.sha256(encoded).hexdigest()
    built = []

    def make_container(_config, root):
        container = MailContainer(root, wait_forever=True)
        built.append(container)
        return container

    runtime = CodingRuntime(
        CodingConfig.for_test(),
        browser=SessionBrowser(),
        store=Journal(),
        container_factory=make_container,
    )

    async def cancel():
        return bool(built and built[0].frozen_input)

    result = await runtime.run(
        "task-a", frozen, {}, None, "lease", "node", 1, stop_requested=cancel
    )
    assert result.status == "canceled"
    assert built[0].frozen_input == (encoded, hashlib.sha256(encoded).hexdigest())


@pytest.mark.asyncio
async def test_coding_runtime_passes_configured_input_window_to_opencode_profile():
    built = []

    class CapturingContainer(MailContainer):
        async def start(self, config, *_args):
            self.profile_limit = config["provider"]["litellm"]["models"]["qwen-plus"]["limit"]

    def make_container(_config, root):
        container = CapturingContainer(root, wait_forever=True)
        built.append(container)
        return container

    runtime = CodingRuntime(
        replace(CodingConfig.for_test(), max_input_tokens=30720),
        browser=SessionBrowser(),
        store=Journal(),
        container_factory=make_container,
    )

    async def cancel():
        return bool(built and built[0].frozen_input)

    result = await runtime.run(
        "task-a", request(replay=False), {}, None, "lease", "node", 1, stop_requested=cancel
    )
    assert result.status == "canceled"
    assert built[0].profile_limit == {"context": 30720, "input": 30720, "output": 2048}


@pytest.mark.asyncio
@pytest.mark.parametrize("response_delay", [0, 31])
async def test_coding_runtime_journals_reservation_before_forward_and_settles_usage(response_delay):
    journal = Journal()

    class Upstream(BaseHTTPRequestHandler):
        calls = 0

        def do_POST(self):
            assert journal.events and journal.events[0][0] == "reserved"
            type(self).calls += 1
            self.rfile.read(int(self.headers["Content-Length"]))
            time.sleep(response_delay)
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
        assert result.terminal_evidence["model_terminal"]["failure"] == {
            "category": "tool_error",
            "tool": "bash",
            "summary": "OpenCode tool reported an error.",
        }
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
        return bool(built and built[0].frozen_input)

    result = await runtime.run(
        "task-a",
        request(replay=False),
        {},
        None,
        "lease",
        "node",
        1,
        stop_requested=cancel,
    )
    assert result.status == "canceled"
    assert result.terminal_evidence["stop_confirmed"] is True
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


@pytest.mark.asyncio
async def test_shipped_workflow_default_fits_browser_session_cap():
    settings = TapperSettings.from_mapping(S3_SETTINGS)
    assert settings.workflow_max_seconds == 900

    class DeadlineBrowser(SessionBrowser):
        async def session(self, _task, _origins, seconds, _key):
            assert 1 <= seconds <= 600
            return {"status": "unknown", "failure_code": "synthetic_stop"}

    frozen = request(replay=False)
    frozen["deadline_at"] = (
        datetime.now(UTC) + timedelta(seconds=settings.workflow_max_seconds)
    ).isoformat()
    result = await CodingRuntime(
        CodingConfig.for_test(), browser=DeadlineBrowser(), store=Journal()
    ).run("task-a", frozen, {}, None, "lease", "node", 1)
    assert result.status == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["cancel", "deadline"])
async def test_blocked_coding_start_observes_task_stop_or_absolute_deadline(trigger):
    entered = asyncio.Event()
    calls = 0
    built = []

    class BlockingContainer(MailContainer):
        async def start(self, *_args):
            entered.set()
            await asyncio.Event().wait()

    def make_container(_config, root):
        container = BlockingContainer(root)
        built.append(container)
        return container

    async def stop_requested():
        nonlocal calls
        calls += 1
        return trigger == "cancel" and entered.is_set()

    frozen = request(replay=False)
    frozen["deadline_at"] = (
        datetime.now(UTC) + timedelta(seconds=5 if trigger == "cancel" else 2)
    ).isoformat()
    runtime = CodingRuntime(
        CodingConfig.for_test(),
        browser=SessionBrowser(),
        store=Journal(),
        container_factory=make_container,
    )
    start = time.monotonic()
    if trigger == "cancel":
        result = await asyncio.wait_for(
            runtime.run(
                "task-a", frozen, {}, None, "lease", "node", 1, stop_requested=stop_requested
            ),
            timeout=2,
        )
        assert result.status == "canceled"
        assert result.terminal_evidence["stop_confirmed"] is True
        assert calls > 0
    else:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                runtime.run("task-a", frozen, {}, None, "lease", "node", 1),
                timeout=3,
            )
    assert entered.is_set() and built[0].stopped
    assert time.monotonic() - start < (1.8 if trigger == "cancel" else 2.8)


@pytest.mark.asyncio
async def test_blocked_browser_action_handler_observes_absolute_task_deadline():
    built = []

    class ActionContainer(MailContainer):
        async def run_prompt(self):
            plan = {
                "id": "plan-a",
                "kind": "browser",
                "deadline_at": time.time() + 10,
                "payload": {
                    "type": "plan",
                    "steps": [{"id": "inspect", "keyword": "Given", "title": "Inspect page"}],
                },
            }
            path = self.root / "requests" / "plan-a.json"
            path.write_text(json.dumps(plan))
            path.chmod(0o600)
            while not (self.root / "responses" / "plan-a.json").exists():
                await asyncio.sleep(0.01)
            (self.root / "requests" / "action-a.json").write_text(
                json.dumps(
                    {
                        "id": "action-a",
                        "kind": "browser",
                        "deadline_at": time.time() + 10,
                        "payload": {"type": "snapshot", "step": "inspect"},
                    }
                )
            )
            (self.root / "requests" / "action-a.json").chmod(0o600)
            await asyncio.Event().wait()

    class BlockedBrowser(SessionBrowser):
        def __init__(self):
            super().__init__()
            self.action_started = asyncio.Event()
            self.action_canceled = False

        async def action(self, *_args, **_kwargs):
            self.action_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.action_canceled = True
                raise

    browser = BlockedBrowser()

    def make_container(_config, root):
        container = ActionContainer(root)
        built.append(container)
        return container

    frozen = request(replay=False)
    frozen["deadline_at"] = (datetime.now(UTC) + timedelta(seconds=2)).isoformat()
    runtime = CodingRuntime(
        CodingConfig.for_test(),
        browser=browser,
        store=Journal(),
        container_factory=make_container,
    )
    start = time.monotonic()
    try:
        await asyncio.wait_for(
            runtime.run("task-a", frozen, {}, None, "lease", "node", 1), timeout=3
        )
    except TimeoutError:
        pass
    assert browser.action_started.is_set() and browser.action_canceled
    assert built[0].stopped and browser.canceled
    assert time.monotonic() - start < 2.8


@pytest.mark.asyncio
async def test_coding_command_accepts_successful_docker_stdout_with_verbose_stderr(
    monkeypatch,
):
    started = {}

    class SuccessfulProcess:
        returncode = 0

        async def communicate(self, _data):
            return b"sha256:owned-image\n", b"fork diagnostic\n" * 400

    async def create_process(*_args, **kwargs):
        started.update(kwargs)
        kwargs["stdout"].write(b"sha256:owned-image\n")
        return SuccessfulProcess()

    monkeypatch.setattr(
        "tap.modules.workflows.adapters.opencode_runtime.asyncio.create_subprocess_exec",
        create_process,
    )
    assert await DockerCodingContainer._command(("docker", "image", "inspect", "owned")) == (
        b"sha256:owned-image\n"
    )
    assert started["stderr"] == asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returncode", "stdout", "max_stdout"),
    [(125, b"", 100), (0, b"too many bytes", 4)],
)
async def test_coding_command_rejects_failed_exit_or_oversize_stdout(
    monkeypatch,
    returncode,
    stdout,
    max_stdout,
):
    class Process:
        async def communicate(self, _data):
            return stdout, b""

    process = Process()
    process.returncode = returncode

    async def create_process(*_args, **kwargs):
        kwargs["stdout"].write(stdout)
        return process

    monkeypatch.setattr(
        "tap.modules.workflows.adapters.opencode_runtime.asyncio.create_subprocess_exec",
        create_process,
    )
    with pytest.raises(RuntimeError, match="coding container command failed"):
        await DockerCodingContainer._command(
            ("docker", "image", "inspect", "owned"), max_stdout=max_stdout
        )


@pytest.mark.asyncio
async def test_coding_command_timeout_reports_safe_stage_and_reaps_process(monkeypatch):
    secret = "secret-model-token-value"

    class HungProcess:
        returncode = None
        killed = False
        waited = False

        async def communicate(self, _data):
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.waited = True
            return self.returncode

    process = HungProcess()

    async def create_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(
        "tap.modules.workflows.adapters.opencode_runtime.asyncio.create_subprocess_exec",
        create_process,
    )
    with pytest.raises(TimeoutError, match="docker run") as error:
        await DockerCodingContainer._command(
            ("docker", "run", "--env", f"TOKEN={secret}", "owned"),
            timeout=0.01,
            stage="docker run",
        )
    assert process.killed and process.waited
    assert secret not in str(error.value)
    assert "TOKEN=" not in str(error.value)


@pytest.mark.asyncio
async def test_coding_command_rejects_unapproved_stage_before_process(monkeypatch):
    async def create_process(*_args, **_kwargs):
        raise AssertionError("process should not start")

    monkeypatch.setattr(
        "tap.modules.workflows.adapters.opencode_runtime.asyncio.create_subprocess_exec",
        create_process,
    )
    with pytest.raises(ValueError, match="invalid coding command stage"):
        await DockerCodingContainer._command(("docker", "run", "owned"), stage="token=private")


@pytest.mark.asyncio
async def test_coding_command_timeout_traceback_omits_underlying_secret(monkeypatch):
    secret = "secret-upstream-error-value"

    class Process:
        returncode = None

        async def communicate(self, _data):
            raise TimeoutError(secret)

        def kill(self):
            self.returncode = -9

        async def wait(self):
            return self.returncode

    async def create_process(*_args, **_kwargs):
        return Process()

    monkeypatch.setattr(
        "tap.modules.workflows.adapters.opencode_runtime.asyncio.create_subprocess_exec",
        create_process,
    )
    with pytest.raises(TimeoutError, match="exec inspect") as error:
        await DockerCodingContainer._command(
            ("docker", "exec", "private", "inspect"), stage="exec inspect"
        )
    assert secret not in "".join(traceback.format_exception(error.value))
    assert error.value.__context__ is None
    assert error.value.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("not_ready", [RuntimeError, TimeoutError])
async def test_coding_start_allows_serve_to_become_ready_after_thirty_seconds(
    monkeypatch, tmp_path, not_ready
):
    class Clock:
        value = 0.0

        def monotonic(self):
            return self.value

    clock = Clock()
    monkeypatch.setattr("tap.modules.workflows.adapters.opencode_runtime.time", clock)
    container = DockerCodingContainer(CodingConfig.for_test(), tmp_path)
    probes = []

    async def command(_argv, *, stage, timeout=30, **_kwargs):
        if stage == "image inspect":
            return (container.config.image_id + "\n").encode()
        if stage == "docker run":
            clock.value += 1
            return b"a" * 64
        assert stage == "exec inspect"
        probes.append(timeout)
        clock.value += min(timeout, 2)
        if clock.value >= 39:
            return b'{"ready":true}'
        raise not_ready("not ready")

    monkeypatch.setattr(DockerCodingContainer, "_command", staticmethod(command))
    await container.start({}, "shim", 60)
    assert clock.value >= 39
    assert probes and all(0 < timeout <= 3 for timeout in probes)


@pytest.mark.asyncio
async def test_coding_start_readiness_respects_remaining_task_deadline(monkeypatch, tmp_path):
    class Clock:
        value = 0.0

        def monotonic(self):
            return self.value

    clock = Clock()
    monkeypatch.setattr("tap.modules.workflows.adapters.opencode_runtime.time", clock)
    container = DockerCodingContainer(CodingConfig.for_test(), tmp_path)
    probes = []

    async def command(_argv, *, stage, timeout=30, **_kwargs):
        if stage == "image inspect":
            return (container.config.image_id + "\n").encode()
        if stage == "docker run":
            clock.value += 3
            return b"a" * 64
        assert stage == "exec inspect"
        probes.append(timeout)
        clock.value += timeout
        raise RuntimeError("not ready")

    monkeypatch.setattr(DockerCodingContainer, "_command", staticmethod(command))
    with pytest.raises(TimeoutError, match="OpenCode serve readiness deadline"):
        await container.start({}, "shim", 8)
    assert clock.value <= 8
    assert probes[0] == 3
    assert probes[-1] <= 2


@pytest.mark.asyncio
async def test_coding_start_readiness_preserves_cancellation(monkeypatch, tmp_path):
    container = DockerCodingContainer(CodingConfig.for_test(), tmp_path)

    async def command(_argv, *, stage, **_kwargs):
        if stage == "image inspect":
            return (container.config.image_id + "\n").encode()
        if stage == "docker run":
            return b"a" * 64
        assert stage == "exec inspect"
        raise asyncio.CancelledError

    monkeypatch.setattr(DockerCodingContainer, "_command", staticmethod(command))
    with pytest.raises(asyncio.CancelledError):
        await container.start({}, "shim", 30)
    assert container.container_id == "a" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "expected"),
    [
        (1, b"", b"Error: No such object: " + b"a" * 64, True),
        (1, b"", b"Cannot connect to the Docker daemon", False),
        (0, b"a" * 64 + b" false\n", b"", True),
        (0, b"a" * 64 + b" true\n", b"", False),
    ],
)
async def test_coding_container_stop_requires_affirmative_exact_inspect(
    monkeypatch, tmp_path, returncode, stdout, stderr, expected
):
    class Probe:
        async def communicate(self):
            return stdout, stderr

        @property
        def returncode(self):
            return returncode

    async def fail_command(*_args, **_kwargs):
        raise RuntimeError("stop/remove failed")

    async def create_probe(*_args, **_kwargs):
        return Probe()

    monkeypatch.setattr(DockerCodingContainer, "_command", staticmethod(fail_command))
    monkeypatch.setattr(
        "tap.modules.workflows.adapters.opencode_runtime.asyncio.create_subprocess_exec",
        create_probe,
    )
    container = DockerCodingContainer(CodingConfig.for_test(), tmp_path)
    container.container_id = "a" * 64
    container._run_attempted = True
    assert await container.stop() is expected


@pytest.mark.asyncio
async def test_coding_container_inspect_timeout_is_unknown(monkeypatch, tmp_path):
    class HungProbe:
        returncode = None
        killed = False

        async def communicate(self):
            if self.killed:
                return b"", b""
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True

    probe = HungProbe()

    async def fail_command(*_args, **_kwargs):
        raise RuntimeError("stop/remove failed")

    async def create_probe(*_args, **_kwargs):
        return probe

    monkeypatch.setattr(DockerCodingContainer, "_command", staticmethod(fail_command))
    monkeypatch.setattr(DockerCodingContainer, "_INSPECT_TIMEOUT", 0.01)
    monkeypatch.setattr(
        "tap.modules.workflows.adapters.opencode_runtime.asyncio.create_subprocess_exec",
        create_probe,
    )
    container = DockerCodingContainer(CodingConfig.for_test(), tmp_path)
    container.container_id = "a" * 64
    container._run_attempted = True
    assert await container.stop() is False
    assert probe.killed


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["owned_running", "owned_stopped", "unresolved", "foreign"])
@pytest.mark.parametrize("lost", ["timeout", "canceled", "invalid_reply"])
async def test_coding_run_lost_id_reconciles_exact_attempt_before_stop(
    monkeypatch, tmp_path, state, lost
):
    container = DockerCodingContainer(CodingConfig.for_test(), tmp_path)
    commands = []
    inspected = 0

    async def command(argv, **_kwargs):
        commands.append(argv)
        if argv[:3] == ("docker", "image", "inspect"):
            return (container.config.image_id + "\n").encode()
        if argv[:2] == ("docker", "run"):
            if lost == "timeout":
                raise TimeoutError("run response lost after acceptance")
            if lost == "canceled":
                raise asyncio.CancelledError("run canceled after acceptance")
            return b""
        return b""

    class Probe:
        def __init__(self, nth):
            self.nth = nth

        @property
        def returncode(self):
            return 1 if state == "unresolved" or (state == "owned_stopped" and self.nth == 2) else 0

        async def communicate(self):
            if state == "unresolved":
                return b"", b"Cannot connect to Docker daemon"
            if state == "owned_stopped" and self.nth == 2:
                return b"", ("Error: No such object: " + "a" * 64).encode()
            labels = {
                "tap.owner": "workflow-coding" if state.startswith("owned") else "foreign",
                "tap.attempt": container.attempt_id,
            }
            return json.dumps(
                {
                    "Id": "a" * 64,
                    "Name": "/" + container.name,
                    "Config": {"Labels": labels},
                    "State": {"Running": True},
                }
            ).encode(), b""

    async def create_probe(*_args, **_kwargs):
        nonlocal inspected
        inspected += 1
        return Probe(inspected)

    monkeypatch.setattr(DockerCodingContainer, "_command", staticmethod(command))
    monkeypatch.setattr(
        "tap.modules.workflows.adapters.opencode_runtime.asyncio.create_subprocess_exec",
        create_probe,
    )
    expected = {
        "timeout": TimeoutError,
        "canceled": asyncio.CancelledError,
        "invalid_reply": RuntimeError,
    }[lost]
    with pytest.raises(expected):
        await container.start({}, "shim", 30)
    assert container.container_id is None
    assert await container.stop() is (state == "owned_stopped")
    stops = [argv for argv in commands if argv[:2] in {("docker", "stop"), ("docker", "rm")}]
    assert bool(stops) is state.startswith("owned")
    if stops:
        assert all(argv[-1] == "a" * 64 for argv in stops)


@pytest.mark.asyncio
async def test_gate_shutdown_waits_for_inflight_durable_settlement_before_terminal():
    entered, release = Event(), Event()

    class SlowUpstream(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            entered.set()
            release.wait(5)
            body = b'{"usage":{"prompt_tokens":2,"completion_tokens":3},"choices":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    class SlowJournal(Journal):
        def __init__(self):
            super().__init__()
            self.settled = asyncio.Event()

        async def settle_action(self, *args, **kwargs):
            await asyncio.sleep(0.05)
            result = await super().settle_action(*args, **kwargs)
            self.settled.set()
            return result

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowUpstream)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    journal = SlowJournal()
    try:
        config = CodingConfig.for_test()
        config = CodingConfig(
            config.browser_origin,
            config.browser_project_id,
            config.browser_token,
            config.image,
            config.image_id,
            f"http://127.0.0.1:{server.server_port}",
            "synthetic-key",
            config.max_input_tokens,
            config.max_output_tokens,
            config.input_price_per_million,
            config.output_price_per_million,
        )
        runtime = CodingRuntime(
            config,
            browser=SessionBrowser(),
            store=journal,
            container_factory=lambda _config, root: MailContainer(root),
        )
        running = asyncio.create_task(
            runtime.run("task-a", request(replay=False), {}, None, "lease", "node", 1)
        )
        assert await asyncio.to_thread(entered.wait, 3)
        started = time.monotonic()
        running.cancel()
        release.set()
        result = await running
        assert result.status == "canceled"
        assert result.terminal_evidence["stop_confirmed"] is True
        assert time.monotonic() - started < 4
        assert journal.settled.is_set()
        assert [event[0] for event in journal.events] == ["reserved", "settled"]
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
async def test_coding_command_cancel_does_not_wait_for_inherited_stdout_pipe():
    import sys

    # A grandchild keeps the stdout descriptor open after the command process dies.
    # Docker exec can exhibit the same pipe-drain condition during cancellation.
    code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)'], "
        "stdout=sys.stdout); time.sleep(2)"
    )
    running = asyncio.create_task(
        DockerCodingContainer._command((sys.executable, "-c", code), timeout=5)
    )
    await asyncio.sleep(0.1)
    running.cancel()
    done, pending = await asyncio.wait({running}, timeout=0.5)
    if pending:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert done, "canceled command waited for inherited stdout pipe"
    with pytest.raises(asyncio.CancelledError):
        await running


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_failure", ["kill_race", "reap_timeout"])
async def test_coding_command_cleanup_keeps_original_cancel(monkeypatch, cleanup_failure):
    class Process:
        returncode = None

        async def communicate(self, _data):
            raise asyncio.CancelledError("original cancel")

        def kill(self):
            if cleanup_failure == "kill_race":
                raise ProcessLookupError("already exited")

        async def wait(self):
            if cleanup_failure == "reap_timeout":
                raise TimeoutError("reap delayed")
            return -9

    async def create_process(*_args, **_kwargs):
        return Process()

    monkeypatch.setattr(
        "tap.modules.workflows.adapters.opencode_runtime.asyncio.create_subprocess_exec",
        create_process,
    )
    with pytest.raises(asyncio.CancelledError, match="original cancel"):
        await DockerCodingContainer._command(("docker", "exec", "owned"))


@pytest.mark.asyncio
async def test_generated_script_cannot_execute_without_a_saved_bdd_plan():
    class Unplanned(CompletedScriptContainer):
        async def run_prompt(self):
            return {"terminal": "complete", "session_id": "ses_test"}

    browser = ReceiptBrowser([])
    runtime = CodingRuntime(
        CodingConfig.for_test(),
        browser=browser,
        store=Journal(),
        container_factory=lambda _config, root: Unplanned(root),
    )
    with pytest.raises(ValueError, match="BDD plan required"):
        await runtime.run("task-a", request(replay=False), {}, None, "lease", "node", 1)
    assert browser.limits == []


@pytest.mark.asyncio
async def test_external_block_without_page_observation_gets_bounded_repair_not_user_question():
    made = []

    class UnsupportedBlock(CompletedScriptContainer):
        async def run_prompt(self):
            return {"terminal": "complete", "session_id": "ses_test"}

        async def collect(self):
            return {"input_request": {"reason": "external_block", "question": "Maybe CAPTCHA"}}

    def factory(_config, mailbox):
        value = UnsupportedBlock(mailbox)
        made.append(value)
        return value

    browser = SessionBrowser()
    runtime = CodingRuntime(
        CodingConfig.for_test(), browser=browser, store=Journal(), container_factory=factory
    )
    result = await runtime.run("task-a", request(replay=False), {}, None, "lease", "node", 1)
    assert result.status == "failed"
    assert result.input_request is None
    assert result.terminal_evidence["failure"] == "external_block_without_page_observation"
    assert len(made[0].goals) == runtime.config.max_repairs + 1
    assert "No page observation" in made[0].goals[-1]
    assert made[0].stopped and browser.canceled


class LiveReceiptBrowser(ReceiptBrowser):
    def __init__(self):
        super().__init__([])
        self.actions = []
        self.registered = []

    async def action(self, _session, action, _key, **_kwargs):
        self.actions.append(action)
        return {
            "status": "completed",
            "action_id": "finish-a" if action["type"] == "finish" else "read-a",
            "observation": {
                "sequence": len(self.actions),
                "url": "https://example.com",
                "bounded_snapshot": "Quote 42",
                "error": None,
                "extracted": {"name": "quote", "value": "42"} if action["type"] == "read" else None,
                "result": {"outputs": [{"quote": "42"}]} if action["type"] == "finish" else None,
            },
        }

    async def request(self, method, path, body, key):
        assert (method, path) == ("POST", "/executions")
        self.registered.append(body)
        return {
            "status": "passed",
            "execution_id": "live-a",
            "bundle_id": "bundle-a",
            "result": {"outputs": [{"quote": "42"}]},
        }


class LiveReadContainer(CompletedScriptContainer):
    async def run_prompt(self):
        terminal = await super().run_prompt()
        if len(self.goals) > 1:
            ident = "read-live"
            body = {
                "id": ident,
                "kind": "browser",
                "deadline_at": time.time() + 10,
                "payload": {
                    "type": "read",
                    "step": "verify",
                    "selector": "#q",
                    "name": "quote",
                    "positive": True,
                },
            }
            path = self.root / "requests" / (ident + ".json")
            path.write_text(json.dumps(body))
            path.chmod(0o600)
            while not (self.root / "responses" / (ident + ".json")).exists():
                await asyncio.sleep(0.01)
        return terminal


@pytest.mark.asyncio
async def test_live_opencode_repairs_missing_verification_in_same_session_without_replay():
    from dataclasses import replace

    browser = LiveReceiptBrowser()
    built = []

    def factory(_config, root):
        container = LiveReadContainer(root)
        built.append(container)
        return container

    runtime = CodingRuntime(
        replace(CodingConfig.for_test(), live_browser=True),
        browser=browser,
        store=Journal(),
        container_factory=factory,
    )
    result = await runtime.run("task-a", request(replay=False), {}, None, "lease", "node", 1)
    assert result.status == "passed"
    assert result.provider_session_id == "ses_test"
    assert len(built) == 1 and len(built[0].goals) == 2
    assert "read" in built[0].goals[1]
    assert browser.limits == []
    assert [action["type"] for action in browser.actions] == ["read", "read", "finish"]
    assert browser.registered[0]["finish_action_id"] == "finish-a"
    assert browser.registered[0]["live_session_id"] == "session-a"
    assert result.terminal_evidence["result"]["outputs"] == [{"quote": "42"}]
    assert built[0].stopped and browser.canceled


@pytest.mark.asyncio
@pytest.mark.parametrize("leaves_page", [True, False])
async def test_live_completion_rechecks_only_current_page_conditions(leaves_page):
    from dataclasses import replace

    class MultiPageBrowser(LiveReceiptBrowser):
        def __init__(self):
            super().__init__()
            self.page = "https://example.com/form"

        async def action(self, _session, action, _key, **_kwargs):
            self.actions.append(action)
            if action["type"] == "click" and leaves_page:
                self.page = "https://example.com/result"
            if (
                action["type"] == "read"
                and action["name"] == "age"
                and self.page != "https://example.com/form"
            ):
                raise AssertionError("final verification reread a departed form")
            value = "30" if action["type"] == "read" and action["name"] == "age" else "42"
            return {
                "status": "completed",
                "action_id": f"receipt-{len(self.actions)}",
                "observation": {
                    "url": self.page,
                    "bounded_snapshot": "Form" if self.page.endswith("form") else "Quote 42",
                    "error": None,
                    "extracted": {"name": action["name"], "value": value}
                    if action["type"] == "read"
                    else None,
                    "result": {"outputs": [{"quote": "42"}]}
                    if action["type"] == "finish"
                    else None,
                },
            }

    class MultiPageContainer(CompletedScriptContainer):
        async def run_prompt(self):
            payloads = [
                {
                    "type": "plan",
                    "steps": [
                        {"id": "open", "keyword": "Given", "title": "Open form"},
                        {"id": "enter", "keyword": "When", "title": "Enter age"},
                        {
                            "id": "age",
                            "keyword": "Then",
                            "title": "Verify age",
                            "verification_kind": "input",
                        },
                        {"id": "go", "keyword": "When", "title": "Calculate"},
                        {"id": "quote", "keyword": "Then", "title": "Verify quote"},
                    ],
                },
                {"type": "navigate", "step": "open", "url": "https://example.com/form"},
                {"type": "fill", "step": "enter", "selector": "#age", "value": "30"},
                {
                    "type": "read",
                    "step": "age",
                    "selector": "#age",
                    "attribute": "value",
                    "name": "age",
                    "equals": "30",
                },
                {"type": "click", "step": "go", "selector": "#calculate"},
            ]
            if not leaves_page:
                payloads.append(
                    {
                        "type": "read",
                        "step": "age",
                        "selector": "#age",
                        "attribute": "value",
                        "name": "age",
                        "equals": "30",
                    }
                )
            payloads.append(
                {
                    "type": "read",
                    "step": "quote",
                    "selector": "#quote",
                    "name": "quote",
                    "equals": "42",
                }
            )
            for index, payload in enumerate(payloads):
                ident = f"browser-{index}"
                path = self.root / "requests" / f"{ident}.json"
                path.write_text(
                    json.dumps(
                        {
                            "id": ident,
                            "kind": "browser",
                            "deadline_at": time.time() + 10,
                            "payload": payload,
                        }
                    )
                )
                path.chmod(0o600)
                while not (self.root / "responses" / f"{ident}.json").exists():
                    await asyncio.sleep(0.01)
            return {"terminal": "complete", "session_id": "ses_test"}

    browser = MultiPageBrowser()
    runtime = CodingRuntime(
        replace(CodingConfig.for_test(), live_browser=True),
        browser=browser,
        store=Journal(),
        container_factory=lambda _config, root: MultiPageContainer(root),
    )
    result = await runtime.run("task-a", request(replay=False), {}, None, "lease", "node", 1)
    assert result.status == "passed"
    assert [action["name"] for action in browser.actions if action["type"] == "read"] == (
        ["age", "quote", "quote"] if leaves_page else ["age", "age", "quote", "age", "quote"]
    )


@pytest.mark.asyncio
async def test_final_finish_on_different_page_cannot_register_live_success():
    from dataclasses import replace

    class FinalPageDrift(LiveReceiptBrowser):
        async def action(self, session, action, key, **kwargs):
            receipt = await super().action(session, action, key, **kwargs)
            if action["type"] == "finish":
                receipt["observation"]["url"] = "https://example.com/other"
            return receipt

    browser = FinalPageDrift()
    runtime = CodingRuntime(
        replace(CodingConfig.for_test(), live_browser=True),
        browser=browser,
        store=Journal(),
        container_factory=lambda _config, root: LiveReadContainer(root),
    )
    with pytest.raises(ValueError, match="result evidence"):
        await runtime.run("task-a", request(replay=False), {}, None, "lease", "node", 1)
    assert browser.registered == []


@pytest.mark.asyncio
async def test_live_business_evidence_survives_missing_script_without_reexecuting():
    from dataclasses import replace

    class NoScript(LiveReadContainer):
        async def collect(self):
            return {}

    browser = LiveReceiptBrowser()
    runtime = CodingRuntime(
        replace(CodingConfig.for_test(), live_browser=True),
        browser=browser,
        store=Journal(),
        container_factory=lambda _config, root: NoScript(root),
    )
    result = await runtime.run("task-a", request(replay=False), {}, None, "lease", "node", 1)
    assert result.status == "passed"
    assert result.terminal_evidence["result"]["outputs"] == [{"quote": "42"}]
    assert result.terminal_evidence["script_status"] == "failed"
    assert browser.limits == [] and browser.registered == []
    assert [a["type"] for a in browser.actions] == ["read", "read", "finish"]


@pytest.mark.asyncio
async def test_failed_live_finish_never_becomes_business_success():
    from dataclasses import replace

    class FailedFinish(LiveReceiptBrowser):
        async def action(self, session, action, key, **kwargs):
            if action["type"] == "finish":
                return {"status": "failed", "observation": {"error": "evidence unavailable"}}
            return await super().action(session, action, key, **kwargs)

    runtime = CodingRuntime(
        replace(CodingConfig.for_test(), live_browser=True),
        browser=FailedFinish(),
        store=Journal(),
        container_factory=lambda _config, root: LiveReadContainer(root),
    )
    with pytest.raises(ValueError, match="result evidence"):
        await runtime.run("task-a", request(replay=False), {}, None, "lease", "node", 1)


@pytest.mark.asyncio
async def test_crash_receipt_stops_model_loop_without_waiting_for_another_model_turn():
    from dataclasses import replace

    built = []

    class CrashedBrowser(SessionBrowser):
        async def action(self, *_args, **_kwargs):
            return {
                "status": "completed",
                "observation": {"error": "locator.click: Target crashed", "bounded_snapshot": ""},
            }

    class CrashActionContainer(CompletedScriptContainer):
        async def run_prompt(self):
            await super().run_prompt()
            path = self.root / "requests" / "crash-action.json"
            path.write_text(
                json.dumps(
                    {
                        "id": "crash-action",
                        "kind": "browser",
                        "deadline_at": time.time() + 10,
                        "payload": {"type": "click", "step": "verify", "selector": "#go"},
                    }
                )
            )
            path.chmod(0o600)
            await asyncio.Event().wait()

    def factory(_config, root):
        item = CrashActionContainer(root)
        built.append(item)
        return item

    browser = CrashedBrowser()
    runtime = CodingRuntime(
        replace(CodingConfig.for_test(), live_browser=True),
        browser=browser,
        store=Journal(),
        container_factory=factory,
    )
    result = await asyncio.wait_for(
        runtime.run("task-a", request(replay=False), {}, None, "lease", "node", 1), timeout=2
    )
    assert result.status == "unknown"
    assert "unknown" in result.terminal_evidence["failure"]
    assert result.usage["calls"] == 0
    assert built[0].stopped and browser.canceled


def test_generation_prompt_explains_observed_refs_and_readonly_date_widgets():
    prompt = CodingRuntime._generation_prompt(
        "Select a date", "https://example.com/", "Date selected", ["https://example.com"], {}
    )
    for guidance in (
        "aria/Label",
        "aria-ref=Label",
        "invalid",
        "existing element",
        "text search",
        "receipts",
        "readonly date",
        "year",
        "month",
        "day",
        "sibling popup",
        "options_total",
        "options_truncated",
    ):
        assert guidance in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("complete_after_rejection", [False, True])
async def test_repeated_missing_target_stops_model_forwarding_repairs_and_bundle_creation(
    monkeypatch, complete_after_rejection
):
    from tap.modules.workflows.adapters.coding_mailbox import CodingMailbox, MailItem

    missing_error = (
        "no target matched before write; take a fresh snapshot and use its aria-ref, "
        "or wait explicitly for an observed stable selector"
    )
    made = []
    forwarded = []
    rejected_model = []

    class MissingTargetBrowser(SessionBrowser):
        def __init__(self):
            super().__init__()
            self.actions = []

        async def action(self, _session, action, _key, **_kwargs):
            self.actions.append(action)
            return {
                "status": "failed",
                "action_id": "missing-target",
                "observation": {
                    "sequence": len(self.actions),
                    "write_started": False,
                    "error": missing_error,
                    "url": "https://example.com/",
                    "bounded_snapshot": "Age form",
                },
            }

        async def bundle(self, *_args):
            raise AssertionError("stalled browser task must not produce a bundle")

    class RepeatingContainer(CompletedScriptContainer):
        def __init__(self, root):
            super().__init__(root)
            self.collected = False
            self.prompt_canceled = False

        async def run_prompt(self):
            try:
                await super().run_prompt()
                for number, operation in enumerate(("click", "fill", "press")):
                    ident = f"repeat-{number}"
                    path = self.root / "requests" / (ident + ".json")
                    path.write_text(
                        json.dumps(
                            {
                                "id": ident,
                                "kind": "browser",
                                "deadline_at": time.time() + 10,
                                "payload": {
                                    "type": operation,
                                    "selector": "#field-age .ant-select-selection-item",
                                    "step": "verify",
                                    "value": str(30 + number),
                                    "key": "Enter",
                                },
                            }
                        )
                    )
                    path.chmod(0o600)
                    while not (self.root / "responses" / (ident + ".json")).exists():
                        await asyncio.sleep(0.001)
                if complete_after_rejection:
                    return {"terminal": "complete", "session_id": "ses_test"}
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.prompt_canceled = True
                raise

        async def collect(self):
            self.collected = True
            return {"input_request": {"reason": "external_block", "question": "Maybe blocked"}}

    def factory(_config, root):
        container = RepeatingContainer(root)
        made.append(container)
        return container

    def forward(_gate, _body, _claimed, **_kwargs):
        forwarded.append(True)
        return {"status": 200, "contentType": "application/json", "bodyBase64": "e30="}

    original_serve = CodingMailbox.serve_once

    async def serve_with_queued_model(mailbox, handler):
        handled = await original_serve(mailbox, handler)
        if (mailbox.root / "responses" / "repeat-2.json").exists() and not rejected_model:
            # Exercise the model handler itself while the stop marker is set,
            # before the outer prompt loop can cancel a queued request.
            response = await handler(
                MailItem(
                    "queued-model",
                    "model",
                    time.time() + 10,
                    {
                        "bodyBase64": base64.b64encode(
                            json.dumps({"model": "qwen-plus", "messages": []}).encode()
                        ).decode()
                    },
                )
            )
            rejected_model.append(response)
            assert response["status"] == 409
            if complete_after_rejection:
                await asyncio.sleep(0.01)
        return handled

    monkeypatch.setattr(CodingMailbox, "serve_once", serve_with_queued_model)
    monkeypatch.setattr(CodingRuntime, "_forward_gate", staticmethod(forward))
    browser = MissingTargetBrowser()
    journal = Journal()
    runtime = CodingRuntime(
        replace(CodingConfig.for_test(), live_browser=True),
        browser=browser,
        store=journal,
        container_factory=factory,
    )
    result = await asyncio.wait_for(
        runtime.run("task-a", request(replay=False), {}, None, "lease", "node", 1), timeout=2
    )
    assert result.status == "failed"
    assert result.terminal_evidence["failure"] == "repeated_missing_target"
    assert result.input_request is None
    assert result.bundle_ref is None
    assert result.usage["calls"] == 0
    assert len(browser.actions) == 1
    assert not forwarded
    assert len(rejected_model) == 1
    assert len(made[0].goals) == 1
    assert not made[0].collected
    assert made[0].prompt_canceled is not complete_after_rejection
    assert made[0].stopped and browser.canceled
