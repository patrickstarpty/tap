from __future__ import annotations

import os

import pytest


@pytest.mark.asyncio
async def test_mysql_seed_is_idempotent_and_retired_revisions_remain_historically_resolvable(
    owned_project_mysql,
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.application.assets import validation_asset_seed

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        repository = MysqlAssetCatalog(
            async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
        )
        seed = validation_asset_seed(VALIDATION_SCOPE)
        await repository.seed(seed)
        await repository.seed(seed)
        agents = await repository.list_agents(VALIDATION_SCOPE)
        assert len(agents) == 1
        await repository.disable_agent(agents[0].revision_id)
        assert await repository.list_agents(VALIDATION_SCOPE) == ()
        historical = await repository.get_historical_agent(VALIDATION_SCOPE, agents[0].revision_id)
        assert historical.revision_id == agents[0].revision_id
        assert historical.content_digest == agents[0].content_digest
        assert historical.status.value == "disabled"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_seed_preserves_retirement_and_resolves_historical_agent_and_skill(
    owned_project_mysql,
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.application.assets import validation_asset_seed
    from tap.modules.ai.domain.assets import AssetRevisionRejected

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        catalog = MysqlAssetCatalog(
            async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
        )
        seed = validation_asset_seed(VALIDATION_SCOPE)
        await catalog.seed(seed)
        await catalog.disable_agent(seed.agents[0].revision_id)
        await catalog.disable_skill(seed.skills[0].revision_id)
        await catalog.seed(seed)
        for resolve in (
            catalog.resolve_agent(
                VALIDATION_SCOPE,
                seed.agents[0].revision_id,
                tools=frozenset({"knowledge.search"}),
                output_schema_digest=seed.agents[0].output_schema_digest,
            ),
            catalog.resolve_skill(VALIDATION_SCOPE, seed.skills[0].revision_id),
        ):
            with pytest.raises(AssetRevisionRejected):
                await resolve
        assert (
            await catalog.resolve_historical_agent(VALIDATION_SCOPE, seed.agents[0].revision_id)
        ).status.value == "disabled"
        assert (
            await catalog.resolve_historical_skill(VALIDATION_SCOPE, seed.skills[0].revision_id)
        ).status.value == "disabled"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_concurrent_seed_serializes_creator_and_revision_numbers(
    owned_project_mysql,
) -> None:
    import asyncio

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog, ai_agent_revision
    from tap.modules.ai.application.assets import ValidationAssetSeed, validation_asset_seed
    from tap.modules.ai.domain.assets import AiAgentRevision, text_digest
    from tap.modules.ai.domain.models import schema_digest

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        seed = validation_asset_seed(VALIDATION_SCOPE)
        await asyncio.gather(
            MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).seed(seed),
            MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).seed(seed),
        )
        v2_schema = {"type": "object"}
        v2 = AiAgentRevision(
            revision_id="validation-knowledge-agent-v3",
            asset_id=seed.agents[0].asset_id,
            display_name="Knowledge agent v2",
            scope=VALIDATION_SCOPE,
            content_digest=text_digest("validation-ai-agent-v3"),
            system_instruction_digest=text_digest("knowledge-agent-system-instruction-v3"),
            tool_allowlist=frozenset({"knowledge.search"}),
            output_schema_digest=schema_digest(v2_schema),
            adopted_from_revision_id=seed.agents[0].revision_id,
            system_instruction="knowledge-agent-system-instruction-v3",
            output_schema_json='{"type":"object"}',
        )
        await asyncio.gather(
            MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).seed(
                ValidationAssetSeed(version="v2", agents=(v2,), skills=())
            ),
            MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).seed(
                ValidationAssetSeed(version="v2", agents=(v2,), skills=())
            ),
        )
        assert [
            item.revision_id
            for item in await MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).list_agents(
                VALIDATION_SCOPE
            )
        ] == [seed.agents[0].revision_id, v2.revision_id]
        async with sessions() as session:
            persisted_numbers = list(
                (
                    await session.execute(
                        select(
                            ai_agent_revision.c.revision_id,
                            ai_agent_revision.c.revision_number,
                        ).order_by(ai_agent_revision.c.revision_number)
                    )
                ).all()
            )
        assert persisted_numbers == [
            (seed.agents[0].revision_id, 1),
            (v2.revision_id, 2),
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_seed_rolls_back_batch_and_releases_its_connection_lock(
    owned_project_mysql,
) -> None:
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog, ai_agent, ai_agent_revision
    from tap.modules.ai.application.assets import ValidationAssetSeed, validation_asset_seed
    from tap.modules.ai.domain.assets import AiAgentRevision, AssetRevisionRejected, text_digest

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)
        seed = validation_asset_seed(VALIDATION_SCOPE)
        invalid = AiAgentRevision(
            revision_id="missing-adoption-v1",
            asset_id="missing-adoption-agent",
            display_name="Missing adoption agent",
            scope=VALIDATION_SCOPE,
            content_digest=text_digest("missing-adoption-content"),
            system_instruction_digest=text_digest("missing-adoption-instruction"),
            tool_allowlist=frozenset({"knowledge.search"}),
            output_schema_digest=text_digest("missing-adoption-schema"),
            adopted_from_revision_id="does-not-exist",
        )
        with pytest.raises(AssetRevisionRejected):
            await catalog.seed(
                ValidationAssetSeed(version="invalid", agents=(seed.agents[0], invalid), skills=())
            )
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(ai_agent)) == 0
            assert await session.scalar(select(func.count()).select_from(ai_agent_revision)) == 0
            assert await session.scalar(select(func.is_free_lock(catalog._lock_name()))) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_seed_cancellation_and_timeout_leave_no_catalog_lock(
    owned_project_mysql,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.application.assets import validation_asset_seed
    from tap.modules.ai.domain.assets import AiAgentRevision, AssetRevisionRejected

    entered = asyncio.Event()
    continue_seed = asyncio.Event()

    class PausingCatalog(MysqlAssetCatalog):
        async def _seed_agent(self, session: AsyncSession, revision: AiAgentRevision) -> None:
            entered.set()
            await continue_seed.wait()
            await super()._seed_agent(session, revision)

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalog = PausingCatalog(sessions, scope=VALIDATION_SCOPE)
        task = asyncio.create_task(catalog.seed(validation_asset_seed(VALIDATION_SCOPE)))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with sessions() as session:
            assert await session.scalar(select(func.is_free_lock(catalog._lock_name()))) == 1

        monkeypatch.setattr(MysqlAssetCatalog, "_LOCK_TIMEOUT_SECONDS", 0)
        async with sessions() as holder:
            assert await holder.scalar(select(func.get_lock(catalog._lock_name(), 0))) == 1
            with pytest.raises(AssetRevisionRejected):
                await MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).seed(
                    validation_asset_seed(VALIDATION_SCOPE)
                )
            assert await holder.scalar(select(func.release_lock(catalog._lock_name()))) == 1
        async with sessions() as session:
            assert await session.scalar(select(func.is_free_lock(catalog._lock_name()))) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_seed_waits_for_paused_release_before_returning_cancelled_connection(
    owned_project_mysql,
) -> None:
    import asyncio

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.application.assets import validation_asset_seed

    release_started = asyncio.Event()
    continue_release = asyncio.Event()

    class PausingReleaseCatalog(MysqlAssetCatalog):
        async def _release_lock_io(self, connection: AsyncConnection, lock_name: str) -> int:
            release_started.set()
            await continue_release.wait()
            return await super()._release_lock_io(connection, lock_name)

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalog = PausingReleaseCatalog(sessions, scope=VALIDATION_SCOPE)
        task = asyncio.create_task(catalog.seed(validation_asset_seed(VALIDATION_SCOPE)))
        await release_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        async with sessions() as session:
            assert await session.scalar(select(func.is_free_lock(catalog._lock_name()))) == 0
        continue_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with sessions() as session:
            assert await session.scalar(select(func.is_free_lock(catalog._lock_name()))) == 1
    finally:
        await engine.dispose()


