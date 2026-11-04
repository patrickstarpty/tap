"""MySQL ledger for immutable approved AI catalog revisions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    Column,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    func,
    select,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON, LONGTEXT, MEDIUMTEXT
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.ai.application.assets import ValidationAssetSeed, resolve_agent_selection
from tap.modules.ai.domain.assets import (
    AiAgentRevision,
    AssetRevisionRejected,
    AssetRevisionStatus,
    SkillRevision,
    text_digest,
)
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import metadata

if TYPE_CHECKING:
    from tap.modules.ai.application.skill_import import ParsedSkill


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
    Column("system_instruction", MEDIUMTEXT),
    Column("tool_allowlist", JSON, nullable=False),
    Column("output_schema_digest", String(71), nullable=False),
    Column("output_schema_json", JSON),
    Column("status", String(16), nullable=False),
    Column("adopted_from_revision_id", String(64)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("model_alias", String(128)),
    Column("description", String(1024)),
    Column("origin", String(16), nullable=False, server_default="seed"),
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
    Column("instruction_template", MEDIUMTEXT),
    Column("applicable_tasks", JSON, nullable=False),
    Column("status", String(16), nullable=False),
    Column("adopted_from_revision_id", String(64)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("origin", String(16), nullable=False, server_default="seed"),
    Column("description", String(1024)),
    Column("requested_tools_json", JSON),
    Column("front_matter_json", JSON),
    Column("source_markdown", LONGTEXT),
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

ai_agent_revision_skill = Table(
    "ai_agent_revision_skill",
    metadata,
    Column("revision_id", String(64), nullable=False),
    Column("position", Integer, nullable=False),
    Column("skill_revision_id", String(64), nullable=False),
    Column("enterprise_id", String(128), nullable=False),
    Column("project_id", String(128), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("identity_mode", String(16), nullable=False),
    Column("identity_origin", String(16), nullable=False),
    PrimaryKeyConstraint("revision_id", "position"),
    UniqueConstraint(
        "project_id", "revision_id", "skill_revision_id", name="uq_ai_agent_revision_skill_once"
    ),
    ForeignKeyConstraint(
        ["project_id", "revision_id"],
        ["ai_agent_revision.project_id", "ai_agent_revision.revision_id"],
        name="fk_ai_agent_revision_skill_agent",
    ),
    ForeignKeyConstraint(
        ["project_id", "skill_revision_id"],
        ["skill_revision.project_id", "skill_revision.revision_id"],
        name="fk_ai_agent_revision_skill_skill",
    ),
    ForeignKeyConstraint(
        ["enterprise_id", "project_id"],
        ["project.enterprise_id", "project.project_id"],
        name="fk_ai_agent_revision_skill_scope_project",
    ),
    ForeignKeyConstraint(
        ["enterprise_id", "actor_id"],
        ["actor_principal.enterprise_id", "actor_principal.actor_id"],
        name="fk_ai_agent_revision_skill_scope_actor",
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
        async with self._catalog_session() as session:
            for revision in seed.agents:
                await self._seed_agent(session, revision)
            for skill_value in seed.skills:
                await self._seed_skill(session, skill_value)

    @asynccontextmanager
    async def _catalog_session(self) -> AsyncIterator[AsyncSession]:
        """Serialize catalog writers for this Project and commit on success."""
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
                    yield session
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
            existing_revision = await self._load_agent(session, existing)
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
        await self._insert_agent_revision(session, revision, revision_number, now)

    async def _insert_agent_revision(
        self,
        session: AsyncSession,
        revision: AiAgentRevision,
        revision_number: int,
        now: datetime,
    ) -> None:
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
                model_alias=revision.model_alias,
                description=revision.description,
                origin=revision.origin,
                **scope_values(self._scope),
            )
        )
        if revision.skill_revision_ids:
            await session.execute(
                ai_agent_revision_skill.insert(),
                [
                    {
                        "revision_id": revision.revision_id,
                        "position": position,
                        "skill_revision_id": skill_revision_id,
                        **scope_values(self._scope),
                    }
                    for position, skill_revision_id in enumerate(revision.skill_revision_ids)
                ],
            )

    async def latest_agent_revision(
        self, scope: ProjectScopeContext, asset_id: str
    ) -> AiAgentRevision:
        self._assert_scope(scope)
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(ai_agent_revision)
                        .where(
                            *scope_predicates(ai_agent_revision, self._scope),
                            ai_agent_revision.c.agent_id == asset_id,
                        )
                        .order_by(ai_agent_revision.c.revision_number.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise AssetRevisionRejected()
            return await self._load_agent(session, row)

    async def insert_agent_revision(
        self, scope: ProjectScopeContext, revision: AiAgentRevision
    ) -> AiAgentRevision:
        """Append an authored revision only when it adopts the current latest revision."""
        self._assert_scope(scope)
        self._assert_scope(revision.scope)
        async with self._catalog_session() as session:
            latest = (
                await session.execute(
                    select(ai_agent_revision.c.revision_id)
                    .where(
                        *scope_predicates(ai_agent_revision, self._scope),
                        ai_agent_revision.c.agent_id == revision.asset_id,
                    )
                    .order_by(ai_agent_revision.c.revision_number.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if latest != revision.adopted_from_revision_id:
                raise AssetRevisionRejected()
            now = datetime.now(timezone.utc)
            if latest is None:
                await session.execute(
                    ai_agent.insert().values(
                        agent_id=revision.asset_id,
                        display_name=revision.display_name,
                        created_at=now,
                        **scope_values(self._scope),
                    )
                )
            for skill_revision_id in revision.skill_revision_ids:
                await self._assert_adoption(session, skill_revision, skill_revision_id)
            revision_number = await self._next_number(
                session, ai_agent_revision, "agent_id", revision.asset_id
            )
            await self._insert_agent_revision(session, revision, revision_number, now)
        return revision

    async def set_agent_status(
        self, scope: ProjectScopeContext, revision_id: str, status: AssetRevisionStatus
    ) -> AiAgentRevision:
        self._assert_scope(scope)
        if not isinstance(status, AssetRevisionStatus):
            raise AssetRevisionRejected()
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(ai_agent_revision)
                .where(
                    *scope_predicates(ai_agent_revision, self._scope),
                    ai_agent_revision.c.revision_id == revision_id,
                )
                .values(status=status.value)
            )
            if result.rowcount != 1:
                raise AssetRevisionRejected()
        return await self.get_historical_agent(scope, revision_id)

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
        await self._insert_skill_revision(session, revision, revision_number, now)

    async def _insert_skill_revision(
        self,
        session: AsyncSession,
        revision: SkillRevision,
        revision_number: int,
        now: datetime,
    ) -> None:
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
                origin=revision.origin,
                description=revision.description,
                requested_tools_json=(
                    list(revision.requested_tools) if revision.requested_tools else None
                ),
                front_matter_json=(
                    None
                    if revision.front_matter_json is None
                    else json.loads(revision.front_matter_json)
                ),
                source_markdown=revision.source_markdown,
                **scope_values(self._scope),
            )
        )

    async def import_skill(
        self, scope: ProjectScopeContext, parsed: ParsedSkill, *, origin: str
    ) -> tuple[SkillRevision, bool]:
        """Append an immutable revision unless the latest one has identical bytes."""
        self._assert_scope(scope)
        async with self._catalog_session() as session:
            parent = (
                (
                    await session.execute(
                        select(skill.c.skill_id)
                        .where(
                            *scope_predicates(skill, self._scope),
                            skill.c.display_name == parsed.name,
                        )
                        .order_by(skill.c.created_at, skill.c.skill_id)
                        .limit(1)
                    )
                )
                .scalars()
                .one_or_none()
            )
            now = datetime.now(timezone.utc)
            latest = None
            if parent is None:
                parent = uuid4().hex
                await session.execute(
                    skill.insert().values(
                        skill_id=parent,
                        display_name=parsed.name,
                        created_at=now,
                        **scope_values(self._scope),
                    )
                )
            else:
                latest = (
                    (
                        await session.execute(
                            select(skill_revision)
                            .where(
                                *scope_predicates(skill_revision, self._scope),
                                skill_revision.c.skill_id == parent,
                            )
                            .order_by(skill_revision.c.revision_number.desc())
                            .limit(1)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if latest is not None and latest["content_digest"] == parsed.content_digest:
                    return self._skill(latest), False
            revision = SkillRevision(
                revision_id=uuid4().hex,
                asset_id=parent,
                display_name=parsed.name,
                scope=self._scope,
                content_digest=parsed.content_digest,
                instruction_template_digest=text_digest(parsed.instructions),
                applicable_tasks=frozenset({"knowledge.answer"}),
                adopted_from_revision_id=None if latest is None else latest["revision_id"],
                instruction_template=parsed.instructions,
                origin=origin,
                description=parsed.description,
                requested_tools=parsed.requested_tools,
                front_matter_json=json.dumps(
                    dict(parsed.front_matter),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
                source_markdown=parsed.source_markdown,
            )
            revision_number = await self._next_number(session, skill_revision, "skill_id", parent)
            await self._insert_skill_revision(session, revision, revision_number, now)
            return revision, True

    async def set_skill_status(
        self, scope: ProjectScopeContext, revision_id: str, status: AssetRevisionStatus
    ) -> SkillRevision:
        self._assert_scope(scope)
        if not isinstance(status, AssetRevisionStatus):
            raise AssetRevisionRejected()
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(skill_revision)
                .where(
                    *scope_predicates(skill_revision, self._scope),
                    skill_revision.c.revision_id == revision_id,
                )
                .values(status=status.value)
            )
            if result.rowcount != 1:
                raise AssetRevisionRejected()
        return await self.get_historical_skill(scope, revision_id)

    async def skill_source(self, scope: ProjectScopeContext, revision_id: str) -> bytes:
        revision = await self.get_historical_skill(scope, revision_id)
        if revision.source_markdown is None:
            raise AssetRevisionRejected()
        return revision.source_markdown.encode("utf-8")

    async def list_agents(self, scope: ProjectScopeContext) -> tuple[AiAgentRevision, ...]:
        return await self._list_agents(scope, enabled_only=True)

    async def list_agent_revisions(self, scope: ProjectScopeContext) -> tuple[AiAgentRevision, ...]:
        return await self._list_agents(scope, enabled_only=False)

    async def _list_agents(
        self, scope: ProjectScopeContext, *, enabled_only: bool
    ) -> tuple[AiAgentRevision, ...]:
        self._assert_scope(scope)
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(ai_agent_revision)
                        .where(
                            *scope_predicates(ai_agent_revision, self._scope),
                            *(
                                (
                                    ai_agent_revision.c.status == AssetRevisionStatus.ENABLED.value,
                                    ai_agent_revision.c.system_instruction.is_not(None),
                                    ai_agent_revision.c.output_schema_json.is_not(None),
                                )
                                if enabled_only
                                else ()
                            ),
                        )
                        .order_by(
                            *(
                                (ai_agent_revision.c.revision_id,)
                                if enabled_only
                                else (
                                    ai_agent_revision.c.agent_id,
                                    ai_agent_revision.c.revision_number.desc(),
                                )
                            )
                        )
                    )
                )
                .mappings()
                .all()
            )
            skills = await self._agent_skills(session, [row["revision_id"] for row in rows])
            return tuple(self._agent(row, skills.get(row["revision_id"], ())) for row in rows)

    async def list_skills(self, scope: ProjectScopeContext) -> tuple[SkillRevision, ...]:
        return await self._list_skills(scope, enabled_only=True)

    async def list_skill_revisions(self, scope: ProjectScopeContext) -> tuple[SkillRevision, ...]:
        return await self._list_skills(scope, enabled_only=False)

    async def _list_skills(
        self, scope: ProjectScopeContext, *, enabled_only: bool
    ) -> tuple[SkillRevision, ...]:
        self._assert_scope(scope)
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(skill_revision)
                    .where(
                        *scope_predicates(skill_revision, self._scope),
                        *(
                            (skill_revision.c.status == AssetRevisionStatus.ENABLED.value,)
                            if enabled_only
                            else ()
                        ),
                    )
                    .order_by(
                        *(
                            (skill_revision.c.revision_id,)
                            if enabled_only
                            else (
                                skill_revision.c.skill_id,
                                skill_revision.c.revision_number.desc(),
                            )
                        )
                    )
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
            value.model_alias,
            value.description,
            value.skill_revision_ids,
            value.origin,
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
            return await self._load_agent(session, row)

    async def _load_agent(self, session: AsyncSession, row) -> AiAgentRevision:
        skills = await self._agent_skills(session, [row["revision_id"]])
        return self._agent(row, skills.get(row["revision_id"], ()))

    async def _agent_skills(
        self, session: AsyncSession, revision_ids: list[str]
    ) -> dict[str, tuple[str, ...]]:
        if not revision_ids:
            return {}
        rows = (
            await session.execute(
                select(
                    ai_agent_revision_skill.c.revision_id,
                    ai_agent_revision_skill.c.skill_revision_id,
                )
                .where(
                    *scope_predicates(ai_agent_revision_skill, self._scope),
                    ai_agent_revision_skill.c.revision_id.in_(revision_ids),
                )
                .order_by(ai_agent_revision_skill.c.revision_id, ai_agent_revision_skill.c.position)
            )
        ).all()
        values: dict[str, list[str]] = {}
        for revision_id, skill_revision_id in rows:
            values.setdefault(revision_id, []).append(skill_revision_id)
        return {key: tuple(items) for key, items in values.items()}

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
    def _agent(cls, row, skill_revision_ids: tuple[str, ...] = ()) -> AiAgentRevision:
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
            model_alias=row["model_alias"],
            description=row["description"],
            skill_revision_ids=skill_revision_ids,
            origin=row["origin"],
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
            origin=row["origin"],
            description=row["description"],
            requested_tools=tuple(row["requested_tools_json"] or ()),
            front_matter_json=(
                None
                if row["front_matter_json"] is None
                else json.dumps(
                    row["front_matter_json"],
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
            ),
            source_markdown=row["source_markdown"],
        )
