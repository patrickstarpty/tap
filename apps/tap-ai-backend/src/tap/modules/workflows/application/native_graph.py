"""Versioned native Agent loop with resumable root and child contexts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.models import (
    ModelMessage,
    ModelOperation,
    ModelRequest,
    ModelToolCall,
    model_tool_specs,
    text_digest,
)
from tap.modules.ai.domain.skill_activation import (
    SkillCatalogEntry,
    activation_rejection,
    catalog_entry,
    render_skill_catalog,
)
from tap.modules.workflows.application.routing import RouteDecision, RouteKind, merge_route
from tap.modules.workflows.domain.delegation import DelegationRequest

NATIVE_GRAPH_VERSION = "agent-workflow-v1"
NATIVE_STATE_SCHEMA_VERSION = 1

Delegate = Callable[[DelegationRequest, str], Awaitable[str]]
OrdinaryTool = Callable[[str, dict[str, object], str], Awaitable[str]]
RouteCommit = Callable[[RouteDecision], Awaitable[None]]
BeforeModel = Callable[[], Awaitable[str]]
SkillActivation = Callable[[str, tuple[dict[str, object], ...]], Awaitable[dict[str, object]]]


class NativeState(TypedDict, total=False):
    graph_version: str
    state_schema_version: int
    execution_mode: Literal["durable"]
    reasoning_mode: Literal["agentic"]
    prompt: str
    model_alias: str
    messages: list[dict[str, Any]]
    model_round: int
    pending_calls: list[dict[str, Any]]
    answer: str
    waiting_reason: str | None
    child_results: list[dict[str, str]]
    result: dict[str, object]
    route_decision: dict[str, object]
    explicit_kind: RouteKind | None
    requested_ids: list[str]
    activated_skills: list[dict[str, Any]]


class NativeChatGateway(Protocol):
    async def chat(self, request: ModelRequest) -> Any: ...


class NativeWorkflowGraph:
    def __init__(
        self,
        gateway: NativeChatGateway,
        *,
        checkpointer: BaseCheckpointSaver,
        delegate: Delegate | None = None,
        ordinary_tool: OrdinaryTool | None = None,
        route_commit: RouteCommit | None = None,
        before_model: BeforeModel | None = None,
        coding_available: bool = False,
        is_root: bool = True,
        agent_instruction: str = "",
        skill_instructions: tuple[str, ...] = (),
        allowed_tool_ids: tuple[str, ...] = ("knowledge.search",),
        delegate_catalog: tuple[dict[str, str], ...] = (),
        governance_digests: tuple[str, ...] = (),
        frozen_route: RouteDecision | None = None,
        skill_catalog: tuple[SkillCatalogEntry, ...] = (),
        skill_activation: SkillActivation | None = None,
        loaded_skill_ids: tuple[str, ...] = (),
    ) -> None:
        self._gateway = gateway
        self._delegate = delegate
        self._ordinary_tool = ordinary_tool
        self._route_commit = route_commit
        self._before_model = before_model
        self._coding_available = coding_available
        self._is_root = is_root
        self._agent_instruction = agent_instruction
        self._skill_instructions = skill_instructions
        self._allowed_tool_ids = frozenset(allowed_tool_ids)
        self._delegate_catalog = delegate_catalog
        self._governance_digests = governance_digests
        self._frozen_route = frozen_route
        self._skill_catalog = skill_catalog
        self._skill_activation = skill_activation
        self._loaded_skill_ids = frozenset(loaded_skill_ids)
        offered = set(self._allowed_tool_ids) & {"knowledge.search"}
        if is_root and delegate is not None:
            offered.add("workflow.delegate")
        if skill_catalog and skill_activation is not None:
            offered.add("skill.activate")
        self._offered_tools = tuple(sorted(offered))
        self._allowed_wire_names = {
            "knowledge_search"
            if item == "knowledge.search"
            else ("skill_activate" if item == "skill.activate" else "workflow_delegate")
            for item in self._offered_tools
        } | ({"knowledge.search"} if "knowledge.search" in offered else set())
        graph = StateGraph(NativeState)
        graph.add_node("ingress", self._ingress)
        graph.add_node("model", self._model)
        graph.add_node("tools", self._tools)
        graph.add_node("verify", self._verify)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "ingress")
        graph.add_edge("ingress", "model")
        graph.add_conditional_edges(
            "model", self._after_model, {"tools": "tools", "verify": "verify", "wait": END}
        )
        graph.add_conditional_edges("tools", self._after_tools, {"wait": END, "model": "model"})
        graph.add_edge("verify", "finalize")
        graph.add_edge("finalize", END)
        self._graph = graph.compile(checkpointer=checkpointer, name=NATIVE_GRAPH_VERSION)
        self._scope: ProjectScopeContext | None = None
        self._task_id = ""
        self._node_id = ""
        self._graph_run_id = ""

    async def _ingress(self, state: NativeState) -> dict[str, Any]:
        updates: dict[str, Any] = {
            "graph_version": NATIVE_GRAPH_VERSION,
            "state_schema_version": NATIVE_STATE_SCHEMA_VERSION,
            "execution_mode": "durable",
            "reasoning_mode": "agentic",
        }
        children = state.get("child_results") or []
        if children:
            messages = list(state.get("messages") or [])
            for child in children:
                index = next(
                    (
                        i
                        for i, item in enumerate(messages)
                        if item.get("role") == "tool"
                        and item.get("tool_call_id") == child["call_id"]
                    ),
                    None,
                )
                record = {
                    "role": "tool",
                    "content": child["summary"],
                    "tool_call_id": child["call_id"],
                }
                if index is None:
                    messages.append(record)
                else:
                    messages[index] = record
            updates.update(messages=messages, child_results=[], waiting_reason=None)
        return updates

    @staticmethod
    def _route_payload(raw: object) -> tuple[RouteDecision | None, str]:
        if not isinstance(raw, str):
            raise ValueError("native model output must be text")
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return None, raw
        if not isinstance(decoded, dict):
            return None, raw
        route = decoded.get("route")
        suggestion = None
        if isinstance(route, dict):
            suggestion = RouteDecision(
                kind=route["kind"],
                reason=route["reason"],
                confidence=route["confidence"],
                required_capabilities=tuple(route.get("required_capabilities", ())),
            )
        answer = decoded.get("answer")
        return suggestion, answer if isinstance(answer, str) else ""

    async def _model(self, state: NativeState) -> dict[str, Any]:
        assert self._scope is not None
        round_number = int(state.get("model_round") or 0) + 1
        prompt = state["prompt"]
        guidance = await self._before_model() if self._before_model is not None else ""
        context = prompt if not guidance else f"{prompt}\n\n已应用的新要求：\n{guidance}"
        messages = tuple(
            ModelMessage(
                role=item["role"],
                content=str(item.get("content", "")),
                tool_call_id=item.get("tool_call_id"),
                tool_calls=tuple(ModelToolCall(**call) for call in item.get("tool_calls", ())),
            )
            for item in state.get("messages", [])
        )
        instructions = [
            self._agent_instruction,
            *self._skill_instructions,
            *(str(item["instruction"]) for item in state.get("activated_skills", [])),
            render_skill_catalog(self._skill_catalog),
        ]
        if self._is_root:
            instructions.append(
                "Choose native, coding, or execute for this request and explain the choice in "
                "a JSON route object. If native, answer directly or call the offered tools. "
                "Never claim coding or execution happened when the capability is unavailable."
            )
            if self._delegate_catalog:
                instructions.append(
                    "Available specialists: "
                    + json.dumps(
                        self._delegate_catalog,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
        else:
            instructions.append(
                "Fulfill only the delegated objective; do not route or delegate again."
            )
        system_prompt = "\n\n".join(item for item in instructions if item)
        governed_allowlist = (
            frozenset((*self._offered_tools, "knowledge.answer"))
            if self._governance_digests
            else frozenset()
        )
        request = ModelRequest(
            scope=self._scope,
            alias=state["model_alias"],
            operation=ModelOperation.CHAT,
            prompt=system_prompt,
            prompt_digest=text_digest(system_prompt),
            context=context,
            timeout_seconds=60.0,
            idempotency_key=(
                f"{self._task_id}:{self._node_id}:graph:{self._graph_run_id}:model:{round_number}"
            ),
            tools=model_tool_specs(self._offered_tools),
            tool_allowlist=governed_allowlist,
            governance_digests=self._governance_digests,
            messages=messages,
        )
        response = await self._gateway.chat(request)
        suggestion, answer = self._route_payload(response.output)
        route_json = state.get("route_decision")
        if route_json is None and self._frozen_route is not None:
            route_json = {
                "kind": self._frozen_route.kind,
                "reason": self._frozen_route.reason,
                "confidence": self._frozen_route.confidence,
                "required_capabilities": list(self._frozen_route.required_capabilities),
            }
        if route_json is None and not self._is_root:
            route_json = {
                "kind": "native",
                "reason": "delegated specialist",
                "confidence": 1.0,
                "required_capabilities": [],
            }
        if route_json is None:
            try:
                decision = merge_route(
                    explicit_kind=state.get("explicit_kind"),
                    suggested=suggestion,
                    coding_available=self._coding_available,
                )
            except ValueError as error:
                if str(error) == "clarification required before capability expansion":
                    question = "请确认希望我只分析问题，还是需要编码或执行已发布制品？"
                    return {
                        "answer": question,
                        "pending_calls": [],
                        "waiting_reason": "clarification",
                        "messages": [
                            *state.get("messages", []),
                            {"role": "assistant", "content": question},
                        ],
                        "model_round": round_number,
                    }
                if str(error) != "coding capability unavailable":
                    raise
                # Freeze the requested path, then report the unavailable capability
                # without entering its provider or any native tool branch.
                decision = merge_route(
                    explicit_kind=state.get("explicit_kind"),
                    suggested=suggestion,
                    coding_available=True,
                )
            if self._route_commit is not None:
                await self._route_commit(decision)
            route_json = {
                "kind": decision.kind,
                "reason": decision.reason,
                "confidence": decision.confidence,
                "required_capabilities": list(decision.required_capabilities),
            }
        calls: list[dict[str, str]]
        if route_json["kind"] != "native":
            label = "编码" if route_json["kind"] == "coding" else "已发布制品执行"
            answer = f"当前{label}能力不可用，任务未执行。"
            calls = []
        else:
            calls = []
            for call in response.tool_calls:
                if call.name not in self._allowed_wire_names:
                    raise ValueError("model tool is not allowed by frozen Agent configuration")
                calls.append(
                    {
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments_json": call.arguments_json,
                    }
                )
        if (
            self._is_root
            and route_json["kind"] == "native"
            and round_number == 1
            and not calls
            and state.get("requested_ids")
        ):
            calls = [
                {
                    "call_id": f"requested:{agent_id}",
                    "name": "workflow_delegate",
                    "arguments_json": json.dumps(
                        {
                            "agentId": agent_id,
                            "objective": prompt,
                            "inputRefs": [],
                            "expectedOutput": "requested specialist result",
                            "dependsOn": [],
                        }
                    ),
                }
                for agent_id in state["requested_ids"]
            ]
        assistant = {
            "role": "assistant",
            "content": answer,
            "tool_calls": calls,
        }
        return {
            "route_decision": route_json,
            "answer": answer,
            "pending_calls": calls,
            "messages": [*state.get("messages", []), assistant],
            "model_round": round_number,
            "waiting_reason": None,
        }

    @staticmethod
    def _after_model(state: NativeState) -> str:
        if state.get("waiting_reason") == "clarification":
            return "wait"
        return "tools" if state.get("pending_calls") else "verify"

    async def _tools(self, state: NativeState) -> dict[str, Any]:
        messages = list(state.get("messages") or [])
        delegated = False
        activated = list(state.get("activated_skills") or [])
        for raw in state.get("pending_calls", []):
            if raw["name"] not in self._allowed_wire_names:
                raise ValueError("checkpointed tool is not allowed by frozen Agent configuration")
            arguments = json.loads(raw["arguments_json"])
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            if raw["name"] == "workflow_delegate":
                if self._delegate is None:
                    raise ValueError("delegation is unavailable")
                request = DelegationRequest(
                    agent_id=arguments["agentId"],
                    objective=arguments["objective"],
                    input_refs=tuple(arguments.get("inputRefs", ())),
                    expected_output=arguments["expectedOutput"],
                    depends_on=tuple(arguments.get("dependsOn", ())),
                )
                node_id = await self._delegate(request, raw["call_id"])
                messages.append(
                    {
                        "role": "tool",
                        "content": f"delegated:{node_id}",
                        "tool_call_id": raw["call_id"],
                    }
                )
                delegated = True
            elif raw["name"] == "skill_activate":
                if self._skill_activation is None:
                    raise ValueError("Skill activation is unavailable")
                name = arguments.get("name")
                if not isinstance(name, str):
                    raise ValueError("Skill name must be text")
                entry = catalog_entry(name, self._skill_catalog)
                reason = activation_rejection(
                    entry,
                    loaded_skill_ids=self._loaded_skill_ids
                    | frozenset(str(item["skillId"]) for item in activated),
                    auto_count=len(activated),
                    loaded_bytes=sum(len(item.encode("utf-8")) for item in self._skill_instructions)
                    + sum(int(item["bytes"]) for item in activated),
                    candidate_bytes=0,
                )
                if reason is not None:
                    summary = f"Skill activation rejected: {reason}"
                else:
                    assert entry is not None
                    record = await self._skill_activation(name, tuple(activated))
                    if (
                        record.get("revisionId") != entry.revision_id
                        or record.get("skillId") != entry.skill_id
                    ):
                        raise ValueError("Skill activation did not resolve frozen revision")
                    instruction = record.get("instruction")
                    if not isinstance(instruction, str):
                        summary = "Skill activation rejected: skill_disabled"
                    else:
                        size = len(instruction.encode("utf-8"))
                        reason = activation_rejection(
                            entry,
                            loaded_skill_ids=self._loaded_skill_ids
                            | frozenset(str(item["skillId"]) for item in activated),
                            auto_count=len(activated),
                            loaded_bytes=sum(
                                len(item.encode("utf-8")) for item in self._skill_instructions
                            )
                            + sum(int(item["bytes"]) for item in activated),
                            candidate_bytes=size,
                        )
                        if reason is None:
                            activated.append({**record, "bytes": size})
                            summary = f"Activated Skill: {name}"
                        else:
                            summary = f"Skill activation rejected: {reason}"
                messages.append(
                    {"role": "tool", "content": summary, "tool_call_id": raw["call_id"]}
                )
            else:
                if self._ordinary_tool is None:
                    raise ValueError("ordinary tool unavailable")
                summary = await self._ordinary_tool(raw["name"], arguments, raw["call_id"])
                messages.append(
                    {"role": "tool", "content": summary, "tool_call_id": raw["call_id"]}
                )
        return {
            "messages": messages,
            "pending_calls": [],
            "activated_skills": activated,
            "waiting_reason": "children" if delegated else None,
        }

    @staticmethod
    def _after_tools(state: NativeState) -> str:
        return "wait" if state.get("waiting_reason") else "model"

    @staticmethod
    async def _verify(state: NativeState) -> dict[str, Any]:
        answer = state.get("answer", "")
        if not answer.strip():
            raise ValueError("native answer is empty")
        outcome = (
            "completed"
            if state.get("route_decision", {}).get("kind") == "native"
            else "unavailable"
        )
        return {"result": {"answer": answer, "outcome": outcome}}

    @staticmethod
    async def _finalize(state: NativeState) -> dict[str, Any]:
        return {"waiting_reason": None}

    async def run(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        node_id: str,
        prompt: str,
        model_alias: str,
        *,
        explicit_kind: RouteKind | None = None,
        requested_ids: tuple[str, ...] = (),
        child_results: tuple[dict[str, str], ...] = (),
        graph_run_id: str | None = None,
    ) -> NativeState:
        self._scope = scope
        self._task_id = task_id
        self._node_id = node_id
        self._graph_run_id = (
            graph_run_id or hashlib.sha256(f"{task_id}:{node_id}".encode()).hexdigest()
        )
        initial: NativeState = {
            "prompt": prompt,
            "model_alias": model_alias,
            "explicit_kind": explicit_kind,
            "requested_ids": list(requested_ids),
        }
        if child_results:
            initial["child_results"] = list(child_results)
        result = await self._graph.ainvoke(
            cast(Any, initial),
            config=RunnableConfig(
                configurable={
                    "thread_id": self._graph_run_id,
                    "checkpoint_ns": node_id,
                    "graph_version": NATIVE_GRAPH_VERSION,
                    "state_schema_version": NATIVE_STATE_SCHEMA_VERSION,
                    "execution_mode": "durable",
                    "reasoning_mode": "agentic",
                }
            ),
        )
        return cast(NativeState, result)
