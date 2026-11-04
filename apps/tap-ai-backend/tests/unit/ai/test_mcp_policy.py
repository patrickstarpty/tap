from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9000/mcp",
        "https://localhost/mcp",
        "http://[::1]:9000/mcp",
        "http://127.0.0.2:8080/mcp",
        "HTTP://LOCALHOST:9000/mcp",
    ],
)
def test_loopback_mcp_urls_are_allowed_by_default(url: str) -> None:
    from tap.modules.ai.domain.mcp import allowed_mcp_url

    assert allowed_mcp_url(url, extra_hosts=frozenset()) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/mcp",
        "file:///etc/passwd",
        "ftp://127.0.0.1/mcp",
        "ws://127.0.0.1:9000/mcp",
        "http://user:secret@127.0.0.1:9000/mcp",
        "http:///mcp",
        "127.0.0.1:9000/mcp",
        "http://10.0.0.1/mcp",
        "http://localhost.example.com/mcp",
        "http://127.0.0.1:9000/" + "a" * 2048,
        "",
        "http://0.0.0.0:9000/mcp",
        "http://[::]:9000/mcp",
        "http://[::ffff:127.0.0.1]:9000/mcp",
        "http://[::ffff:7f00:1]/mcp",
        "http://[::127.0.0.1]/mcp",
        "http://127.1/mcp",
        "http://2130706433/mcp",
    ],
)
def test_non_loopback_and_non_http_urls_are_rejected(url: str) -> None:
    from tap.modules.ai.domain.mcp import McpRejected, allowed_mcp_url

    with pytest.raises(McpRejected) as raised:
        allowed_mcp_url(url, extra_hosts=frozenset())
    assert raised.value.code == "mcp-url-not-allowed"


def test_configured_hosts_extend_the_allowlist(monkeypatch) -> None:
    from tap.modules.ai.domain.mcp import McpRejected, allowed_mcp_url, mcp_allowed_hosts

    monkeypatch.setenv("TAP_MCP_ALLOWED_HOSTS", " mcp.internal , Tools.Example.org,,")
    hosts = mcp_allowed_hosts()

    assert hosts == frozenset({"mcp.internal", "tools.example.org"})
    assert allowed_mcp_url("https://mcp.internal/mcp", extra_hosts=hosts)
    assert allowed_mcp_url("http://TOOLS.example.org:8443/x", extra_hosts=hosts)
    with pytest.raises(McpRejected):
        allowed_mcp_url("https://other.internal/mcp", extra_hosts=hosts)
    monkeypatch.delenv("TAP_MCP_ALLOWED_HOSTS")
    assert mcp_allowed_hosts() == frozenset()


@pytest.mark.parametrize("slug", ["jira", "jira-cloud", "a1", "x" * 32])
def test_valid_server_slugs(slug: str) -> None:
    from tap.modules.ai.domain.mcp import validate_server_slug

    assert validate_server_slug(slug) == slug


@pytest.mark.parametrize(
    "slug", ["", "Jira", "jira_cloud", "-jira", "jira-", "jira--cloud", "x" * 33, "jira.cloud"]
)
def test_invalid_server_slugs(slug: str) -> None:
    from tap.modules.ai.domain.mcp import McpRejected, validate_server_slug

    with pytest.raises(McpRejected) as raised:
        validate_server_slug(slug)
    assert raised.value.code == "mcp-server-invalid"


@pytest.mark.parametrize("name", ["JIRA_TOKEN", "_TOKEN", "TOKEN2"])
def test_auth_header_env_accepts_variable_names_only(name: str) -> None:
    from tap.modules.ai.domain.mcp import validate_auth_header_env

    assert validate_auth_header_env(name) == name


@pytest.mark.parametrize("name", ["", "jira_token", "Bearer abc", "2TOKEN", "A" * 129])
def test_auth_header_env_rejects_secrets_and_bad_names(name: str) -> None:
    from tap.modules.ai.domain.mcp import McpRejected, validate_auth_header_env

    with pytest.raises(McpRejected):
        validate_auth_header_env(name)


