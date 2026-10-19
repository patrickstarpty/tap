import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from tap.modules.ai.application.chat_graph import (
    CHAT_GRAPH_VERSION,
    ChatGraph,
    ChatGraphNodes,
    ChatGraphState,
)
from tap.modules.ai.application.interaction_graph import GraphVersionConflict
from tap.modules.ai.domain.graph_runs import GraphCheckpointRetryable
from tap.platform.budget import TurnBudget, current_meter


async def _unreachable_tools(_state: ChatGraphState) -> dict[str, object]:
    """Default `tools` double for tests that never route `model -> tools`
    (their stub `model` never returns a `"tool_calls"` step)."""
    raise AssertionError("tools must not run unless model returns a tool_calls step")


def _always_authorized() -> Callable[[], Awaitable[bool]]:
    async def authorize() -> bool:
        return True

    return authorize


def _recording_nodes(calls: list[str]) -> ChatGraphNodes:
    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        return {}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        calls.append("insights_explain")
        return {"answer_plan": {"kind": "insights"}}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        calls.append("seed_search")
        return {"step": {"name": "seed_search"}}

    async def model(_state: ChatGraphState) -> dict[str, object]:
        calls.append("model")
        return {"step": {"name": "model"}}

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        calls.append("verify")
        return {"step": {"name": "verify"}}

    async def finalize(_state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        return {"result": {"answer": "ok"}}

    return ChatGraphNodes(
        guard_in=guard_in,
        insights_explain=insights_explain,
        seed_search=seed_search,
        model=model,
        tools=_unreachable_tools,
        verify=verify,
        finalize=finalize,
    )


@pytest.mark.asyncio
async def test_chat_route_runs_nodes_in_order() -> None:
    calls: list[str] = []
    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=_recording_nodes(calls),
        authorize=_always_authorized(),
    )

    await graph.start(run_id="run-chat", payload={"query": "hi"}, route="chat")

    assert calls == ["guard_in", "seed_search", "model", "verify", "finalize"]


@pytest.mark.asyncio
async def test_insights_route_skips_answer_nodes() -> None:
    calls: list[str] = []
    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=_recording_nodes(calls),
        authorize=_always_authorized(),
    )

    await graph.start(run_id="run-insights", payload={"query": "hi"}, route="insights")

    assert calls == ["guard_in", "insights_explain", "finalize"]


