"""Standalone AA recovery uses runtime identity without depending on LCA grants."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.interfaces.http.routes.lca import LcaGateway
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.access.domain.context import IdentityMode
from tap.modules.workflows.adapters.browser_client import BrowserApiClient

PATH = "/api/v1/browser-context"


class Authority:
    def __init__(self, scope=VALIDATION_SCOPE, actions=frozenset({"knowledge.answer"})):
        self.scope = scope
        self.actions = actions
        self.seen = []

    async def current(self, facts):
        return self.scope

    async def authorize(self, scope, action, resource):
        self.seen.append(action)
        return AuthorizationDecision(action in self.actions, "selected-workflow-policy")


def client_for(authority=None, *, enabled=True, project="tapper-demo", gateway=None):
    authority = authority or Authority()
    return TestClient(
        create_app(
            HttpServices(
                scope=authority.scope,
                scope_provider=authority,
                authorization_policy=authority,
                browser_enabled=enabled,
                browser_client=BrowserApiClient(
                    "http://127.0.0.1:8001", project, "private-aa-server-token"
                ),
            ),
            lca_gateway=gateway,
            lca_gateway_from_environment=False,
        ),
        base_url="http://127.0.0.1:8000",
    )


def test_standalone_aa_context_needs_no_lca_configuration_or_lca_grant():
    authority = Authority()
    client = client_for(authority)
    response = client.get(PATH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    context = response.json()
    assert context["available"] is True
    assert context["project_id"] == "tapper-demo"
    assert context["actor_id"] == "tapper-local-user"
    assert context["identity_mode"] == "validation"
    assert len(context["gateway_instance_id"]) == 32
    assert len(context["context_binding"]) == 64
    assert "private-aa-server-token" not in response.text
    assert client.get(PATH).json() == context
    assert authority.seen == ["knowledge.answer", "knowledge.answer"]
    assert client.get("/api/lca/context").status_code == 403


def test_browser_and_lca_context_share_the_same_api_gateway_incarnation():
    authority = Authority(actions=frozenset({"knowledge.answer", "lca.read"}))
    gateway = LcaGateway("http://127.0.0.1:8001", "private-lca-server-token")
    client = client_for(authority, gateway=gateway)
    browser = client.get(PATH)
    assert browser.status_code == 200
    assert browser.json() == client.get("/api/lca/context").json()
    assert (
        TestClient(client.app, base_url="http://localhost:5174").get(PATH).json() == browser.json()
    )
    separate = client_for(authority, gateway=gateway).get(PATH).json()
    assert separate["gateway_instance_id"] != browser.json()["gateway_instance_id"]
    assert separate["context_binding"] != browser.json()["context_binding"]


@pytest.mark.parametrize("enabled,project", [(False, "tapper-demo"), (True, "other-project")])
def test_disabled_or_foreign_browser_runtime_exposes_no_recovery_binding(enabled, project):
    response = client_for(enabled=enabled, project=project).get(PATH)
    assert response.status_code == 200
    assert response.json() == {"available": False, "project_id": "tapper-demo"}
    assert response.headers["cache-control"] == "no-store"


def test_browser_context_rechecks_policy_and_current_scope_on_every_request():
    authority = Authority()
    client = client_for(authority)
    assert client.get(PATH).status_code == 200
    authority.actions = frozenset()
    response = client.get(PATH)
    assert response.status_code == 403
    assert "context_binding" not in response.text
    authority.actions = frozenset({"knowledge.answer"})
    authority.scope = replace(VALIDATION_SCOPE, actor_id="changed-actor")
    assert client.get(PATH).status_code == 403


@pytest.mark.parametrize(
    "authority,origin,headers",
    [
        (Authority(replace(VALIDATION_SCOPE, identity_mode=IdentityMode.PRODUCT)), None, {}),
        (None, "https://remote.example", {"X-Forwarded-Host": "127.0.0.1:8000"}),
        (None, None, {"X-Actor-Id": "spoofed"}),
    ],
)
def test_browser_context_rejects_nonvalidation_remote_or_caller_authority(
    authority, origin, headers
):
    client = client_for(authority)
    if origin:
        client = TestClient(client.app, base_url=origin)
    response = client.get(PATH, headers=headers)
    assert response.status_code == 403
    assert "context_binding" not in response.text
