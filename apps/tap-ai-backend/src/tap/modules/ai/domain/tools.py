"""Built-in tool registry: the single closed set of tool ids agents may request.

This replaces the hard-coded ``{"knowledge.search", "knowledge.answer"}`` closed
sets previously duplicated across the Agent/Skill domain, chat turn input, model
gateway governance, and the LiteLLM adapter's request validation. Every one of
those call sites now derives its closed set from :func:`known_tool_ids`, so
adding a built-in tool means adding one entry here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolDefinition:
    tool_id: str
    names: Mapping[str, str]
    descriptions: Mapping[str, str]
    required: bool
    tasks: frozenset[str]


TOOL_REGISTRY: Mapping[str, ToolDefinition] = {
    "knowledge.search": ToolDefinition(
        tool_id="knowledge.search",
        names={"en": "Knowledge Search", "zh": "知识检索"},
        descriptions={
            "en": "Searches approved knowledge sources for passages relevant to the turn.",
            "zh": "在已核准的知识来源中检索与当前对话相关的内容。",
        },
        required=True,
        tasks=frozenset({"knowledge.answer"}),
    ),
    "knowledge.answer": ToolDefinition(
        tool_id="knowledge.answer",
        names={"en": "Knowledge Answer", "zh": "知识问答"},
        descriptions={
            "en": "Produces a governed, citation-grounded answer from retrieved knowledge.",
            "zh": "基于检索到的知识生成受治理、带引用依据的回答。",
        },
        required=True,
        tasks=frozenset({"knowledge.answer"}),
    ),
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
