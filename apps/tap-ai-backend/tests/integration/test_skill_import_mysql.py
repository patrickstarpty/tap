from __future__ import annotations

import pytest

SKILL = b"""---
name: release-notes
description: Summarize release notes.
allowed-tools: [knowledge.search, Bash]
---

Cite every claim.
"""


@pytest.mark.asyncio
async def test_mysql_skill_import_is_idempotent_versioned_and_project_scoped(
    owned_project_mysql,
) -> None:
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.access.domain.context import ProjectScopeContext
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog, skill, skill_revision
    from tap.modules.ai.application.skill_import import SkillImportService
    from tap.modules.ai.application.tool_registry import ToolRegistry, builtin_tools
    from tap.modules.ai.domain.assets import AssetRevisionRejected, AssetRevisionStatus

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)
        service = SkillImportService(catalog, ToolRegistry(builtin_tools()))

        first, created = await service.import_markdown(VALIDATION_SCOPE, SKILL)
        assert created is True
        assert (first.display_name, first.origin, first.status) == (
            "release-notes",
            "import",
            AssetRevisionStatus.ENABLED,
        )
        assert first.requested_tools == ("knowledge.search", "Bash")
        assert first.instruction_template == "Cite every claim."
        assert first.applicable_tasks == frozenset({"knowledge.answer"})
        assert await service.source(VALIDATION_SCOPE, first.revision_id) == SKILL

        async def counts() -> tuple[int, int]:
            async with sessions() as session:
                return (
                    await session.scalar(select(func.count()).select_from(skill)) or 0,
                    await session.scalar(select(func.count()).select_from(skill_revision)) or 0,
                )

        before = await counts()
        replay, created = await service.import_markdown(VALIDATION_SCOPE, SKILL)
        assert created is False and replay == first
        assert await counts() == before

        changed = SKILL.replace(b"Cite every claim.", b"Cite every claim twice.")
        second, created = await service.import_markdown(VALIDATION_SCOPE, changed)
        assert created is True
        assert second.asset_id == first.asset_id
        assert second.adopted_from_revision_id == first.revision_id
        async with sessions() as session:
            numbers = dict(
                (
                    await session.execute(
                        select(
                            skill_revision.c.revision_id, skill_revision.c.revision_number
                        ).where(skill_revision.c.skill_id == first.asset_id)
                    )
                ).all()
            )
        assert numbers == {first.revision_id: 1, second.revision_id: 2}
        assert (await catalog.get_historical_skill(VALIDATION_SCOPE, first.revision_id)) == first

        authored, created = await service.create(
            VALIDATION_SCOPE, name="authored-skill", description="ok", instructions="Do it."
        )
        assert created is True and authored.origin == "editor"

        other_scope = ProjectScopeContext(
            enterprise_id=VALIDATION_SCOPE.enterprise_id,
            project_id="other-project",
            actor_id=VALIDATION_SCOPE.actor_id,
            identity_mode=VALIDATION_SCOPE.identity_mode,
        )
        async with sessions.begin() as session:
            from tap.modules.access.adapters.mysql import project

            await session.execute(
                project.insert().values(
                    enterprise_id=other_scope.enterprise_id, project_id=other_scope.project_id
                )
            )
        other = MysqlAssetCatalog(sessions, scope=other_scope)
        foreign, created = await SkillImportService(
            other, ToolRegistry(builtin_tools())
        ).import_markdown(other_scope, SKILL)
        assert created is True
        assert foreign.asset_id != first.asset_id
        with pytest.raises(AssetRevisionRejected):
            await catalog.get_historical_skill(VALIDATION_SCOPE, foreign.revision_id)

        disabled = await service.set_status(
            VALIDATION_SCOPE, second.revision_id, AssetRevisionStatus.DISABLED
        )
        assert disabled.status is AssetRevisionStatus.DISABLED
        managed = await catalog.list_skill_revisions(VALIDATION_SCOPE)
        assert disabled in managed
        assert foreign not in managed
        assert disabled not in await catalog.list_skills(VALIDATION_SCOPE)
        history = [item for item in managed if item.asset_id == first.asset_id]
        assert [item.revision_id for item in history] == [second.revision_id, first.revision_id]
        with pytest.raises(AssetRevisionRejected):
            await catalog.resolve_skill(VALIDATION_SCOPE, second.revision_id)
        enabled = await service.set_status(
            VALIDATION_SCOPE, second.revision_id, AssetRevisionStatus.ENABLED
        )
        assert (await catalog.resolve_skill(VALIDATION_SCOPE, enabled.revision_id)) == enabled
        with pytest.raises(AssetRevisionRejected):
            await service.set_status(VALIDATION_SCOPE, "missing", AssetRevisionStatus.DISABLED)
    finally:
        await engine.dispose()
