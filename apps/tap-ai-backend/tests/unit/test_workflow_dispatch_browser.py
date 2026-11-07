"""Frozen browser tasks use the coding runtime before any native graph model."""

import json
from types import SimpleNamespace

import pytest

from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime
from tap.modules.workflows.ports.coding import CodingResult


class State:
    def __init__(self):
        self.calls = []

    async def start_node(self, *args):
        self.calls.append(("start", args[-1]))
        return 1

    async def finish_node(self, *args):
        self.calls.append(("finish_node", args[-2], args[-1]))

    async def finish_task(self, *args):
        self.calls.append(("finish_task", args[-2], args[-1]))

    async def await_input(self, *args):
        self.calls.append(("await_input", args[-1]))

    async def fail_node(self, *args):
        self.calls.append(("fail_node", args[-1]))

    async def fail_task(self, *args):
        self.calls.append(("fail_task", args[-1]))


class Store:
    def __init__(self):
        self.events = []

    async def append_event(self, *args):
        self.events.append((args[-2], args[-1]))

    async def active_lease(self, *args):
        return True

    async def pending_commands(self, *args):
        return ()

    async def graph_lineage(self, *args):
        raise AssertionError("browser task entered native graph")


class Coding:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def run(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def runtime(result):
    state, store, coding = State(), Store(), Coding(result)
    selected = NativeWorkflowRuntime(
        store,
        state,
        object(),
        checkpointer_factory=lambda *_: (_ for _ in ()).throw(AssertionError("native graph")),
        action_gateway_factory=lambda *_: (_ for _ in ()).throw(AssertionError("native model")),
        owner="test",
        coding=coding,
        standalone_authorize=lambda *_: approved(),
    )
    return selected, state, store, coding


async def approved():
    return True


@pytest.mark.asyncio
async def test_browser_pass_uses_coding_receipt_and_records_task_node_event():
    result = CodingResult(
        "passed",
        "ses_provider",
        "bundle-a",
        "execution-a",
        {"calls": 1},
        {"status": "passed", "result": {"outputs": [{"value": "42"}]}},
    )
    selected, state, store, coding = runtime(result)
    request = {"kind": "browser", "goal": "Read page", "browser": {}}
    status = await selected._run_node(
        SimpleNamespace(),
        "task-a",
        "lease-a",
        "node-a",
        request,
        {"events": (), "nodes": []},
        root=True,
    )
    assert status == "finished"
    assert len(coding.calls) == 1
    assert any(item[0] == "browser.coding_result" for item in store.events)
    answer = next(item[1] for item in state.calls if item[0] == "finish_task")
    assert json.loads(answer)["outputs"] == [{"value": "42"}]
    assert not any(item[0] == "fail_task" for item in state.calls)


@pytest.mark.asyncio
async def test_browser_blocked_maps_to_await_input_without_success():
    result = CodingResult(
        "blocked", input_request={"reason": "missing_input", "question": "Which account?"}
    )
    selected, state, store, _ = runtime(result)
    status = await selected._run_node(
        SimpleNamespace(),
        "task-a",
        "lease-a",
        "node-a",
        {"kind": "browser", "goal": "Open account", "browser": {}},
        {"events": (), "nodes": []},
        root=True,
    )
    assert status == "awaiting_input"
    assert ("await_input", "Which account?") in state.calls
    assert not any(item[0] == "finish_task" for item in state.calls)
    assert store.events[0][1]["status"] == "blocked"


@pytest.mark.asyncio
async def test_browser_outputs_use_compact_utf8_size_boundary():
    value = {"value": "中" * 6000}
    selected, state, _store, _ = runtime(
        CodingResult("passed", terminal_evidence={"result": {"outputs": [value]}})
    )
    status = await selected._run_node(
        SimpleNamespace(),
        "task-a",
        "lease-a",
        "node-a",
        {"kind": "browser", "goal": "Read page", "browser": {}},
        {"events": (), "nodes": []},
        root=True,
    )
    assert status == "finished"
    assert any(item[0] == "finish_task" for item in state.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("outputs", [[{"value": "x" * 40000}], {"invalid": "shape"}])
async def test_invalid_business_outputs_fail_explicitly(outputs):
    selected, state, store, _ = runtime(
        CodingResult("passed", terminal_evidence={"result": {"outputs": outputs}})
    )
    status = await selected._run_node(
        SimpleNamespace(),
        "task-a",
        "lease-a",
        "node-a",
        {"kind": "browser", "goal": "Read page", "browser": {}},
        {"events": (), "nodes": []},
        root=True,
    )
    assert status == "failed"
    assert store.events[0][1]["status"] == "failed"
    assert any(item[0] == "fail_task" for item in state.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [True, False])
async def test_browser_cancel_only_persists_current_stop_proof_when_confirmed(confirmed):
    result = CodingResult(
        "canceled",
        terminal_evidence={"stop_confirmed": confirmed},
    )
    selected, _state, store, _coding = runtime(result)
    status = await selected._run_node(
        SimpleNamespace(),
        "task-a",
        "lease-a",
        "node-a",
        {"kind": "browser", "goal": "Read page", "browser": {}},
        {"events": (), "nodes": []},
        root=True,
    )
    assert status == "stopped"
    proof = [payload for kind, payload in store.events if kind == "browser.stop_confirmed"]
    assert len(proof) == int(confirmed)
    if proof:
        assert proof[0] == {
            "node_id": "node-a",
            "attempt": 1,
            "execution_token": "lease-a",
            "stop_confirmed": True,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "category", "summary"),
    [
        (
            {
                "category": "missing_frozen_input",
                "tool": "bash",
                "summary": "sk-secret raw tool output",
            },
            "missing_frozen_input",
            "Frozen browser inputs are unavailable in the coding container.",
        ),
        (
            {"category": "sk-secret", "tool": "bash secret", "summary": "sk-secret"},
            "unknown",
            "OpenCode failed without classified error.",
        ),
        (
            {"category": ["sk-secret"], "tool": ["sk-secret"], "summary": "sk-secret"},
            "unknown",
            "OpenCode failed without classified error.",
        ),
    ],
)
async def test_browser_failed_terminal_persists_only_bounded_safe_diagnostic(
    failure, category, summary
):
    selected, state, store, _ = runtime(
        CodingResult(
            "failed",
            terminal_evidence={"model_terminal": {"terminal": "failed", "failure": failure}},
        )
    )
    status = await selected._run_node(
        SimpleNamespace(),
        "task-a",
        "lease-a",
        "node-a",
        {"kind": "browser", "goal": "Read page", "browser": {}},
        {"events": (), "nodes": []},
        root=True,
    )
    assert status == "failed"
    diagnostic = store.events[0][1]["coding_failure"]
    assert diagnostic == {
        "category": category,
        "tool": "bash" if category != "unknown" else None,
        "summary": summary,
    }
    assert ("fail_task", f"OpenCode failed: {summary}") in state.calls
    assert "sk-secret" not in json.dumps(store.events)
    assert "sk-secret" not in json.dumps(state.calls)
