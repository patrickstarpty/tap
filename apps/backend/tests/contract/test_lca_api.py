from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.objects import BrowserObjectStore
from tap_platform.browser_automation.service import BrowserService
from tap_platform.browser_automation.store import (
    BrowserStore,
    metadata as browser_metadata,
)
from tap_platform.lca.http import create_lca_router
from tap_platform.lca.service import LcaService
from tap_platform.lca.store import LcaStore, metadata


def setup(tmp_path, configured=True):
    engine = create_engine(f"sqlite:///{tmp_path / 'http.db'}")
    metadata.create_all(engine)
    browser_metadata.create_all(engine)
    service = LcaService(
        LcaStore(engine),
        BrowserService(BrowserStore(engine), BrowserObjectStore(tmp_path / "objects")),
    )

    def authorize(request, project):
        return AccessPrincipal(
            project_id="p",
            principal_id=request.headers.get("x-test-actor", "alice"),
            principal_type="user",
            audience="tap-lca",
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            enabled=True,
            actions=frozenset(
                {
                    "lca.flows.create",
                    "lca.flows.read",
                    "lca.drafts.save",
                    "lca.runs.create",
                    "lca.runs.read",
                    "lca.runs.cancel",
                    "lca.evidence.read",
                }
            ),
        )

    app = FastAPI()
    app.include_router(
        create_lca_router(
            service if configured else None, authorize if configured else None
        )
    )
    return TestClient(app)


def body():
    return {
        "title": "Owned flow",
        "definition": {
            "schema_version": "lca-debug-v1",
            "allowed_origins": ["https://example.com"],
            "targets": [],
            "parameters": [],
            "steps": [
                {
                    "id": "open",
                    "operation": "navigate",
                    "arguments": {"url": "https://example.com"},
                    "timeout_ms": 1000,
                }
            ],
        },
    }


def test_flow_save_and_explicit_run_http_contract(tmp_path):
    client = setup(tmp_path)
    created = client.post(
        "/api/projects/p/lca/flows", json=body(), headers={"Idempotency-Key": "create"}
    )
    assert created.status_code == 201
    flow = created.json()
    assert flow["definition"]["steps"][0]["id"] == "open"
    assert "canonical_bytes" not in flow
    assert client.get("/api/projects/p/lca/flows").json()[0]["id"] == flow["id"]
    assert client.get(f"/api/projects/p/lca/flows/{flow['id']}/runs").json() == []
    payload = {"expected_version": flow["version"], "parameters": {}}
    url = f"/api/projects/p/lca/flows/{flow['id']}/runs"
    first = client.post(url, json=payload, headers={"Idempotency-Key": "run"})
    assert first.status_code == 202
    assert first.json()["execution_id"]
    assert "execution_key" not in first.json()
    assert "execution_request" not in first.json()
    assert (
        client.post(url, json=payload, headers={"Idempotency-Key": "run"}).json()
        == first.json()
    )
    assert (
        client.post(
            url,
            json={**payload, "expected_version": 99},
            headers={"Idempotency-Key": "run"},
        ).status_code
        == 409
    )
    assert (
        client.get(
            f"/api/projects/p/lca/flows/{flow['id']}", headers={"x-test-actor": "bob"}
        ).status_code
        == 404
    )
    assert client.get("/api/projects/other/lca/flows").status_code == 403


def test_unconfigured_router_and_invalid_requests_fail_closed(tmp_path):
    assert setup(tmp_path, False).get("/api/projects/p/lca/flows").status_code == 503
    client = setup(tmp_path)
    assert client.post("/api/projects/p/lca/flows", json=body()).status_code == 400
    assert (
        client.post(
            "/api/projects/p/lca/flows",
            json={**body(), "actor": "admin"},
            headers={"Idempotency-Key": "c"},
        ).status_code
        == 422
    )


def test_cancel_and_evidence_routes_enforce_owner(tmp_path):
    client = setup(tmp_path)
    flow = client.post(
        "/api/projects/p/lca/flows", json=body(), headers={"Idempotency-Key": "create"}
    ).json()
    run = client.post(
        f"/api/projects/p/lca/flows/{flow['id']}/runs",
        json={"expected_version": 1},
        headers={"Idempotency-Key": "run"},
    ).json()
    assert (
        client.post(f"/api/projects/p/lca/runs/{run['id']}/cancel").status_code == 202
    )
    assert (
        client.post(
            f"/api/projects/p/lca/runs/{run['id']}/cancel",
            headers={"x-test-actor": "bob"},
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/projects/p/lca/runs/{run['id']}/evidence/not-owned"
        ).status_code
        == 403
    )


