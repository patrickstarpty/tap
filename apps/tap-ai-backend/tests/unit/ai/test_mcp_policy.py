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
        ("Bad Name", {"type": "object"}),
        ("deep", {"type": "object", "properties": _deep(40)}),
        ("array", {"type": "array"}),
    ):
        spec = McpToolSpec(name=name, description="", input_schema=schema, read_only=True)
        assert mcp_descriptor(spec, server_id="s", slug="fake", enabled=True) is None


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
