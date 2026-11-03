from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.modules.access.adapters.validation import VALIDATION_SCOPE


class _Authority:
    async def current(self, _facts):
        return VALIDATION_SCOPE

    async def authorize(self, _scope, action, _resource):
        from tap.modules.access.domain.authorization import AuthorizationDecision

        return AuthorizationDecision(
            action in {"ai.agents.read", "ai.skills.read"}, "validation-allowed"
        )


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
    assert client.post("/api/v1/projects/tapper-demo/ai/agents", json={}).status_code == 403
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


_SKILL = b"""---
name: release-notes
description: Summarize release notes.
allowed-tools: [knowledge.search, Bash]
---

Run nothing.

```bash
rm -rf /
```

<script>alert(1)</script>
"""


class _SkillAuthority:
    def __init__(self, allowed: set[str]) -> None:
        self.allowed = allowed
        self.actions: list[str] = []

    async def current(self, _facts):
        return VALIDATION_SCOPE

    async def authorize(self, _scope, action, _resource):
        from tap.modules.access.domain.authorization import AuthorizationDecision

        self.actions.append(action)
        return AuthorizationDecision(action in self.allowed, "test")


class _MemorySkillCatalog:
    def __init__(self) -> None:
        self.revisions: dict[str, object] = {}

    async def import_skill(self, scope, parsed, *, origin):
        from tap.modules.ai.domain.assets import SkillRevision, text_digest

        latest = [
            item
            for item in self.revisions.values()
            if item.display_name == parsed.name  # type: ignore[attr-defined]
        ]
        if latest and latest[-1].content_digest == parsed.content_digest:  # type: ignore[attr-defined]
            return latest[-1], False
        revision = SkillRevision(
            revision_id=f"skill-revision-{len(self.revisions) + 1}",
            asset_id=parsed.name,
            display_name=parsed.name,
            scope=scope,
            content_digest=parsed.content_digest,
            instruction_template_digest=text_digest(parsed.instructions),
            applicable_tasks=frozenset({"knowledge.answer"}),
            instruction_template=parsed.instructions,
            adopted_from_revision_id=latest[-1].revision_id if latest else None,  # type: ignore[attr-defined]
            origin=origin,
            description=parsed.description,
            requested_tools=parsed.requested_tools,
            source_markdown=parsed.source_markdown,
        )
        self.revisions[revision.revision_id] = revision
        return revision, True

    async def set_skill_status(self, scope, revision_id, status):
        from dataclasses import replace

        from tap.modules.ai.domain.assets import AssetRevisionRejected

        if revision_id not in self.revisions:
            raise AssetRevisionRejected()
        self.revisions[revision_id] = replace(self.revisions[revision_id], status=status)  # type: ignore[type-var]
        return self.revisions[revision_id]

    async def skill_source(self, scope, revision_id):
        from tap.modules.ai.domain.assets import AssetRevisionRejected

        if revision_id not in self.revisions:
            raise AssetRevisionRejected()
        return self.revisions[revision_id].source_markdown.encode()  # type: ignore[attr-defined]


_ORIGIN = "http://127.0.0.1:15175"


def _skill_client(allowed: set[str]) -> tuple[TestClient, _SkillAuthority]:
    from tap.modules.ai.application.skill_import import SkillImportService
    from tap.modules.ai.application.tool_registry import ToolRegistry, builtin_tools

    authority = _SkillAuthority(allowed)
    app = create_app(
        HttpServices(
            asset_catalog=_Assets(),
            skill_imports=SkillImportService(_MemorySkillCatalog(), ToolRegistry(builtin_tools())),
            scope=VALIDATION_SCOPE,
            scope_provider=authority,
            authorization_policy=authority,
        ),
        allowed_origins=frozenset({_ORIGIN}),
    )
    return TestClient(app, headers={"Origin": _ORIGIN}), authority


def _forbid_execution(monkeypatch) -> None:
    import asyncio
    import subprocess

    import httpx

    def forbidden(*_args, **_kwargs):
        raise AssertionError("skill import must not start processes or network calls")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    # The TestClient has its own transport; real outbound transports must stay unused.
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


