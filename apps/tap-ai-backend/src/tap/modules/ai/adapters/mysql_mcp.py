"""MySQL registry of MCP servers and their last synchronized tools."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    delete,
    select,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import ColumnElement

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.application.mcp_servers import McpServerRecord, RepositoryMcpToolSource
from tap.modules.ai.domain.mcp import McpClient, McpRejected, McpToolSpec
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import metadata


def _scope_columns() -> list[Column[str]]:
    return [
        Column("enterprise_id", String(128), nullable=False),
        Column("project_id", String(128), nullable=False),
        Column("actor_id", String(128), nullable=False),
        Column("identity_mode", String(16), nullable=False),
        Column("identity_origin", String(16), nullable=False),
    ]


def _scope_foreign_keys(name: str) -> list[ForeignKeyConstraint]:
    return [
        ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name=f"fk_{name}_scope_project",
        ),
        ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name=f"fk_{name}_scope_actor",
        ),
    ]


ai_mcp_server = Table(
    "ai_mcp_server",
    metadata,
    Column("server_id", String(64), primary_key=True),
    Column("slug", String(32), nullable=False),
    Column("display_name", String(128), nullable=False),
    Column("url", String(2048), nullable=False),
    Column("auth_header_env", String(128)),
    Column("enabled", Boolean, nullable=False, server_default="1"),
    Column("last_synced_at", DATETIME(fsp=6)),
    Column("last_error", String(64)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    *_scope_columns(),
    UniqueConstraint("project_id", "server_id", name="uq_ai_mcp_server_project_pk"),
    UniqueConstraint("project_id", "slug", name="uq_ai_mcp_server_slug"),
    *_scope_foreign_keys("ai_mcp_server"),
)

ai_mcp_tool = Table(
    "ai_mcp_tool",
    metadata,
    Column("server_id", String(64), nullable=False),
    Column("tool_name", String(128), nullable=False),
    Column("description", String(1024), nullable=False),
    Column("input_schema_json", JSON, nullable=False),
    Column("effect", String(16), nullable=False),
    Column("enabled", Boolean, nullable=False, server_default="1"),
    *_scope_columns(),
    PrimaryKeyConstraint("server_id", "tool_name"),
    ForeignKeyConstraint(
        ["project_id", "server_id"],
        ["ai_mcp_server.project_id", "ai_mcp_server.server_id"],
        name="fk_ai_mcp_tool_server",
    ),
    *_scope_foreign_keys("ai_mcp_tool"),
)


def _naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _aware(value: datetime | None) -> datetime | None:
    return None if value is None else value.replace(tzinfo=UTC)


class MysqlMcpServerRepository:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)

    @property
    def scope(self) -> ProjectScopeContext:
        return self._scope

    def _assert_scope(self, scope: ProjectScopeContext) -> None:
        if type(scope) is not ProjectScopeContext or (
            scope.enterprise_id,
            scope.project_id,
        ) != (self._scope.enterprise_id, self._scope.project_id):
            raise McpRejected("mcp-server-not-found")

    async def create_server(self, scope: ProjectScopeContext, record: McpServerRecord) -> None:
        self._assert_scope(scope)
        try:
            async with self._sessions.begin() as session:
                await session.execute(
                    ai_mcp_server.insert().values(
                        server_id=record.server_id,
                        slug=record.slug,
                        display_name=record.display_name,
                        url=record.url,
                        auth_header_env=record.auth_header_env,
                        enabled=record.enabled,
                        created_at=_naive(datetime.now(UTC)),
                        **scope_values(scope),
                    )
                )
        except IntegrityError as error:
            raise McpRejected("mcp-server-conflict") from error

    async def get_server(self, scope: ProjectScopeContext, server_id: str) -> McpServerRecord:
        self._assert_scope(scope)
        async with self._sessions() as session:
            records = await self._records(session, ai_mcp_server.c.server_id == server_id)
        if not records:
            raise McpRejected("mcp-server-not-found")
        return records[0]

    async def list_servers(self, scope: ProjectScopeContext) -> tuple[McpServerRecord, ...]:
        self._assert_scope(scope)
        async with self._sessions() as session:
            return await self._records(session)

    async def replace_tools(
        self,
        scope: ProjectScopeContext,
        server_id: str,
        tools: tuple[McpToolSpec, ...],
        *,
        synced_at: datetime,
    ) -> None:
        self._assert_scope(scope)
        async with self._sessions.begin() as session:
            await self._touch(session, server_id, last_synced_at=_naive(synced_at), last_error=None)
            await session.execute(
                delete(ai_mcp_tool).where(
                    *scope_predicates(ai_mcp_tool, self._scope),
                    ai_mcp_tool.c.server_id == server_id,
                )
            )
            if tools:
                await session.execute(
                    ai_mcp_tool.insert(),
                    [
                        {
                            "server_id": server_id,
                            "tool_name": item.name,
                            "description": item.description,
                            "input_schema_json": dict(item.input_schema),
                            "effect": "read" if item.read_only else "external",
                            "enabled": True,
                            **scope_values(scope),
                        }
                        for item in tools
                    ],
                )

    async def record_sync_error(
        self, scope: ProjectScopeContext, server_id: str, code: str, *, at: datetime
    ) -> None:
        self._assert_scope(scope)
        async with self._sessions.begin() as session:
            await self._touch(session, server_id, last_synced_at=_naive(at), last_error=code)

    async def set_enabled(self, scope: ProjectScopeContext, server_id: str, enabled: bool) -> None:
        self._assert_scope(scope)
        async with self._sessions.begin() as session:
            await self._touch(session, server_id, enabled=enabled)

    async def _touch(self, session: AsyncSession, server_id: str, **values: object) -> None:
        exists = await session.scalar(
            select(ai_mcp_server.c.server_id)
            .where(
                *scope_predicates(ai_mcp_server, self._scope),
                ai_mcp_server.c.server_id == server_id,
            )
            .with_for_update()
        )
        if exists is None:
            raise McpRejected("mcp-server-not-found")
        await session.execute(
            update(ai_mcp_server)
            .where(
                *scope_predicates(ai_mcp_server, self._scope),
                ai_mcp_server.c.server_id == server_id,
            )
            .values(**values)
        )

    async def _records(
        self, session: AsyncSession, *conditions: ColumnElement[bool]
    ) -> tuple[McpServerRecord, ...]:
        servers = (
            (
                await session.execute(
                    select(ai_mcp_server)
                    .where(*scope_predicates(ai_mcp_server, self._scope), *conditions)
                    .order_by(ai_mcp_server.c.slug)
                )
            )
            .mappings()
            .all()
        )
        if not servers:
            return ()
        rows = (
            (
                await session.execute(
                    select(ai_mcp_tool)
                    .where(
                        *scope_predicates(ai_mcp_tool, self._scope),
                        ai_mcp_tool.c.server_id.in_([row["server_id"] for row in servers]),
                    )
                    .order_by(ai_mcp_tool.c.server_id, ai_mcp_tool.c.tool_name)
                )
            )
            .mappings()
            .all()
        )
        tools: dict[str, list[McpToolSpec]] = {}
        for row in rows:
            tools.setdefault(row["server_id"], []).append(
                McpToolSpec(
                    name=row["tool_name"],
                    description=row["description"],
                    input_schema=row["input_schema_json"],
                    read_only=row["effect"] == "read",
                )
            )
        return tuple(
            McpServerRecord(
                server_id=row["server_id"],
                slug=row["slug"],
                display_name=row["display_name"],
                url=row["url"],
                auth_header_env=row["auth_header_env"],
                enabled=bool(row["enabled"]),
                last_synced_at=_aware(row["last_synced_at"]),
                last_error=row["last_error"],
                tools=tuple(tools.get(row["server_id"], ())),
            )
            for row in servers
        )


class MysqlMcpToolSource(RepositoryMcpToolSource):
    """Registry `McpToolSource` backed by the MySQL MCP server registry."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        client: McpClient,
        *,
        scope: ProjectScopeContext,
        extra_hosts: frozenset[str],
        environ: Mapping[str, str] = os.environ,
    ) -> None:
        super().__init__(
            MysqlMcpServerRepository(sessions, scope=scope),
            client,
            extra_hosts=extra_hosts,
            environ=environ,
        )
