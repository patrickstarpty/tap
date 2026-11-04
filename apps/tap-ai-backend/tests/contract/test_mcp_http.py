from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tests.mcp_fake import fake_server, serve

_ORIGIN = "http://127.0.0.1:15175"
_BASE = "/api/v1/projects/tapper-demo/ai"


class _Authority:
    def __init__(self, allowed: set[str]) -> None:
        self.allowed = allowed

    async def current(self, _facts):
        return VALIDATION_SCOPE

    async def authorize(self, _scope, action, _resource):
        from tap.modules.access.domain.authorization import AuthorizationDecision

        return AuthorizationDecision(action in self.allowed, "test")


def _client(allowed: set[str], *, environ: dict[str, str] | None = None):
    from tap.modules.ai.adapters.mcp_client import StreamableHttpMcpClient
    from tap.modules.ai.application.mcp_servers import (
        InMemoryMcpServerRepository,
        McpServerService,
        RepositoryMcpToolSource,
    )
    from tap.modules.ai.application.tool_registry import ToolRegistry, builtin_tools

    repository = InMemoryMcpServerRepository()
    mcp = StreamableHttpMcpClient(extra_hosts=frozenset(), call_timeout=1.0)
    environment = {} if environ is None else environ
    registry = ToolRegistry(
        builtin_tools(),
        RepositoryMcpToolSource(repository, mcp, extra_hosts=frozenset(), environ=environment),
    )
    authority = _Authority(allowed)
    app = create_app(
        HttpServices(
            scope=VALIDATION_SCOPE,
            scope_provider=authority,
            authorization_policy=authority,
            tool_registry=registry,
            mcp_servers=McpServerService(
                repository, mcp, extra_hosts=frozenset(), environ=environment
            ),
        ),
        allowed_origins=frozenset({_ORIGIN}),
    )
    return TestClient(app, headers={"Origin": _ORIGIN}), registry


_ALL = {"ai.tools.manage", "ai.agents.read"}


