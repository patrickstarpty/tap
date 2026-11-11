import io
import json
import time
from urllib.error import HTTPError

import httpx
import pytest
from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.interfaces.http.routes.lca import LcaGateway
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision


class Authority:
    async def current(self, facts):
        return VALIDATION_SCOPE

    async def authorize(self, scope, action, resource):
        assert action in {"lca.read", "lca.write", "lca.execute", "lca.publish"}
        return AuthorizationDecision(True, "allowed")


def test_gateway_uses_verified_scope_and_never_client_credentials():
    received = []

    def backend(request):
        received.append(request)
        return httpx.Response(200, json=[])

    authority = Authority()
    app = create_app(
        HttpServices(
            scope=VALIDATION_SCOPE, scope_provider=authority, authorization_policy=authority
        ),
        lca_gateway=LcaGateway(
            "http://127.0.0.1:8001", "test-server-token", httpx.MockTransport(backend)
        ),
    )
    client = TestClient(app)
    response = client.get(
        "/api/projects/tapper-demo/lca/flows",
        headers={"Authorization": "Bearer attacker", "x-lca-actor": "attacker"},
    )
    assert response.status_code == 200
    assert received[0].headers["authorization"] == "Bearer test-server-token"
    assert received[0].headers["x-lca-actor"] == VALIDATION_SCOPE.actor_id
    assert client.get("/api/projects/other/lca/flows").status_code == 403
    assert len(received) == 1
    assert client.get("/api/projects/tapper-demo/lca/arbitrary-admin").status_code == 404
    assert len(received) == 1


def test_gateway_context_and_unconfigured_path_fail_closed():
    authority = Authority()
    app = create_app(
        HttpServices(
            scope=VALIDATION_SCOPE, scope_provider=authority, authorization_policy=authority
        )
    )
    client = TestClient(app)
    assert client.get("/api/lca/context").json() == {
        "project_id": VALIDATION_SCOPE.project_id,
        "available": False,
    }
    assert client.get("/api/projects/tapper-demo/lca/flows").status_code == 503


