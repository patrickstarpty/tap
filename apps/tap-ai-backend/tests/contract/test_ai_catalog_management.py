from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.assets import ApprovedAssetCatalog, validation_asset_seed
from tap.modules.ai.domain.assets import AssetRevisionStatus
from tests.contract.test_ai_asset_http import _SkillAuthority


def _client(actions: set[str]):
    seed = validation_asset_seed(VALIDATION_SCOPE)
    agent = replace(seed.agents[0], status=AssetRevisionStatus.DISABLED)
    skill = replace(seed.skills[0], status=AssetRevisionStatus.DISABLED)
    catalog = ApprovedAssetCatalog(agents=(agent,), skills=(skill,))
    catalog.scope = VALIDATION_SCOPE
    authority = _SkillAuthority(actions)
    client = TestClient(
        create_app(
            HttpServices(
                asset_catalog=catalog,
                scope=VALIDATION_SCOPE,
                scope_provider=authority,
                authorization_policy=authority,
            )
        )
    )
    return client, agent, skill


def test_management_lists_and_details_include_disabled_revisions_without_exposing_them_for_chat():
    client, agent, skill = _client({"ai.agents.read", "ai.skills.read"})
    base = "/api/v1/projects/tapper-demo/ai"
    for kind, revision, field, expected in (
        ("agents", agent, "systemInstruction", agent.system_instruction),
        ("skills", skill, "instructionTemplate", skill.instruction_template),
    ):
        selection = client.get(f"{base}/{kind}")
        assert selection.status_code == 200 and selection.json()["items"] == []
        listed = client.get(f"{base}/{kind}/revisions")
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["status"] == "disabled"
        assert listed.json()["items"][0][field] == expected
        detail = client.get(f"{base}/{kind}/revisions/{revision.revision_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json() == listed.json()["items"][0]
        assert client.get(f"{base}/{kind}/{revision.revision_id}").status_code == 404
        assert client.get(f"{base}/{kind}/revisions/unknown").status_code == 404
        assert client.get(f"/api/v1/projects/other/ai/{kind}/revisions").status_code == 403


def test_management_requires_corresponding_read_permission():
    client, agent, skill = _client(set())
    base = "/api/v1/projects/tapper-demo/ai"
    for kind, revision in (("agents", agent), ("skills", skill)):
        assert client.get(f"{base}/{kind}/revisions").status_code == 403
        assert client.get(f"{base}/{kind}/revisions/{revision.revision_id}").status_code == 403
