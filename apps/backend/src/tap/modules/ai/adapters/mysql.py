"""MySQL ledger for immutable approved AI catalog revisions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    UniqueConstraint,
    func,
    select,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.ai.application.assets import ValidationAssetSeed, resolve_agent_selection
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
    Column("system_instruction", String(8000)),
    Column("tool_allowlist", JSON, nullable=False),
    Column("output_schema_digest", String(71), nullable=False),
    Column("output_schema_json", JSON),
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
    Column("instruction_template", String(8000)),
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
    _LOCK_TIMEOUT_SECONDS = 20

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
        engine = self._sessions.kw.get("bind")
        if not isinstance(engine, AsyncEngine):
            raise TypeError("asset catalog requires an async engine-bound session factory")
        # MySQL advisory locks belong to a physical connection, not to a
        # transaction. Own that connection independently of the session so a
        # commit or rollback cannot return it to the pool before RELEASE_LOCK.
        async with engine.connect() as connection:
            lock_name = self._lock_name()
            acquired = await connection.scalar(
                select(func.get_lock(lock_name, self._LOCK_TIMEOUT_SECONDS))
            )
            if acquired != 1:
                raise AssetRevisionRejected()
            # GET_LOCK may have opened a repeatable-read transaction before a
            # waiting caller acquired the lock. Reset that transaction while
            # retaining the connection-scoped lock so reads see its predecessor.
            await connection.rollback()
            failure: BaseException | None = None
            async with self._sessions(bind=connection) as session:
                try:
                    for revision in seed.agents:
                        await self._seed_agent(session, revision)
                    for skill_value in seed.skills:
                        await self._seed_skill(session, skill_value)
                    await session.commit()
                except BaseException as error:
                    failure = error
                    await session.rollback()
                    raise
                finally:
                    try:
                        await self._release_catalog_lock(connection, lock_name)
                    except BaseException as release_error:
                        if failure is not None:
                            raise release_error from failure
                        raise release_error

    def _lock_name(self) -> str:
        return f"tap:ai-assets:{self._scope.enterprise_id}:{self._scope.project_id}"

    async def _release_catalog_lock(self, connection: AsyncConnection, lock_name: str) -> None:
        release_task = asyncio.create_task(self._release_lock_io(connection, lock_name))
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                released = await asyncio.shield(release_task)
                break
            except asyncio.CancelledError as error:
                if release_task.done():
                    await connection.invalidate()
                    raise error
                cancellation = error
            except BaseException as error:
                await connection.invalidate()
                if cancellation is not None:
                    raise cancellation from error
                raise
        if released != 1:
            await connection.invalidate()
            rejection = AssetRevisionRejected()
            if cancellation is not None:
                raise cancellation from rejection
            raise rejection
        if cancellation is not None:
            raise cancellation

    async def _release_lock_io(self, connection: AsyncConnection, lock_name: str) -> int | None:
        return await connection.scalar(select(func.release_lock(lock_name)))

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
            existing_revision = self._agent(existing)
            if self._agent_content(existing_revision) != self._agent_content(
                revision
            ) or not self._creator_matches(existing, revision.scope):
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
        revision_number = await self._next_number(
            session, ai_agent_revision, "agent_id", revision.asset_id
        )
        await self._assert_adoption(session, ai_agent_revision, revision.adopted_from_revision_id)
        await session.execute(
            ai_agent_revision.insert().values(
                revision_id=revision.revision_id,
                agent_id=revision.asset_id,
                revision_number=revision_number,
                display_name=revision.display_name,
                content_digest=revision.content_digest,
                system_instruction_digest=revision.system_instruction_digest,
                system_instruction=revision.system_instruction,
                tool_allowlist=sorted(revision.tool_allowlist),
                output_schema_digest=revision.output_schema_digest,
                output_schema_json=revision.output_schema_json,
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
            existing_revision = self._skill(existing)
            if self._skill_content(existing_revision) != self._skill_content(
                revision
            ) or not self._creator_matches(existing, revision.scope):
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
        revision_number = await self._next_number(
            session, skill_revision, "skill_id", revision.asset_id
        )
        await self._assert_adoption(session, skill_revision, revision.adopted_from_revision_id)
        await session.execute(
            skill_revision.insert().values(
                revision_id=revision.revision_id,
                skill_id=revision.asset_id,
                revision_number=revision_number,
                display_name=revision.display_name,
                content_digest=revision.content_digest,
                instruction_template_digest=revision.instruction_template_digest,
                instruction_template=revision.instruction_template,
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
                        ai_agent_revision.c.system_instruction.is_not(None),
                        ai_agent_revision.c.output_schema_json.is_not(None),
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

    async def resolve_historical_agent(
        self, scope: ProjectScopeContext, revision_id: str
    ) -> AiAgentRevision:
        return await self.get_historical_agent(scope, revision_id)

    async def get_skill(self, scope: ProjectScopeContext, revision_id: str) -> SkillRevision:
        return await self._get_skill(scope, revision_id, enabled=True)

    async def resolve_skill(self, scope: ProjectScopeContext, revision_id: str) -> SkillRevision:
        return await self.get_skill(scope, revision_id)

    async def get_historical_skill(
        self, scope: ProjectScopeContext, revision_id: str
    ) -> SkillRevision:
        return await self._get_skill(scope, revision_id, enabled=False)

    async def resolve_historical_skill(
        self, scope: ProjectScopeContext, revision_id: str
    ) -> SkillRevision:
        return await self.get_historical_skill(scope, revision_id)

    async def resolve_agent(
        self,
        scope: ProjectScopeContext,
        revision_id: str,
        *,
        tools: frozenset[str],
        output_schema_digest: str,
    ) -> AiAgentRevision:
        return resolve_agent_selection(
            await self.get_agent(scope, revision_id),
            tools=tools,
            output_schema_digest=output_schema_digest,
        )

    async def disable_agent(self, revision_id: str) -> None:
        await self._disable(ai_agent_revision, revision_id)

    async def disable_skill(self, revision_id: str) -> None:
        await self._disable(skill_revision, revision_id)

    async def _disable(self, table: Table, revision_id: str) -> None:
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(table)
                .where(
                    *scope_predicates(table, self._scope),
                    table.c.revision_id == revision_id,
                    table.c.status == AssetRevisionStatus.ENABLED.value,
                )
                .values(status=AssetRevisionStatus.DISABLED.value)
            )
        if result.rowcount != 1:
            raise AssetRevisionRejected()

    async def _get_skill(
        self, scope: ProjectScopeContext, revision_id: str, *, enabled: bool
    ) -> SkillRevision:
        self._assert_scope(scope)
        conditions = [
            *scope_predicates(skill_revision, self._scope),
            skill_revision.c.revision_id == revision_id,
        ]
        if enabled:
            conditions.append(skill_revision.c.status == AssetRevisionStatus.ENABLED.value)
        async with self._sessions() as session:
            row = (
                (await session.execute(select(skill_revision).where(*conditions)))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise AssetRevisionRejected()
        return self._skill(row)

    async def _next_number(
        self, session: AsyncSession, table: Table, asset_column: str, asset_id: str
    ) -> int:
        value = await session.scalar(
            select(func.max(table.c.revision_number)).where(
                *scope_predicates(table, self._scope), table.c[asset_column] == asset_id
            )
        )
        return int(value or 0) + 1

    async def _assert_adoption(
        self, session: AsyncSession, table: Table, predecessor_id: str | None
    ) -> None:
        if predecessor_id is None:
            return
        row = (
            await session.execute(
                select(table.c.revision_id).where(
                    *scope_predicates(table, self._scope), table.c.revision_id == predecessor_id
                )
            )
        ).one_or_none()
        if row is None:
            raise AssetRevisionRejected()

    @staticmethod
    def _agent_content(value: AiAgentRevision) -> tuple[object, ...]:
        return (
            value.revision_id,
            value.asset_id,
            value.display_name,
            value.content_digest,
            value.system_instruction_digest,
            value.tool_allowlist,
            value.output_schema_digest,
            value.system_instruction,
            value.output_schema_json,
            value.adopted_from_revision_id,
        )

    @staticmethod
    def _skill_content(value: SkillRevision) -> tuple[object, ...]:
        return (
            value.revision_id,
            value.asset_id,
            value.display_name,
            value.content_digest,
            value.instruction_template_digest,
            value.instruction_template,
            value.applicable_tasks,
            value.adopted_from_revision_id,
        )

    @staticmethod
    def _creator_matches(row, scope: ProjectScopeContext) -> bool:
        values = scope_values(scope)
        return all(row[name] == value for name, value in values.items())

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
        if type(scope) is not ProjectScopeContext or scope != self._scope:
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
            system_instruction=row["system_instruction"],
            output_schema_json=row["output_schema_json"],
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
            instruction_template=row["instruction_template"],
            applicable_tasks=frozenset(row["applicable_tasks"]),
            status=AssetRevisionStatus(row["status"]),
            adopted_from_revision_id=row["adopted_from_revision_id"],
        )
