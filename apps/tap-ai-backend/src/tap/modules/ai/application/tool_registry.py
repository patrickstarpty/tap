"""Composite registry of built-in and MCP tools that resolves Agent allowlists."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.tools import (
    McpToolSource,
    ToolDescriptor,
    ToolEffect,
    ToolExecutor,
    ToolOrigin,
    ToolRejected,
    validate_allowlist,
)

__all__ = ["ToolRegistry", "ToolRejected", "builtin_tools"]

_SYSTEM_EFFECTS = frozenset({ToolEffect.READ, ToolEffect.PROPOSE})

KNOWLEDGE_SEARCH_SCHEMA: Mapping[str, object] = MappingProxyType(
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 500},
            "limit": {"type": "integer", "minimum": 1, "maximum": 8},
        },
    }
)


def graph_builtin_tools() -> Mapping[str, tuple[ToolDescriptor, ToolExecutor | None]]:
    string = {"type": "string", "minLength": 1, "maxLength": 512}
    tools = {}
    for identity, properties, required in (
        (
            "graph.neighbors",
            {
                "nodeId": string,
                "nodeLabel": string,
                "depth": {"type": "integer", "minimum": 1, "maximum": 2},
            },
            [],
        ),
        (
            "graph.path",
            {
                "from": string,
                "to": string,
                "maxHops": {"type": "integer", "minimum": 1, "maximum": 4},
            },
            ["from", "to"],
        ),
    ):
        descriptor = ToolDescriptor(
            tool_id=identity,
            display_name=identity,
            description=(
                "Explore the immutable knowledge graph authorized for this Turn; "
                "returns citable R relations."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": required,
                "properties": properties,
            },
            effect=ToolEffect.READ,
            origin=ToolOrigin.BUILTIN,
        )
        tools[identity] = (descriptor, None)
    return tools


def builtin_tools(
    *, knowledge_search: ToolExecutor | None = None
) -> Mapping[str, tuple[ToolDescriptor, ToolExecutor | None]]:
    """Return the built-in catalog; executors are composed by trusted runtime wiring."""

    answer = ToolDescriptor(
        tool_id="knowledge.answer",
        display_name="Knowledge answer",
        description="Reserved governed-answer capability; never exposed as a callable tool.",
        input_schema={"type": "object", "properties": {}},
        effect=ToolEffect.READ,
        origin=ToolOrigin.BUILTIN,
    )
    search = ToolDescriptor(
        tool_id="knowledge.search",
        display_name="Knowledge search",
        description="Search published Project knowledge and return labelled evidence.",
        input_schema=KNOWLEDGE_SEARCH_SCHEMA,
        effect=ToolEffect.READ,
        origin=ToolOrigin.BUILTIN,
    )
    return MappingProxyType(
        {
            answer.tool_id: (answer, None),
            search.tool_id: (search, knowledge_search),
            **graph_builtin_tools(),
        }
    )


class ToolRegistry:
    def __init__(
        self,
        builtins: Mapping[str, tuple[ToolDescriptor, ToolExecutor | None]],
        mcp: McpToolSource | None = None,
    ) -> None:
        for tool_id, (descriptor, _executor) in builtins.items():
            if descriptor.tool_id != tool_id or descriptor.origin is not ToolOrigin.BUILTIN:
                raise ToolRejected("tool-descriptor-invalid")
        self._builtins = MappingProxyType(dict(builtins))
        self._mcp = mcp

    async def list(self, scope: ProjectScopeContext) -> tuple[ToolDescriptor, ...]:
        return (*(item for item, _ in self._builtins.values()), *(await self._mcp_tools(scope)))

    async def get(self, scope: ProjectScopeContext, tool_id: str) -> ToolDescriptor:
        builtin = self._builtins.get(tool_id)
        if builtin is not None:
            return builtin[0]
        for descriptor in await self._mcp_tools(scope):
            if descriptor.tool_id == tool_id:
                return descriptor
        raise ToolRejected("tool-unknown")

    async def require_allowlist(
        self, scope: ProjectScopeContext, tool_ids: Iterable[str], *, system: bool = False
    ) -> frozenset[str]:
        allowlist = validate_allowlist(tool_ids)
        known = {item.tool_id: item for item in await self.list(scope)}
        for tool_id in allowlist:
            descriptor = known.get(tool_id)
            if descriptor is None:
                raise ToolRejected("tool-unknown")
            if not descriptor.enabled:
                raise ToolRejected("tool-disabled")
            if system and descriptor.effect not in _SYSTEM_EFFECTS:
                raise ToolRejected("tool-effect-not-allowed")
        return allowlist

    async def executor(self, scope: ProjectScopeContext, tool_id: str) -> ToolExecutor:
        descriptor = await self.get(scope, tool_id)
        if not descriptor.callable or not descriptor.enabled:
            raise ToolRejected("tool-not-callable")
        builtin = self._builtins.get(tool_id)
        if builtin is not None:
            if builtin[1] is None:
                raise ToolRejected("tool-not-callable")
            return builtin[1]
        if self._mcp is None:
            raise ToolRejected("tool-unknown")
        return await self._mcp.executor(scope, tool_id)

    async def _mcp_tools(self, scope: ProjectScopeContext) -> tuple[ToolDescriptor, ...]:
        if self._mcp is None:
            return ()
        values = await self._mcp.descriptors(scope)
        for descriptor in values:
            if descriptor.origin is not ToolOrigin.MCP or descriptor.tool_id in self._builtins:
                raise ToolRejected("tool-descriptor-invalid")
        return values
