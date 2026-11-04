"""MCP server policy: Streamable HTTP URLs, host allowlist, slugs and result bounds."""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from tap.modules.ai.domain.tools import (
    ToolDescriptor,
    ToolEffect,
    ToolOrigin,
    ToolRejected,
    ToolResult,
)

ALLOWED_HOSTS_ENV = "TAP_MCP_ALLOWED_HOSTS"
SYNC_TIMEOUT_SECONDS = 10.0
CALL_TIMEOUT_SECONDS = 30.0
MAX_SERVER_TOOLS = 64
MAX_RESULT_BYTES = 16 * 1024
MAX_URL_LENGTH = 2048
MAX_SLUG_LENGTH = 32
MAX_DISPLAY_NAME_LENGTH = 128
_MAX_TOOL_DESCRIPTION = 1024
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
_SCHEMES = frozenset({"http", "https"})


class McpRejected(ValueError):
    """An MCP server registration, sync or URL violates the server policy."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class McpToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, object]
    read_only: bool


class McpClient(Protocol):
    async def list_tools(self, url: str, headers: Mapping[str, str]) -> tuple[McpToolSpec, ...]: ...

    async def call_tool(
        self,
        url: str,
        headers: Mapping[str, str],
        name: str,
        arguments: Mapping[str, object],
    ) -> ToolResult: ...


def mcp_allowed_hosts(value: str | None = None) -> frozenset[str]:
    """Hosts added to the loopback default by the comma-separated environment setting."""
    raw = os.environ.get(ALLOWED_HOSTS_ENV, "") if value is None else value
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_allowed(host: str | None, *, extra_hosts: frozenset[str]) -> bool:
    if not host:
        return False
    host = host.lower()
    return _loopback(host) or host in extra_hosts


def allowed_mcp_url(url: str, *, extra_hosts: frozenset[str]) -> str:
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise McpRejected("mcp-url-not-allowed")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        _ = parts.port  # raises for an out-of-range port
    except ValueError as error:
        raise McpRejected("mcp-url-not-allowed") from error
    if (
        parts.scheme not in _SCHEMES
        or parts.username is not None
        or parts.password is not None
        or not host_allowed(host, extra_hosts=extra_hosts)
    ):
        raise McpRejected("mcp-url-not-allowed")
    return url


def validate_server_slug(value: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_SLUG_LENGTH or _SLUG.fullmatch(value) is None:
        raise McpRejected("mcp-server-invalid")
    return value


def validate_display_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_DISPLAY_NAME_LENGTH:
        raise McpRejected("mcp-server-invalid")
    return value.strip()


def validate_auth_header_env(value: str) -> str:
    """Only an environment variable name is stored; the secret never enters the database."""
    if not isinstance(value, str) or _ENV_NAME.fullmatch(value) is None:
        raise McpRejected("mcp-server-invalid")
    return value


def mcp_tool_id(slug: str, name: str) -> str:
    return f"mcp.{slug}.{name}"


def mcp_descriptor(
    spec: McpToolSpec, *, server_id: str, slug: str, enabled: bool
) -> ToolDescriptor | None:
    """Map an untrusted MCP tool to a registry descriptor, or None when it is unusable."""
    try:
        return ToolDescriptor(
            tool_id=mcp_tool_id(slug, spec.name),
            display_name=spec.name,
            description=spec.description[:_MAX_TOOL_DESCRIPTION],
            input_schema=spec.input_schema,
            effect=ToolEffect.READ if spec.read_only else ToolEffect.EXTERNAL,
            origin=ToolOrigin.MCP,
            server_id=server_id,
            enabled=enabled,
        )
    except (ToolRejected, TypeError):
        return None


def truncate_result(text: str) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_RESULT_BYTES:
        return text, False
    return encoded[:MAX_RESULT_BYTES].decode("utf-8", errors="ignore"), True
