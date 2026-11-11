"""Continuity gates suspend work without terminating or recreating sessions."""

import asyncio
import time

import pytest

from tap.modules.workflows.adapters import opencode_runtime


@pytest.mark.asyncio
async def test_control_wait_preserves_epoch_and_rejects_late_actions():
    state = {"control_state": "agent_active", "control_epoch": 0}

    async def read():
        return dict(state)

    gate_type = getattr(opencode_runtime, "BrowserControlWait", None)
    assert gate_type is not None, "control wait protocol is missing"
    gate = gate_type(read, deadline=time.monotonic() + 2)
    assert await gate.before_model() == 0
    state.update(control_state="human_active", control_epoch=1)
    waiting = asyncio.create_task(gate.before_action())
    await asyncio.sleep(0.12)
    assert not waiting.done()
    state.update(control_state="agent_active", control_epoch=2)
    assert await waiting == (2, False)
    assert await gate.before_model() == 2
    assert await gate.before_action() == (2, True)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["cancel", "deadline", "blocked", "closed"])
async def test_control_wait_remains_interruptible(ending):
    state = {"control_state": "human_active", "control_epoch": 1}

    async def read():
        return dict(state)

    gate_type = getattr(opencode_runtime, "BrowserControlWait", None)
    assert gate_type is not None, "control wait protocol is missing"
    gate = gate_type(read, deadline=time.monotonic() + (0.08 if ending == "deadline" else 2))
    waiting = asyncio.create_task(gate.before_model())
    await asyncio.sleep(0.02)
    if ending == "cancel":
        waiting.cancel()
        expected = asyncio.CancelledError
    elif ending == "deadline":
        expected = TimeoutError
    else:
        state["control_state"] = ending
        expected = RuntimeError
    with pytest.raises(expected):
        await asyncio.wait_for(waiting, 0.3)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["continue", "cancel", "deadline"])
