"""Built-in tool registry: the single closed set of tool ids agents may request.

This replaces the hard-coded ``{"knowledge.search", "knowledge.answer"}`` closed
sets previously duplicated across the Agent/Skill domain, chat turn input, model
gateway governance, and the LiteLLM adapter's request validation. Every one of
those call sites now derives its closed set from :func:`known_tool_ids`, so
adding a built-in tool means adding one entry here.

``wire_name`` is the online function name a model-calling adapter exposes to
the model (OpenAI-style function names reject ``.``); ``input_schema`` is the
closed JSON Schema object the model must satisfy for a tool call; only
``model_callable`` tools are ever offered to a model (``knowledge.answer`` is
a governance marker, never an invocable function).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolDefinition:
    tool_id: str
    wire_name: str
    names: Mapping[str, str]
    descriptions: Mapping[str, str]
    required: bool
    default_enabled: bool
    model_callable: bool
    input_schema: Mapping[str, object]
    tasks: frozenset[str]


_KNOWLEDGE_SEARCH_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query"],
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 500},
        "sourceIds": {
            "type": "array",
            "maxItems": 20,
            "items": {"type": "string", "minLength": 1, "maxLength": 128},
        },
    },
}

_GRAPH_RELATIONS_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query"],
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 500},
    },
}

_INSIGHTS_QUERY_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["metricIds", "filters", "from", "to", "timezone", "asOf"],
    "properties": {
        "metricIds": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "string",
                "enum": [
                    "first_pass_rate",
                    "final_pass_rate",
                    "retry_recovery_rate",
                    "recovery_contribution_rate",
                    "skipped_count",
                    "p95_duration_seconds",
                ],
            },
        },
        "filters": {
            "type": "object",
            "additionalProperties": False,
            # Optional, mirroring `StructuredInsightsTool.invoke`, which reads each
            # of these with `filters.get(key, ())` rather than requiring them.
            "required": [],
            "properties": {
                "sourceIds": {
                    "type": "array",
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 128},
                },
                "runIds": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {"type": "string", "minLength": 1, "maxLength": 128},
                },
                "buildIds": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {"type": "string", "minLength": 1, "maxLength": 128},
                },
                "branches": {
                    "type": "array",
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 128},
                },
                "environments": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {"type": "string", "minLength": 1, "maxLength": 128},
                },
                "configurations": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {"type": "string", "minLength": 1, "maxLength": 128},
                },
            },
        },
        "from": {"type": "string", "minLength": 1, "maxLength": 32},
        "to": {"type": "string", "minLength": 1, "maxLength": 32},
        "timezone": {"type": "string", "minLength": 1, "maxLength": 64},
        "asOf": {"type": "string", "minLength": 1, "maxLength": 64},
    },
}

TOOL_REGISTRY: Mapping[str, ToolDefinition] = {
    "knowledge.search": ToolDefinition(
        tool_id="knowledge.search",
        wire_name="knowledge_search",
        names={"en": "Knowledge Search", "zh": "知识检索"},
        descriptions={
            "en": "Searches approved knowledge sources for passages relevant to the turn.",
            "zh": "在已核准的知识来源中检索与当前对话相关的内容。",
        },
        required=True,
        default_enabled=True,
        model_callable=True,
        input_schema=_KNOWLEDGE_SEARCH_SCHEMA,
        tasks=frozenset({"knowledge.answer"}),
    ),
    "knowledge.answer": ToolDefinition(
        tool_id="knowledge.answer",
        wire_name="knowledge_answer",
        names={"en": "Knowledge Answer", "zh": "知识问答"},
        descriptions={
            "en": "Produces a governed, citation-grounded answer from retrieved knowledge.",
            "zh": "基于检索到的知识生成受治理、带引用依据的回答。",
        },
        required=True,
        default_enabled=True,
        model_callable=False,
        input_schema={},
        tasks=frozenset({"knowledge.answer"}),
    ),
    "graph.relations": ToolDefinition(
        tool_id="graph.relations",
        wire_name="graph_relations",
        names={"en": "Graph Relations", "zh": "关系图谱"},
        descriptions={
            "en": "Explores entity relations within the turn's frozen knowledge scope.",
            "zh": "在当前对话冻结的知识范围内检索实体关系。",
        },
        required=False,
        default_enabled=True,
        model_callable=True,
        input_schema=_GRAPH_RELATIONS_SCHEMA,
        tasks=frozenset({"knowledge.answer"}),
    ),
    "insights.query": ToolDefinition(
        tool_id="insights.query",
        wire_name="insights_query",
        names={"en": "Insights Query", "zh": "测试洞察查询"},
        descriptions={
            "en": "Queries authorized TAP Insights metrics frozen for the turn.",
            "zh": "查询当前对话冻结授权的 TAP Insights 指标。",
        },
        required=False,
        default_enabled=True,
        model_callable=True,
        input_schema=_INSIGHTS_QUERY_SCHEMA,
        tasks=frozenset({"knowledge.answer"}),
    ),
}

_WIRE_NAME_INDEX: Mapping[str, str] = {
    definition.wire_name: tool_id for tool_id, definition in TOOL_REGISTRY.items()
}


def known_tool_ids() -> frozenset[str]:
    """The closed set of tool ids an agent's ``tool_allowlist`` may draw from."""

    return frozenset(TOOL_REGISTRY)


def required_tool_ids(task: str) -> frozenset[str]:
    """Tool ids that are required (non-closable) for the given task."""

    return frozenset(
        tool_id
        for tool_id, definition in TOOL_REGISTRY.items()
        if definition.required and task in definition.tasks
    )


def tool_for_wire_name(name: str) -> ToolDefinition | None:
    """Resolve a model-facing online function name back to its tool definition."""

    tool_id = _WIRE_NAME_INDEX.get(name)
    if tool_id is None:
        return None
    return TOOL_REGISTRY[tool_id]


def visible_tool_ids(
    *,
    agent_allowlist: frozenset[str] | None,
    relations_available: bool,
    insights_available: bool,
) -> tuple[str, ...]:
    """Model-callable tool ids visible for this turn, in registry order.

    Starts from the model-callable subset of the registry, drops
    ``graph.relations``/``insights.query`` when their runtime availability is
    false, and finally intersects with ``agent_allowlist`` when one is given.
    """

    visible = [
        tool_id for tool_id, definition in TOOL_REGISTRY.items() if definition.model_callable
    ]
    if not relations_available:
        visible = [tool_id for tool_id in visible if tool_id != "graph.relations"]
    if not insights_available:
        visible = [tool_id for tool_id in visible if tool_id != "insights.query"]
    if agent_allowlist is not None:
        visible = [tool_id for tool_id in visible if tool_id in agent_allowlist]
    return tuple(visible)