def test_tool_spec_maps_to_a_registry_descriptor() -> None:
    from tap.modules.ai.domain.mcp import McpToolSpec, mcp_descriptor
    from tap.modules.ai.domain.tools import ToolEffect, ToolOrigin

    read = mcp_descriptor(
        McpToolSpec(
            name="echo", description="Echo", input_schema={"type": "object"}, read_only=True
        ),
        server_id="server-1",
        slug="fake",
        enabled=True,
    )
    write = mcp_descriptor(
        McpToolSpec(
            name="create_ticket",
            description="x" * 2000,
            input_schema={"type": "object"},
            read_only=False,
        ),
        server_id="server-1",
        slug="fake",
        enabled=False,
    )

    assert read is not None and read.tool_id == "mcp.fake.echo"
    assert (read.effect, read.origin, read.server_id) == (
        ToolEffect.READ,
        ToolOrigin.MCP,
        "server-1",
    )
    assert write is not None and write.effect is ToolEffect.EXTERNAL
    assert write.enabled is False and len(write.description) == 1024
    for name, schema in (
        ("", {"type": "object"}),
        ("_hidden", {"type": "object"}),
        ("x" * 129, {"type": "object"}),
        ("deep", {"type": "object", "properties": _deep(40)}),
        ("array", {"type": "array"}),
    ):
        spec = McpToolSpec(name=name, description="", input_schema=schema, read_only=True)
        assert mcp_descriptor(spec, server_id="s", slug="fake", enabled=True) is None


@pytest.mark.parametrize(
    ("name", "tool_id"),
    [
        ("getWeather", "mcp.fake.getweather"),
        ("Bad Name", "mcp.fake.bad_name"),
        ("files/read.v2", "mcp.fake.files_read_v2"),
        ("echo", "mcp.fake.echo"),
    ],
)
def test_non_canonical_tool_names_normalize_but_keep_the_original_name(name, tool_id) -> None:
    from tap.modules.ai.domain.mcp import McpToolSpec, mcp_descriptor, mcp_tool_problem

    spec = McpToolSpec(name=name, description="", input_schema={"type": "object"}, read_only=True)
    descriptor = mcp_descriptor(spec, server_id="s", slug="fake", enabled=True)

    assert descriptor is not None
    assert (descriptor.tool_id, descriptor.display_name) == (tool_id, name)
    assert mcp_tool_problem(spec, slug="fake") is None


def test_unusable_tools_report_a_reason() -> None:
    from tap.modules.ai.domain.mcp import McpSkippedTool, McpToolSpec, mcp_tool_problem

    def problem(name: str, schema: dict[str, object]) -> McpSkippedTool | None:
        spec = McpToolSpec(name=name, description="", input_schema=schema, read_only=True)
        return mcp_tool_problem(spec, slug="fake")

    assert problem("_hidden", {"type": "object"}) == McpSkippedTool("_hidden", "name-invalid")
    assert problem("x" * 300, {"type": "object"}) == McpSkippedTool("x" * 128, "name-invalid")
    assert problem("list", {"type": "array"}) == McpSkippedTool("list", "schema-invalid")


class _ListingClient:
    def __init__(self, specs) -> None:
        self.specs = specs
        self.called: list[str] = []

    async def list_tools(self, url, headers):
        return self.specs

    async def call_tool(self, url, headers, name, arguments):
        from tap.modules.ai.domain.tools import ToolResult

        self.called.append(name)
        return ToolResult("ok")


def _spec(name: str, schema: dict[str, object] | None = None):
    from tap.modules.ai.domain.mcp import McpToolSpec

    return McpToolSpec(
        name=name, description="d", input_schema=schema or {"type": "object"}, read_only=True
    )


