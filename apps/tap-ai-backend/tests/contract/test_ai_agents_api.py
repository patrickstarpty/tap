"""HTTP contract for the Agent authoring and read surface."""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.ai.application.agent_authoring import AgentAuthoringService
from tap.modules.ai.domain.assets import AssetRevisionRejected, AssetRevisionStatus, SkillRevision
from tap.modules.ai.domain.models import ModelCapability, ModelDescriptor


class _FakeCatalog:
    """Backs both read (`AssetCatalogHttpService`) and write
    (`AgentAuthoringCatalog`) ports."""

    def __init__(self) -> None:
        self.scope = VALIDATION_SCOPE
        self._agents: dict[str, list] = {}
        self._skills: dict[str, list[SkillRevision]] = {}

    # -- read: AssetCatalogHttpService ----------------------------------

    async def list_agents(self, scope):
        return tuple(
            revision
            for history in self._agents.values()
            for revision in history
            if revision.status == AssetRevisionStatus.ENABLED
        )

    async def get_agent(self, scope, revision_id):
        for history in self._agents.values():
            for revision in history:
                if revision.revision_id == revision_id and (
                    revision.status == AssetRevisionStatus.ENABLED
                ):
                    return revision
        raise AssetRevisionRejected()

    async def list_skills(self, scope):
        return ()

    async def get_skill(self, scope, revision_id):
        for history in self._skills.values():
            for revision in history:
                if revision.revision_id == revision_id and (
                    revision.status == AssetRevisionStatus.ENABLED
                ):
                    return revision
        raise AssetRevisionRejected()

    async def list_revisions(self, kind, asset_id):
        store = self._agents if kind == "agent" else self._skills
        return tuple(store.get(asset_id, ()))

    # -- write: AgentAuthoringCatalog -------------------------------------

    async def insert_agent_revision(self, revision, *, base_revision_id):
        from tap.modules.ai.domain.assets import RevisionConflict

        history = self._agents.setdefault(revision.asset_id, [])
        latest = history[-1].revision_id if history else None
        if latest != base_revision_id:
            raise RevisionConflict()
        history.append(revision)
        return revision

    async def set_asset_enabled(self, kind, asset_id, enabled):
        store = self._agents if kind == "agent" else self._skills
        history = store.get(asset_id)
        if not history:
            raise AssetRevisionRejected()
        status = AssetRevisionStatus.ENABLED if enabled else AssetRevisionStatus.DISABLED
        history[-1] = replace(history[-1], status=status)

    async def find_agent_by_name(self, scope, name):
        for history in self._agents.values():
            if history and history[-1].display_name == name:
                return history[-1]
        return None

    # -- test seeding helper -----------------------------------------------

    def seed_skill(self, revision: SkillRevision) -> None:
        self._skills.setdefault(revision.asset_id, []).append(revision)


class _FakeModels:
    def __init__(self, models: tuple[ModelDescriptor, ...] = ()) -> None:
        self._models = models

    async def list_models(self, scope):
        return self._models


class _Authority:
    def __init__(self, allowed: frozenset[str] | None = None) -> None:
        self._allowed = allowed or frozenset({"ai.agents.read", "ai.agents.write"})

    async def current(self, _facts):
        return VALIDATION_SCOPE

    async def authorize(self, _scope, action, _resource):
        if action not in self._allowed:
            return AuthorizationDecision(False, "action-not-allowed")
        return AuthorizationDecision(True, "validation-allowed")


ORIGIN = "http://127.0.0.1:15175"
BASE = "/api/v1/projects/tapper-demo/ai"


def _app(
    *, allowed: frozenset[str] | None = None, models: tuple[ModelDescriptor, ...] = ()
) -> tuple[TestClient, _FakeCatalog]:
    catalog = _FakeCatalog()
    agent_authoring = AgentAuthoringService(catalog, _FakeModels(models), scope=VALIDATION_SCOPE)
    authority = _Authority(allowed)
    app = create_app(
        HttpServices(
            asset_catalog=catalog,
            agent_authoring=agent_authoring,
            scope=VALIDATION_SCOPE,
            scope_provider=authority,
            authorization_policy=authority,
        ),
        allowed_origins=frozenset({ORIGIN}),
    )
    client = TestClient(app, headers={"Origin": ORIGIN})
    return client, catalog


