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

        assert action in {"ai.agents.read", "ai.skills.read"}
        return AuthorizationDecision(True, "validation-allowed")


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
            tool_allowlist=frozenset({"knowledge.search"}),
            output_schema_digest=text_digest("output-schema"),
        )

    async def list_skills(self, scope):
        return ()

    async def get_skill(self, scope, revision_id):
        raise LookupError(revision_id)


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
                "displayName": "Knowledge agent",
                "contentDigest": text_digest("agent-v1"),
                "toolAllowlist": ["knowledge.search"],
                "outputSchemaDigest": text_digest("output-schema"),
            }
        ]
    }
    assert detail.status_code == 200
    assert "systemInstruction" not in detail.text
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