def test_gateway_mutations_enforce_origin_and_preserve_intent_key_without_retry():
    calls = []

    def backend(request):
        calls.append(request)
        raise httpx.ReadTimeout("lost response")

    authority = Authority()
    app = create_app(
        HttpServices(
            scope=VALIDATION_SCOPE, scope_provider=authority, authorization_policy=authority
        ),
        allowed_origins=frozenset({"http://127.0.0.1:5174"}),
        lca_gateway=LcaGateway(
            "http://127.0.0.1:8001", "test-server-token", httpx.MockTransport(backend)
        ),
    )
    client = TestClient(app)
    path = "/api/projects/tapper-demo/lca/flows/flow1/runs"
    assert (
        client.post(
            path, json={"expected_version": 1}, headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    assert calls == []
    response = client.post(
        path,
        json={"expected_version": 1},
        headers={"Origin": "http://127.0.0.1:5174", "Idempotency-Key": "same-run"},
    )
    assert response.status_code == 502
    assert len(calls) == 1
    assert calls[0].headers["idempotency-key"] == "same-run"
    assert "test-server-token" not in response.text


def test_local_debug_gateway_does_not_grant_browser_actions_to_product_readers():
    from dataclasses import replace

    from tap.modules.access.domain.context import IdentityMode

    scope = replace(VALIDATION_SCOPE, identity_mode=IdentityMode.PRODUCT)

    class ProductAuthority(Authority):
        async def current(self, facts):
            return scope

        async def authorize(self, scope, action, resource):
            return AuthorizationDecision(action == "knowledge.answer", "reader-only")

    authority = ProductAuthority()
    calls = []

    def backend(request):
        calls.append(request)
        return httpx.Response(200, json=[])

    client = TestClient(
        create_app(
            HttpServices(scope=scope, scope_provider=authority, authorization_policy=authority),
            lca_gateway=LcaGateway(
                "http://127.0.0.1:8001", "test-server-token", httpx.MockTransport(backend)
            ),
        )
    )
    assert client.get("/api/lca/context").status_code == 403
    assert client.get("/api/projects/tapper-demo/lca/flows").status_code == 403
    assert calls == []


def test_recording_gateway_requires_flow_ownership_and_actor_bound_capability():
    from tap.modules.workflows.adapters.browser_client import BrowserApiClient

    class Browser(BrowserApiClient):
        def __init__(self):
            super().__init__(
                "http://127.0.0.1:8001", "tapper-demo", "service-token-test", gateway_token="g" * 32
            )
            self.calls = []

        async def request(self, method, path, *args, **kwargs):
            self.calls.append((method, path))
            return {"image_base64": "fixture"}

    browser = Browser()
    authority = Authority()
    owned = [True]

    def backend(request):
        assert request.headers["x-lca-actor"] == VALIDATION_SCOPE.actor_id
        return httpx.Response(
            200 if owned[0] else 404,
            json={"control": {"control_state": "human_active", "control_epoch": 1}},
        )

    client = TestClient(
        create_app(
            HttpServices(
                scope=VALIDATION_SCOPE,
                scope_provider=authority,
                authorization_policy=authority,
                browser_client=browser,
            ),
            allowed_origins=frozenset({"http://127.0.0.1:5174"}),
            lca_gateway=LcaGateway(
                "http://127.0.0.1:8001", "test-server-token", httpx.MockTransport(backend)
            ),
        )
    )
    base = "/api/projects/tapper-demo/lca/flows/flow/recordings/session"
    assert client.get(base + "/frame").status_code == 403
    assert browser.calls == []
    grant = client.post(base + "/capability", headers={"Origin": "http://127.0.0.1:5174"}).json()
    headers = {"X-LCA-Capability": grant["capability"]}
    assert client.get(base + "/frame", headers=headers).status_code == 200
    owned[0] = False
    assert client.get(base + "/frame", headers=headers).status_code == 404
    assert len(browser.calls) == 1


@pytest.fixture
def recording_browser(monkeypatch):
    from tap.interfaces.http.browser_capabilities import BrowserCapabilities
    from tap.modules.workflows.adapters import browser_client

    browser = browser_client.BrowserApiClient(
        "http://127.0.0.1:8001", "tapper-demo", "service-token-test", gateway_token="g" * 32
    )
    state = {
        "status": 409,
        "detail": "live frame pending",
        "control": "human_active",
        "now": time.time(),
    }
    calls = []

    def reject(request, **kwargs):
        calls.append((request.method, request.full_url))
        raise HTTPError(
            request.full_url,
            state["status"],
            "rejected",
            {},
            io.BytesIO(json.dumps({"detail": state["detail"]}).encode()),
        )

    monkeypatch.setattr(browser_client, "urlopen", reject)
    authority = Authority()
    app = create_app(
        HttpServices(
            scope=VALIDATION_SCOPE,
            scope_provider=authority,
            authorization_policy=authority,
            browser_client=browser,
        ),
        allowed_origins=frozenset({"http://127.0.0.1:5174"}),
        lca_gateway=LcaGateway(
            "http://127.0.0.1:8001",
            "test-server-token",
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"control": {"control_state": state["control"], "control_epoch": 1}}
                )
            ),
        ),
    )
    app.state.lca_browser_capabilities = BrowserCapabilities(clock=lambda: state["now"])
    client = TestClient(app)
    base = "/api/projects/tapper-demo/lca/flows/flow/recordings/session"
    headers = {"Origin": "http://127.0.0.1:5174", "Idempotency-Key": "action-once"}
    grant = client.post(base + "/capability", headers=headers)
    assert grant.status_code == 200
    headers["X-LCA-Capability"] = grant.json()["capability"]
    return client, base, headers, state, calls


def test_recording_frame_pending_is_a_safe_exact_signal(recording_browser):
    client, base, headers, state, calls = recording_browser
    response = client.get(base + "/frame", headers=headers)
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "live_frame_pending"}}
    assert calls == [
        ("GET", "http://127.0.0.1:8001/api/projects/tapper-demo/browser/sessions/session/frame")
    ]


@pytest.mark.parametrize(
    "status, detail, expected_status",
    [
        (409, "live frame pending later", 409),
        (409, "live frame pending ", 409),
        (409, "Live frame pending", 409),
        (409, "browser session blocked: private backend reason", 409),
        (409, {"code": "live_frame_pending"}, 409),
        (403, "live frame pending", 403),
        (422, "live frame pending", 422),
        (500, "live frame pending", 502),
    ],
)
def test_recording_other_frame_errors_stay_generic(
    recording_browser, status, detail, expected_status
):
    client, base, headers, state, calls = recording_browser
    state.update(status=status, detail=detail)
    response = client.get(base + "/frame", headers=headers)
    assert response.status_code == expected_status
    assert response.json() == {
        "detail": "Recording command unavailable; reconcile the original request"
    }
    assert len(calls) == 1


def test_recording_action_pending_error_does_not_become_retry_signal(recording_browser):
    client, base, headers, state, calls = recording_browser
    response = client.post(
        base + "/actions",
        headers=headers,
        json={"expected_epoch": 1, "action": {"type": "click", "selector": "#safe"}},
    )
    assert response.status_code == 409
    assert response.json() == {
        "detail": "Recording command unavailable; reconcile the original request"
    }
    assert len(calls) == 1
    assert calls[0][0] == "POST"