def test_0011_migration_is_exercised_by_the_owned_upgrade_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.getenv("TAP_RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires owned isolated MySQL")
    from scripts.migration_support import BASELINE_ROWS, run_migration_gate

    monkeypatch.delenv("TAP_DATABASE_URL", raising=False)
    monkeypatch.delenv("TAP_ALEMBIC_DATABASE_URL", raising=False)
    result = run_migration_gate("0011_ai_agent_skill_catalog")
    assert result["status"] == "passed"
    assert set(result["preserved_rows"]) == set(BASELINE_ROWS)
    assert result["ai_asset_downgrade_replay"] == "passed"
    assert result["source_backfill"] == "passed"
    assert result["scope_backfill"] == "passed"
    assert result["ai_asset_nonempty_downgrade"] == "rejected"


@pytest.mark.asyncio
async def test_insert_skill_revision_round_trips_files(owned_project_mysql) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.domain.assets import SkillFile, SkillRevision, text_digest

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        catalog = MysqlAssetCatalog(
            async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
        )
        body = "Follow the governed answer flow and cite the attached excerpt."
        attached = "Attached reference excerpt."
        files = (
            SkillFile(
                relative_path="SKILL.md",
                size_bytes=len(body.encode("utf-8")),
                sha256="a" * 64,
                category="skill",
                attached=False,
            ),
            SkillFile(
                relative_path="references/notes.md",
                size_bytes=len(attached.encode("utf-8")),
                sha256="b" * 64,
                category="reference",
                attached=True,
            ),
        )
        revision = SkillRevision(
            revision_id="authored-skill-v1",
            asset_id="authored-skill",
            display_name="Authored skill",
            scope=VALIDATION_SCOPE,
            content_digest=text_digest("authored-skill-v1"),
            instruction_template_digest=text_digest(body + attached),
            applicable_tasks=frozenset({"knowledge.answer"}),
            instruction_template=body,
            attached_references=attached,
            description="An authored skill with attached reference text.",
            source_kind="authored",
            frontmatter={"name": "authored-skill", "description": "Authored skill"},
            package_digest=text_digest("authored-skill-package"),
            files=files,
        )
        persisted = await catalog.insert_skill_revision(revision, base_revision_id=None)
        assert persisted == revision

        fetched = await catalog.get_skill(VALIDATION_SCOPE, revision.revision_id)
        assert fetched.instruction_template == body
        assert fetched.attached_references == attached
        assert fetched.description == revision.description
        assert fetched.source_kind == "authored"
        assert dict(fetched.frontmatter or {}) == dict(revision.frontmatter or {})
        assert fetched.package_digest == revision.package_digest
        assert set(fetched.files) == set(files)

        revisions = await catalog.list_revisions("skill", revision.asset_id)
        assert [item.revision_id for item in revisions] == [revision.revision_id]
        assert set(revisions[0].files) == set(files)

        found = await catalog.find_skill_by_name(VALIDATION_SCOPE, revision.display_name)
        assert found is not None
        assert found.revision_id == revision.revision_id
        assert await catalog.find_skill_by_name(VALIDATION_SCOPE, "no-such-skill") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_base_revision_conflicts(owned_project_mysql) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.domain.assets import RevisionConflict, SkillRevision, text_digest

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        catalog = MysqlAssetCatalog(
            async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
        )

        def _revision(revision_id: str, body: str) -> SkillRevision:
            return SkillRevision(
                revision_id=revision_id,
                asset_id="conflicting-skill",
                display_name="Conflicting skill",
                scope=VALIDATION_SCOPE,
                content_digest=text_digest(revision_id),
                instruction_template_digest=text_digest(body),
                applicable_tasks=frozenset({"knowledge.answer"}),
                instruction_template=body,
                source_kind="authored",
            )

        v1 = _revision("conflicting-skill-v1", "v1 body")
        await catalog.insert_skill_revision(v1, base_revision_id=None)

        # A second create attempt with no base also conflicts: the asset already
        # has a latest revision.
        with pytest.raises(RevisionConflict):
            await catalog.insert_skill_revision(
                _revision("conflicting-skill-v1-retry", "retry body"), base_revision_id=None
            )

        # A stale base_revision_id (not the current latest) conflicts.
        with pytest.raises(RevisionConflict):
            await catalog.insert_skill_revision(
                _revision("conflicting-skill-v2-stale", "stale body"),
                base_revision_id="does-not-exist",
            )

        # The correct latest base_revision_id succeeds.
        v2 = _revision("conflicting-skill-v2", "v2 body")
        await catalog.insert_skill_revision(v2, base_revision_id=v1.revision_id)
        revisions = await catalog.list_revisions("skill", "conflicting-skill")
        assert [item.revision_id for item in revisions] == [v1.revision_id, v2.revision_id]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_seed_marks_built_in_and_persists_text(owned_project_mysql) -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog, ai_agent_revision, skill_revision
    from tap.modules.ai.application.assets import validation_asset_seed

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)
        seed = validation_asset_seed(VALIDATION_SCOPE)
        await catalog.seed(seed)

        async with sessions() as session:
            agent_row = (
                (
                    await session.execute(
                        select(
                            ai_agent_revision.c.source_kind,
                            ai_agent_revision.c.system_instruction,
                        ).where(ai_agent_revision.c.revision_id == seed.agents[0].revision_id)
                    )
                )
                .mappings()
                .one()
            )
            skill_row = (
                (
                    await session.execute(
                        select(
                            skill_revision.c.source_kind,
                            skill_revision.c.instruction_body,
                        ).where(skill_revision.c.revision_id == seed.skills[0].revision_id)
                    )
                )
                .mappings()
                .one()
            )
        assert agent_row["source_kind"] == "built_in"
        assert agent_row["system_instruction"] == seed.agents[0].system_instruction
        assert skill_row["source_kind"] == "built_in"
        assert skill_row["instruction_body"] == seed.skills[0].instruction_template

        resolved_agent = await catalog.get_agent(VALIDATION_SCOPE, seed.agents[0].revision_id)
        resolved_skill = await catalog.get_skill(VALIDATION_SCOPE, seed.skills[0].revision_id)
        assert resolved_agent.source_kind == "built_in"
        assert resolved_skill.source_kind == "built_in"
    finally:
        await engine.dispose()
