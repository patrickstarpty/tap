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
