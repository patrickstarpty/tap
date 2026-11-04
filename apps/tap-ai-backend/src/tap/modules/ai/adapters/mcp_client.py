"""Streamable HTTP MCP client over the official SDK; stdio servers are never started."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import timedelta

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from tap.modules.ai.domain.mcp import (
    CALL_TIMEOUT_SECONDS,
    MAX_SERVER_TOOLS,
    SYNC_TIMEOUT_SECONDS,
    McpClient,
    McpRejected,
    McpToolSpec,
    host_allowed,
    truncate_result,
)
from tap.modules.ai.domain.tools import ToolResult

__all__ = ["McpClient", "StreamableHttpMcpClient"]

_MAX_LIST_PAGES = 8


class StreamableHttpMcpClient:
    def __init__(
        self,
        *,
        extra_hosts: frozenset[str],
        sync_timeout: float = SYNC_TIMEOUT_SECONDS,
        call_timeout: float = CALL_TIMEOUT_SECONDS,
    ) -> None:
        self._extra_hosts = extra_hosts
        self._sync_timeout = sync_timeout
        self._call_timeout = call_timeout

    def _http_client(self, headers: Mapping[str, str], timeout: float) -> httpx.AsyncClient:
        async def guard(request: httpx.Request) -> None:
            # Every request, including a followed redirect, must stay on an allowed host.
            if not host_allowed(request.url.host, extra_hosts=self._extra_hosts):
                raise McpRejected("mcp-url-not-allowed")

        return httpx.AsyncClient(
            headers=dict(headers),
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            event_hooks={"request": [guard]},
        )

    async def list_tools(self, url: str, headers: Mapping[str, str]) -> tuple[McpToolSpec, ...]:
        specs: list[McpToolSpec] = []
        try:
            with anyio.fail_after(self._sync_timeout):
                async with (
                    self._http_client(headers, self._sync_timeout) as client,
                    streamable_http_client(url, http_client=client) as (read, write, _),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    cursor: str | None = None
                    for _page in range(_MAX_LIST_PAGES):
                        listed = await session.list_tools(cursor=cursor)
                        for tool in listed.tools:
                            specs.append(
                                McpToolSpec(
                                    name=tool.name,
                                    description=tool.description or "",
                                    input_schema=tool.inputSchema,
                                    read_only=bool(
                                        tool.annotations is not None
                                        and tool.annotations.readOnlyHint is True
                                    ),
                                )
                            )
                            if len(specs) > MAX_SERVER_TOOLS:
                                raise McpRejected("mcp-too-many-tools")
                        cursor = listed.nextCursor
                        if not cursor:
                            break
                    else:
                        raise McpRejected("mcp-too-many-tools")
        except McpRejected:
            raise
        except TimeoutError as error:
            raise McpRejected("mcp-timeout") from error
        except Exception as error:
            raise _unwrap(error) from error
        return tuple(specs)

    async def call_tool(
        self,
        url: str,
        headers: Mapping[str, str],
        name: str,
        arguments: Mapping[str, object],
    ) -> ToolResult:
        try:
            with anyio.fail_after(self._call_timeout):
                async with (
                    self._http_client(headers, self._call_timeout) as client,
                    streamable_http_client(url, http_client=client) as (read, write, _),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    result = await session.call_tool(
                        name,
                        dict(arguments),
                        read_timeout_seconds=timedelta(seconds=self._call_timeout),
                    )
        except TimeoutError:
            return ToolResult(content="mcp-timeout", is_error=True)
        except Exception as error:
            return ToolResult(content=_unwrap(error).code, is_error=True)
        parts: list[str] = []
        for item in result.content:
            if isinstance(item, TextContent):
                parts.append(item.text)
            else:
                parts.append(f"[{item.type} content omitted]")
        if not parts and result.structuredContent is not None:
            parts.append(json.dumps(result.structuredContent, ensure_ascii=False, default=str))
        text, truncated = truncate_result("\n".join(parts))
        return ToolResult(content=text, truncated=truncated, is_error=bool(result.isError))


def _unwrap(error: BaseException) -> McpRejected:
    """Map transport failures, possibly wrapped in exception groups, to stable codes."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    unreachable = False
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, McpRejected):
            return current
        if isinstance(current, TimeoutError | httpx.TimeoutException):
            return McpRejected("mcp-timeout")
        unreachable = unreachable or isinstance(current, httpx.HTTPError)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)
    return McpRejected("mcp-unreachable" if unreachable else "mcp-protocol-error")
