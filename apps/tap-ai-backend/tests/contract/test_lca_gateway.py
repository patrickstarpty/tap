import io
import json
import re
import time
from dataclasses import replace
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
    assert client.get("/api/lca/context").status_code == 403
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


def test_file_asset_gateway_bounds_upload_and_preserves_attachment_headers():
    received = []

    def backend(request):
        received.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"id": "asset"})
        return httpx.Response(
            200,
            content=b"captured",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": 'attachment; filename="result.txt"',
                "X-Content-Type-Options": "nosniff",
                "X-Content-SHA256": "a" * 64,
            },
        )

    authority = Authority()
    client = TestClient(
        create_app(
            HttpServices(
                scope=VALIDATION_SCOPE, scope_provider=authority, authorization_policy=authority
            ),
            allowed_origins=frozenset({"http://127.0.0.1:5174"}),
            lca_gateway=LcaGateway(
                "http://127.0.0.1:8001", "test-server-token", httpx.MockTransport(backend)
            ),
        )
    )
    path = "/api/projects/tapper-demo/lca/assets"
    headers = {"Origin": "http://127.0.0.1:5174", "Idempotency-Key": "selected-file"}
    response = client.post(
        path, json={"filename": "file.txt", "content_base64": "YQ=="}, headers=headers
    )
    assert response.status_code == 201
    assert received[0].headers["x-lca-actor"] == VALIDATION_SCOPE.actor_id
    assert received[0].headers["idempotency-key"] == "selected-file"
    assert client.post(path, content=b"x" * 350001, headers=headers).status_code == 413
    assert len(received) == 1
    result = client.get("/api/projects/tapper-demo/lca/runs/run/evidence/evidence")
    assert result.content == b"captured"
    assert result.headers["content-disposition"] == 'attachment; filename="result.txt"'
    assert result.headers["x-content-type-options"] == "nosniff"


def context_client(gateway, scope=VALIDATION_SCOPE, *, origin="http://127.0.0.1:8000"):
    class ContextAuthority(Authority):
        async def current(self, facts):
            return scope

    authority = ContextAuthority()
    return TestClient(
        create_app(
            HttpServices(scope=scope, scope_provider=authority, authorization_policy=authority),
            lca_gateway=gateway,
            lca_gateway_from_environment=False,
        ),
        base_url=origin,
    )


def test_embed_context_is_authoritative_stable_bounded_and_not_a_credential():
    gateway = LcaGateway("http://127.0.0.1:8001", "context-test-server-secret")
    client = context_client(gateway)
    first = client.get("/api/lca/context")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    context = first.json()
    assert context == {
        "available": True,
        "project_id": "tapper-demo",
        "actor_id": "tapper-local-user",
        "identity_mode": "validation",
        "gateway_instance_id": context["gateway_instance_id"],
        "context_binding": context["context_binding"],
    }
    assert re.fullmatch(r"[0-9a-f]{32}", context["gateway_instance_id"])
    assert re.fullmatch(r"[0-9a-f]{64}", context["context_binding"])
    assert client.get("/api/lca/context").json() == context
    # Frontends can share one gateway across their distinct loopback proxy hosts.
    assert (
        TestClient(client.app, base_url="http://localhost:5174").get("/api/lca/context").json()
        == context
    )
    assert "context-test-server-secret" not in first.text
    assert "8001" not in first.text


def test_separate_same_named_gateways_never_share_embed_recovery_context():
    first = context_client(LcaGateway("http://127.0.0.1:8001", "same-server-token"))
    second = context_client(LcaGateway("http://127.0.0.1:8001", "same-server-token"))
    original = first.get("/api/lca/context").json()
    other = second.get("/api/lca/context").json()
    assert original["gateway_instance_id"] != other["gateway_instance_id"]
    assert original["context_binding"] != other["context_binding"]


@pytest.mark.parametrize(
    "scope",
    [
        replace(VALIDATION_SCOPE, actor_id="another-actor"),
        replace(VALIDATION_SCOPE, project_id="another-project"),
        replace(VALIDATION_SCOPE, enterprise_id="another-enterprise"),
    ],
)
def test_embed_binding_changes_when_authoritative_scope_changes(scope):
    gateway = LcaGateway("http://127.0.0.1:8001", "same-server-token")
    client = context_client(gateway)
    original = client.get("/api/lca/context").json()
    client.app.state.http_services = context_client(gateway, scope).app.state.http_services
    changed = client.get("/api/lca/context").json()
    assert original["gateway_instance_id"] == changed["gateway_instance_id"]
    assert original["context_binding"] != changed["context_binding"]


@pytest.mark.parametrize("origin", ["http://192.0.2.1:8000", "https://tap.example"])
def test_embed_context_rejects_nonloopback_request_even_with_forwarded_host(origin):
    client = context_client(LcaGateway("http://127.0.0.1:8001", "same-server-token"), origin=origin)
    response = client.get("/api/lca/context", headers={"X-Forwarded-Host": "127.0.0.1:8000"})
    assert response.status_code == 403
    assert "context_binding" not in response.text


def test_embed_context_rejects_nonloopback_upstream_and_all_product_identities():
    from tap.modules.access.domain.context import IdentityMode

    client = context_client(LcaGateway("https://backend.example", "same-server-token"))
    assert client.get("/api/lca/context").status_code == 403
    client = context_client(
        LcaGateway("http://127.0.0.1:8001", "same-server-token"),
        replace(VALIDATION_SCOPE, identity_mode=IdentityMode.PRODUCT),
    )
    response = client.get("/api/lca/context")
    assert response.status_code == 403
    assert "context_binding" not in response.text


def test_unavailable_embed_context_has_no_binding_and_cannot_be_cached():
    response = context_client(None).get("/api/lca/context")
    assert response.status_code == 200
    assert response.json() == {"available": False, "project_id": "tapper-demo"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "headers,query",
    [({"X-Actor-Id": "forged"}, ""), ({}, "?project_id=forged")],
)
def test_embed_context_cannot_be_overridden_by_caller(headers, query):
    response = context_client(LcaGateway("http://127.0.0.1:8001", "same-server-token")).get(
        "/api/lca/context" + query, headers=headers
    )
    assert response.status_code == 403
    assert "context_binding" not in response.text
