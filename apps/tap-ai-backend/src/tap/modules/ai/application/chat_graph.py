"""The versioned `tapper-chat-v2` LangGraph: chat answering and Insights explanation."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, cast

from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from opentelemetry.trace import Status, StatusCode

from tap.modules.ai.application.interaction_graph import VersionedGraphRunner
from tap.platform.budget import (
    CallBudgetExhausted,
    CallMeter,
    TurnBudget,
    TurnBudgetExhausted,
    metered,
)
from tap.platform.telemetry import span

CHAT_GRAPH_VERSION = "tapper-chat-v2"
CHAT_STATE_SCHEMA_VERSION = 2


class ChatGraphState(TypedDict, total=False):
    graph_version: str
    state_schema_version: int
    execution_mode: Literal["inline", "durable"]
    reasoning_mode: Literal["direct", "workflow", "agentic"]
    payload: dict[str, object]
    route: Literal["chat", "insights", "out_of_scope"]
    answer_plan: dict[str, Any] | None
    plan_attempt: int
    step: dict[str, object] | None
    started_at_ms: int
    failure: dict[str, str] | None
    result: dict[str, object]
    budget: dict[str, int | float]
    budget_used: dict[str, int]
    # PR 3: stream event types already committed early (via `conversations.
    # emit`) by `seed_search`/`verify`, so `finalize`'s own `streamEvents`
    # batch never repeats them.
    committed_stream_events: list[str]


GraphNode = Callable[[ChatGraphState], Awaitable[dict[str, Any]]]
AuthorizationCheck = Callable[[], Awaitable[bool]]


async def _default_ingress(state: ChatGraphState) -> dict[str, Any]:
    """Fallback `ingress` for callers (mostly tests) that do not supply one:

    stamps `startedAtMs` and zeroes `budget_used` the same way
    `ChatTurnNodes.ingress` does, but without the turn-specific audit span
    attributes -- those require a bound Turn and are set by the real
    implementation only."""
    budget = dict(state.get("budget") or {})
    if "startedAtMs" not in budget:
        budget["startedAtMs"] = int(time.time() * 1000)
        return {
            "budget": budget,
            "budget_used": {"modelCalls": 0, "costMicros": 0, "toolRounds": 0},
        }
    return {}


@dataclass(frozen=True, slots=True)
class ChatGraphNodes:
    guard_in: GraphNode
    insights_explain: GraphNode
    seed_search: GraphNode
    model: GraphNode
    verify: GraphNode
    finalize: GraphNode
    ingress: GraphNode = _default_ingress


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
        canceled: AuthorizationCheck | None = None,
        propagate: tuple[type[BaseException], ...] = (),
        retryable: Callable[[BaseException], bool] = lambda _error: False,
    ) -> None:
        self._authorize = authorize
        self._canceled = canceled
        self._propagate = propagate
        self._retryable = retryable
        self._checkpointer = checkpointer

        builder = StateGraph(ChatGraphState)
        builder.add_node(
            "ingress", RunnableLambda(self._wrap("ingress", nodes.ingress, enforce_budget=False))
        )
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
            RunnableLambda(
                self._wrap("finalize", nodes.finalize, capture_failure=False, enforce_budget=False)
            ),
        )

        builder.add_edge(START, "ingress")
        builder.add_edge("ingress", "guard_in")
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
        if state.get("failure"):
            return "finalize"
        route = state.get("route")
        if route == "insights":
            return "insights"
        if route == "out_of_scope":
            return "finalize"
        return "chat"

    @staticmethod
    def _route_on_failure(state: ChatGraphState) -> str:
        return "finalize" if state.get("failure") else "continue"

    def _wrap(
        self,
        name: str,
        node: GraphNode,
        *,
        capture_failure: bool = True,
        enforce_budget: bool = True,
    ) -> GraphNode:
        async def run(state: ChatGraphState) -> dict[str, Any]:
            if not await self._authorize():
                raise PermissionError("graph run authorization changed")
            if self._canceled is not None and await self._canceled():
                raise PermissionError("graph run was canceled")
            with span(f"graph.node.{name}", {"tap.graph.version": CHAT_GRAPH_VERSION}) as node_span:
                try:
                    if enforce_budget:
                        return await self._run_budgeted(node, state, node_span)
                    return await node(state)
                except PermissionError:
                    raise
                except TurnBudgetExhausted as exc:
                    node_span.set_status(Status(StatusCode.ERROR, "TurnBudgetExhausted"))
                    node_span.record_exception(exc)
                    return {
                        "failure": {
                            "node": name,
                            "error": "TurnBudgetExhausted",
                            "reason": exc.reason,
                        }
                    }
                except self._propagate:
                    raise
                except Exception as exc:
                    if self._retryable(exc):
                        node_span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                        node_span.record_exception(exc)
                        raise
                    if not capture_failure:
                        raise
                    node_span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    node_span.record_exception(exc)
                    return {"failure": {"node": name, "error": type(exc).__name__}}

        return run

    @staticmethod
    async def _run_budgeted(
        node: GraphNode, state: ChatGraphState, node_span: Any
    ) -> dict[str, Any]:
        budget = state.get("budget") or {}
        max_seconds = float(budget.get("maxSeconds", 0.0))
        max_model_calls = int(budget.get("maxModelCalls", 0))
        max_cost_micros = int(budget.get("maxCostMicros", 0))
        started_at_ms = int(budget.get("startedAtMs", 0))
        used = state.get("budget_used") or {"modelCalls": 0, "costMicros": 0, "toolRounds": 0}
        calls_used = int(used.get("modelCalls", 0))
        cost_used = int(used.get("costMicros", 0))
        tool_rounds_used = int(used.get("toolRounds", 0))

        now_ms = int(time.time() * 1000)
        remaining_s = max_seconds - (now_ms - started_at_ms) / 1000
        if remaining_s <= 0:
            raise TurnBudgetExhausted("wall-clock")
        if calls_used >= max_model_calls:
            raise TurnBudgetExhausted("model-calls")
        if max_cost_micros > 0 and cost_used >= max_cost_micros:
            raise TurnBudgetExhausted("cost")

        meter = CallMeter(
            remaining_calls=max_model_calls - calls_used,
            remaining_cost_micros=None if max_cost_micros == 0 else max_cost_micros - cost_used,
        )
        node_started = time.monotonic()
        timeout_cm = asyncio.timeout(remaining_s)
        try:
            async with timeout_cm:
                with metered(meter):
                    result = await node(state)
        except TimeoutError:
            if timeout_cm.expired():
                raise TurnBudgetExhausted("wall-clock") from None
            raise
        except CallBudgetExhausted:
            if meter.calls >= meter.remaining_calls:
                raise TurnBudgetExhausted("model-calls") from None
            raise TurnBudgetExhausted("cost") from None

        seconds_used = time.monotonic() - node_started
        total_calls = calls_used + meter.calls
        total_cost = cost_used + meter.cost_micros
        node_span.set_attribute("tap.budget.model_calls_used", total_calls)
        node_span.set_attribute("tap.budget.cost_micros_used", total_cost)
        node_span.set_attribute("tap.budget.seconds_used", seconds_used)
        merged = dict(result)
        merged["budget_used"] = {
            "modelCalls": total_calls,
            "costMicros": total_cost,
            "toolRounds": tool_rounds_used,
        }
        return merged

    async def start(
        self,
        *,
        run_id: str,
        payload: Mapping[str, object],
        route: Literal["chat", "insights"],
        budget: TurnBudget = TurnBudget(),
    ) -> ChatGraphState:
        state: ChatGraphState = {
            "graph_version": CHAT_GRAPH_VERSION,
            "state_schema_version": CHAT_STATE_SCHEMA_VERSION,
            "execution_mode": "inline",
            "reasoning_mode": "direct",
            "payload": dict(payload),
            "route": route,
            "failure": None,
            "budget": budget.to_dict(),
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
        """Discard a stale checkpoint (e.g. from a superseded graph version) before a
        fresh `start`. Prefers the checkpointer's `arestart_thread`, which leaves the
        run id in a state that stays reclaimable if the caller crashes before the next
        checkpoint write; falls back to `adelete_thread` for savers without it (e.g.
        `InMemorySaver`, which has no stranding-prone backing row to begin with).
        """
        restart = getattr(self._checkpointer, "arestart_thread", None)
        if restart is not None:
            await restart(run_id)
        else:
            await self._checkpointer.adelete_thread(run_id)