def test_delegated_authorizer_requires_server_credential_and_fixed_project():
    from datetime import datetime, UTC
    import pytest
    from starlette.requests import Request
    from tap_platform.lca.http import DelegatedLcaAuthorizer

    authorizer = DelegatedLcaAuthorizer("synthetic-token-for-test", "p")

    def request(token, actor="alice"):
        return Request(
            {
                "type": "http",
                "headers": [
                    (b"authorization", f"Bearer {token}".encode()),
                    (b"x-lca-actor", actor.encode()),
                ],
            }
        )

    principal = authorizer(request("synthetic-token-for-test"), "p")
    assert principal.principal_id == "alice"
    assert principal.audience == "tap-lca"
    assert principal.expires_at > datetime.now(UTC)
    with pytest.raises(PermissionError):
        authorizer(request("bad"), "p")
    with pytest.raises(PermissionError):
        authorizer(request("synthetic-token-for-test"), "other")


def test_platform_app_exposes_unconfigured_lca_without_identity_fallback(monkeypatch):
    from tap_platform.app import create_app

    monkeypatch.delenv("TAP_LCA_GATEWAY_TOKEN", raising=False)
    client = TestClient(create_app())
    assert client.get("/api/projects/p/lca/flows").status_code == 503


def test_platform_app_wires_configured_lca_and_persists_across_app_restart(
    tmp_path, monkeypatch
):
    from sqlalchemy import create_engine
    from tap_platform.app import create_app
    from tap_platform.browser_automation.service import BrowserService
    from tap_platform.browser_automation.store import BrowserStore, metadata as bm
    from tap_platform.browser_automation.objects import BrowserObjectStore
    from tap_platform.lca.store import metadata as lm
    from tap_platform.access import AccessPrincipal

    engine = create_engine(f"sqlite:///{tmp_path / 'platform.db'}")
    bm.create_all(engine)
    lm.create_all(engine)
    browser = BrowserService(
        BrowserStore(engine), BrowserObjectStore(tmp_path / "objects")
    )
    principal = AccessPrincipal(
        project_id="p",
        principal_id="platform-service",
        principal_type="service",
        audience="tap-browser",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        enabled=True,
        actions=frozenset({"browser.executions.read"}),
    )
    monkeypatch.setenv("TAP_LCA_GATEWAY_TOKEN", "server-delegation-token")
    monkeypatch.setenv("TAP_BROWSER_GATEWAY_TOKEN", "g" * 32)

    def app():
        return create_app(
            browser_service=browser,
            browser_principal=principal,
            browser_token="browser-service-token",
        )

    headers = {
        "Authorization": "Bearer server-delegation-token",
        "X-LCA-Actor": "alice",
        "Idempotency-Key": "platform-create",
    }
    first = TestClient(app())
    assert (
        first.post(
            "/api/projects/p/browser/sessions/missing/control", json={}
        ).status_code
        == 401
    )
    created = first.post("/api/projects/p/lca/flows", json=body(), headers=headers)
    assert created.status_code == 201
    second = TestClient(app())
    assert (
        second.get("/api/projects/p/lca/flows", headers=headers).json()[0]["id"]
        == created.json()["id"]
    )
    assert (
        second.get(
            "/api/projects/p/lca/flows", headers={**headers, "X-LCA-Actor": "bob"}
        ).json()
        == []
    )
    assert second.get("/api/projects/p/lca/flows").status_code == 403


def test_video_evidence_returns_webm_with_verified_digest(tmp_path, monkeypatch):
    import hashlib

    data = b"\x1a\x45\xdf\xa3verified-video"
    digest = hashlib.sha256(data).hexdigest()
    calls = []

    def read_evidence(self, principal, run_id, evidence_id):
        calls.append((principal.principal_id, run_id, evidence_id))
        return {"kind": "video", "sha256": digest}, data

    monkeypatch.setattr(LcaService, "read_evidence", read_evidence)
    response = setup(tmp_path).get("/api/projects/p/lca/runs/run/evidence/video")
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/webm"
    assert response.headers["x-content-sha256"] == digest
    assert response.headers["cache-control"] == "no-store"
    assert response.content == data
    assert calls == [("alice", "run", "video")]
