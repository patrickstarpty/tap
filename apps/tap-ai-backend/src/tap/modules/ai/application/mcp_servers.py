"""Register, sync and enable MCP servers whose tools join the Agent tool registry."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.mcp import (
    MAX_SERVER_TOOLS,
    McpClient,
    McpRejected,
    McpSkippedTool,
    McpToolSpec,
    allowed_mcp_url,
    mcp_descriptor,
    mcp_tool_id,
    mcp_tool_problem,
    validate_auth_header_env,
    validate_display_name,
    validate_server_slug,
)
from tap.modules.ai.domain.tools import ToolDescriptor, ToolExecutor, ToolRejected, ToolResult
from tap.platform.telemetry import span

# Tools whose names or schemas cannot form a registry descriptor are reported, not stored.
_MAX_TOOL_DESCRIPTION = 1024


@dataclass(frozen=True)
class McpServerRecord:
    server_id: str
    slug: str
    display_name: str
    url: str
    auth_header_env: str | None
    enabled: bool = True
    last_synced_at: datetime | None = None
    last_error: str | None = None
    tools: tuple[McpToolSpec, ...] = ()
    skipped_tools: tuple[McpSkippedTool, ...] = ()


@dataclass(frozen=True)
class McpServerView:
    server_id: str
    slug: str
    display_name: str
    url: str
    auth_header_env: str | None
    enabled: bool
    last_synced_at: datetime | None
    last_error: str | None
    tools: tuple[ToolDescriptor, ...] = field(default_factory=tuple)
    skipped_tools: tuple[McpSkippedTool, ...] = field(default_factory=tuple)


class McpServerRepository(Protocol):
    async def create_server(self, scope: ProjectScopeContext, record: McpServerRecord) -> None: ...

    async def get_server(self, scope: ProjectScopeContext, server_id: str) -> McpServerRecord: ...

    async def list_servers(self, scope: ProjectScopeContext) -> tuple[McpServerRecord, ...]: ...

    async def replace_tools(
        self,
        scope: ProjectScopeContext,
        server_id: str,
        tools: tuple[McpToolSpec, ...],
        *,
        synced_at: datetime,
        skipped: tuple[McpSkippedTool, ...] = (),
    ) -> None: ...

    async def record_sync_error(
        self, scope: ProjectScopeContext, server_id: str, code: str, *, at: datetime
    ) -> None: ...

    async def set_enabled(
        self, scope: ProjectScopeContext, server_id: str, enabled: bool
    ) -> None: ...


class InMemoryMcpServerRepository:
    def __init__(self) -> None:
        self.servers: dict[tuple[str, str, str], McpServerRecord] = {}

    @staticmethod
    def _key(scope: ProjectScopeContext, server_id: str) -> tuple[str, str, str]:
        return scope.enterprise_id, scope.project_id, server_id

    async def create_server(self, scope: ProjectScopeContext, record: McpServerRecord) -> None:
        if (
            any(item.slug == record.slug for item in await self.list_servers(scope))
            or self._key(scope, record.server_id) in self.servers
        ):
            raise McpRejected("mcp-server-conflict")
        self.servers[self._key(scope, record.server_id)] = record

    async def get_server(self, scope: ProjectScopeContext, server_id: str) -> McpServerRecord:
        record = self.servers.get(self._key(scope, server_id))
        if record is None:
            raise McpRejected("mcp-server-not-found")
        return record

    async def list_servers(self, scope: ProjectScopeContext) -> tuple[McpServerRecord, ...]:
        return tuple(
            record
            for (enterprise_id, project_id, _), record in sorted(self.servers.items())
            if (enterprise_id, project_id) == (scope.enterprise_id, scope.project_id)
        )

    async def replace_tools(self, scope, server_id, tools, *, synced_at, skipped=()):
        record = await self.get_server(scope, server_id)
        self.servers[self._key(scope, server_id)] = replace(
            record,
            tools=tools,
            skipped_tools=skipped,
            last_synced_at=synced_at,
            last_error=None,
        )

    async def record_sync_error(self, scope, server_id, code, *, at):
        record = await self.get_server(scope, server_id)
        self.servers[self._key(scope, server_id)] = replace(
            record, last_synced_at=at, last_error=code
        )

    async def set_enabled(self, scope, server_id, enabled):
        record = await self.get_server(scope, server_id)
        self.servers[self._key(scope, server_id)] = replace(record, enabled=enabled)


def _descriptors(record: McpServerRecord) -> tuple[ToolDescriptor, ...]:
    values = (
        mcp_descriptor(spec, server_id=record.server_id, slug=record.slug, enabled=record.enabled)
        for spec in record.tools
    )
    return tuple(item for item in values if item is not None)


def _view(record: McpServerRecord) -> McpServerView:
    return McpServerView(
        server_id=record.server_id,
        slug=record.slug,
        display_name=record.display_name,
        url=record.url,
        auth_header_env=record.auth_header_env,
        enabled=record.enabled,
        last_synced_at=record.last_synced_at,
        last_error=record.last_error,
        tools=_descriptors(record),
        skipped_tools=record.skipped_tools,
    )


def _headers(record: McpServerRecord, environ: Mapping[str, str]) -> dict[str, str]:
    if record.auth_header_env is None:
        return {}
    value = environ.get(record.auth_header_env)
    if not value:
        raise McpRejected("mcp-auth-missing")
    return {"Authorization": value}


class McpServerService:
    def __init__(
        self,
        repository: McpServerRepository,
        client: McpClient,
        *,
        extra_hosts: frozenset[str],
        environ: Mapping[str, str] = os.environ,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._repository = repository
        self._client = client
        self._extra_hosts = extra_hosts
        self._environ = environ
        self._clock = clock
        self._ids = id_factory

    async def register(
        self,
        scope: ProjectScopeContext,
        *,
        slug: str,
        display_name: str,
        url: str,
        auth_header_env: str | None,
    ) -> McpServerView:
        record = McpServerRecord(
            server_id=self._ids(),
            slug=validate_server_slug(slug),
            display_name=validate_display_name(display_name),
            url=allowed_mcp_url(url, extra_hosts=self._extra_hosts),
            auth_header_env=None
            if auth_header_env is None
            else validate_auth_header_env(auth_header_env),
        )
        await self._repository.create_server(scope, record)
        return await self.sync(scope, record.server_id)

    async def sync(self, scope: ProjectScopeContext, server_id: str) -> McpServerView:
        record = await self._repository.get_server(scope, server_id)
        now = self._clock()
        try:
            url = allowed_mcp_url(record.url, extra_hosts=self._extra_hosts)
            specs = await self._client.list_tools(url, _headers(record, self._environ))
            if len(specs) > MAX_SERVER_TOOLS:
                raise McpRejected("mcp-too-many-tools")
            if len({item.name for item in specs}) != len(specs):
                raise McpRejected("mcp-protocol-error")
            usable: list[McpToolSpec] = []
            skipped: list[McpSkippedTool] = []
            for spec in specs:
                problem = mcp_tool_problem(spec, slug=record.slug)
                if problem is None:
                    usable.append(
                        replace(spec, description=spec.description[:_MAX_TOOL_DESCRIPTION])
                    )
                else:
                    skipped.append(problem)
            # Distinct server names may normalize to one registry id; never pick one silently.
            if len({mcp_tool_id(record.slug, item.name) for item in usable}) != len(usable):
                raise McpRejected("mcp-tool-name-collision")
        except McpRejected as error:
            # A failed sync keeps the previous tool set and records a stable code.
            await self._repository.record_sync_error(scope, server_id, error.code, at=now)
        else:
            await self._repository.replace_tools(
                scope, server_id, tuple(usable), synced_at=now, skipped=tuple(skipped)
            )
        return _view(await self._repository.get_server(scope, server_id))

    async def set_enabled(
        self, scope: ProjectScopeContext, server_id: str, enabled: bool
    ) -> McpServerView:
        if type(enabled) is not bool:
            raise McpRejected("mcp-server-invalid")
        await self._repository.set_enabled(scope, server_id, enabled)
        return _view(await self._repository.get_server(scope, server_id))

    async def list(self, scope: ProjectScopeContext) -> tuple[McpServerView, ...]:
        return tuple(_view(item) for item in await self._repository.list_servers(scope))


class TracedMcpCall:
    """Record each MCP call as a `tool.mcp.call` span without arguments or result text."""

    def __init__(self, client: McpClient) -> None:
        self._client = client

    async def call(
        self,
        *,
        server: str,
        url: str,
        headers: Mapping[str, str],
        name: str,
        arguments: Mapping[str, object],
    ) -> ToolResult:
        started = time.monotonic()
        with span("tool.mcp.call", {"tap.mcp.server": server, "tap.mcp.tool": name}) as current:
            result = await self._client.call_tool(url, headers, name, arguments)
            current.set_attribute("tap.mcp.duration_ms", int((time.monotonic() - started) * 1000))
            current.set_attribute("tap.mcp.status", "error" if result.is_error else "ok")
            current.set_attribute("tap.mcp.truncated", result.truncated)
            current.set_attribute("tap.mcp.result_bytes", len(result.content.encode("utf-8")))
        return result


class _McpToolExecutor:
    def __init__(
        self,
        calls: TracedMcpCall,
        record: McpServerRecord,
        name: str,
        *,
        extra_hosts: frozenset[str],
        environ: Mapping[str, str],
    ) -> None:
        self._calls = calls
        self._record = record
        self._name = name
        self._extra_hosts = extra_hosts
        self._environ = environ

    async def invoke(
        self, scope: ProjectScopeContext, arguments: Mapping[str, object]
    ) -> ToolResult:
        try:
            url = allowed_mcp_url(self._record.url, extra_hosts=self._extra_hosts)
            headers = _headers(self._record, self._environ)
        except McpRejected as error:
            return ToolResult(content=error.code, is_error=True)
        return await self._calls.call(
            server=self._record.slug,
            url=url,
            headers=headers,
            name=self._name,
            arguments=arguments,
        )


class RepositoryMcpToolSource:
    """`McpToolSource` over registered servers; disabled servers expose disabled tools."""

    def __init__(
        self,
        repository: McpServerRepository,
        client: McpClient,
        *,
        extra_hosts: frozenset[str],
        environ: Mapping[str, str] = os.environ,
    ) -> None:
        self._repository = repository
        self._calls = TracedMcpCall(client)
        self._extra_hosts = extra_hosts
        self._environ = environ

    async def descriptors(self, scope: ProjectScopeContext) -> tuple[ToolDescriptor, ...]:
        return tuple(
            descriptor
            for record in await self._repository.list_servers(scope)
            for descriptor in _descriptors(record)
        )

    async def executor(self, scope: ProjectScopeContext, tool_id: str) -> ToolExecutor:
        for record in await self._repository.list_servers(scope):
            for spec, descriptor in zip(record.tools, _descriptors_aligned(record), strict=True):
                if descriptor is not None and descriptor.tool_id == tool_id:
                    if not descriptor.enabled:
                        raise ToolRejected("tool-not-callable")
                    return _McpToolExecutor(
                        self._calls,
                        record,
                        spec.name,
                        extra_hosts=self._extra_hosts,
                        environ=self._environ,
                    )
        raise ToolRejected("tool-unknown")


def _descriptors_aligned(record: McpServerRecord) -> tuple[ToolDescriptor | None, ...]:
    return tuple(
        mcp_descriptor(spec, server_id=record.server_id, slug=record.slug, enabled=record.enabled)
        for spec in record.tools
    )