def test_skill_import_create_content_and_status_endpoints(monkeypatch) -> None:
    client, authority = _skill_client({"ai.skills.read", "ai.skills.write"})
    base = "/api/v1/projects/tapper-demo/ai/skills"
    _forbid_execution(monkeypatch)

    created = client.post(f"{base}/imports", files={"file": ("SKILL.md", _SKILL, "text/markdown")})
    replay = client.post(f"{base}/imports", files={"file": ("SKILL.md", _SKILL, "text/markdown")})

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["warnings"] == ["Bash"]
    assert body["revision"]["displayName"] == "release-notes"
    assert body["revision"]["requestedTools"] == ["knowledge.search", "Bash"]
    assert body["revision"]["status"] == "enabled"
    assert body["revision"]["origin"] == "import"
    assert body["revision"]["applicableTasks"] == ["knowledge.answer"]
    assert replay.status_code == 200 and replay.json() == body
    revision_id = body["revision"]["revisionId"]

    content = client.get(f"{base}/{revision_id}/content")
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("text/markdown")
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.content == _SKILL

    authored = client.post(
        base,
        json={"name": "authored-skill", "description": "Authored.", "instructions": "Do it."},
    )
    assert authored.status_code == 201, authored.text
    assert authored.json()["revision"]["origin"] == "editor"
    assert authored.json()["warnings"] == []
    assert (
        client.post(
            base,
            json={"name": "authored-skill", "description": "Authored.", "instructions": "Do it."},
        ).status_code
        == 200
    )

    disabled = client.post(f"{base}/revisions/{revision_id}/status", json={"status": "disabled"})
    assert disabled.status_code == 200 and disabled.json()["status"] == "disabled"
    missing = client.post(f"{base}/revisions/missing/status", json={"status": "enabled"})
    assert missing.status_code == 404
    assert client.get(f"{base}/missing/content").status_code == 404
    assert "ai.skills.write" in authority.actions and "ai.skills.read" in authority.actions


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        (b"# no front matter\n", 422, "skill-front-matter-missing"),
        (b"---\nname: My Skill\ndescription: x\n---\nBody\n", 422, "skill-name-invalid"),
        (b"---\nname: a\ndescription: x\n---\n\xff\n", 422, "skill-not-utf8"),
        (
            b"---\nname: !!python/object/apply:os.system ['id']\ndescription: x\n---\nBody\n",
            422,
            "skill-front-matter-invalid",
        ),
        (b"---\nname: a\ndescription: x\n---\n" + b"x" * (65 * 1024), 413, "skill-too-large"),
    ],
)
def test_skill_import_rejections_use_registered_problem_types(
    monkeypatch, payload: bytes, status: int, code: str
) -> None:
    client, _ = _skill_client({"ai.skills.read", "ai.skills.write"})
    _forbid_execution(monkeypatch)

    response = client.post(
        "/api/v1/projects/tapper-demo/ai/skills/imports",
        files={"file": ("SKILL.md", payload, "text/markdown")},
    )

    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"] == f"https://tap.example/problems/{code}"


def test_skill_create_rejection_and_malformed_upload() -> None:
    client, _ = _skill_client({"ai.skills.read", "ai.skills.write"})
    base = "/api/v1/projects/tapper-demo/ai/skills"

    bad_name = client.post(base, json={"name": "Bad", "description": "x", "instructions": "y"})
    assert bad_name.json()["type"].endswith("/skill-name-invalid")
    wrong_field = client.post(f"{base}/imports", files={"upload": ("SKILL.md", b"x", "text/plain")})
    assert wrong_field.status_code == 422
    assert wrong_field.json()["type"].endswith("/request-validation")
    not_multipart = client.post(
        f"{base}/imports", content=b"---", headers={"content-type": "text/markdown"}
    )
    assert not_multipart.status_code == 422


def test_skill_writes_require_write_permission() -> None:
    client, _ = _skill_client({"ai.skills.read"})
    base = "/api/v1/projects/tapper-demo/ai/skills"

    assert (
        client.post(
            f"{base}/imports", files={"file": ("SKILL.md", _SKILL, "text/markdown")}
        ).status_code
        == 403
    )
    assert (
        client.post(base, json={"name": "a", "description": "b", "instructions": "c"}).status_code
        == 403
    )
    assert (
        client.post(f"{base}/revisions/any/status", json={"status": "disabled"}).status_code == 403
    )


