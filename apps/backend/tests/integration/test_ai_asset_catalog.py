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

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.application.assets import ValidationAssetSeed, validation_asset_seed
    from tap.modules.ai.domain.assets import AiAgentRevision, text_digest

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        seed = validation_asset_seed(VALIDATION_SCOPE)
        await asyncio.gather(
            MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).seed(seed),
            MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE).seed(seed),
        )
        v2 = AiAgentRevision(
            revision_id="validation-knowledge-agent-v2",
            asset_id=seed.agents[0].asset_id,
            display_name="Knowledge agent v2",
            scope=VALIDATION_SCOPE,
            content_digest=text_digest("validation-ai-agent-v2"),
            system_instruction_digest=text_digest("knowledge-agent-system-instruction-v2"),
            tool_allowlist=frozenset({"knowledge.search"}),
            output_schema_digest=text_digest("knowledge-agent-output-schema-v2"),
            adopted_from_revision_id=seed.agents[0].revision_id,
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
    assert result["ai_asset_nonempty_downgrade"] == "rejected"