def _create_agent(client: TestClient, **overrides) -> dict:
    body = {
        "name": "demo-agent",
        "description": "A demo agent for contract tests.",
        "systemInstruction": "Answer using only approved knowledge.",
        "toolIds": ["knowledge.search", "knowledge.answer"],
    }
    body.update(overrides)
    response = client.post(f"{BASE}/agents", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# -- POST /agents (create) -------------------------------------------------


def test_create_agent_returns_authored_revision_detail() -> None:
    client, _catalog = _app()

    body = _create_agent(client)

    assert body["sourceKind"] == "authored"
    assert body["builtIn"] is False
    assert body["status"] == "enabled"
    assert body["isLatest"] is True
    assert body["agentId"] == body["assetId"]
    assert body["systemInstruction"] == "Answer using only approved knowledge."
    assert body["hasSkillUpdates"] is False
    assert sorted(body["toolAllowlist"]) == ["knowledge.answer", "knowledge.search"]


def test_create_agent_without_tool_ids_fills_in_the_required_tools() -> None:
    # The real UI never sends `toolIds` at all (built-in tools are always
    # enabled and shown read-only) — this reproduces that exact payload,
    # which previously 400'd with agent_tool_allowlist_invalid.
    client, _catalog = _app()

    response = client.post(
        f"{BASE}/agents",
        json={
            "name": "demo-agent-no-tools",
            "description": "A demo agent for contract tests.",
            "systemInstruction": "Answer using only approved knowledge.",
        },
    )

    assert response.status_code == 201, response.text
    assert sorted(response.json()["toolAllowlist"]) == [
        "knowledge.answer",
        "knowledge.search",
    ]


def test_create_agent_requires_write_permission() -> None:
    client, _catalog = _app(allowed=frozenset({"ai.agents.read"}))

    response = client.post(
        f"{BASE}/agents",
        json={
            "name": "demo-agent",
            "description": "demo",
            "systemInstruction": "demo",
            "toolIds": ["knowledge.search", "knowledge.answer"],
        },
    )

    assert response.status_code == 403


def test_create_agent_duplicate_name_is_a_409_conflict() -> None:
    client, _catalog = _app()
    _create_agent(client)

    conflict = _app_post_duplicate(client)
    assert conflict.status_code == 409
    assert conflict.json() == {
        "code": "agent_name_conflict",
        "detail": "an agent named 'demo-agent' already exists",
    }


def _app_post_duplicate(client: TestClient):
    return client.post(
        f"{BASE}/agents",
        json={
            "name": "demo-agent",
            "description": "A second agent with the same name.",
            "systemInstruction": "Different instruction body.",
            "toolIds": ["knowledge.search", "knowledge.answer"],
        },
    )


def test_create_agent_rejects_unknown_default_model() -> None:
    client, _catalog = _app(models=())

    response = client.post(
        f"{BASE}/agents",
        json={
            "name": "demo-agent",
            "description": "demo",
            "systemInstruction": "demo instruction",
            "toolIds": ["knowledge.search", "knowledge.answer"],
            "defaultModelAlias": "not-a-model",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "agent_model_not_selectable"


def test_create_agent_accepts_structured_default_model() -> None:
    models = (
        ModelDescriptor(
            alias="structured-model",
            display_name="Structured model",
            capabilities=frozenset({ModelCapability.STRUCTURED}),
        ),
    )
    client, _catalog = _app(models=models)

    body = _create_agent(client, defaultModelAlias="structured-model")

    assert body["defaultModelAlias"] == "structured-model"


def test_create_agent_rejects_disabled_skill_binding() -> None:
    client, catalog = _app()
    catalog.seed_skill(
        SkillRevision(
            revision_id="skill-rev-1",
            asset_id="skill-1",
            display_name="demo-skill",
            scope=VALIDATION_SCOPE,
            content_digest=_digest("skill-content"),
            instruction_template_digest=_digest("skill-instructions"),
            applicable_tasks=frozenset({"knowledge.answer"}),
            instruction_template="skill-instructions",
            status=AssetRevisionStatus.DISABLED,
            source_kind="authored",
        )
    )

    response = client.post(
        f"{BASE}/agents",
        json={
            "name": "demo-agent",
            "description": "demo",
            "systemInstruction": "demo instruction",
            "toolIds": ["knowledge.search", "knowledge.answer"],
            "skillRevisionIds": ["skill-rev-1"],
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "agent_skill_unavailable"


def _digest(value: str) -> str:
    from tap.modules.ai.domain.assets import text_digest

    return text_digest(value)


# -- POST /agents/{agentId}/revisions --------------------------------------


def test_revise_agent_requires_matching_base_revision() -> None:
    client, _catalog = _app()
    created = _create_agent(client)

    stale = client.post(
        f"{BASE}/agents/{created['agentId']}/revisions",
        json={
            "name": "demo-agent",
            "description": "v2",
            "systemInstruction": "v2 instruction",
            "toolIds": ["knowledge.search", "knowledge.answer"],
            "baseRevisionId": "not-the-real-base",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "revision_conflict"

    revised = client.post(
        f"{BASE}/agents/{created['agentId']}/revisions",
        json={
            "name": "demo-agent",
            "description": "v2",
            "systemInstruction": "v2 instruction",
            "toolIds": ["knowledge.search", "knowledge.answer"],
            "baseRevisionId": created["revisionId"],
        },
    )
    assert revised.status_code == 201
    assert revised.json()["revisionId"] != created["revisionId"]
    assert revised.json()["isLatest"] is True


def test_revise_built_in_agent_is_forbidden_but_create_copies_it() -> None:
    client, catalog = _app()
    created = _create_agent(client, name="built-in-agent")
    catalog._agents[created["assetId"]][-1] = replace(
        catalog._agents[created["assetId"]][-1], source_kind="built_in"
    )

    forbidden = client.post(
        f"{BASE}/agents/{created['agentId']}/revisions",
        json={
            "name": "built-in-agent",
            "description": "v2",
            "systemInstruction": "v2 instruction",
            "toolIds": ["knowledge.search", "knowledge.answer"],
            "baseRevisionId": created["revisionId"],
        },
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "agent_built_in_immutable"

    copy = _create_agent(client, name="built-in-agent-copy")
    assert copy["sourceKind"] == "authored"


# -- POST /agents/{agentId}/disable, /enable -------------------------------


def test_disable_then_enable_toggles_status() -> None:
    client, _catalog = _app()
    created = _create_agent(client)
    agent_id = created["agentId"]

    disabled = client.post(f"{BASE}/agents/{agent_id}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    enabled = client.post(f"{BASE}/agents/{agent_id}/enable")
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "enabled"


# -- GET /agents, /agents/{revisionId}, /agents/{agentId}/revisions -------


def test_list_agents_keeps_legacy_fields_and_adds_new_ones() -> None:
    client, _catalog = _app()
    created = _create_agent(client)

    listed = client.get(f"{BASE}/agents")
    assert listed.status_code == 200
    (item,) = listed.json()["items"]
    legacy_fields = ("revisionId", "assetId", "displayName", "contentDigest", "outputSchemaDigest")
    for legacy_field in legacy_fields:
        assert legacy_field in item
    for new_field in (
        "description",
        "sourceKind",
        "builtIn",
        "status",
        "isLatest",
        "agentId",
        "skillRevisionIds",
        "defaultModelAlias",
        "hasSkillUpdates",
    ):
        assert new_field in item
    assert "systemInstruction" not in item
    assert item["revisionId"] == created["revisionId"]
    assert item["isLatest"] is True


def test_get_agent_detail_includes_system_instruction() -> None:
    # spec §3.3: `GET /ai/agents/{revisionId}` returns the full revision
    # detail (same shape the create/revise responses return), unlike the
    # list route, which only ever returns the summary shape and omits it —
    # see test_agent_list_omits_system_instruction below.
    client, _catalog = _app()
    created = _create_agent(client)

    detail = client.get(f"{BASE}/agents/{created['revisionId']}")
    assert detail.status_code == 200
    assert detail.json()["systemInstruction"] == created["systemInstruction"]


def test_agent_list_omits_system_instruction() -> None:
    client, _catalog = _app()
    _create_agent(client)

    listed = client.get(f"{BASE}/agents")
    assert listed.status_code == 200
    assert "systemInstruction" not in listed.text


def test_revision_history_marks_latest_revision() -> None:
    client, _catalog = _app()
    created = _create_agent(client)
    client.post(
        f"{BASE}/agents/{created['agentId']}/revisions",
        json={
            "name": "demo-agent",
            "description": "v2",
            "systemInstruction": "v2 instruction",
            "toolIds": ["knowledge.search", "knowledge.answer"],
            "baseRevisionId": created["revisionId"],
        },
    )

    history = client.get(f"{BASE}/agents/{created['agentId']}/revisions")
    assert history.status_code == 200
    items = history.json()["items"]
    assert len(items) == 2
    assert items[0]["isLatest"] is False
    assert items[1]["isLatest"] is True


def test_revision_history_unknown_agent_is_not_found() -> None:
    client, _catalog = _app()

    response = client.get(f"{BASE}/agents/unknown-agent/revisions")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_has_skill_updates_becomes_true_after_skill_revision() -> None:
    client, catalog = _app()
    catalog.seed_skill(
        SkillRevision(
            revision_id="skill-rev-1",
            asset_id="skill-1",
            display_name="demo-skill",
            scope=VALIDATION_SCOPE,
            content_digest=_digest("skill-content"),
            instruction_template_digest=_digest("skill-instructions"),
            applicable_tasks=frozenset({"knowledge.answer"}),
            instruction_template="skill-instructions",
            source_kind="authored",
        )
    )
    created = _create_agent(client, skillRevisionIds=["skill-rev-1"])
    assert created["hasSkillUpdates"] is False

    catalog.seed_skill(
        SkillRevision(
            revision_id="skill-rev-2",
            asset_id="skill-1",
            display_name="demo-skill",
            scope=VALIDATION_SCOPE,
            content_digest=_digest("skill-content-v2"),
            instruction_template_digest=_digest("skill-instructions-v2"),
            applicable_tasks=frozenset({"knowledge.answer"}),
            instruction_template="skill-instructions-v2",
            source_kind="authored",
            adopted_from_revision_id="skill-rev-1",
        )
    )

    refreshed = client.get(f"{BASE}/agents/{created['revisionId']}")
    assert refreshed.json()["hasSkillUpdates"] is True