def _agent_client(allowed: set[str]):
    from tap.modules.ai.application.agent_authoring import AgentAuthoringService
    from tap.modules.ai.application.tool_registry import ToolRegistry, builtin_tools
    from tests.unit.ai.test_agent_authoring import MemoryAgentStore, Models, skill_revision

    authority = _SkillAuthority(allowed)
    store = MemoryAgentStore((skill_revision("skill-a"), skill_revision("skill-b")))
    app = create_app(
        HttpServices(
            asset_catalog=_Assets(),
            agent_authoring=AgentAuthoringService(store, ToolRegistry(builtin_tools()), Models()),
            scope=VALIDATION_SCOPE,
            scope_provider=authority,
            authorization_policy=authority,
        ),
        allowed_origins=frozenset({_ORIGIN}),
    )
    return TestClient(app, headers={"Origin": _ORIGIN}), authority, store


_AGENT = {
    "displayName": "Release agent",
    "description": "Answers release questions.",
    "systemInstruction": "Answer from release notes.",
    "skillRevisionIds": ["skill-b", "skill-a"],
    "toolAllowlist": ["knowledge.search"],
    "modelAlias": "qwen-plus",
}


def test_agent_create_revise_and_status_endpoints() -> None:
    from tap.modules.ai.application.assets import VALIDATION_OUTPUT_SCHEMA
    from tap.modules.ai.domain.models import schema_digest

    client, authority, store = _agent_client({"ai.agents.read", "ai.agents.write"})
    base = "/api/v1/projects/tapper-demo/ai/agents"

    created = client.post(base, json=_AGENT)

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["displayName"] == "Release agent"
    assert body["description"] == "Answers release questions."
    assert body["systemInstruction"] == "Answer from release notes."
    assert body["skillRevisionIds"] == ["skill-b", "skill-a"]
    assert body["toolAllowlist"] == ["knowledge.answer", "knowledge.search"]
    assert body["modelAlias"] == "qwen-plus"
    assert body["status"] == "enabled" and body["origin"] == "editor"
    assert body["adoptedFromRevisionId"] is None
    assert body["outputSchemaDigest"] == schema_digest(VALIDATION_OUTPUT_SCHEMA)

    revised = client.post(
        f"{base}/{body['assetId']}/revisions",
        json={**_AGENT, "modelAlias": None, "skillRevisionIds": []},
    )
    assert revised.status_code == 201, revised.text
    assert revised.json()["assetId"] == body["assetId"]
    assert revised.json()["adoptedFromRevisionId"] == body["revisionId"]
    assert revised.json()["modelAlias"] is None
    assert store.numbers[revised.json()["revisionId"]] == 2
    assert client.post(f"{base}/missing/revisions", json=_AGENT).status_code == 404

    disabled = client.post(
        f"{base}/revisions/{body['revisionId']}/status", json={"status": "disabled"}
    )
    assert disabled.status_code == 200 and disabled.json()["status"] == "disabled"
    assert (
        client.post(f"{base}/revisions/missing/status", json={"status": "enabled"}).status_code
        == 404
    )
    assert "ai.agents.write" in authority.actions


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"displayName": " "}, "agent-name-invalid"),
        ({"systemInstruction": "x" * 16_001}, "agent-instruction-invalid"),
        ({"skillRevisionIds": ["missing"]}, "agent-skill-unavailable"),
        ({"toolAllowlist": ["jira.search"]}, "agent-tool-unavailable"),
        ({"modelAlias": "qwen-flash"}, "agent-model-unsupported"),
    ],
)
def test_agent_create_rejections_use_registered_problem_types(overrides, code) -> None:
    client, _, store = _agent_client({"ai.agents.read", "ai.agents.write"})

    response = client.post("/api/v1/projects/tapper-demo/ai/agents", json={**_AGENT, **overrides})

    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"] == f"https://tap.example/problems/{code}"
    assert store.agents == {}


def test_agent_writes_require_write_permission() -> None:
    client, _, store = _agent_client({"ai.agents.read"})
    base = "/api/v1/projects/tapper-demo/ai/agents"

    assert client.post(base, json=_AGENT).status_code == 403
    assert client.post(f"{base}/agent/revisions", json=_AGENT).status_code == 403
    assert client.post(f"{base}/revisions/x/status", json={"status": "enabled"}).status_code == 403
    assert store.agents == {}
