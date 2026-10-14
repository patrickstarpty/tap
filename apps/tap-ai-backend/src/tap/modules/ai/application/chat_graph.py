"""The versioned `tapper-chat-v2` LangGraph: chat answering and Insights explanation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, cast

from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from tap.modules.ai.application.interaction_graph import VersionedGraphRunner
from tap.platform.telemetry import span

CHAT_GRAPH_VERSION = "tapper-chat-v2"
CHAT_STATE_SCHEMA_VERSION = 1


class ChatGraphState(TypedDict, total=False):
    graph_version: str
    state_schema_version: int
    execution_mode: Literal["inline", "durable"]
    reasoning_mode: Literal["direct", "workflow", "agentic"]
    payload: dict[str, object]
    route: Literal["chat", "insights"]
    answer_plan: dict[str, Any] | None
    step: dict[str, object] | None
    started_at_ms: int
    failure: dict[str, str] | None
    result: dict[str, object]


GraphNode = Callable[[ChatGraphState], Awaitable[dict[str, Any]]]
AuthorizationCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class ChatGraphNodes:
    guard_in: GraphNode
    insights_explain: GraphNode
    seed_search: GraphNode
    model: GraphNode
    verify: GraphNode
    finalize: GraphNode


class ChatGraph:
    """`tapper-chat-v2`: guard_in routes to either the insights explanation or the

    chat answer path (seed_search -> model -> verify), both converging on finalize.
    Any answer-path node failure short-circuits straight to finalize with a captured
    failure, letting the caller resume once the underlying fault is retried.
    """

    def __init__(
        self,
        *,
        checkpointer: BaseCheckpointSaver,
        nodes: ChatGraphNodes,
        authorize: AuthorizationCheck,
        propagate: tuple[type[BaseException], ...] = (),
    ) -> None:
        self._authorize = authorize
        self._propagate = propagate
        self._checkpointer = checkpointer

        builder = StateGraph(ChatGraphState)
        builder.add_node("guard_in", RunnableLambda(self._wrap("guard_in", nodes.guard_in)))
        builder.add_node(
            "insights_explain",
            RunnableLambda(self._wrap("insights_explain", nodes.insights_explain)),
        )
        builder.add_node(
            "seed_search", RunnableLambda(self._wrap("seed_search", nodes.seed_search))
        )
        builder.add_node("model", RunnableLambda(self._wrap("model", nodes.model)))
        builder.add_node("verify", RunnableLambda(self._wrap("verify", nodes.verify)))
        builder.add_node(
            "finalize",
            RunnableLambda(self._wrap("finalize", nodes.finalize, capture_failure=False)),
        )

        builder.add_edge(START, "guard_in")
        builder.add_conditional_edges(
            "guard_in",
            self._route_after_guard,
            {"insights": "insights_explain", "finalize": "finalize", "chat": "seed_search"},
        )
        builder.add_conditional_edges(
            "seed_search",
            self._route_on_failure,
            {"finalize": "finalize", "continue": "model"},
        )
        builder.add_conditional_edges(
            "model",
            self._route_on_failure,
            {"finalize": "finalize", "continue": "verify"},
        )
        builder.add_edge("verify", "finalize")
        builder.add_edge("insights_explain", "finalize")
        builder.add_edge("finalize", END)

        self._runner = VersionedGraphRunner(
            builder.compile(
                checkpointer=checkpointer,
                name=f"tap-chat-{CHAT_GRAPH_VERSION}",
            ),
            graph_version=CHAT_GRAPH_VERSION,
            state_schema_version=CHAT_STATE_SCHEMA_VERSION,
            authorize=authorize,
        )

    @staticmethod
    def _route_after_guard(state: ChatGraphState) -> str:
        if state.get("route") == "insights":
            return "insights"
        if state.get("failure"):
            return "finalize"
        return "chat"

    @staticmethod
    def _route_on_failure(state: ChatGraphState) -> str:
        return "finalize" if state.get("failure") else "continue"

    def _wrap(self, name: str, node: GraphNode, *, capture_failure: bool = True) -> GraphNode:
        async def run(state: ChatGraphState) -> dict[str, Any]:
            if not await self._authorize():
                raise PermissionError("graph run authorization changed")
            with span(f"graph.node.{name}", {"tap.graph.version": CHAT_GRAPH_VERSION}):
                try:
                    return await node(state)
                except PermissionError:
                    raise
                except self._propagate:
                    raise
                except Exception as exc:
                    if not capture_failure:
                        raise
                    return {"failure": {"node": name, "error": type(exc).__name__}}

        return run

    async def start(
        self,
        *,
        run_id: str,
        payload: Mapping[str, object],
        route: Literal["chat", "insights"],
    ) -> ChatGraphState:
        state: ChatGraphState = {
            "graph_version": CHAT_GRAPH_VERSION,
            "state_schema_version": CHAT_STATE_SCHEMA_VERSION,
            "execution_mode": "inline",
            "reasoning_mode": "direct",
            "payload": dict(payload),
            "route": route,
            "failure": None,
        }
        result = await self._runner.start(run_id=run_id, state=state)
        return cast(ChatGraphState, result)

    async def resume(self, *, run_id: str) -> ChatGraphState:
        result = await self._runner.resume(run_id=run_id)
        return cast(ChatGraphState, result)

    async def has_checkpoint(self, *, run_id: str) -> bool:
        return await self._runner.has_checkpoint(run_id=run_id)

    async def stored_version(self, *, run_id: str) -> tuple[str | None, int | None] | None:
        return await self._runner.stored_version(run_id=run_id)

    async def discard(self, *, run_id: str) -> None:
        await self._checkpointer.adelete_thread(run_id)