@pytest.mark.asyncio
async def test_sync_keeps_camel_case_tools_reports_skipped_and_calls_the_original_name() -> None:
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.application.mcp_servers import (
        InMemoryMcpServerRepository,
        McpServerService,
        RepositoryMcpToolSource,
    )
    from tap.modules.ai.domain.mcp import McpSkippedTool

    repository = InMemoryMcpServerRepository()
    client = _ListingClient(
        (_spec("getWeather"), _spec("_hidden"), _spec("table", {"type": "array"}))
    )
    service = McpServerService(repository, client, extra_hosts=frozenset())

    view = await service.register(
        VALIDATION_SCOPE,
        slug="fake",
        display_name="Fake",
        url="http://127.0.0.1:9000/mcp",
        auth_header_env=None,
    )

    assert view.last_error is None
    assert [item.tool_id for item in view.tools] == ["mcp.fake.getweather"]
    assert view.skipped_tools == (
        McpSkippedTool("_hidden", "name-invalid"),
        McpSkippedTool("table", "schema-invalid"),
    )
    assert (await service.list(VALIDATION_SCOPE))[0].skipped_tools == view.skipped_tools
    source = RepositoryMcpToolSource(repository, client, extra_hosts=frozenset())
    executor = await source.executor(VALIDATION_SCOPE, "mcp.fake.getweather")
    await executor.invoke(VALIDATION_SCOPE, {})
    assert client.called == ["getWeather"]


@pytest.mark.asyncio
async def test_sync_fails_explicitly_when_names_collide_after_normalization() -> None:
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.application.mcp_servers import (
        InMemoryMcpServerRepository,
        McpServerService,
    )

    repository = InMemoryMcpServerRepository()
    client = _ListingClient((_spec("echo"),))
    service = McpServerService(repository, client, extra_hosts=frozenset())
    view = await service.register(
        VALIDATION_SCOPE,
        slug="fake",
        display_name="Fake",
        url="http://127.0.0.1:9000/mcp",
        auth_header_env=None,
    )
    client.specs = (_spec("getWeather"), _spec("GetWeather"), _spec("echo"))

    synced = await service.sync(VALIDATION_SCOPE, view.server_id)

    assert synced.last_error == "mcp-tool-name-collision"
    assert [item.tool_id for item in synced.tools] == ["mcp.fake.echo"]


@pytest.mark.asyncio
async def test_mcp_transport_never_reaches_a_redirect_or_disallowed_host() -> None:
    import httpx

    from tap.modules.ai.adapters.mcp_client import StreamableHttpMcpClient
    from tap.modules.ai.domain.mcp import McpRejected

    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.host))
        return httpx.Response(307, headers={"location": "http://evil.example/mcp"})

    mcp = StreamableHttpMcpClient(extra_hosts=frozenset())
    async with mcp._http_client({}, 1.0) as client:
        client._transport = httpx.MockTransport(respond)
        response = await client.post("http://127.0.0.1:9000/mcp")
        assert response.status_code == 307
        with pytest.raises(McpRejected, match="mcp-url-not-allowed"):
            await client.post("http://127.0.0.1:9000/mcp", follow_redirects=True)
        for host in ("evil.example", "0.0.0.0", "[::ffff:127.0.0.1]"):
            with pytest.raises(McpRejected, match="mcp-url-not-allowed"):
                await client.post(f"http://{host}/mcp")

    assert seen == ["127.0.0.1", "127.0.0.1"]


def _deep(depth: int) -> dict[str, object]:
    value: dict[str, object] = {}
    for _ in range(depth):
        value = {"x": value}
    return value


def test_result_text_is_truncated_at_16_kib_on_a_character_boundary() -> None:
    from tap.modules.ai.domain.mcp import MAX_RESULT_BYTES, truncate_result

    assert truncate_result("short") == ("short", False)
    text, truncated = truncate_result("é" * MAX_RESULT_BYTES)
    assert truncated is True
    assert len(text.encode("utf-8")) <= MAX_RESULT_BYTES
    assert text == "é" * (MAX_RESULT_BYTES // 2)
