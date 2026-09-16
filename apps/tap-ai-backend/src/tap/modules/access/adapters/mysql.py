"""Authoritative Enterprise/Project/Actor registry for validation authorization."""

from typing import Literal, cast

from sqlalchemy import Boolean, Column, ForeignKey, String, Table, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.authorization import ActorPrincipal
from tap.platform.db.schema import metadata

enterprise = Table(
    "enterprise",
    metadata,
    Column("enterprise_id", String(128), primary_key=True),
    Column("enabled", Boolean, nullable=False, server_default="1"),
)
project = Table(
    "project",
    metadata,
    Column("project_id", String(128), primary_key=True),
    Column(
        "enterprise_id",
        String(128),
        ForeignKey("enterprise.enterprise_id", name="fk_project_enterprise"),
        nullable=False,
    ),
    Column("enabled", Boolean, nullable=False, server_default="1"),
    UniqueConstraint("enterprise_id", "project_id", name="uq_project_enterprise_project"),
)
actor_principal = Table(
    "actor_principal",
    metadata,
    Column("actor_id", String(128), primary_key=True),
    Column(
        "enterprise_id",
        String(128),
        ForeignKey("enterprise.enterprise_id", name="fk_actor_principal_enterprise"),
        nullable=False,
    ),
    Column("principal_type", String(24), nullable=False),
    Column("enabled", Boolean, nullable=False, server_default="1"),
    UniqueConstraint("enterprise_id", "actor_id", name="uq_actor_principal_enterprise_actor"),
)


class MysqlIdentityRegistry:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_principal(
        self, enterprise_id: str, project_id: str, actor_id: str
    ) -> ActorPrincipal | None:
        statement = (
            select(actor_principal)
            .join(enterprise, enterprise.c.enterprise_id == actor_principal.c.enterprise_id)
            .join(project, project.c.enterprise_id == enterprise.c.enterprise_id)
            .where(
                enterprise.c.enterprise_id == enterprise_id,
                enterprise.c.enabled.is_(True),
                project.c.project_id == project_id,
                project.c.enabled.is_(True),
                actor_principal.c.actor_id == actor_id,
            )
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).mappings().one_or_none()
        if row is None:
            return None
        return ActorPrincipal(
            enterprise_id=row["enterprise_id"],
            actor_id=row["actor_id"],
            principal_type=cast(Literal["VALIDATION", "USER"], row["principal_type"]),
            enabled=row["enabled"],
        )
