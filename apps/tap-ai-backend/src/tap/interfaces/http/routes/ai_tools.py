"""Tool registry catalog and MCP server registration (Streamable HTTP only)."""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Depends, Request, status

from tap.contracts.http import (
    McpServerCreateRequest,
    McpServerPage,
    McpServerUpdateRequest,
    ToolPage,
    ToolView,
)
from tap.contracts.http import McpServerView as McpServerContract
from tap.interfaces.http.dependencies import mcp_server_service, tool_registry_service
from tap.interfaces.http.problems import problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.modules.ai.application.mcp_servers import McpServerView
from tap.modules.ai.domain.tools import ToolDescriptor

router = APIRouter(prefix="/ai", tags=["ai"])

_MCP_RESPONSES: dict[int | str, dict[str, object]] = {
    404: problem_response_metadata("MCP server not found"),
    409: problem_response_metadata("MCP server conflict"),
    422: problem_response_metadata("MCP server rejected"),
    503: problem_response_metadata("Knowledge runtime unavailable"),
}


def _tool(value: ToolDescriptor) -> ToolView:
    return ToolView(
        tool_id=value.tool_id,
        display_name=value.display_name,
        description=value.description,
        effect=cast(Literal["read", "propose", "external"], value.effect.value),
        origin=cast(Literal["builtin", "mcp"], value.origin.value),
        server_id=value.server_id,
        enabled=value.enabled,
        callable=value.callable,
    )


def _server(value: McpServerView) -> McpServerContract:
    return McpServerContract(
        server_id=value.server_id,
        slug=value.slug,
        display_name=value.display_name,
        url=value.url,
        auth_header_env=value.auth_header_env,
        enabled=value.enabled,
        last_synced_at=None if value.last_synced_at is None else value.last_synced_at.isoformat(),
        last_error=value.last_error,
        tools=[_tool(item) for item in value.tools],
    )


@router.get(
    "/tools",
    operation_id="ai_list_tools",
    response_model=ToolPage,
    dependencies=[Depends(project_authorization("ai.agents.read"))],
    responses={
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def list_tools(request: Request) -> ToolPage:
    values = await tool_registry_service(request).list(request.state.project_scope)
    return ToolPage(items=[_tool(item) for item in values])


@router.get(
    "/mcp-servers",
    operation_id="ai_list_mcp_servers",
    response_model=McpServerPage,
    dependencies=[Depends(project_authorization("ai.tools.manage"))],
    responses={
        422: problem_response_metadata("Request validation failed"),
        503: problem_response_metadata("Knowledge runtime unavailable"),
    },
)
async def list_mcp_servers(request: Request) -> McpServerPage:
    values = await mcp_server_service(request).list(request.state.project_scope)
    return McpServerPage(items=[_server(item) for item in values])


@router.post(
    "/mcp-servers",
    operation_id="ai_register_mcp_server",
    response_model=McpServerContract,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(project_authorization("ai.tools.manage"))],
    responses=_MCP_RESPONSES,
)
async def register_mcp_server(request: Request, body: McpServerCreateRequest) -> McpServerContract:
    return _server(
        await mcp_server_service(request).register(
            request.state.project_scope,
            slug=body.slug,
            display_name=body.display_name,
            url=body.url,
            auth_header_env=body.auth_header_env,
        )
    )


@router.post(
    "/mcp-servers/{server_id}/sync",
    operation_id="ai_sync_mcp_server",
    response_model=McpServerContract,
    dependencies=[Depends(project_authorization("ai.tools.manage"))],
    responses=_MCP_RESPONSES,
)
async def sync_mcp_server(request: Request, server_id: str) -> McpServerContract:
    return _server(await mcp_server_service(request).sync(request.state.project_scope, server_id))


@router.patch(
    "/mcp-servers/{server_id}",
    operation_id="ai_update_mcp_server",
    response_model=McpServerContract,
    dependencies=[Depends(project_authorization("ai.tools.manage"))],
    responses=_MCP_RESPONSES,
)
async def update_mcp_server(
    request: Request, server_id: str, body: McpServerUpdateRequest
) -> McpServerContract:
    return _server(
        await mcp_server_service(request).set_enabled(
            request.state.project_scope, server_id, body.enabled
        )
    )
