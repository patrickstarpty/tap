from __future__ import annotations

from dataclasses import replace

import pytest

SKILL = b"""---
name: release-notes
description: Summarize release notes.
---

Cite every claim.
"""


@pytest.mark.asyncio
async def test_mysql_agent_revisions_are_immutable_numbered_and_bind_skills(
    owned_project_mysql,
) -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import (
        MysqlAssetCatalog,
        ai_agent_revision,
        ai_agent_revision_skill,
    )
    from tap.modules.ai.application.agent_authoring import AgentAuthoringService, AgentDraft
    from tap.modules.ai.application.skill_import import SkillImportService
    from tap.modules.ai.application.tool_registry import ToolRegistry, builtin_tools
    from tap.modules.ai.domain.assets import AssetRevisionRejected, AssetRevisionStatus
    from tests.unit.ai.test_agent_authoring import Models

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)
        registry = ToolRegistry(builtin_tools())
        skills = SkillImportService(catalog, registry)
        first_skill, _ = await skills.import_markdown(VALIDATION_SCOPE, SKILL)
        second_skill, _ = await skills.create(
            VALIDATION_SCOPE, name="tone", description="Tone.", instructions="Be brief."
        )
        service = AgentAuthoringService(catalog, registry, Models())
        draft = AgentDraft(
            display_name="Release agent",
            description="Answers release questions.",
            system_instruction="x" * 16_000,
            skill_revision_ids=(second_skill.revision_id, first_skill.revision_id),
            tool_allowlist=frozenset({"knowledge.search"}),
            model_alias="qwen-plus",
        )

        first = await service.create(VALIDATION_SCOPE, draft)
        assert await catalog.get_agent(VALIDATION_SCOPE, first.revision_id) == first
        assert first.skill_revision_ids == (second_skill.revision_id, first_skill.revision_id)
        assert first.origin == "editor" and first.model_alias == "qwen-plus"

        second = await service.revise(
            VALIDATION_SCOPE,
            first.asset_id,
            replace(draft, skill_revision_ids=(), model_alias=None, system_instruction="Short."),
        )
        assert second.adopted_from_revision_id == first.revision_id
        assert await catalog.get_historical_agent(VALIDATION_SCOPE, second.revision_id) == second
        assert await catalog.get_historical_agent(VALIDATION_SCOPE, first.revision_id) == first

        async with sessions() as session:
            numbers = dict(
                (
                    await session.execute(
                        select(
                            ai_agent_revision.c.revision_id, ai_agent_revision.c.revision_number
                        ).where(ai_agent_revision.c.agent_id == first.asset_id)
                    )
                ).all()
            )
            links = (
                await session.execute(
                    select(
                        ai_agent_revision_skill.c.revision_id,
                        ai_agent_revision_skill.c.position,
                        ai_agent_revision_skill.c.skill_revision_id,
                    ).order_by(
                        ai_agent_revision_skill.c.revision_id, ai_agent_revision_skill.c.position
                    )
                )
            ).all()
        assert numbers == {first.revision_id: 1, second.revision_id: 2}
        assert [tuple(row) for row in links] == [
            (first.revision_id, 0, second_skill.revision_id),
            (first.revision_id, 1, first_skill.revision_id),
        ]

        listed = {item.revision_id for item in await catalog.list_agents(VALIDATION_SCOPE)}
        assert {first.revision_id, second.revision_id} <= listed
        disabled = await service.set_status(
            VALIDATION_SCOPE, first.revision_id, AssetRevisionStatus.DISABLED
        )
        assert disabled == replace(first, status=AssetRevisionStatus.DISABLED)
        with pytest.raises(AssetRevisionRejected):
            await catalog.get_agent(VALIDATION_SCOPE, first.revision_id)
        with pytest.raises(AssetRevisionRejected):
            await service.revise(VALIDATION_SCOPE, "missing-agent", draft)
        with pytest.raises(AssetRevisionRejected):
            # A stale predecessor cannot fork the revision chain.
            await catalog.insert_agent_revision(
                VALIDATION_SCOPE,
                replace(second, revision_id="stale-fork", adopted_from_revision_id=None),
            )
    finally:
        await engine.dispose()


