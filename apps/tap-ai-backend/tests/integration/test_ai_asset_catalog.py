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
        # C-1/C-2: `list_agents` returns one row per asset -- its latest
        # revision -- regardless of enabled/disabled status, so a disabled
        # asset is still visible to an "all/disabled" catalog filter
        # instead of disappearing outright.
        after_disable = await repository.list_agents(VALIDATION_SCOPE)
        assert [item.revision_id for item in after_disable] == [agents[0].revision_id]
        assert after_disable[0].status.value == "disabled"
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
        # A revision id distinct from any built-in id (`validation-knowledge-agent-v2`,
        # `-v3`, ...) so this locally-constructed "adopted" revision cannot alias a
        # built-in seed revision with different content.
        adopted_schema = {"type": "object"}
        adopted = AiAgentRevision(
            revision_id="validation-knowledge-agent-adopted-test-v1",
            asset_id=seed.agents[0].asset_id,
            display_name="Knowledge agent adopted test",
            scope=VALIDATION_SCOPE,
            content_digest=text_digest("validation-ai-agent-adopted-test-v1"),
            system_instruction_digest=text_digest("knowledge-agent-system-instruction-adopted-v1"),
            tool_allowlist=frozenset({"knowledge.search"}),
            output_schema_digest=schema_digest(adopted_schema),
            adopted_from_revision_id=seed.agents[0].revision_id,
            system_instruction="knowledge-agent-system-instruction-adopted-v1",
            output_schema_json='{"type":"object"}',
        )
        await asyncio.gather(
            MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).seed(
                ValidationAssetSeed(version="adopted-test-v1", agents=(adopted,), skills=())
            ),
            MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).seed(
                ValidationAssetSeed(version="adopted-test-v1", agents=(adopted,), skills=())
            ),
        )
        # C-1: `list_agents` returns one row per asset -- its latest
        # revision -- not every enabled revision; `adopted` is a further
        # revision of the *same* asset (`seed.agents[0].asset_id`), so only
        # it should be listed now, not the built-in v2/v3 revisions too.
        assert [
            item.revision_id
            for item in await MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).list_agents(
                VALIDATION_SCOPE
            )
        ] == [adopted.revision_id]
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
        # The initial `seed(seed)` call persists both built-in revisions
        # (v2, v3) of this asset before the locally adopted revision is
        # seeded, so the adopted revision is the third revision number.
        assert persisted_numbers == [
            (seed.agents[0].revision_id, 1),
            (seed.agents[1].revision_id, 2),
            (adopted.revision_id, 3),
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
async def test_set_asset_enabled_is_asset_level_across_every_revision(
    owned_project_mysql,
) -> None:
    """C-2: disabling a Skill must flip *every* revision's status, not just
    the latest, so an Agent bound to an older (non-latest) revision of the
    Skill can be told it's disabled too -- and disabling/re-enabling must be
    reversible rather than the one-way flip the bug produced."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.domain.assets import SkillRevision, text_digest

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        catalog = MysqlAssetCatalog(
            async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
        )

        def _revision(revision_id: str, body: str, *, adopted: str | None) -> SkillRevision:
            return SkillRevision(
                revision_id=revision_id,
                asset_id="multi-rev-skill",
                display_name="Multi revision skill",
                scope=VALIDATION_SCOPE,
                content_digest=text_digest(revision_id),
                instruction_template_digest=text_digest(body),
                applicable_tasks=frozenset({"knowledge.answer"}),
                instruction_template=body,
                adopted_from_revision_id=adopted,
            )

        v1 = _revision("multi-rev-skill-v1", "v1 instructions", adopted=None)
        await catalog.insert_skill_revision(v1, base_revision_id=None)
        v2 = _revision("multi-rev-skill-v2", "v2 instructions", adopted=v1.revision_id)
        await catalog.insert_skill_revision(v2, base_revision_id=v1.revision_id)

        await catalog.set_asset_enabled("skill", v1.asset_id, False)

        # Both the older (v1, still what some Agent might be bound to) and
        # the latest (v2) revision read as disabled now.
        v1_disabled = await catalog.resolve_historical_skill(VALIDATION_SCOPE, v1.revision_id)
        v2_disabled = await catalog.resolve_historical_skill(VALIDATION_SCOPE, v2.revision_id)
        assert v1_disabled.status.value == "disabled"
        assert v2_disabled.status.value == "disabled"

        # `list_skills` still surfaces the asset (latest revision, any
        # status) so a "Disabled" catalog filter can find it.
        listed = await catalog.list_skills(VALIDATION_SCOPE)
        matching = [item for item in listed if item.asset_id == v1.asset_id]
        assert [item.revision_id for item in matching] == [v2.revision_id]
        assert matching[0].status.value == "disabled"

        # Re-enabling is reversible (not a one-way flip) and also applies
        # to every revision.
        await catalog.set_asset_enabled("skill", v1.asset_id, True)
        v1_enabled = await catalog.resolve_historical_skill(VALIDATION_SCOPE, v1.revision_id)
        v2_enabled = await catalog.resolve_historical_skill(VALIDATION_SCOPE, v2.revision_id)
        assert v1_enabled.status.value == "enabled"
        assert v2_enabled.status.value == "enabled"
        assert (await catalog.get_skill(VALIDATION_SCOPE, v2.revision_id)).revision_id == (
            v2.revision_id
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_skills_returns_latest_revision_per_asset_beyond_the_old_page_cap(
    owned_project_mysql,
) -> None:
    """C-1: a Skill with many revisions must not multiply its entries in
    `list_skills` -- only its latest revision is listed, however many
    revisions the asset has accumulated (here, more than the old 64-item
    response cap would have tolerated if every revision were listed)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.domain.assets import SkillRevision, text_digest

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        catalog = MysqlAssetCatalog(
            async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
        )
        base_id: str | None = None
        latest_id = ""
        for index in range(70):
            revision_id = f"many-rev-skill-v{index}"
            revision = SkillRevision(
                revision_id=revision_id,
                asset_id="many-rev-skill",
                display_name="Many revision skill",
                scope=VALIDATION_SCOPE,
                content_digest=text_digest(revision_id),
                instruction_template_digest=text_digest(f"instructions {index}"),
                applicable_tasks=frozenset({"knowledge.answer"}),
                instruction_template=f"instructions {index}",
                adopted_from_revision_id=base_id,
            )
            await catalog.insert_skill_revision(revision, base_revision_id=base_id)
            base_id = revision_id
            latest_id = revision_id

        listed = await catalog.list_skills(VALIDATION_SCOPE)
        matching = [item for item in listed if item.asset_id == "many-rev-skill"]
        assert [item.revision_id for item in matching] == [latest_id]

        history = await catalog.list_revisions("skill", "many-rev-skill")
        assert len(history) == 70
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


@pytest.mark.asyncio
async def test_concurrent_insert_skill_revision_on_the_same_base_conflicts_cleanly(
    owned_project_mysql,
) -> None:
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.domain.assets import RevisionConflict, SkillRevision, text_digest

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        def _revision(revision_id: str, body: str) -> SkillRevision:
            return SkillRevision(
                revision_id=revision_id,
                asset_id="race-skill",
                display_name="Race skill",
                scope=VALIDATION_SCOPE,
                content_digest=text_digest(revision_id),
                instruction_template_digest=text_digest(body),
                applicable_tasks=frozenset({"knowledge.answer"}),
                instruction_template=body,
                source_kind="authored",
            )

        v1 = _revision("race-skill-v1", "v1 body")
        await MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).insert_skill_revision(
            v1, base_revision_id=None
        )

        results = await asyncio.gather(
            MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).insert_skill_revision(
                _revision("race-skill-v2a", "v2a body"), base_revision_id=v1.revision_id
            ),
            MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).insert_skill_revision(
                _revision("race-skill-v2b", "v2b body"), base_revision_id=v1.revision_id
            ),
            return_exceptions=True,
        )

        successes = [item for item in results if isinstance(item, SkillRevision)]
        conflicts = [item for item in results if isinstance(item, RevisionConflict)]
        other_errors = [
            item for item in results if not isinstance(item, (SkillRevision, RevisionConflict))
        ]
        assert other_errors == []
        assert len(successes) == 1
        assert len(conflicts) == 1

        catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)
        revisions = await catalog.list_revisions("skill", "race-skill")
        assert [item.revision_id for item in revisions] == [
            v1.revision_id,
            successes[0].revision_id,
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_insert_agent_revision_on_the_same_base_conflicts_cleanly(
    owned_project_mysql,
) -> None:
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.domain.assets import AiAgentRevision, RevisionConflict, text_digest

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        def _revision(revision_id: str, instruction: str) -> AiAgentRevision:
            return AiAgentRevision(
                revision_id=revision_id,
                asset_id="race-agent",
                display_name="Race agent",
                scope=VALIDATION_SCOPE,
                content_digest=text_digest(revision_id),
                system_instruction_digest=text_digest(instruction),
                tool_allowlist=frozenset({"knowledge.search"}),
                output_schema_digest=text_digest("schema"),
                source_kind="authored",
            )

        v1 = _revision("race-agent-v1", "v1 instruction")
        await MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).insert_agent_revision(
            v1, base_revision_id=None
        )

        results = await asyncio.gather(
            MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).insert_agent_revision(
                _revision("race-agent-v2a", "v2a instruction"), base_revision_id=v1.revision_id
            ),
            MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).insert_agent_revision(
                _revision("race-agent-v2b", "v2b instruction"), base_revision_id=v1.revision_id
            ),
            return_exceptions=True,
        )

        successes = [item for item in results if isinstance(item, AiAgentRevision)]
        conflicts = [item for item in results if isinstance(item, RevisionConflict)]
        other_errors = [
            item for item in results if not isinstance(item, (AiAgentRevision, RevisionConflict))
        ]
        assert other_errors == []
        assert len(successes) == 1
        assert len(conflicts) == 1

        catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)
        revisions = await catalog.list_revisions("agent", "race-agent")
        assert [item.revision_id for item in revisions] == [
            v1.revision_id,
            successes[0].revision_id,
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_authored_agent_is_resolvable_for_a_conversation_turn(owned_project_mysql) -> None:
    # Reproduces the `make demo-e2e` defect through the exact real-wiring
    # path the API uses: `AgentAuthoringService.create` (the UI's "New
    # agent"/"Copy as custom" flow) writes to the real `MysqlAssetCatalog`,
    # then `routes/conversations.py`'s `_input()` calls
    # `asset_catalog.resolve_agent(..., tools=..., output_schema_digest=...)`
    # on exactly this revision before accepting a Turn that names it. Before
    # the fix, every authored Agent had `output_schema_json=None` and a
    # digest that could never match, so this always raised
    # `AssetRevisionRejected` (surfaced as the 404
    # `asset-revision-unavailable`) — see agent_authoring.py.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.application.agent_authoring import AgentAuthoringService, AgentDraft
    from tap.modules.ai.application.assets import VALIDATION_OUTPUT_SCHEMA
    from tap.modules.ai.domain.models import schema_digest

    class _NoSelectableModels:
        async def list_models(self, scope):
            return ()

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        catalog = MysqlAssetCatalog(
            async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
        )
        service = AgentAuthoringService(catalog, _NoSelectableModels(), scope=VALIDATION_SCOPE)

        # The real `AgentForm` never sends `toolIds` either (see round 3).
        created = await service.create(
            VALIDATION_SCOPE,
            AgentDraft(
                name="demo-authored-agent",
                description="An authored agent for integration tests.",
                system_instruction="Answer using only approved knowledge.",
            ),
        )

        resolved = await catalog.resolve_agent(
            VALIDATION_SCOPE,
            created.revision_id,
            tools=frozenset({"knowledge.answer"}),
            output_schema_digest=schema_digest(VALIDATION_OUTPUT_SCHEMA),
        )

        assert resolved.revision_id == created.revision_id
        assert resolved.output_schema_json is not None
        assert resolved.output_schema_digest == schema_digest(VALIDATION_OUTPUT_SCHEMA)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_seed_v3_appends_latest_built_in_agent_revision(owned_project_mysql) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.application.assets import ValidationAssetSeed, validation_asset_seed

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        catalog = MysqlAssetCatalog(
            async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
        )
        full_seed = validation_asset_seed(VALIDATION_SCOPE)
        v2_only = ValidationAssetSeed(
            version="validation-ai-assets-v2",
            agents=(full_seed.agents[0],),
            skills=full_seed.skills,
        )

        await catalog.seed(v2_only)
        revisions_after_v2 = await catalog.list_revisions("agent", "validation-knowledge-agent")
        assert [item.revision_id for item in revisions_after_v2] == [
            "validation-knowledge-agent-v2"
        ]

        await catalog.seed(full_seed)
        revisions_after_v3 = await catalog.list_revisions("agent", "validation-knowledge-agent")
        assert [item.revision_id for item in revisions_after_v3] == [
            "validation-knowledge-agent-v2",
            "validation-knowledge-agent-v3",
        ]
        assert revisions_after_v3[0].status.value == "enabled"
        assert revisions_after_v3[1].status.value == "enabled"

        # Repeating the full seed is idempotent and does not error.
        await catalog.seed(full_seed)
        revisions_repeat = await catalog.list_revisions("agent", "validation-knowledge-agent")
        assert [item.revision_id for item in revisions_repeat] == [
            "validation-knowledge-agent-v2",
            "validation-knowledge-agent-v3",
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_revision_and_event_commit_together(owned_project_mysql) -> None:
    # Task 2: the revision insert and its maintenance event must land in the
    # same `self._sessions.begin()` block. Pre-insert a maintenance event
    # row that collides on `event_id` with the one the second revise sends,
    # so the event insert itself fails with a clean `IntegrityError` -- then
    # assert the revision never landed either.
    from datetime import datetime, timezone

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.agent_team.adapters.mysql import agent_maintenance_event
    from tap.modules.agent_team.domain.models import MaintenanceEvent
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.domain.assets import AiAgentRevision, RevisionConflict, text_digest
    from tap.platform.db.project_scope import scope_values

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)

        def _revision(revision_id: str, instruction: str) -> AiAgentRevision:
            return AiAgentRevision(
                revision_id=revision_id,
                asset_id="event-atomic-agent",
                display_name="Event atomic agent",
                scope=VALIDATION_SCOPE,
                content_digest=text_digest(revision_id),
                system_instruction_digest=text_digest(instruction),
                tool_allowlist=frozenset({"knowledge.search"}),
                output_schema_digest=text_digest("schema"),
                source_kind="authored",
            )

        v1 = _revision("event-atomic-agent-v1", "v1 instruction")
        await catalog.insert_agent_revision(v1, base_revision_id=None)

        now = datetime.now(timezone.utc)
        async with sessions() as session:
            await session.execute(
                agent_maintenance_event.insert().values(
                    event_id="dup-event",
                    agent_id="event-atomic-agent",
                    kind="revised",
                    revision_id=None,
                    feedback_id=None,
                    note=None,
                    occurred_at=now,
                    **scope_values(VALIDATION_SCOPE),
                )
            )
            await session.commit()

        v2 = _revision("event-atomic-agent-v2", "v2 instruction")
        colliding_event = MaintenanceEvent(
            event_id="dup-event",
            project_id=VALIDATION_SCOPE.project_id,
            agent_id="event-atomic-agent",
            actor_id=VALIDATION_SCOPE.actor_id,
            kind="revised",
            revision_id=v2.revision_id,
            feedback_id=None,
            note=None,
            occurred_at=now,
        )

        with pytest.raises(RevisionConflict):
            await catalog.insert_agent_revision(
                v2, base_revision_id=v1.revision_id, event=colliding_event
            )

        revisions = await catalog.list_revisions("agent", "event-atomic-agent")
        assert [item.revision_id for item in revisions] == [v1.revision_id]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_new_agent_owner_defaults_to_creator(owned_project_mysql) -> None:
    # Task 1's ruling: the first `ai_agent` insert (inside
    # `insert_agent_revision`, when no parent row exists yet) writes
    # `owner_actor_id`/`owner_assigned_at` from the creating scope. Kept
    # here as Task 2 coverage since authoring writes (this task's actual
    # scope) run through this same path.
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog, ai_agent
    from tap.modules.ai.domain.assets import AiAgentRevision, text_digest

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)

        v1 = AiAgentRevision(
            revision_id="owner-default-agent-v1",
            asset_id="owner-default-agent",
            display_name="Owner default agent",
            scope=VALIDATION_SCOPE,
            content_digest=text_digest("owner-default-agent-v1"),
            system_instruction_digest=text_digest("v1 instruction"),
            tool_allowlist=frozenset({"knowledge.search"}),
            output_schema_digest=text_digest("schema"),
            source_kind="authored",
        )
        await catalog.insert_agent_revision(v1, base_revision_id=None)

        async with sessions() as session:
            row = (
                (
                    await session.execute(
                        select(
                            ai_agent.c.owner_actor_id,
                            ai_agent.c.owner_assigned_at,
                            ai_agent.c.created_at,
                        ).where(ai_agent.c.agent_id == "owner-default-agent")
                    )
                )
                .mappings()
                .one()
            )

        assert row["owner_actor_id"] == VALIDATION_SCOPE.actor_id
        assert row["owner_assigned_at"] == row["created_at"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_disables_write_exactly_one_event(owned_project_mysql) -> None:
    """B5 (not run locally: needs isolated MySQL): two simultaneous disables
    of one Agent both pass the service's pre-check, but the conditional
    UPDATE lets only the first record a maintenance event."""
    import asyncio
    from datetime import UTC, datetime

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.agent_team.adapters.mysql import agent_maintenance_event
    from tap.modules.agent_team.domain.models import new_event
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        catalog = MysqlAssetCatalog(
            async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
        )
        from tap.modules.ai.application.assets import validation_asset_seed

        await catalog.seed(validation_asset_seed(VALIDATION_SCOPE))
        agent_id = (await catalog.list_agents(VALIDATION_SCOPE))[0].asset_id

        def _event():
            return new_event(VALIDATION_SCOPE, agent_id, "disabled", now=datetime.now(UTC))

        await asyncio.gather(
            catalog.set_asset_enabled("agent", agent_id, False, event=_event()),
            catalog.set_asset_enabled("agent", agent_id, False, event=_event()),
        )
        async with engine.connect() as connection:
            count = await connection.scalar(
                sa.select(sa.func.count())
                .select_from(agent_maintenance_event)
                .where(agent_maintenance_event.c.kind == "disabled")
            )
        assert count == 1
    finally:
        await engine.dispose()
