"""Fake Streamable HTTP MCP servers bound to an ephemeral loopback port for tests."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


def fake_server(*, tool_count: int = 0, extra_names: tuple[str, ...] = ()) -> FastMCP:
    server = FastMCP("fake", stateless_http=True, json_response=True, log_level="WARNING")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True), description="Echo the text.")
    def echo(text: str) -> str:
        return f"echo: {text}"

    @server.tool(description="Create a ticket.")
    def create_ticket(title: str) -> str:
        return f"created {title}"

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True), description="Large output.")
    def big(size: int) -> str:
        return "x" * size

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True), description="Slow tool.")
    async def slow(seconds: float) -> str:
        await asyncio.sleep(seconds)
        return "done"

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True), description="Auth header.")
    def whoami() -> str:
        request = server.get_context().request_context.request
        return "" if request is None else request.headers.get("authorization", "")

    for index in range(tool_count):
        server.add_tool(lambda: "ok", name=f"extra_{index}", description="Extra tool.")
    for name in extra_names:
        server.add_tool(lambda name=name: f"called {name}", name=name, description="Named tool.")
    return server


@contextmanager
def serve(server: FastMCP) -> Iterator[str]:
    """Run the FastMCP Streamable HTTP app on 127.0.0.1 and yield its `/mcp` URL."""
    config = uvicorn.Config(
        server.streamable_http_app(), host="127.0.0.1", port=0, log_level="warning", lifespan="on"
    )
    runner = uvicorn.Server(config)
    thread = threading.Thread(target=runner.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not runner.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("fake MCP server did not start")
        time.sleep(0.01)
    port = runner.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        runner.should_exit = True
        thread.join(timeout=10)
