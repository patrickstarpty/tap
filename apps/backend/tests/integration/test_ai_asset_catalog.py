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