def test_registered_mcp_tools_join_the_registry_and_follow_the_server_switch(
    span_recorder,
) -> None:
    from tap.modules.ai.domain.tools import ToolRejected

    client, registry = _client(_ALL)
    with serve(fake_server()) as url:
        created = client.post(
            f"{_BASE}/mcp-servers", json={"slug": "fake", "displayName": "Fake", "url": url}
        )
        assert created.status_code == 201, created.text
        server = created.json()
        assert server["lastError"] is None and server["enabled"] is True
        assert server["lastSyncedAt"] is not None
        assert server["authHeaderEnv"] is None
        tools = {item["toolId"]: item for item in server["tools"]}
        assert tools["mcp.fake.echo"]["effect"] == "read"
        assert tools["mcp.fake.create_ticket"]["effect"] == "external"

        listed = client.get(f"{_BASE}/tools")
        assert listed.status_code == 200, listed.text
        catalog = {item["toolId"]: item for item in listed.json()["items"]}
        assert catalog["knowledge.answer"]["callable"] is False
        assert catalog["knowledge.search"]["origin"] == "builtin"
        assert catalog["mcp.fake.echo"] == {
            "toolId": "mcp.fake.echo",
            "displayName": "echo",
            "description": "Echo the text.",
            "effect": "read",
            "origin": "mcp",
            "serverId": server["serverId"],
            "enabled": True,
            "callable": True,
        }
        assert catalog["mcp.fake.create_ticket"]["effect"] == "external"

        async def call(tool_id: str, arguments: dict[str, object]):
            executor = await registry.executor(VALIDATION_SCOPE, tool_id)
            return await executor.invoke(VALIDATION_SCOPE, arguments)

        echoed = asyncio.run(call("mcp.fake.echo", {"text": "hi"}))
        assert (echoed.content, echoed.is_error, echoed.truncated) == ("echo: hi", False, False)
        large = asyncio.run(call("mcp.fake.big", {"size": 20_000}))
        assert large.truncated is True and len(large.content.encode()) == 16 * 1024
        slow = asyncio.run(call("mcp.fake.slow", {"seconds": 3}))
        assert slow.is_error is True and slow.content == "mcp-timeout"

        spans = [
            item for item in span_recorder.get_finished_spans() if item.name == "tool.mcp.call"
        ]
        assert len(spans) == 3
        assert spans[0].attributes["tap.mcp.server"] == "fake"
        assert spans[0].attributes["tap.mcp.tool"] == "echo"
        assert spans[0].attributes["tap.mcp.status"] == "ok"
        assert spans[2].attributes["tap.mcp.status"] == "error"
        assert "tap.mcp.duration_ms" in spans[0].attributes
        assert all("hi" not in str(dict(item.attributes)) for item in spans)

        disabled = client.patch(
            f"{_BASE}/mcp-servers/{server['serverId']}", json={"enabled": False}
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["enabled"] is False
        assert all(item["enabled"] is False for item in disabled.json()["tools"])
        catalog = {item["toolId"]: item for item in client.get(f"{_BASE}/tools").json()["items"]}
        assert catalog["mcp.fake.echo"]["enabled"] is False
        with pytest.raises(ToolRejected, match="tool-disabled"):
            asyncio.run(registry.require_allowlist(VALIDATION_SCOPE, {"mcp.fake.echo"}))
        with pytest.raises(ToolRejected):
            asyncio.run(call("mcp.fake.echo", {"text": "hi"}))

        enabled = client.patch(f"{_BASE}/mcp-servers/{server['serverId']}", json={"enabled": True})
        assert enabled.json()["enabled"] is True
        assert asyncio.run(
            registry.require_allowlist(VALIDATION_SCOPE, {"mcp.fake.echo"})
        ) == frozenset({"mcp.fake.echo"})
        # System Agents may only read or propose; external MCP tools stay user-only.
        with pytest.raises(ToolRejected, match="tool-effect-not-allowed"):
            asyncio.run(
                registry.require_allowlist(
                    VALIDATION_SCOPE, {"mcp.fake.create_ticket"}, system=True
                )
            )

        resynced = client.post(f"{_BASE}/mcp-servers/{server['serverId']}/sync")
        assert resynced.status_code == 200 and resynced.json()["lastError"] is None

        page = client.get(f"{_BASE}/mcp-servers")
        assert page.status_code == 200
        assert [item["slug"] for item in page.json()["items"]] == ["fake"]

        duplicate = client.post(
            f"{_BASE}/mcp-servers", json={"slug": "fake", "displayName": "Fake", "url": url}
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["type"].endswith("/mcp-server-conflict")


def test_sync_keeps_non_canonical_names_and_reports_skipped_tools() -> None:
    client, registry = _client(_ALL)
    with serve(fake_server(extra_names=("getWeather", "_hidden"))) as url:
        created = client.post(
            f"{_BASE}/mcp-servers", json={"slug": "named", "displayName": "Named", "url": url}
        )
        assert created.status_code == 201, created.text
        server = created.json()
        tools = {item["toolId"]: item for item in server["tools"]}
        assert tools["mcp.named.getweather"]["displayName"] == "getWeather"
        assert server["skippedTools"] == [{"name": "_hidden", "reason": "name-invalid"}]
        listed = client.get(f"{_BASE}/mcp-servers").json()["items"][0]
        assert listed["skippedTools"] == server["skippedTools"]

        async def call():
            executor = await registry.executor(VALIDATION_SCOPE, "mcp.named.getweather")
            return await executor.invoke(VALIDATION_SCOPE, {})

        result = asyncio.run(call())
        assert (result.content, result.is_error) == ("called getWeather", False)


def test_sync_rejects_more_than_64_tools_and_keeps_no_tools() -> None:
    client, _ = _client(_ALL)
    with serve(fake_server(tool_count=60)) as url:
        created = client.post(
            f"{_BASE}/mcp-servers", json={"slug": "crowded", "displayName": "Crowded", "url": url}
        )

    assert created.status_code == 201, created.text
    assert created.json()["lastError"] == "mcp-too-many-tools"
    assert created.json()["tools"] == []


def test_sync_records_unreachable_servers() -> None:
    client, _ = _client(_ALL)

    created = client.post(
        f"{_BASE}/mcp-servers",
        json={"slug": "gone", "displayName": "Gone", "url": "http://127.0.0.1:9/mcp"},
    )

    assert created.status_code == 201, created.text
    assert created.json()["lastError"] == "mcp-unreachable"


def test_auth_header_comes_from_the_named_environment_variable() -> None:
    environ: dict[str, str] = {}
    client, registry = _client(_ALL, environ=environ)
    with serve(fake_server()) as url:
        created = client.post(
            f"{_BASE}/mcp-servers",
            json={
                "slug": "secured",
                "displayName": "Secured",
                "url": url,
                "authHeaderEnv": "FAKE_MCP_TOKEN",
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["lastError"] == "mcp-auth-missing"
        assert created.json()["authHeaderEnv"] == "FAKE_MCP_TOKEN"
        assert created.json()["tools"] == []

        environ["FAKE_MCP_TOKEN"] = "Bearer test-token"
        synced = client.post(f"{_BASE}/mcp-servers/{created.json()['serverId']}/sync")
        assert synced.json()["lastError"] is None
        assert "Bearer test-token" not in synced.text

        async def whoami():
            executor = await registry.executor(VALIDATION_SCOPE, "mcp.secured.whoami")
            return await executor.invoke(VALIDATION_SCOPE, {})

        assert asyncio.run(whoami()).content == "Bearer test-token"


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"url": "http://example.com/mcp"}, 422, "mcp-url-not-allowed"),
        ({"url": "file:///etc/passwd"}, 422, "mcp-url-not-allowed"),
        ({"slug": "Bad Slug"}, 422, "mcp-server-invalid"),
        ({"authHeaderEnv": "Bearer secret"}, 422, "mcp-server-invalid"),
    ],
)
def test_registration_rejections_use_problem_types(payload, status, code) -> None:
    client, _ = _client(_ALL)
    body = {"slug": "fake", "displayName": "Fake", "url": "http://127.0.0.1:9/mcp", **payload}

    response = client.post(f"{_BASE}/mcp-servers", json=body)

    assert response.status_code == status, response.text
    assert response.json()["type"] == f"https://tap.example/problems/{code}"


def test_unknown_server_and_permissions() -> None:
    client, _ = _client(_ALL)
    assert client.post(f"{_BASE}/mcp-servers/missing/sync").status_code == 404
    assert client.patch(f"{_BASE}/mcp-servers/missing", json={"enabled": True}).status_code == 404

    reader, _ = _client({"ai.agents.read"})
    assert reader.get(f"{_BASE}/tools").status_code == 200
    assert reader.get(f"{_BASE}/mcp-servers").status_code == 403
    assert (
        reader.post(
            f"{_BASE}/mcp-servers",
            json={"slug": "fake", "displayName": "Fake", "url": "http://127.0.0.1:9/mcp"},
        ).status_code
        == 403
    )
    manager, _ = _client({"ai.tools.manage"})
    assert manager.get(f"{_BASE}/tools").status_code == 403