@pytest.mark.asyncio
async def test_out_of_scope_route_goes_straight_to_finalize() -> None:
    calls: list[str] = []

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        return {"route": "out_of_scope"}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the out_of_scope route")

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("seed_search must not run on the out_of_scope route")

    async def model(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("model must not run on the out_of_scope route")

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("verify must not run on the out_of_scope route")

    async def finalize(_state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        return {"result": {"answer": "refused"}}

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
    )

    await graph.start(run_id="run-out-of-scope", payload={}, route="chat")

    assert calls == ["guard_in", "finalize"]
    assert calls.count("seed_search") == 0
    assert calls.count("model") == 0
    assert calls.count("verify") == 0
    assert calls.count("insights_explain") == 0
    assert calls.count("finalize") == 1


@pytest.mark.asyncio
async def test_guard_in_failure_on_insights_route_skips_insights_explain() -> None:
    calls: list[str] = []
    captured: dict[str, object] = {}

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        raise RuntimeError("guard rejected the request")

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run after a guard_in failure")

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("seed_search must not run after a guard_in failure")

    async def model(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("model must not run after a guard_in failure")

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("verify must not run after a guard_in failure")

    async def finalize(state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        captured["failure"] = state.get("failure")
        return {"result": {"answer": "degraded"}}

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
    )

    await graph.start(run_id="run-insights-guard-failure", payload={}, route="insights")

    assert calls == ["guard_in", "finalize"]
    assert captured["failure"] == {"node": "guard_in", "error": "RuntimeError"}


@pytest.mark.asyncio
async def test_node_failure_routes_to_finalize_with_failure() -> None:
    calls: list[str] = []

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        calls.append("seed_search")
        return {}

    async def model(_state: ChatGraphState) -> dict[str, object]:
        calls.append("model")
        raise RuntimeError("model exploded")

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        calls.append("verify")
        return {}

    captured: dict[str, object] = {}

    async def finalize(state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        captured["failure"] = state.get("failure")
        return {"result": {"answer": "degraded"}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
    )

    await graph.start(run_id="run-failure", payload={}, route="chat")

    assert calls == ["guard_in", "seed_search", "model", "finalize"]
    assert captured["failure"] == {"node": "model", "error": "RuntimeError"}


@pytest.mark.asyncio
async def test_failed_model_node_resumes_without_repeating_seed_search() -> None:
    calls: list[str] = []
    failed = False

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        calls.append("seed_search")
        return {}

    async def model(_state: ChatGraphState) -> dict[str, object]:
        nonlocal failed
        calls.append("model")
        if not failed:
            failed = True
            raise GraphCheckpointRetryable("connection lost")
        return {}

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        calls.append("verify")
        return {}

    async def finalize(_state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        return {"result": {"answer": "ok"}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
        propagate=(GraphCheckpointRetryable,),
    )

    with pytest.raises(GraphCheckpointRetryable):
        await graph.start(run_id="run-resume", payload={}, route="chat")

    await graph.resume(run_id="run-resume")

    assert calls.count("seed_search") == 1
    assert calls.count("model") == 2


@pytest.mark.asyncio
async def test_retryable_node_error_propagates_and_resume_skips_completed_nodes() -> None:
    calls: list[str] = []
    failed = False

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        calls.append("seed_search")
        return {}

    async def model(_state: ChatGraphState) -> dict[str, object]:
        nonlocal failed
        calls.append("model")
        if not failed:
            failed = True
            raise ConnectionError("connection lost")
        return {}

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        calls.append("verify")
        return {}

    async def finalize(state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        assert state.get("failure") is None
        return {"result": {"answer": "ok"}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
        retryable=lambda exc: isinstance(exc, ConnectionError),
    )

    with pytest.raises(ConnectionError):
        await graph.start(run_id="run-retryable", payload={}, route="chat")

    await graph.resume(run_id="run-retryable")

    assert calls.count("seed_search") == 1
    assert calls.count("model") == 2
    assert calls.count("finalize") == 1


@pytest.mark.asyncio
async def test_non_retryable_node_error_still_routes_to_finalize() -> None:
    calls: list[str] = []

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        calls.append("seed_search")
        return {}

    async def model(_state: ChatGraphState) -> dict[str, object]:
        calls.append("model")
        raise ConnectionError("connection lost")

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("verify must not run after a model failure")

    captured: dict[str, object] = {}

    async def finalize(state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        captured["failure"] = state.get("failure")
        return {"result": {"answer": "degraded"}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
        retryable=lambda _exc: False,
    )

    state = await graph.start(run_id="run-non-retryable", payload={}, route="chat")

    assert calls == ["guard_in", "seed_search", "model", "finalize"]
    assert captured["failure"] == {"node": "model", "error": "ConnectionError"}
    assert state["failure"] == {"node": "model", "error": "ConnectionError"}


@pytest.mark.asyncio
async def test_resume_rejects_other_graph_version() -> None:
    from langchain_core.runnables import RunnableLambda
    from langgraph.graph import END, START, StateGraph

    checkpointer = InMemorySaver()

    async def seed(_state: ChatGraphState) -> dict[str, object]:
        return {}

    legacy_builder = StateGraph(ChatGraphState)
    legacy_builder.add_node("seed", RunnableLambda(seed))
    legacy_builder.add_edge(START, "seed")
    legacy_builder.add_edge("seed", END)
    legacy_compiled = legacy_builder.compile(checkpointer=checkpointer)
    await legacy_compiled.ainvoke(
        {
            "graph_version": "fast-chat-v1",
            "state_schema_version": 1,
            "execution_mode": "inline",
            "reasoning_mode": "direct",
        },
        {"configurable": {"thread_id": "run-version"}},
        durability="sync",
    )

    calls: list[str] = []
    graph = ChatGraph(
        checkpointer=checkpointer,
        nodes=_recording_nodes(calls),
        authorize=_always_authorized(),
    )

    with pytest.raises(GraphVersionConflict, match="fast-chat-v1"):
        await graph.resume(run_id="run-version")

    assert await graph.stored_version(run_id="run-version") == ("fast-chat-v1", 1)


@pytest.mark.asyncio
async def test_nodes_emit_spans(span_recorder) -> None:
    calls: list[str] = []
    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=_recording_nodes(calls),
        authorize=_always_authorized(),
    )

    await graph.start(run_id="run-spans", payload={}, route="chat")

    names = [span.name for span in span_recorder.get_finished_spans()]
    expected = [
        "graph.node.ingress",
        "graph.node.guard_in",
        "graph.node.seed_search",
        "graph.node.model",
        "graph.node.verify",
        "graph.node.finalize",
    ]
    assert [name for name in names if name.startswith("graph.node.")] == expected
    for span in span_recorder.get_finished_spans():
        if span.name.startswith("graph.node."):
            assert span.attributes.get("tap.graph.version") == CHAT_GRAPH_VERSION


@pytest.mark.asyncio
async def test_failed_node_span_has_error_status(span_recorder) -> None:
    from opentelemetry.trace import StatusCode

    calls: list[str] = []

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        calls.append("seed_search")
        return {}

    async def model(_state: ChatGraphState) -> dict[str, object]:
        calls.append("model")
        raise RuntimeError("model exploded")

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("verify must not run after a model failure")

    async def finalize(state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        return {"result": {"answer": "degraded"}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
    )

    await graph.start(run_id="run-failed-span", payload={}, route="chat")

    spans_by_name = {
        recorded.name: recorded
        for recorded in span_recorder.get_finished_spans()
        if recorded.name.startswith("graph.node.")
    }
    assert spans_by_name["graph.node.model"].status.status_code == StatusCode.ERROR
    assert spans_by_name["graph.node.guard_in"].status.status_code != StatusCode.ERROR
    assert spans_by_name["graph.node.seed_search"].status.status_code != StatusCode.ERROR


@pytest.mark.asyncio
async def test_authorization_loss_raises_permission_error() -> None:
    calls: list[str] = []
    allowed = True

    async def authorize() -> bool:
        return allowed

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        nonlocal allowed
        calls.append("seed_search")
        allowed = False
        return {}

    async def model(_state: ChatGraphState) -> dict[str, object]:
        calls.append("model")
        return {}

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        calls.append("verify")
        return {}

    async def finalize(_state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        return {"result": {}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=authorize,
    )

    with pytest.raises(PermissionError, match="authorization changed"):
        await graph.start(run_id="run-permission", payload={}, route="chat")

    assert calls == ["guard_in", "seed_search"]


@pytest.mark.asyncio
async def test_ingress_writes_budget_before_guard_in() -> None:
    calls: list[str] = []

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        meter = current_meter()
        assert meter is not None
        meter.before_call()
        meter.after_call(0)
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        calls.append("seed_search")
        return {}

    async def model(_state: ChatGraphState) -> dict[str, object]:
        calls.append("model")
        return {}

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        calls.append("verify")
        return {}

    async def finalize(_state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        return {"result": {"answer": "ok"}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
    )

    state = await graph.start(
        run_id="run-ingress-budget",
        payload={},
        route="chat",
        budget=TurnBudget(max_model_calls=4),
    )

    assert state["budget"]["maxModelCalls"] == 4
    assert isinstance(state["budget"]["startedAtMs"], int)
    assert state["budget"]["startedAtMs"] > 0
    assert state["budget_used"] == {"modelCalls": 1, "costMicros": 0, "toolRounds": 0}


@pytest.mark.asyncio
async def test_model_call_budget_exhaustion_fails_turn_without_calling_node() -> None:
    calls: list[str] = []
    captured: dict[str, object] = {}

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        meter = current_meter()
        assert meter is not None
        meter.before_call()
        meter.after_call(0)
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("seed_search must not run once the model-call budget is exhausted")

    async def model(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("model must not run once the model-call budget is exhausted")

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("verify must not run once the model-call budget is exhausted")

    async def finalize(state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        captured["failure"] = state.get("failure")
        return {"result": {"answer": "degraded"}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
    )

    await graph.start(
        run_id="run-model-call-budget",
        payload={},
        route="chat",
        budget=TurnBudget(max_model_calls=1),
    )

    assert calls == ["guard_in", "finalize"]
    assert captured["failure"] == {
        "node": "seed_search",
        "error": "TurnBudgetExhausted",
        "reason": "model-calls",
    }


@pytest.mark.asyncio
async def test_in_node_call_budget_exhaustion_maps_to_model_calls_reason() -> None:
    """M6: a node that itself spends more model calls than remain (not the
    `_run_budgeted` pre-check) raises `CallBudgetExhausted` from the meter;
    `_run_budgeted` must still map that to `TurnBudgetExhausted("model-calls")`
    when the meter's own call count is what's exhausted."""
    calls: list[str] = []
    captured: dict[str, object] = {}

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        calls.append("seed_search")
        meter = current_meter()
        assert meter is not None
        meter.before_call()
        meter.after_call(0)
        # A second call within the same node, with only one call's worth of
        # budget remaining: the meter itself refuses it, mid-node.
        meter.before_call()
        raise AssertionError("unreachable: before_call above must raise first")

    async def model(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("model must not run once the in-node budget is exhausted")

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("verify must not run once the in-node budget is exhausted")

    async def finalize(state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        captured["failure"] = state.get("failure")
        return {"result": {"answer": "degraded"}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
    )

    await graph.start(
        run_id="run-in-node-model-calls",
        payload={},
        route="chat",
        budget=TurnBudget(max_model_calls=1),
    )

    assert calls == ["guard_in", "seed_search", "finalize"]
    assert captured["failure"] == {
        "node": "seed_search",
        "error": "TurnBudgetExhausted",
        "reason": "model-calls",
    }


@pytest.mark.asyncio
async def test_in_node_call_budget_exhaustion_maps_to_cost_reason() -> None:
    """M6: when a node's calls are still within `max_model_calls` but the
    meter's cost is exhausted, `_run_budgeted` must map the in-node
    `CallBudgetExhausted` to `TurnBudgetExhausted("cost")`, not
    `"model-calls"`."""
    calls: list[str] = []
    captured: dict[str, object] = {}

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        calls.append("seed_search")
        meter = current_meter()
        assert meter is not None
        meter.before_call()
        meter.after_call(100)
        # Plenty of model calls remain (max_model_calls=5), but the cost
        # spent above already exhausts the turn's cost budget.
        meter.before_call()
        raise AssertionError("unreachable: before_call above must raise first")

    async def model(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("model must not run once the in-node budget is exhausted")

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("verify must not run once the in-node budget is exhausted")

    async def finalize(state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        captured["failure"] = state.get("failure")
        return {"result": {"answer": "degraded"}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
    )

    await graph.start(
        run_id="run-in-node-cost",
        payload={},
        route="chat",
        budget=TurnBudget(max_model_calls=5, max_cost_micros=100),
    )

    assert calls == ["guard_in", "seed_search", "finalize"]
    assert captured["failure"] == {
        "node": "seed_search",
        "error": "TurnBudgetExhausted",
        "reason": "cost",
    }


@pytest.mark.asyncio
async def test_wall_clock_expiry_mid_node_fails_with_budget_exhausted() -> None:
    calls: list[str] = []
    captured: dict[str, object] = {}

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        calls.append("seed_search")
        return {}

    async def model(_state: ChatGraphState) -> dict[str, object]:
        calls.append("model")
        await asyncio.sleep(5)
        return {}

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("verify must not run after a wall-clock budget failure")

    async def finalize(state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        captured["failure"] = state.get("failure")
        return {"result": {"answer": "degraded"}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
        retryable=lambda exc: isinstance(exc, TimeoutError),
    )

    started = time.monotonic()
    state = await graph.start(
        run_id="run-wall-clock-budget",
        payload={},
        route="chat",
        budget=TurnBudget(max_seconds=0.2),
    )
    elapsed = time.monotonic() - started

    assert elapsed < 2
    assert calls == ["guard_in", "seed_search", "model", "finalize"]
    assert captured["failure"] == {
        "node": "model",
        "error": "TurnBudgetExhausted",
        "reason": "wall-clock",
    }
    assert state["failure"] == captured["failure"]


@pytest.mark.asyncio
async def test_budget_usage_survives_resume() -> None:
    calls: list[str] = []
    failed = False
    captured: dict[str, object] = {}

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        meter = current_meter()
        assert meter is not None
        meter.before_call()
        meter.after_call(0)
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        calls.append("seed_search")
        return {}

    async def model(state: ChatGraphState) -> dict[str, object]:
        nonlocal failed
        calls.append("model")
        captured.setdefault("first_started_at", state["budget"]["startedAtMs"])
        meter = current_meter()
        assert meter is not None
        meter.before_call()
        meter.after_call(0)
        if not failed:
            failed = True
            raise GraphCheckpointRetryable("connection lost")
        return {}

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        calls.append("verify")
        return {}

    async def finalize(_state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        return {"result": {"answer": "ok"}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
        propagate=(GraphCheckpointRetryable,),
    )

    with pytest.raises(GraphCheckpointRetryable):
        await graph.start(
            run_id="run-budget-resume",
            payload={},
            route="chat",
            budget=TurnBudget(max_model_calls=10),
        )

    state = await graph.resume(run_id="run-budget-resume")

    assert calls.count("model") == 2
    assert state["budget_used"] == {"modelCalls": 2, "costMicros": 0, "toolRounds": 0}
    assert state["budget"]["startedAtMs"] == captured["first_started_at"]


@pytest.mark.asyncio
async def test_canceled_check_raises_permission_error() -> None:
    """Task 7: an optional `canceled` check runs alongside `authorize`
    before every node, independent of authorization loss, so a canceled
    Turn stops at the next node boundary even when `authorize` itself
    stays true throughout."""
    calls: list[str] = []
    is_canceled = False

    async def authorize() -> bool:
        return True

    async def canceled() -> bool:
        return is_canceled

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        nonlocal is_canceled
        calls.append("seed_search")
        is_canceled = True
        return {}

    async def model(_state: ChatGraphState) -> dict[str, object]:
        calls.append("model")
        return {}

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        calls.append("verify")
        return {}

    async def finalize(_state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        return {"result": {}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=_unreachable_tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=authorize,
        canceled=canceled,
    )

    with pytest.raises(PermissionError, match="canceled"):
        await graph.start(run_id="run-canceled", payload={}, route="chat")

    assert calls == ["guard_in", "seed_search"]


@pytest.mark.asyncio
async def test_model_tool_calls_route_through_tools_and_back() -> None:
    """Task 9: a `model` response carrying a `"tool_calls"` step routes to
    `tools`, which hands control back to `model`; a later `"generated"`
    step continues to `verify`/`finalize` as usual, and `tools` counts one
    tool round into `budget_used`."""
    calls: list[str] = []
    model_calls = 0

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        calls.append("seed_search")
        return {"step": {"kind": "prepared"}}

    async def model(_state: ChatGraphState) -> dict[str, object]:
        nonlocal model_calls
        calls.append("model")
        model_calls += 1
        if model_calls == 1:
            return {"step": {"kind": "tool_calls", "pending_tool_calls": []}}
        return {"step": {"kind": "generated"}}

    async def tools(_state: ChatGraphState) -> dict[str, object]:
        calls.append("tools")
        return {"step": {"kind": "prepared"}, "tool_rounds_delta": 1}

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        calls.append("verify")
        return {"step": {"kind": "verified"}}

    async def finalize(state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        return {"result": {"budgetUsed": state.get("budget_used")}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
    )

    final_state = await graph.start(run_id="run-tool-loop", payload={}, route="chat")

    assert calls == ["guard_in", "seed_search", "model", "tools", "model", "verify", "finalize"]
    assert final_state["budget_used"]["toolRounds"] == 1
    assert cast(dict, final_state["result"])["budgetUsed"]["toolRounds"] == 1


@pytest.mark.asyncio
async def test_tools_failure_short_circuits_to_finalize() -> None:
    calls: list[str] = []

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        calls.append("guard_in")
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        calls.append("seed_search")
        return {"step": {"kind": "prepared"}}

    async def model(_state: ChatGraphState) -> dict[str, object]:
        calls.append("model")
        return {"step": {"kind": "tool_calls", "pending_tool_calls": []}}

    async def tools(_state: ChatGraphState) -> dict[str, object]:
        calls.append("tools")
        raise ValueError("tools exploded")

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("verify must not run after a tools failure")

    async def finalize(_state: ChatGraphState) -> dict[str, object]:
        calls.append("finalize")
        return {"result": {"answer": "degraded"}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
    )

    final_state = await graph.start(run_id="run-tools-failure", payload={}, route="chat")

    assert calls == ["guard_in", "seed_search", "model", "tools", "finalize"]
    assert final_state["failure"]["node"] == "tools"


@pytest.mark.asyncio
async def test_ten_rounds_fit_recursion_limit() -> None:
    """Task 9: `recursion_limit = 2 * maxToolRounds + 12` must be enough
    for a full budget's worth of tool rounds to complete without
    LangGraph's `GraphRecursionError`."""
    rounds_done = 0

    async def guard_in(_state: ChatGraphState) -> dict[str, object]:
        return {}

    async def seed_search(_state: ChatGraphState) -> dict[str, object]:
        return {"step": {"kind": "prepared"}}

    async def model(state: ChatGraphState) -> dict[str, object]:
        used = state.get("budget_used") or {}
        if int(used.get("toolRounds", 0)) < 10:
            return {"step": {"kind": "tool_calls", "pending_tool_calls": []}}
        return {"step": {"kind": "generated"}}

    async def tools(_state: ChatGraphState) -> dict[str, object]:
        nonlocal rounds_done
        rounds_done += 1
        return {"step": {"kind": "prepared"}, "tool_rounds_delta": 1}

    async def verify(_state: ChatGraphState) -> dict[str, object]:
        return {"step": {"kind": "verified"}}

    async def finalize(_state: ChatGraphState) -> dict[str, object]:
        return {"result": {"answer": "ok"}}

    async def insights_explain(_state: ChatGraphState) -> dict[str, object]:
        raise AssertionError("insights_explain must not run on the chat route")

    graph = ChatGraph(
        checkpointer=InMemorySaver(),
        nodes=ChatGraphNodes(
            guard_in=guard_in,
            insights_explain=insights_explain,
            seed_search=seed_search,
            model=model,
            tools=tools,
            verify=verify,
            finalize=finalize,
        ),
        authorize=_always_authorized(),
    )

    final_state = await graph.start(
        run_id="run-ten-rounds",
        payload={},
        route="chat",
        budget=TurnBudget(max_tool_rounds=10, max_model_calls=50),
    )

    assert rounds_done == 10
    assert final_state["budget_used"]["toolRounds"] == 10
    assert final_state["result"]["answer"] == "ok"