def test_recording_pending_does_not_bypass_expired_capability(recording_browser):
    client, base, headers, state, calls = recording_browser
    state["now"] += 300
    response = client.get(base + "/frame", headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Recording capability unavailable"}
    assert calls == []


@pytest.mark.parametrize("control", ["blocked", "closed"])
def test_recording_unavailable_control_cannot_issue_capability(recording_browser, control):
    client, base, headers, state, calls = recording_browser
    state["control"] = control
    response = client.post(base + "/capability", headers=headers)
    assert response.status_code == 409
    assert response.json() == {"detail": "Recording control unavailable"}
    assert calls == []


def test_product_lca_permissions_are_action_specific():
    from dataclasses import replace

    from tap.modules.access.domain.context import IdentityMode

    scope = replace(VALIDATION_SCOPE, identity_mode=IdentityMode.PRODUCT)
    seen = []
    allowed = {"lca.read"}

    class ProductAuthority:
        async def current(self, facts):
            return scope

        async def authorize(self, actual, action, resource):
            assert actual == scope and resource.kind == "lca"
            seen.append(action)
            return AuthorizationDecision(action in allowed, "product-policy")

    authority = ProductAuthority()
    calls = []

    def backend(request):
        calls.append(request)
        return httpx.Response(200, json=[])

    client = TestClient(
        create_app(
            HttpServices(scope=scope, scope_provider=authority, authorization_policy=authority),
            allowed_origins=frozenset({"http://127.0.0.1:5174"}),
            lca_gateway=LcaGateway(
                "http://127.0.0.1:8001", "test-server-token", httpx.MockTransport(backend)
            ),
        )
    )
    assert client.get("/api/lca/context").status_code == 200
    assert client.get("/api/projects/tapper-demo/lca/flows").status_code == 200
    assert (
        client.post(
            "/api/projects/tapper-demo/lca/flows",
            json={},
            headers={"Origin": "http://127.0.0.1:5174"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/projects/tapper-demo/lca/flows/f/runs",
            json={},
            headers={"Origin": "http://127.0.0.1:5174"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/projects/tapper-demo/lca/flows/f/releases",
            json={},
            headers={"Origin": "http://127.0.0.1:5174"},
        ).status_code
        == 403
    )
    assert len(calls) == 1
    assert seen == ["lca.read", "lca.read", "lca.write", "lca.execute", "lca.publish"]
    allowed.add("lca.execute")
    assert (
        client.post(
            "/api/projects/tapper-demo/lca/flows/f/runs",
            json={},
            headers={"Origin": "http://127.0.0.1:5174"},
        ).status_code
        == 200
    )


@pytest.mark.parametrize("action", ["lca.read", "lca.write", "lca.execute", "lca.publish"])
def test_lca_actions_reuse_current_project_principal_policy(action):
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    from tap.modules.access.application.authorize import authorize_project_action
    from tap.modules.access.domain.authorization import ProjectPrincipal, ResourceRef

    now = datetime.now(UTC)
    principal = ProjectPrincipal(
        enterprise_id="e",
        project_id="p",
        actor_id="a",
        audience="tap-api",
        expires_at=now + timedelta(minutes=1),
        actions=frozenset({action}),
        enabled=True,
    )
    resource = ResourceRef(enterprise_id="e", project_id="p", kind="lca")

    def allowed(candidate, target=resource):
        return authorize_project_action(
            candidate, action, target, expected_audience="tap-api", now=now
        ).allowed

    assert allowed(principal)
    assert not allowed(replace(principal, enabled=False))
    assert not allowed(replace(principal, expires_at=now))
    assert not allowed(replace(principal, actions=frozenset({"knowledge.answer"})))
    assert not allowed(replace(principal, audience="other"))
    assert not allowed(principal, replace(resource, project_id="other"))


def test_aa_preview_is_scoped_read_only_and_uses_server_workflow_source(monkeypatch):
    from tap.modules.workflows.adapters.browser_client import BrowserApiClient
    from tap.modules.workflows.application.browser_artifacts import BrowserArtifactAdapter

    calls = []

    async def preview(self, scope, task_id):
        calls.append((scope, task_id))
        return {"complete": False, "definition": None, "issues": [{"reason": "unsupported"}]}

    monkeypatch.setattr(BrowserArtifactAdapter, "lca_draft", preview)
    authority = Authority()
    app = create_app(
        HttpServices(
            scope=VALIDATION_SCOPE,
            scope_provider=authority,
            authorization_policy=authority,
            workflow_store=object(),
            browser_client=BrowserApiClient(
                "http://127.0.0.1:39999", VALIDATION_SCOPE.project_id, "test-browser-token"
            ),
        ),
        lca_gateway=LcaGateway("http://127.0.0.1:8001", "test-server-token"),
    )
    client = TestClient(app)
    path = "/api/projects/tapper-demo/lca/agent-tasks/task1/preview"
    response = client.get(path)
    assert response.status_code == 200
    assert calls == [(VALIDATION_SCOPE, "task1")]
    assert response.headers["cache-control"] == "no-store"
    assert client.get(path.replace("tapper-demo", "other")).status_code == 403
    assert len(calls) == 1
