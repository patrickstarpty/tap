"""Bounded LangGraph tool loop that lets a selected Agent call its allowlisted tools."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.application.tool_registry import ToolRegistry, builtin_tools
from tap.modules.ai.domain.models import (
    ChatMessage,
    ModelRequest,
    ToolCall,
    ToolSpec,
    ToolTurn,
)
from tap.modules.ai.domain.tools import (
    RESERVED_TOOLS,
    McpToolSource,
    ToolDescriptor,
    ToolExecutor,
    ToolRejected,
    ToolResult,
)
from tap.modules.ai.ports.gateway import ModelGateway
from tap.platform.telemetry import span

__all__ = [
    "DEFAULT_CONTEXT_TOKENS",
    "AgentToolRuntime",
    "ToolLoop",
    "ToolLoopLimits",
    "ToolLoopOutcome",
    "validate_tool_arguments",
]

logger = logging.getLogger(__name__)

# The LiteLLM catalog does not report context windows; assume a conservative one.
DEFAULT_CONTEXT_TOKENS = 32768
MAX_TOOL_STEPS = 4
MAX_PARALLEL_CALLS = 3
_MAX_RESULT_CHARS = 16 * 1024
# OpenAI-compatible function names are at most 64 characters once `.` travels as `__`.
_MAX_WIRE_NAME = 64
_MAX_SCHEMA_DEPTH = 32

Redact = Callable[[str], Awaitable[str]]
StopReason = Literal["model", "steps", "budget"]


@dataclass(frozen=True, kw_only=True)
class ToolLoopLimits:
    token_budget: int
    max_steps: int = MAX_TOOL_STEPS
    max_parallel: int = MAX_PARALLEL_CALLS

    def __post_init__(self) -> None:
        if (
            type(self.token_budget) is not int
            or self.token_budget < 1
            or type(self.max_steps) is not int
            or not 1 <= self.max_steps <= MAX_TOOL_STEPS
            or type(self.max_parallel) is not int
            or not 1 <= self.max_parallel <= MAX_PARALLEL_CALLS
        ):
            raise ValueError("tool loop limits are invalid")

    @classmethod
    def for_context(cls, context_tokens: int | None) -> ToolLoopLimits:
        """Budget half of the model context window."""

        return cls(token_budget=(context_tokens or DEFAULT_CONTEXT_TOKENS) // 2)


@dataclass(frozen=True)
class ToolLoopOutcome:
    transcript: tuple[ChatMessage, ...]
    evidence: tuple[object, ...]
    steps: int
    stopped_by: StopReason
    tool_labels: Mapping[str, str] = field(default_factory=dict)


def _matches_type(kind: object, value: object) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return type(value) is str
    if kind == "integer":
        return type(value) is int
    if kind == "number":
        return type(value) in {int, float} and math.isfinite(value)  # type: ignore[arg-type]
    if kind == "boolean":
        return type(value) is bool
    if kind == "null":
        return value is None
    return True


def _json_equal(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def validate_tool_arguments(schema: object, value: object, _depth: int = 0) -> bool:
    """Check model-supplied arguments against the common JSON Schema subset.

    Supports type, required, properties, additionalProperties, enum, string length, numeric
    bounds and array items/size. Unknown keywords are permissive; `_raw` (unparseable
    argument JSON) is never valid unless the schema declares it.
    """

    if _depth > _MAX_SCHEMA_DEPTH or schema is False:
        return False
    if not isinstance(schema, Mapping):
        return True
    kind = schema.get("type")
    if kind is not None:
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(_matches_type(item, value) for item in kinds):
            return False
    choices = schema.get("enum")
    if isinstance(choices, list) and not any(_json_equal(value, item) for item in choices):
        return False
    if type(value) is str:
        minimum, maximum = schema.get("minLength"), schema.get("maxLength")
        if (type(minimum) is int and len(value) < minimum) or (
            type(maximum) is int and len(value) > maximum
        ):
            return False
    if type(value) in {int, float}:
        number = float(value)  # type: ignore[arg-type]
        for bound, below in (
            ("minimum", True),
            ("maximum", False),
            ("exclusiveMinimum", True),
            ("exclusiveMaximum", False),
        ):
            limit = schema.get(bound)
            if type(limit) not in {int, float}:
                continue
            exclusive = bound.startswith("exclusive")
            if below and (number < limit or (exclusive and number == limit)):  # type: ignore[operator]
                return False
            if not below and (number > limit or (exclusive and number == limit)):  # type: ignore[operator]
                return False
    if isinstance(value, dict):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        if "_raw" in value and "_raw" not in properties:
            return False
        required = schema.get("required")
        if isinstance(required, list) and any(key not in value for key in required):
            return False
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                if not validate_tool_arguments(properties[key], item, _depth + 1):
                    return False
            elif extra is False or (
                isinstance(extra, Mapping) and not validate_tool_arguments(extra, item, _depth + 1)
            ):
                return False
    if isinstance(value, list):
        minimum, maximum = schema.get("minItems"), schema.get("maxItems")
        if (type(minimum) is int and len(value) < minimum) or (
            type(maximum) is int and len(value) > maximum
        ):
            return False
        items = schema.get("items")
        if items is not None and not all(
            validate_tool_arguments(items, item, _depth + 1) for item in value
        ):
            return False
    return True


@dataclass(frozen=True)
class _Run:
    scope: ProjectScopeContext
    request: ModelRequest
    exposed: Mapping[str, ToolDescriptor]
    specs: tuple[ToolSpec, ...]


class _State(TypedDict):
    run: _Run
    messages: list[ChatMessage]
    pending: list[ToolCall]
    steps: int
    model_calls: int
    used_tokens: int
    evidence: list[object]
    labels: dict[str, str]
    stopped_by: str


def _call_key(base: str, index: int) -> str:
    suffix = f":tool-{index}"
    return base[: 128 - len(suffix)] + suffix


def _tokens(turn: ToolTurn, messages: Iterable[ChatMessage]) -> int:
    usage = turn.result.usage
    if usage.input_tokens is not None or usage.output_tokens is not None:
        return (usage.input_tokens or 0) + (usage.output_tokens or 0)
    # Without provider usage, estimate roughly four characters per token.
    return sum(len(item.content) for item in messages) // 4 + len(turn.text or "") // 4


class ToolLoop:
    """`model → tools → model` until the model answers or a step/budget limit is reached.

    At a limit the model is called one last time without tools so it can conclude.
    """

    def __init__(
        self,
        gateway: ModelGateway,
        registry: ToolRegistry,
        limits: ToolLoopLimits,
        *,
        redact: Redact | None = None,
    ) -> None:
        self._gateway = gateway
        self._registry = registry
        self._limits = limits
        self._redact = redact
        graph = StateGraph(_State)
        graph.add_node("model", self._model)
        graph.add_node("tools", self._tools)
        graph.add_edge(START, "model")
        graph.add_conditional_edges(
            "model", lambda state: "tools" if state["pending"] else END, ["tools", END]
        )
        graph.add_edge("tools", "model")
        self._graph = graph.compile()

    async def run(
        self,
        scope: ProjectScopeContext,
        request: ModelRequest,
        messages: tuple[ChatMessage, ...],
        allowlist: frozenset[str],
    ) -> ToolLoopOutcome:
        if scope != request.scope:
            raise ToolRejected("tool-scope-mismatch")
        allowed = await self._registry.require_allowlist(scope, allowlist)
        exposed: dict[str, ToolDescriptor] = {}
        for tool_id in sorted(allowed):
            descriptor = await self._registry.get(scope, tool_id)
            if (
                not descriptor.callable
                or not descriptor.enabled
                or len(tool_id.replace(".", "__")) > _MAX_WIRE_NAME
            ):
                continue
            try:
                await self._registry.executor(scope, tool_id)
            except ToolRejected:
                continue
            exposed[tool_id] = descriptor
        run = _Run(
            scope,
            request,
            exposed,
            tuple(
                ToolSpec(item.tool_id, item.description, item.input_schema)
                for item in exposed.values()
            ),
        )
        state: _State = {
            "run": run,
            "messages": list(messages),
            "pending": [],
            "steps": 0,
            "model_calls": 0,
            "used_tokens": 0,
            "evidence": [],
            "labels": {},
            "stopped_by": "model",
        }
        config = RunnableConfig(recursion_limit=2 * self._limits.max_steps + 2)
        final = await self._graph.ainvoke(cast(Any, state), config=config)
        stopped_by: StopReason = final["stopped_by"]
        return ToolLoopOutcome(
            transcript=tuple(final["messages"]),
            evidence=tuple(final["evidence"]),
            steps=final["steps"],
            stopped_by=stopped_by,
            tool_labels=dict(final["labels"]),
        )

    async def _model(self, state: _State) -> dict[str, object]:
        run = state["run"]
        limits = self._limits
        stop: StopReason | None = None
        if state["steps"] >= limits.max_steps:
            stop = "steps"
        elif state["used_tokens"] >= limits.token_budget:
            stop = "budget"
        tools = () if stop is not None else run.specs
        calls = state["model_calls"] + 1
        messages = tuple(state["messages"])
        turn = await self._gateway.chat_with_tools(
            replace(run.request, idempotency_key=_call_key(run.request.idempotency_key, calls)),
            messages,
            tools,
        )
        requested = turn.tool_calls if tools else ()
        return {
            "messages": [
                *messages,
                ChatMessage("assistant", turn.text or "", tool_calls=requested),
            ],
            "pending": list(requested),
            "model_calls": calls,
            "used_tokens": state["used_tokens"] + _tokens(turn, messages),
            "stopped_by": stop or "model",
        }

    async def _tools(self, state: _State) -> dict[str, object]:
        run = state["run"]
        calls = state["pending"]
        steps = state["steps"] + 1
        executed = calls[: self._limits.max_parallel]
        with span("tool.loop.step", {"tap.tool.step": steps, "tap.tool.call_count": len(calls)}):
            results = list(await asyncio.gather(*(self._invoke(run, call) for call in executed)))
        results += [ToolResult("tool-call-limit", is_error=True)] * (len(calls) - len(executed))
        messages = list(state["messages"])
        labels = dict(state["labels"])
        evidence = list(state["evidence"])
        for call, result in zip(calls, results, strict=True):
            label = f"T{len(labels) + 1}"
            labels[label] = call.name
            evidence.extend(result.evidence)
            messages.append(
                ChatMessage(
                    "tool", await self._wrap(label, call.name, result), tool_call_id=call.call_id
                )
            )
        return {
            "messages": messages,
            "pending": [],
            "steps": steps,
            "labels": labels,
            "evidence": evidence,
        }

    async def _invoke(self, run: _Run, call: ToolCall) -> ToolResult:
        descriptor = run.exposed.get(call.name)
        if descriptor is None:
            with span("tool.rejected", {"tap.tool.requested": call.name[:128]}):
                return ToolResult("tool-not-allowed", is_error=True)
        with span(f"tool.{descriptor.tool_id}", {"tap.tool.id": descriptor.tool_id}) as current:
            result = await self._execute(run, descriptor, call.arguments)
            current.set_attribute("tap.tool.status", "error" if result.is_error else "ok")
            current.set_attribute("tap.tool.truncated", result.truncated)
            current.set_attribute("tap.tool.evidence_count", len(result.evidence))
            return result

    async def _execute(
        self, run: _Run, descriptor: ToolDescriptor, arguments: Mapping[str, object]
    ) -> ToolResult:
        if not validate_tool_arguments(descriptor.input_schema, arguments):
            return ToolResult("tool-arguments-invalid", is_error=True)
        try:
            executor = await self._registry.executor(run.scope, descriptor.tool_id)
        except ToolRejected:
            return ToolResult("tool-not-allowed", is_error=True)
        try:
            result = await executor.invoke(run.scope, arguments)
        except Exception:
            logger.warning("agent tool %s failed", descriptor.tool_id, exc_info=True)
            return ToolResult("tool-failed", is_error=True)
        if not isinstance(result, ToolResult) or not isinstance(result.content, str):
            return ToolResult("tool-failed", is_error=True)
        return result

    async def _wrap(self, label: str, tool_id: str, result: ToolResult) -> str:
        """Present a tool result as labelled, untrusted data rather than instructions."""

        content = result.content[:_MAX_RESULT_CHARS]
        truncated = result.truncated or len(result.content) > _MAX_RESULT_CHARS
        is_error = result.is_error

        def wrapped(data: str, failed: bool) -> str:
            return json.dumps(
                {
                    "label": label,
                    "tool": tool_id,
                    "status": "error" if failed else "ok",
                    "truncated": truncated,
                    "trust": "untrusted",
                    "data": data,
                },
                ensure_ascii=False,
            )

        if self._redact is None:
            return wrapped(content, is_error)
        try:
            text = wrapped(await self._redact(content), is_error)
            # The gateway only sends text that redaction leaves unchanged.
            if await self._redact(text) == text:
                return text
        except Exception:
            logger.warning("agent tool %s result redaction failed", tool_id, exc_info=True)
        return wrapped("tool-result-unavailable", True)


class AgentToolRuntime:
    """Compose per-Turn tool loops over the shared MCP source and model gateway."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        mcp: McpToolSource | None = None,
        redact: Redact | None = None,
        limits: ToolLoopLimits | None = None,
    ) -> None:
        self._gateway = gateway
        self._mcp = mcp
        self._redact = redact
        self.limits = limits or ToolLoopLimits.for_context(None)

    def registry(self, knowledge_search: ToolExecutor | None = None) -> ToolRegistry:
        return ToolRegistry(builtin_tools(knowledge_search=knowledge_search), mcp=self._mcp)

    def loop(self, knowledge_search: ToolExecutor | None = None) -> ToolLoop:
        return ToolLoop(
            self._gateway, self.registry(knowledge_search), self.limits, redact=self._redact
        )

    async def callable_tools(
        self,
        scope: ProjectScopeContext,
        allowlist: Iterable[str],
        *,
        knowledge_search: bool,
    ) -> frozenset[str]:
        """Allowlisted tools a Turn could call; empty keeps the one-shot answer path."""

        registry = self.registry()
        result: set[str] = set()
        for tool_id in set(allowlist) - RESERVED_TOOLS:
            if tool_id == "knowledge.search":
                if knowledge_search:
                    result.add(tool_id)
                continue
            try:
                descriptor = await registry.get(scope, tool_id)
                if descriptor.callable and descriptor.enabled:
                    await registry.executor(scope, tool_id)
                    result.add(tool_id)
            except ToolRejected:
                continue
        return frozenset(result)