async def test_pinned_opencode_continues_live_tool_after_human_wait(tmp_path, ending):
    """Actual pinned engine, synthetic transport and browser ledger, no paid model.

    Removing the wait or epoch comparison permits the stale click and fails this
    test. This is a protocol probe, not real Chromium/website acceptance.
    """
    import base64
    import json
    import os
    import shutil
    from pathlib import Path

    from tap.modules.workflows.adapters.coding_mailbox import CodingMailbox
    from tap.modules.workflows.adapters.local_opencode import LocalCodingContainer
    from tap.modules.workflows.adapters.opencode_profiles import build_config

    binary_value = os.environ.get("TAP_TEST_OPENCODE_BINARY")
    if not binary_value:
        pytest.skip("set TAP_TEST_OPENCODE_BINARY for no-paid pinned engine probe")
    binary = Path(binary_value).resolve(strict=True)
    root = Path(__file__).resolve().parents[4]
    mailbox = CodingMailbox.create(tmp_path / "mailbox")
    adapter = LocalCodingContainer(
        opencode_runtime.CodingConfig.for_test(),
        mailbox.root,
        opencode_binary=binary,
        node_binary=Path(shutil.which("node")),
        client_path=root / "deploy/browser/opencode-client.mjs",
    )
    config = build_config(
        {
            "model_alias": "synthetic",
            "requested_ids": [],
            "candidates": [],
            "root": {
                "agent_id": "orchestrator",
                "revision_id": "synthetic",
                "description": "synthetic",
                "instruction": "Use only tap_browser.",
            },
        },
        "http://127.0.0.1:39001",
        "synthetic-local-only",
        output_tokens=2048,
        input_tokens=100000,
    )
    state = {"control_state": "agent_active", "control_epoch": 0}
    browser_session = "synthetic-browser-session-one"
    sequence = 1
    writes = []
    requests = []
    human_started = asyncio.Event()
    stale_seen = asyncio.Event()

    async def read_control():
        return dict(state)

    gate = opencode_runtime.BrowserControlWait(read_control, deadline=time.monotonic() + 35)

    def model_response(action=None):
        delta = {"role": "assistant", "content": "Done"}
        if action is not None:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"call_{len(requests)}",
                        "type": "function",
                        "function": {
                            "name": "tap_browser",
                            "arguments": json.dumps({"action": action}),
                        },
                    }
                ],
            }
        data = {
            "id": "synthetic",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "synthetic",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        final = {
            **data,
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": "tool_calls" if action else "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        raw = (
            "data: " + json.dumps(data) + "\n\ndata: " + json.dumps(final) + "\n\ndata: [DONE]\n\n"
        )
        return {
            "status": 200,
            "contentType": "text/event-stream",
            "bodyBase64": base64.b64encode(raw.encode()).decode(),
        }

    async def handle(item):
        nonlocal sequence
        if item.kind == "model":

            async def reserve(epoch, _request_id):
                if state != {"control_state": "agent_active", "control_epoch": epoch}:
                    return {"status": "rejected"}
                return {"status": "reserved"}

            async def settle(_request_id, outcome):
                assert outcome == "completed"
                if state["control_state"] == "takeover_pending":
                    state.update(control_state="human_active", control_epoch=1)
                    human_started.set()

            async def forward(_epoch):
                body = json.loads(base64.b64decode(item.payload["bodyBase64"]))
                # Auxiliary title requests never acquire browser access.
                if not any(
                    t.get("function", {}).get("name") == "tap_browser"
                    for t in body.get("tools", [])
                ):
                    return model_response()
                requests.append((state["control_state"], item.payload.get("claimedSessionId")))
                if len(requests) == 1:
                    state["control_state"] = "takeover_pending"
                    return model_response({"type": "click", "step": "one", "selector": "#submit"})
                if len(requests) == 2:
                    return model_response({"type": "click", "step": "one", "selector": "#submit"})
                return model_response()

            return await gate.forward(item.identifier, forward, reserve, settle)
        epoch, current = await gate.before_action()
        if not current:
            sequence += 1
            stale_seen.set()
            return {
                "error": "stale epoch; action not dispatched",
                "browser_session_id": browser_session,
                "observation_sequence": sequence,
                "bounded_snapshot": "Human set value=synthetic.",
            }
        writes.append((browser_session, epoch, sequence))
        return {
            "status": "completed",
            "browser_session_id": browser_session,
            "observation_sequence": sequence + 1,
        }

    running = None
    serving = None
    try:
        await adapter.start(config, "synthetic-local-only", 40)
        await adapter.set_goal("Use tap_browser to click the synthetic submit button.")
        running = asyncio.create_task(adapter.run_prompt())

        async def serve():
            while not running.done():
                if not await mailbox.serve_once(handle):
                    await asyncio.sleep(0.01)

        serving = asyncio.create_task(serve())
        await asyncio.wait_for(human_started.wait(), 20)
        before = json.loads((adapter.workspace / "session.json").read_text())["session_id"]
        count = len(requests)
        await asyncio.sleep(0.4)
        assert len(requests) == count == 1
        assert not running.done()
        assert writes == []
        if ending != "continue":
            started = time.monotonic()
            if ending == "cancel":
                serving.cancel()
                expected = asyncio.CancelledError
            else:
                gate.deadline = time.monotonic() + 0.08
                expected = TimeoutError
            with pytest.raises(expected):
                await asyncio.wait_for(serving, 0.5)
            assert time.monotonic() - started < 0.5
            assert writes == []
            assert (
                json.loads((adapter.workspace / "session.json").read_text())["session_id"] == before
            )
            return
        state.update(control_state="agent_active", control_epoch=2)
        result = await asyncio.wait_for(running, 15)
        await serving
        assert result["terminal"] == "complete"
        assert result["session_id"] == before
        assert stale_seen.is_set()
        assert writes == [(browser_session, 2, 2)]
        assert all(kind == "agent_active" for kind, _ in requests)
        assert {session for _, session in requests} == {before}
    finally:
        for pending in (serving, running):
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        assert await adapter.stop()
        mailbox.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["cancel", "deadline"])
async def test_runtime_human_wait_keeps_resources_until_stop(ending, monkeypatch):
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    from tests.unit.test_opencode_runtime import Journal, MailContainer, SessionBrowser, request

    class ControlledBrowser(SessionBrowser):
        entered = asyncio.Event()

        async def session(self, *args, **kwargs):
            assert kwargs["control_version"] == "browser-control-v1"
            return await super().session(*args)

        async def get_control(self, _session_id):
            self.entered.set()
            return {"control_state": "human_active", "control_epoch": 1}

    browser = ControlledBrowser()
    containers = []

    def factory(_config, root):
        container = MailContainer(root)
        containers.append(container)
        return container

    journal = Journal()
    runtime = opencode_runtime.CodingRuntime(
        replace(opencode_runtime.CodingConfig.for_test(), control_enabled=True),
        browser=browser,
        store=journal,
        container_factory=factory,
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("model forwarding during human control")

    monkeypatch.setattr(runtime, "_forward_gate", forbidden)
    stop = False

    async def stop_requested():
        return stop

    frozen = request(replay=False)
    frozen["deadline_at"] = (datetime.now(UTC) + timedelta(seconds=2)).isoformat()
    running = asyncio.create_task(
        runtime.run(
            "task-a",
            frozen,
            {},
            None,
            "lease",
            "node",
            1,
            stop_requested=stop_requested,
        )
    )
    await asyncio.wait_for(browser.entered.wait(), 1)
    await asyncio.sleep(0.1)
    assert not running.done()
    assert not containers[0].stopped
    assert not browser.canceled
    if ending == "cancel":
        stop = True
    if ending == "cancel":
        assert (await asyncio.wait_for(running, 3)).status == "canceled"
    else:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(running, 3)
    assert containers[0].stopped
    assert browser.canceled
    events = [payload for kind, payload in journal.events if kind == "browser.session_started"]
    assert len(events) == 1
    assert events[0]["session_id"] == "session-a"
    assert events[0]["control_version"] == "browser-control-v1"


@pytest.mark.asyncio
async def test_model_reservation_denial_waits_without_forwarding():
    state = {"control_state": "agent_active", "control_epoch": 0}
    reserved = asyncio.Event()
    calls = []
    settled = []

    async def read():
        return dict(state)

    async def reserve(epoch, request_id):
        if epoch == 0:
            state.update(control_state="human_active", control_epoch=1)
            reserved.set()
            return {"status": "rejected"}
        return {"status": "reserved"}

    async def settle(request_id, outcome):
        settled.append((request_id, outcome))

    async def work(epoch):
        calls.append(epoch)
        return "response"

    gate = opencode_runtime.BrowserControlWait(read, deadline=time.monotonic() + 2)
    assert hasattr(gate, "forward"), "atomic model forwarding reservation is missing"
    task = asyncio.create_task(gate.forward("request-a", work, reserve, settle))
    await reserved.wait()
    await asyncio.sleep(0.08)
    assert calls == []
    state.update(control_state="agent_active", control_epoch=2)
    assert await task == "response"
    assert calls == [2]
    assert settled == [("request-a", "completed")]


@pytest.mark.asyncio
async def test_canceled_model_does_not_settle_until_forwarding_drains():
    entered = asyncio.Event()
    release = asyncio.Event()
    settled = []

    async def read():
        return {"control_state": "agent_active", "control_epoch": 0}

    async def reserve(_epoch, _request_id):
        return {"status": "reserved"}

    async def settle(_request_id, outcome):
        settled.append(outcome)

    async def work(_epoch):
        entered.set()
        await release.wait()
        return "response"

    gate = opencode_runtime.BrowserControlWait(read, deadline=time.monotonic() + 2)
    assert hasattr(gate, "forward"), "atomic model forwarding reservation is missing"
    task = asyncio.create_task(gate.forward("request-a", work, reserve, settle))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0.08)
    assert not task.done()
    assert settled == []
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert settled == ["completed"]


@pytest.mark.asyncio
async def test_auxiliary_model_cannot_relabel_a_stale_browser_tool():
    state = {"control_state": "agent_active", "control_epoch": 0}

    async def read():
        return dict(state)

    async def reserve(_epoch, _request_id):
        return {"status": "reserved"}

    async def settle(_request_id, _outcome):
        pass

    async def work(_epoch):
        return "title"

    gate = opencode_runtime.BrowserControlWait(read, deadline=time.monotonic() + 2)
    await gate.before_model()
    state["control_epoch"] = 2
    await gate.forward("title-a", work, reserve, settle, track_epoch=False)
    assert await gate.before_action() == (2, False)
