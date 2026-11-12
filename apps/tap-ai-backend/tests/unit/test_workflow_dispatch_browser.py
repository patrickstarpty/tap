"""Frozen browser tasks use the coding runtime before any native graph model."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tap.modules.workflows.adapters.opencode_runtime import CodingConfig, CodingRuntime
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
    ("session", "confirmed"),
    [
        ({"status": "canceled", "stop_receipt": {"stopped": True}}, True),
        ({"status": "canceled", "stop_receipt": {"stopped": False}, "stop_confirmed": True}, False),
        ({"status": "canceled"}, False),
        ({"status": "unknown", "stop_receipt": {"stopped": False}}, False),
    ],
)
async def test_early_browser_session_stop_proof_reaches_dispatch_only_when_authoritative(
    session, confirmed
):
    class EarlySessionBrowser:
        async def session(self, *_args):
            return session

    request = {
        "kind": "browser",
        "goal": "Read page",
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
            "expected_result": "Read page",
            "input_data": {},
            "input_sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
        },
        "budget_limit": {"model_calls": 3, "cost_micros": 100000},
        "deadline_at": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
    }
    selected, _state, store, _ = runtime(CodingResult("failed"))
    selected._coding = CodingRuntime(
        CodingConfig.for_test(),
        browser=EarlySessionBrowser(),
        store=store,
        container_factory=lambda *_: (_ for _ in ()).throw(AssertionError("container started")),
    )
    status = await selected._run_node(
        SimpleNamespace(),
        "task-a",
        "lease-a",
        "node-a",
        request,
        {"events": (), "nodes": [], "budget_used": {}},
        root=True,
    )
    assert status == "stopped"
    assert store.events[0][0] == "browser.coding_result"
    assert [kind for kind, _ in store.events].count("browser.stop_confirmed") == int(confirmed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "category", "summary"),
    [
        (
            {"category": "budget_cost", "tool": "bash", "summary": "sk-secret"},
            "budget_cost",
            "Task stopped: budget_cost",
        ),
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


@pytest.mark.asyncio
async def test_automatic_route_freezes_intent_before_coding_and_uses_updated_budget(monkeypatch):
    from tap.modules.workflows.application.routing import RouteDecision
    from tap.modules.workflows.domain.browser_intent import effective_request

    selected, state, store, coding = runtime(
        CodingResult("passed", terminal_evidence={"result": {"outputs": [{"value": "42"}]}})
    )
    coding.config = SimpleNamespace(model_alias="browser-model")
    admitted = {
        "goal": "Read https://example.test/quote",
        "model_alias": "chat-model",
        "requested_ids": [],
    }
    snapshot = {
        "events": (),
        "nodes": [{"node_id": "node-a", "role": "orchestrator"}],
        "budget_used": {"model_calls": 0},
    }

    async def lineage(*args):
        return "lineage", 1

    async def current(*args):
        return {
            **snapshot,
            "budget_used": {"model_calls": 1},
            "events": tuple(
                SimpleNamespace(kind=kind, payload=payload) for kind, payload in store.events
            ),
        }

    class Graph:
        def __init__(self, gateway, **kwargs):
            assert kwargs["browser_available"]
            self.commit = kwargs["route_commit"]

        async def run(self, *args, **kwargs):
            assert kwargs["requested_ids"] == ()
            await self.commit(
                RouteDecision(
                    "browser", "actual page", 1.0, ("browser",), "https://example.test/quote"
                )
            )
            return {"result": {"outcome": "browser_handoff"}}

    store.graph_lineage = lineage
    store.snapshot = current
    selected._action_gateway_factory = lambda *args: object()
    selected._checkpointer_factory = lambda *args: object()
    monkeypatch.setattr("tap.modules.workflows.application.dispatch.NativeWorkflowGraph", Graph)
    status = await selected._run_node(
        SimpleNamespace(), "task-a", "lease-a", "node-a", admitted, snapshot, root=True
    )
    assert status == "finished"
    assert "kind" not in admitted
    args, _ = coding.calls[0]
    routed = args[1]
    assert args[2]["budget_used"]["model_calls"] == 1
    assert routed["model_alias"] == "browser-model"
    assert routed["browser"]["input_data"] == {"goal": admitted["goal"]}
    assert routed["browser"]["allowed_origins"] == ["https://example.test"]
    assert store.events[0][0] == "task.route_decided"
    restored = effective_request(
        admitted, (SimpleNamespace(kind=store.events[0][0], payload=store.events[0][1]),)
    )
    assert restored == routed


@pytest.mark.asyncio
@pytest.mark.parametrize("result_status", ["passed", "failed", "unknown", "canceled", "blocked"])
@pytest.mark.parametrize("nested", [False, True])
async def test_terminal_diagnostics_are_durable_and_resanitized(result_status, nested):
    diagnostics = [
        {"tool": "read", "status": "completed", "category": "none", "arguments": "SECRET"},
        {"tool": "SECRET", "status": "SECRET", "category": "SECRET", "sequence": "SECRET"},
    ] * 20
    evidence = (
        {"model_terminal": {"diagnostics": diagnostics}} if nested else {"diagnostics": diagnostics}
    )
    selected, _, store, _ = runtime(CodingResult(result_status, terminal_evidence=evidence))
    await selected._run_node(
        SimpleNamespace(),
        "task-a",
        "lease-a",
        "node-a",
        {"kind": "browser", "goal": "Read page", "browser": {}},
        {"events": (), "nodes": []},
        root=True,
    )
    payload = next(payload for kind, payload in store.events if kind == "browser.coding_result")
    assert payload["diagnostics_available"] is True
    assert len(payload["diagnostics"]) == 32
    assert payload["diagnostics"][0] == {
        "sequence": 1,
        "tool": "read",
        "status": "completed",
        "category": "none",
    }
    assert payload["diagnostics"][1] == {
        "sequence": 2,
        "tool": "other",
        "status": "pending",
        "category": "none",
    }
    assert "SECRET" not in json.dumps(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence", [{}, {"diagnostics": None}, {"diagnostics": [], "diagnostics_unavailable": True}]
)
async def test_absent_terminal_diagnostics_are_not_invented(evidence):
    selected, _, store, _ = runtime(CodingResult("unknown", terminal_evidence=evidence))
    await selected._run_node(
        SimpleNamespace(),
        "task-a",
        "lease-a",
        "node-a",
        {"kind": "browser", "goal": "Read page", "browser": {}},
        {"events": (), "nodes": []},
        root=True,
    )
    payload = next(payload for kind, payload in store.events if kind == "browser.coding_result")
    assert payload["diagnostics_available"] is False
    assert payload["diagnostics"] == []


def test_public_diagnostic_event_resanitizes_and_preserves_kind():
    from tap.modules.workflows.application.queries import _event_texts, project_event

    event = SimpleNamespace(
        event_id="e",
        task_id="t",
        sequence=1,
        node_id="n",
        attempt=2,
        kind="browser.tool_diagnostics",
        occurred_at=datetime.now(UTC),
        payload={
            "schema_version": "SECRET",
            "diagnostics_available": True,
            "raw": "SECRET",
            "selector": "SECRET",
            "input": "SECRET",
            "error": "SECRET",
            "diagnostics": [
                {"tool": "read", "status": "completed", "category": "none", "path": "SECRET"}
            ],
        },
    )
    assert _event_texts(event) == ()
    projected = project_event(event, redact=lambda value: value)
    assert projected["kind"] == "browser.tool_diagnostics"
    assert projected["attempt"] == 2
    assert projected["payload"]["diagnostics"][0]["tool"] == "read"
    assert "SECRET" not in json.dumps(projected)
