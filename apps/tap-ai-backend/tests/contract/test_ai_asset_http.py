from __future__ import annotations

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.modules.access.adapters.validation import VALIDATION_SCOPE


class _Authority:
    async def current(self, _facts):
        return VALIDATION_SCOPE

    async def authorize(self, _scope, action, _resource):
        from tap.modules.access.domain.authorization import AuthorizationDecision

        allowed = {"ai.agents.read", "ai.skills.read"}
        assert action in allowed | {"ai.agents.write"}
        return AuthorizationDecision(action in allowed, "validation-allowed")


class _Assets:
    scope = VALIDATION_SCOPE

    async def list_agents(self, scope):
        assert scope == VALIDATION_SCOPE
        return (await self.get_agent(scope, "agent-revision"),)

    async def get_agent(self, scope, revision_id):
        from tap.modules.ai.domain.assets import (
            AiAgentRevision,
            AssetRevisionRejected,
            text_digest,
        )

        assert scope == VALIDATION_SCOPE
        if revision_id != "agent-revision":
            raise AssetRevisionRejected()
        return AiAgentRevision(
            revision_id=revision_id,
            asset_id="knowledge-agent",
            display_name="Knowledge agent",
            scope=scope,
            content_digest=text_digest("agent-v1"),
            system_instruction_digest=text_digest("system-instruction"),
            system_instruction="system-instruction",
            tool_allowlist=frozenset({"knowledge.search"}),
            output_schema_digest=text_digest("output-schema"),
        )

    async def list_skills(self, scope):
        return ()

    async def get_skill(self, scope, revision_id):
        raise LookupError(revision_id)

    async def resolve_historical_agent(self, scope, revision_id):
        return await self.get_agent(scope, revision_id)

    async def resolve_historical_skill(self, scope, revision_id):
        return await self.get_skill(scope, revision_id)

    async def list_revisions(self, kind, asset_id):
        if kind == "agent" and asset_id == "knowledge-agent":
            return (await self.get_agent(VALIDATION_SCOPE, "agent-revision"),)
        return ()


def test_agent_and_skill_catalog_is_project_scoped_read_only_and_omits_content() -> None:
    from tap.modules.ai.domain.assets import text_digest

    authority = _Authority()
    app = create_app(
        HttpServices(
            asset_catalog=_Assets(),
            scope=VALIDATION_SCOPE,
            scope_provider=authority,
            authorization_policy=authority,
        )
    )
    client = TestClient(app)

    listed = client.get("/api/v1/projects/tapper-demo/ai/agents")
    detail = client.get("/api/v1/projects/tapper-demo/ai/agents/agent-revision")
    denied = client.get("/api/v1/projects/other/ai/agents")

    assert listed.status_code == 200
    assert listed.json() == {
        "items": [
            {
                "revisionId": "agent-revision",
                "assetId": "knowledge-agent",
                "agentId": "knowledge-agent",
                "displayName": "Knowledge agent",
                "description": None,
                "contentDigest": text_digest("agent-v1"),
                "toolAllowlist": ["knowledge.search"],
                "outputSchemaDigest": text_digest("output-schema"),
                "sourceKind": "built_in",
                "builtIn": True,
                "status": "enabled",
                "isLatest": True,
                "skillRevisionIds": [],
                "defaultModelAlias": None,
                "hasSkillUpdates": False,
            }
        ]
    }
    assert detail.status_code == 200
    # The single-revision GET returns the full detail shape (spec §3.3),
    # including systemInstruction — unlike the list route above, which only
    # ever returns the summary shape and omits it.
    assert detail.json()["systemInstruction"] == "system-instruction"
    assert "systemInstruction" not in listed.text
    assert client.post("/api/v1/projects/tapper-demo/ai/agents", json={}).status_code in {403, 405}
    assert denied.status_code == 403


def test_unknown_agent_revision_uses_a_safe_problem_response() -> None:
    authority = _Authority()
    app = create_app(
        HttpServices(
            asset_catalog=_Assets(),
            scope=VALIDATION_SCOPE,
            scope_provider=authority,
            authorization_policy=authority,
        )
    )

    response = TestClient(app).get("/api/v1/projects/tapper-demo/ai/agents/unknown")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"] == "https://tap.example/problems/asset-revision-unavailable"


def test_tool_catalog_defaults_to_english_and_supports_zh_locale() -> None:
    from tap.modules.ai.domain.tools import TOOL_REGISTRY

    authority = _Authority()
    app = create_app(
        HttpServices(
            asset_catalog=_Assets(),
            scope=VALIDATION_SCOPE,
            scope_provider=authority,
            authorization_policy=authority,
        )
    )
    client = TestClient(app)

    default = client.get("/api/v1/projects/tapper-demo/ai/tools")
    english = client.get("/api/v1/projects/tapper-demo/ai/tools?locale=en")
    chinese = client.get("/api/v1/projects/tapper-demo/ai/tools?locale=zh")
    denied = client.get("/api/v1/projects/other/ai/tools")

    assert default.status_code == english.status_code == chinese.status_code == 200
    assert default.json() == english.json()

    expected_tool_ids = sorted(TOOL_REGISTRY)
    english_body = english.json()
    assert sorted(item["toolId"] for item in english_body["items"]) == expected_tool_ids
    for item in english_body["items"]:
        definition = TOOL_REGISTRY[item["toolId"]]
        assert item["name"] == definition.names["en"]
        assert item["description"] == definition.descriptions["en"]
        assert item["required"] is True

    chinese_body = chinese.json()
    assert sorted(item["toolId"] for item in chinese_body["items"]) == expected_tool_ids
    for item in chinese_body["items"]:
        definition = TOOL_REGISTRY[item["toolId"]]
        assert item["name"] == definition.names["zh"]
        assert item["description"] == definition.descriptions["zh"]

    assert denied.status_code == 403


class _AssetsWithoutSystemInstruction:
    """An Agent revision whose stored `system_instruction` is absent (e.g.
    a built-in seeded without one) — the detail route must return it as
    `null` rather than coercing to `""` and 500ing against the response
    model's `min_length=1` constraint."""

    scope = VALIDATION_SCOPE

    async def get_agent(self, scope, revision_id):
        from tap.modules.ai.domain.assets import AiAgentRevision, text_digest

        assert scope == VALIDATION_SCOPE
        return AiAgentRevision(
            revision_id=revision_id,
            asset_id="bare-agent",
            display_name="Bare agent",
            scope=scope,
            content_digest=text_digest("bare-agent-v1"),
            system_instruction_digest=text_digest(""),
            tool_allowlist=frozenset({"knowledge.search"}),
            output_schema_digest=text_digest("output-schema"),
        )

    async def resolve_historical_agent(self, scope, revision_id):
        return await self.get_agent(scope, revision_id)

    async def list_revisions(self, kind, asset_id):
        return (await self.get_agent(VALIDATION_SCOPE, "bare-agent-revision"),)


def test_get_agent_detail_tolerates_a_missing_system_instruction() -> None:
    authority = _Authority()
    app = create_app(
        HttpServices(
            asset_catalog=_AssetsWithoutSystemInstruction(),
            scope=VALIDATION_SCOPE,
            scope_provider=authority,
            authorization_policy=authority,
        )
    )
    client = TestClient(app)

    detail = client.get("/api/v1/projects/tapper-demo/ai/agents/bare-agent-revision")

    assert detail.status_code == 200
    assert detail.json()["systemInstruction"] is None
