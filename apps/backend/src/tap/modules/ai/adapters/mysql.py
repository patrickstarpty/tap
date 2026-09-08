"""MySQL ledger for immutable approved AI catalog revisions."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    UniqueConstraint,
    select,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.ai.application.assets import ValidationAssetSeed
from tap.modules.ai.domain.assets import (
    AiAgentRevision,
    AssetRevisionRejected,
    AssetRevisionStatus,
    SkillRevision,
)
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import metadata


def _asset_table(name: str, id_column: str) -> Table:
    return Table(
        name,
        metadata,
        Column(id_column, String(64), primary_key=True),
        Column("display_name", String(128), nullable=False),
        Column("created_at", DATETIME(fsp=6), nullable=False),
        Column("enterprise_id", String(128), nullable=False),
        Column("project_id", String(128), nullable=False),
        Column("actor_id", String(128), nullable=False),
        Column("identity_mode", String(16), nullable=False),
        Column("identity_origin", String(16), nullable=False),
        UniqueConstraint("project_id", id_column, name=f"uq_{name}_project_pk"),
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
    )


ai_agent = _asset_table("ai_agent", "agent_id")
skill = _asset_table("skill", "skill_id")

ai_agent_revision = Table(
    "ai_agent_revision",
    metadata,
    Column("revision_id", String(64), primary_key=True),
    Column("agent_id", String(64), nullable=False),
    Column("revision_number", Integer, nullable=False),
    Column("display_name", String(128), nullable=False),
    Column("content_digest", String(71), nullable=False),
    Column("system_instruction_digest", String(71), nullable=False),
    Column("tool_allowlist", JSON, nullable=False),
    Column("output_schema_digest", String(71), nullable=False),
    Column("status", String(16), nullable=False),
    Column("adopted_from_revision_id", String(64)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("enterprise_id", String(128), nullable=False),
    Column("project_id", String(128), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("identity_mode", String(16), nullable=False),
    Column("identity_origin", String(16), nullable=False),
    UniqueConstraint("project_id", "revision_id", name="uq_ai_agent_revision_project_pk"),
    UniqueConstraint(
        "project_id", "agent_id", "revision_number", name="uq_ai_agent_revision_number"
    ),
    ForeignKeyConstraint(
        ["project_id", "agent_id"],
        ["ai_agent.project_id", "ai_agent.agent_id"],
        name="fk_ai_agent_revision_agent",
    ),
    ForeignKeyConstraint(
        ["project_id", "adopted_from_revision_id"],
        ["ai_agent_revision.project_id", "ai_agent_revision.revision_id"],
        name="fk_ai_agent_revision_adopted",
    ),
    ForeignKeyConstraint(
        ["enterprise_id", "project_id"],
        ["project.enterprise_id", "project.project_id"],
        name="fk_ai_agent_revision_scope_project",
    ),
    ForeignKeyConstraint(
        ["enterprise_id", "actor_id"],
        ["actor_principal.enterprise_id", "actor_principal.actor_id"],
        name="fk_ai_agent_revision_scope_actor",
    ),
)

skill_revision = Table(
    "skill_revision",
    metadata,
    Column("revision_id", String(64), primary_key=True),
    Column("skill_id", String(64), nullable=False),
    Column("revision_number", Integer, nullable=False),
    Column("display_name", String(128), nullable=False),
    Column("content_digest", String(71), nullable=False),
    Column("instruction_template_digest", String(71), nullable=False),
    Column("applicable_tasks", JSON, nullable=False),
    Column("status", String(16), nullable=False),
    Column("adopted_from_revision_id", String(64)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("enterprise_id", String(128), nullable=False),
    Column("project_id", String(128), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("identity_mode", String(16), nullable=False),
    Column("identity_origin", String(16), nullable=False),
    UniqueConstraint("project_id", "revision_id", name="uq_skill_revision_project_pk"),
    UniqueConstraint("project_id", "skill_id", "revision_number", name="uq_skill_revision_number"),
    ForeignKeyConstraint(
        ["project_id", "skill_id"],
        ["skill.project_id", "skill.skill_id"],
        name="fk_skill_revision_skill",
    ),
    ForeignKeyConstraint(
        ["project_id", "adopted_from_revision_id"],
        ["skill_revision.project_id", "skill_revision.revision_id"],
        name="fk_skill_revision_adopted",
    ),
    ForeignKeyConstraint(
        ["enterprise_id", "project_id"],
        ["project.enterprise_id", "project.project_id"],
        name="fk_skill_revision_scope_project",
    ),
    ForeignKeyConstraint(
        ["enterprise_id", "actor_id"],
        ["actor_principal.enterprise_id", "actor_principal.actor_id"],
        name="fk_skill_revision_scope_actor",
    ),
)


class MysqlAssetCatalog:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)

    @property
    def scope(self) -> ProjectScopeContext:
        return self._scope

    async def seed(self, seed: ValidationAssetSeed) -> None:
        if not isinstance(seed, ValidationAssetSeed):
            raise TypeError("asset seed must be versioned server configuration")
        async with self._sessions.begin() as session:
            for revision in seed.agents:
                await self._seed_agent(session, revision)
            for skill_value in seed.skills:
                await self._seed_skill(session, skill_value)

    async def _seed_agent(self, session: AsyncSession, revision: AiAgentRevision) -> None:
        self._assert_scope(revision.scope)
        existing = (
            (
                await session.execute(
                    select(ai_agent_revision).where(
                        *scope_predicates(ai_agent_revision, self._scope),
                        ai_agent_revision.c.revision_id == revision.revision_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if self._agent(existing) != revision:
                raise AssetRevisionRejected()
            return
        now = datetime.now(timezone.utc)
        agent = (
            (
                await session.execute(
                    select(ai_agent).where(
                        *scope_predicates(ai_agent, self._scope),
                        ai_agent.c.agent_id == revision.asset_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if agent is None:
            await session.execute(
                ai_agent.insert().values(
                    agent_id=revision.asset_id,
                    display_name=revision.display_name,
                    created_at=now,
                    **scope_values(self._scope),
                )
            )
        await session.execute(
            ai_agent_revision.insert().values(
                revision_id=revision.revision_id,
                agent_id=revision.asset_id,
                revision_number=1,
                display_name=revision.display_name,
                content_digest=revision.content_digest,
                system_instruction_digest=revision.system_instruction_digest,
                tool_allowlist=sorted(revision.tool_allowlist),
                output_schema_digest=revision.output_schema_digest,
                status=revision.status.value,
                adopted_from_revision_id=revision.adopted_from_revision_id,
                created_at=now,
                **scope_values(self._scope),
            )
        )

    async def _seed_skill(self, session: AsyncSession, revision: SkillRevision) -> None:
        self._assert_scope(revision.scope)
        existing = (
            (
                await session.execute(
                    select(skill_revision).where(
                        *scope_predicates(skill_revision, self._scope),
                        skill_revision.c.revision_id == revision.revision_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if self._skill(existing) != revision:
                raise AssetRevisionRejected()
            return
        now = datetime.now(timezone.utc)
        parent = (
            (
                await session.execute(
                    select(skill).where(
                        *scope_predicates(skill, self._scope), skill.c.skill_id == revision.asset_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if parent is None:
            await session.execute(
                skill.insert().values(
                    skill_id=revision.asset_id,
                    display_name=revision.display_name,
                    created_at=now,
                    **scope_values(self._scope),
                )
            )
        await session.execute(
            skill_revision.insert().values(
                revision_id=revision.revision_id,
                skill_id=revision.asset_id,
                revision_number=1,
                display_name=revision.display_name,
                content_digest=revision.content_digest,
                instruction_template_digest=revision.instruction_template_digest,
                applicable_tasks=sorted(revision.applicable_tasks),
                status=revision.status.value,
                adopted_from_revision_id=revision.adopted_from_revision_id,
                created_at=now,
                **scope_values(self._scope),
            )
        )

    async def list_agents(self, scope: ProjectScopeContext) -> tuple[AiAgentRevision, ...]:
        self._assert_scope(scope)
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(ai_agent_revision)
                    .where(
                        *scope_predicates(ai_agent_revision, self._scope),
                        ai_agent_revision.c.status == AssetRevisionStatus.ENABLED.value,
                    )
                    .order_by(ai_agent_revision.c.revision_id)
                )
            ).mappings()
            return tuple(self._agent(row) for row in rows)

    async def list_skills(self, scope: ProjectScopeContext) -> tuple[SkillRevision, ...]:
        self._assert_scope(scope)
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(skill_revision)
                    .where(
                        *scope_predicates(skill_revision, self._scope),
                        skill_revision.c.status == AssetRevisionStatus.ENABLED.value,
                    )
                    .order_by(skill_revision.c.revision_id)
                )
            ).mappings()
            return tuple(self._skill(row) for row in rows)

    async def get_agent(self, scope: ProjectScopeContext, revision_id: str) -> AiAgentRevision:
        return await self._get_agent(scope, revision_id, enabled=True)

    async def get_historical_agent(
        self, scope: ProjectScopeContext, revision_id: str
    ) -> AiAgentRevision:
        return await self._get_agent(scope, revision_id, enabled=False)

    async def get_skill(self, scope: ProjectScopeContext, revision_id: str) -> SkillRevision:
        self._assert_scope(scope)
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(skill_revision).where(
                            *scope_predicates(skill_revision, self._scope),
                            skill_revision.c.revision_id == revision_id,
                            skill_revision.c.status == AssetRevisionStatus.ENABLED.value,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise AssetRevisionRejected()
        return self._skill(row)

    async def disable_agent(self, revision_id: str) -> None:
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(ai_agent_revision)
                .where(
                    *scope_predicates(ai_agent_revision, self._scope),
                    ai_agent_revision.c.revision_id == revision_id,
                    ai_agent_revision.c.status == AssetRevisionStatus.ENABLED.value,
                )
                .values(status=AssetRevisionStatus.DISABLED.value)
            )
        if result.rowcount != 1:
            raise AssetRevisionRejected()

    async def _get_agent(
        self, scope: ProjectScopeContext, revision_id: str, *, enabled: bool
    ) -> AiAgentRevision:
        self._assert_scope(scope)
        conditions = [
            *scope_predicates(ai_agent_revision, self._scope),
            ai_agent_revision.c.revision_id == revision_id,
        ]
        if enabled:
            conditions.append(ai_agent_revision.c.status == AssetRevisionStatus.ENABLED.value)
        async with self._sessions() as session:
            row = (
                (await session.execute(select(ai_agent_revision).where(*conditions)))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise AssetRevisionRejected()
        return self._agent(row)

    def _assert_scope(self, scope: ProjectScopeContext) -> None:
        if type(scope) is not ProjectScopeContext or (scope.enterprise_id, scope.project_id) != (
            self._scope.enterprise_id,
            self._scope.project_id,
        ):
            raise AssetRevisionRejected()

    @staticmethod
    def _row_scope(row) -> ProjectScopeContext:
        return ProjectScopeContext(
            enterprise_id=row["enterprise_id"],
            project_id=row["project_id"],
            actor_id=row["actor_id"],
            identity_mode=IdentityMode(row["identity_mode"]),
        )

    @classmethod
    def _agent(cls, row) -> AiAgentRevision:
        return AiAgentRevision(
            revision_id=row["revision_id"],
            asset_id=row["agent_id"],
            display_name=row["display_name"],
            scope=cls._row_scope(row),
            content_digest=row["content_digest"],
            system_instruction_digest=row["system_instruction_digest"],
            tool_allowlist=frozenset(row["tool_allowlist"]),
            output_schema_digest=row["output_schema_digest"],
            status=AssetRevisionStatus(row["status"]),
            adopted_from_revision_id=row["adopted_from_revision_id"],
        )

    @classmethod
    def _skill(cls, row) -> SkillRevision:
        return SkillRevision(
            revision_id=row["revision_id"],
            asset_id=row["skill_id"],
            display_name=row["display_name"],
            scope=cls._row_scope(row),
            content_digest=row["content_digest"],
            instruction_template_digest=row["instruction_template_digest"],
            applicable_tasks=frozenset(row["applicable_tasks"]),
            status=AssetRevisionStatus(row["status"]),
            adopted_from_revision_id=row["adopted_from_revision_id"],
        )
