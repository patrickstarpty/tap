from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_mysql_mcp_resync_replaces_tools_and_servers_are_project_scoped(
    owned_project_mysql,
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.mysql import project
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.access.domain.context import ProjectScopeContext
    from tap.modules.ai.adapters.mcp_client import StreamableHttpMcpClient
    from tap.modules.ai.adapters.mysql_mcp import MysqlMcpServerRepository, MysqlMcpToolSource
    from tap.modules.ai.application.mcp_servers import McpServerService
    from tap.modules.ai.application.tool_registry import ToolRegistry, builtin_tools
    from tap.modules.ai.domain.mcp import McpRejected
    from tap.modules.ai.domain.tools import ToolEffect
    from tests.mcp_fake import fake_server, serve

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    client = StreamableHttpMcpClient(extra_hosts=frozenset())
    server = fake_server()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        service = McpServerService(
            MysqlMcpServerRepository(sessions, scope=VALIDATION_SCOPE),
            client,
            extra_hosts=frozenset(),
            environ={},
        )
        with serve(server) as url:
            created = await service.register(
                VALIDATION_SCOPE, slug="fake", display_name="Fake", url=url, auth_header_env=None
            )
            assert created.last_error is None and created.last_synced_at is not None
            names = {item.tool_id for item in created.tools}
            assert {"mcp.fake.echo", "mcp.fake.create_ticket"} <= names
            effects = {item.tool_id: item.effect for item in created.tools}
            assert effects["mcp.fake.echo"] is ToolEffect.READ
            assert effects["mcp.fake.create_ticket"] is ToolEffect.EXTERNAL

            server.remove_tool("create_ticket")
            server.add_tool(lambda: "ok", name="close_ticket", description="Close.")
            resynced = await service.sync(VALIDATION_SCOPE, created.server_id)
            names = {item.tool_id for item in resynced.tools}
            assert "mcp.fake.create_ticket" not in names
            assert "mcp.fake.close_ticket" in names

            registry = ToolRegistry(
                builtin_tools(),
                MysqlMcpToolSource(
                    sessions,
                    client,
                    scope=VALIDATION_SCOPE,
                    extra_hosts=frozenset(),
                    environ={},
                ),
            )
            executor = await registry.executor(VALIDATION_SCOPE, "mcp.fake.echo")
            assert (await executor.invoke(VALIDATION_SCOPE, {"text": "db"})).content == "echo: db"

        failed = await service.sync(VALIDATION_SCOPE, created.server_id)
        assert failed.last_error == "mcp-unreachable"
        assert {item.tool_id for item in failed.tools} == names

        disabled = await service.set_enabled(VALIDATION_SCOPE, created.server_id, False)
        assert disabled.enabled is False and all(not item.enabled for item in disabled.tools)
        assert [item.slug for item in await service.list(VALIDATION_SCOPE)] == ["fake"]
        with pytest.raises(McpRejected, match="mcp-server-conflict"):
            await service.register(
                VALIDATION_SCOPE,
                slug="fake",
                display_name="Again",
                url="http://127.0.0.1:9/mcp",
                auth_header_env=None,
            )

        other_scope = ProjectScopeContext(
            enterprise_id=VALIDATION_SCOPE.enterprise_id,
            project_id="other-project",
            actor_id=VALIDATION_SCOPE.actor_id,
            identity_mode=VALIDATION_SCOPE.identity_mode,
        )
        async with sessions.begin() as session:
            await session.execute(
                project.insert().values(
                    enterprise_id=other_scope.enterprise_id, project_id=other_scope.project_id
                )
            )
        other = McpServerService(
            MysqlMcpServerRepository(sessions, scope=other_scope),
            client,
            extra_hosts=frozenset(),
            environ={},
        )
        assert await other.list(other_scope) == ()
        with pytest.raises(McpRejected, match="mcp-server-not-found"):
            await other.sync(other_scope, created.server_id)
        foreign = await other.register(
            other_scope,
            slug="fake",
            display_name="Same slug elsewhere",
            url="http://127.0.0.1:9/mcp",
            auth_header_env=None,
        )
        assert foreign.server_id != created.server_id
        assert [item.server_id for item in await service.list(VALIDATION_SCOPE)] == [
            created.server_id
        ]
    finally:
        await engine.dispose()
