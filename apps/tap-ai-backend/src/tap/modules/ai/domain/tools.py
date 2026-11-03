"""Provider-neutral tool descriptors and allowlist format rules."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from tap.modules.access.domain.context import ProjectScopeContext

RESERVED_TOOLS = frozenset({"knowledge.answer"})
MAX_ALLOWLIST_TOOLS = 32
_TOOL_ID = re.compile(r"[a-z][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*){1,3}")
_MAX_TOOL_ID_LENGTH = 128
_MAX_DISPLAY_NAME_LENGTH = 128
_MAX_DESCRIPTION_LENGTH = 1024
_MAX_SCHEMA_BYTES = 16 * 1024
_MAX_SCHEMA_DEPTH = 16


class ToolRejected(ValueError):
    """A tool identity, descriptor, or allowlist is malformed or not authorized."""

    def __init__(self, reason: str = "tool-rejected") -> None:
        super().__init__(reason)


class ToolEffect(StrEnum):
    READ = "read"
    PROPOSE = "propose"
    EXTERNAL = "external"


class ToolOrigin(StrEnum):
    BUILTIN = "builtin"
    MCP = "mcp"


def validate_tool_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_TOOL_ID_LENGTH
        or _TOOL_ID.fullmatch(value) is None
    ):
        raise ToolRejected("tool-id-invalid")
    return value


def validate_allowlist(values: Iterable[str]) -> frozenset[str]:
    if isinstance(values, str | bytes):
        raise ToolRejected("tool-allowlist-invalid")
    try:
        items = list(values)
    except TypeError as error:
        raise ToolRejected("tool-allowlist-invalid") from error
    if len(items) > MAX_ALLOWLIST_TOOLS:
        raise ToolRejected("tool-allowlist-too-large")
    return frozenset(validate_tool_id(item) for item in items)


def _schema_depth(value: object, depth: int = 0) -> int:
    if isinstance(value, Mapping):
        return max((_schema_depth(item, depth + 1) for item in value.values()), default=depth + 1)
    if isinstance(value, list):
        return max((_schema_depth(item, depth + 1) for item in value), default=depth + 1)
    return depth


def _mapping_json(value: object) -> dict[object, object]:
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("tool schema must be JSON")


def _validate_schema(value: Mapping[str, object]) -> None:
    if not isinstance(value, Mapping) or value.get("type") != "object":
        raise ToolRejected("tool-schema-invalid")
    try:
        encoded = json.dumps(
            dict(value), allow_nan=False, separators=(",", ":"), default=_mapping_json
        )
    except (TypeError, ValueError) as error:
        raise ToolRejected("tool-schema-invalid") from error
    if len(encoded.encode("utf-8")) > _MAX_SCHEMA_BYTES or _schema_depth(value) > (
        _MAX_SCHEMA_DEPTH
    ):
        raise ToolRejected("tool-schema-invalid")


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    tool_id: str
    display_name: str
    description: str
    input_schema: Mapping[str, object]
    effect: ToolEffect
    origin: ToolOrigin
    server_id: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        validate_tool_id(self.tool_id)
        if (
            not isinstance(self.display_name, str)
            or not self.display_name.strip()
            or len(self.display_name) > _MAX_DISPLAY_NAME_LENGTH
            or not isinstance(self.description, str)
            or len(self.description) > _MAX_DESCRIPTION_LENGTH
            or not isinstance(self.effect, ToolEffect)
            or not isinstance(self.origin, ToolOrigin)
            or type(self.enabled) is not bool
        ):
            raise ToolRejected("tool-descriptor-invalid")
        is_mcp = self.origin is ToolOrigin.MCP
        if is_mcp != self.tool_id.startswith("mcp.") or is_mcp != (
            isinstance(self.server_id, str) and bool(self.server_id.strip())
        ):
            raise ToolRejected("tool-descriptor-invalid")
        _validate_schema(self.input_schema)

    @property
    def callable(self) -> bool:
        return self.tool_id not in RESERVED_TOOLS


@dataclass(frozen=True)
class ToolResult:
    content: str
    truncated: bool = False
    evidence: tuple[object, ...] = ()
    is_error: bool = False


class ToolExecutor(Protocol):
    async def invoke(
        self, scope: ProjectScopeContext, arguments: Mapping[str, object]
    ) -> ToolResult: ...


class McpToolSource(Protocol):
    async def descriptors(self, scope: ProjectScopeContext) -> tuple[ToolDescriptor, ...]: ...

    async def executor(self, scope: ProjectScopeContext, tool_id: str) -> ToolExecutor: ...
