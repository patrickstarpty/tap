from collections.abc import Awaitable, Callable

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
            verify=verify,
            finalize=finalize,
        ),
        authorize=authorize,
    )

    with pytest.raises(PermissionError, match="authorization changed"):
        await graph.start(run_id="run-permission", payload={}, route="chat")

    assert calls == ["guard_in", "seed_search"]
