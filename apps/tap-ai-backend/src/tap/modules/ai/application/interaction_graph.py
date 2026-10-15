"""Versioned LangGraph boundary for resumable TAP AI interactions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, TypedDict, cast

from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph


class GraphVersionConflict(RuntimeError):
    """Raised when a runtime attempts to resume an incompatible persisted graph."""


class InteractionGraphState(TypedDict, total=False):
    graph_version: str
    state_schema_version: int
    execution_mode: Literal["inline", "durable"]
    reasoning_mode: Literal["direct", "workflow", "agentic"]
    payload: dict[str, object]
    admitted: bool
    result: dict[str, object]
    waiting_reason: str | None
    answer_plan: dict[str, Any]
    insights_explanation: dict[str, Any]


GraphNode = Callable[[InteractionGraphState], Awaitable[dict[str, Any]]]
AuthorizationCheck = Callable[[], Awaitable[bool]]


class VersionedGraphRunner:
    """Owns durable start/resume/checkpoint access for a single compiled graph version."""

    def __init__(
        self,
        compiled: Any,
        *,
        graph_version: str,
        state_schema_version: int,
        authorize: AuthorizationCheck,
    ) -> None:
        if not graph_version.strip() or state_schema_version < 1:
            raise ValueError("graph and state schema versions must be explicit")
        self._compiled = compiled
        self._graph_version = graph_version
        self._state_schema_version = state_schema_version
        self._authorize = authorize

    @staticmethod
    def _config(
        run_id: str,
        *,
        graph_version: str | None = None,
        state_schema_version: int | None = None,
        execution_mode: str | None = None,
    ) -> RunnableConfig:
        if not run_id.strip():
            raise ValueError("graph run identity must be nonblank")
        configurable: dict[str, object] = {"thread_id": run_id}
        if graph_version is not None:
            configurable["graph_version"] = graph_version
        if state_schema_version is not None:
            configurable["state_schema_version"] = state_schema_version
        if execution_mode is not None:
            configurable["execution_mode"] = execution_mode
        return cast(RunnableConfig, {"configurable": configurable})

    async def start(self, *, run_id: str, state: Mapping[str, Any]) -> dict[str, Any]:
        result = await self._compiled.ainvoke(
            cast(Any, state),
            self._config(
                run_id,
                graph_version=self._graph_version,
                state_schema_version=self._state_schema_version,
                execution_mode=cast(str | None, state.get("execution_mode")),
            ),
            durability=cast(Literal["sync"], "sync"),
        )
        return cast(dict[str, Any], result)

    async def resume(self, *, run_id: str) -> dict[str, Any]:
        config = self._config(run_id)
        snapshot = await self._compiled.aget_state(config)
        if not snapshot.values:
            raise LookupError("graph run checkpoint was not found")
        stored_version = snapshot.values.get("graph_version")
        stored_schema = snapshot.values.get("state_schema_version")
        if stored_version != self._graph_version or stored_schema != self._state_schema_version:
            raise GraphVersionConflict(
                "cannot resume graph checkpoint "
                f"{stored_version!s}/{stored_schema!s} with "
                f"{self._graph_version}/{self._state_schema_version}"
            )
        if not await self._authorize():
            raise PermissionError("graph run authorization changed")
        result = await self._compiled.ainvoke(
            None, config, durability=cast(Literal["sync"], "sync")
        )
        return cast(dict[str, Any], result)

    async def has_checkpoint(self, *, run_id: str) -> bool:
        return bool((await self._compiled.aget_state(self._config(run_id))).values)

    async def stored_version(self, *, run_id: str) -> tuple[str | None, int | None] | None:
        snapshot = await self._compiled.aget_state(self._config(run_id))
        if not snapshot.values:
            return None
        return (
            cast(str | None, snapshot.values.get("graph_version")),
            cast(int | None, snapshot.values.get("state_schema_version")),
        )


class InteractionGraph:
    """A small fixed graph whose durable state is owned by a supplied checkpointer."""

    def __init__(
        self,
        *,
        graph_version: str,
        state_schema_version: int,
        checkpointer: BaseCheckpointSaver,
        classify: GraphNode,
        admit: GraphNode,
        execute: GraphNode,
        authorize: AuthorizationCheck,
    ) -> None:
        if not graph_version.strip() or state_schema_version < 1:
            raise ValueError("graph and state schema versions must be explicit")
        self._graph_version = graph_version
        self._state_schema_version = state_schema_version
        self._authorize = authorize

        builder = StateGraph(InteractionGraphState)
        builder.add_node("classify", RunnableLambda(self._guarded(classify)))
        builder.add_node("admit", RunnableLambda(self._guarded(admit)))
        builder.add_node("execute", RunnableLambda(self._guarded(execute)))
        builder.add_edge(START, "classify")
        builder.add_edge("classify", "admit")
        builder.add_conditional_edges(
            "admit",
            lambda state: "wait" if state.get("waiting_reason") else "execute",
            {"wait": END, "execute": "execute"},
        )
        builder.add_edge("execute", END)
        self._runner = VersionedGraphRunner(
            builder.compile(
                checkpointer=checkpointer,
                name=f"tap-interaction-{graph_version}",
            ),
            graph_version=graph_version,
            state_schema_version=state_schema_version,
            authorize=authorize,
        )

    def _guarded(self, node: GraphNode) -> GraphNode:
        async def run(state: InteractionGraphState) -> dict[str, Any]:
            if not await self._authorize():
                raise PermissionError("graph run authorization changed")
            return await node(state)

        return run

    async def start(
        self,
        *,
        run_id: str,
        payload: Mapping[str, object],
        execution_mode: Literal["inline", "durable"],
    ) -> InteractionGraphState:
        state: InteractionGraphState = {
            "graph_version": self._graph_version,
            "state_schema_version": self._state_schema_version,
            "execution_mode": execution_mode,
            "reasoning_mode": "direct",
            "payload": dict(payload),
            "waiting_reason": None,
        }
        result = await self._runner.start(run_id=run_id, state=state)
        return cast(InteractionGraphState, result)

    async def resume(self, *, run_id: str) -> InteractionGraphState:
        result = await self._runner.resume(run_id=run_id)
        return cast(InteractionGraphState, result)

    async def has_checkpoint(self, *, run_id: str) -> bool:
        return await self._runner.has_checkpoint(run_id=run_id)