def test_mysql_turn_snapshot_keeps_the_agent_model_and_skills_after_revision(
    owned_project_mysql,
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from starlette.testclient import TestClient

    from tap.interfaces.http.app import create_app
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.access.domain.authorization import AuthorizationDecision
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.application.agent_authoring import AgentAuthoringService
    from tap.modules.ai.application.skill_import import SkillImportService
    from tap.modules.ai.application.tool_registry import ToolRegistry, builtin_tools
    from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
    from tap.modules.chat.application.conversations import ConversationService
    from tests.conftest import validation_http_services
    from tests.unit.ai.test_agent_authoring import Models

    class Allow:
        async def authorize(self, *_args):
            return AuthorizationDecision(True, "test")

    class Knowledge:
        scope = VALIDATION_SCOPE

    engine = create_async_engine(
        owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"), poolclass=NullPool
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)
    registry = ToolRegistry(builtin_tools())
    conversations = ConversationService(
        MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE), scope=VALIDATION_SCOPE
    )
    services = replace(
        validation_http_services(knowledge=Knowledge()),
        asset_catalog=catalog,
        tool_registry=registry,
        skill_imports=SkillImportService(catalog, registry),
        agent_authoring=AgentAuthoringService(catalog, registry, Models()),
        model_catalog=Models(),
        conversations=conversations,
        authorization_policy=Allow(),
    )
    origin = "http://127.0.0.1:15175"
    base = "/api/v1/projects/tapper-demo"
    with TestClient(
        create_app(services, allowed_origins=frozenset({origin})), headers={"Origin": origin}
    ) as client:
        bound = client.post(
            f"{base}/ai/skills", json={"name": "bound", "description": "b", "instructions": "B."}
        ).json()["revision"]["revisionId"]
        extra = client.post(
            f"{base}/ai/skills", json={"name": "extra", "description": "e", "instructions": "E."}
        ).json()["revision"]["revisionId"]
        created = client.post(
            f"{base}/ai/agents",
            json={
                "displayName": "Release agent",
                "description": "",
                "systemInstruction": "Answer from release notes.",
                "skillRevisionIds": [bound],
                "toolAllowlist": ["knowledge.search"],
                "modelAlias": "qwen-plus",
            },
        )
        assert created.status_code == 201, created.text
        agent = created.json()
        turn = client.post(
            f"{base}/conversations",
            json={
                "message": "question",
                "modelAlias": "qwen-max",
                "agentRevisionId": agent["revisionId"],
                "skillRevisionIds": [extra, bound],
            },
            headers={"Idempotency-Key": "agent-turn"},
        )
        assert turn.status_code == 202, turn.text
        conversation_id = turn.json()["conversationId"]

        revised = client.post(
            f"{base}/ai/agents/{agent['assetId']}/revisions",
            json={
                "displayName": "Release agent",
                "description": "",
                "systemInstruction": "Changed instruction.",
                "skillRevisionIds": [],
                "toolAllowlist": [],
                "modelAlias": None,
            },
        )
        assert revised.status_code == 201, revised.text
        assert revised.json()["adoptedFromRevisionId"] == agent["revisionId"]

        detail = client.get(f"{base}/conversations/{conversation_id}")
        assert detail.status_code == 200, detail.text
        frozen = detail.json()["turns"][0]["input"]
        assert frozen["modelAlias"] == "qwen-plus"
        assert frozen["agentRevisionId"] == agent["revisionId"]
        assert frozen["skillRevisionIds"] == [bound, extra]

        replay = client.post(
            f"{base}/conversations",
            json={
                "message": "question",
                "modelAlias": "qwen-max",
                "agentRevisionId": agent["revisionId"],
                "skillRevisionIds": [extra, bound],
            },
            headers={"Idempotency-Key": "agent-turn"},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json() == turn.json()

    async def snapshot():
        try:
            loaded = await conversations.load(conversation_id)
            return loaded.turns[0].input_snapshot.value
        finally:
            await engine.dispose()

    import asyncio

    value = asyncio.run(snapshot())
    assert value.agent_system_instruction == "Answer from release notes."
    assert value.skill_instruction_templates == ("B.", "E.")
    assert value.agent_tool_allowlist == ("knowledge.answer", "knowledge.search")
