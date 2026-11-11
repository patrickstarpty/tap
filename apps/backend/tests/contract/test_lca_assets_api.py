import base64
from datetime import UTC, datetime
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from tap_platform.browser_automation.objects import BrowserEvidenceSink
from tap_platform.lca.http import create_lca_router
from sqlalchemy import create_engine
from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.objects import BrowserObjectStore
from tap_platform.browser_automation.service import BrowserService
from tap_platform.browser_automation.store import (
    BrowserStore,
    metadata as browser_metadata,
)
from tap_platform.lca.service import LcaService
from tap_platform.lca.store import LcaStore, metadata
from test_lca_api import body


def actor(**changes):
    values = dict(
        project_id="project-a",
        principal_id="alice",
        principal_type="user",
        audience="tap-lca",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        enabled=True,
        actions=frozenset(
            {
                "lca.flows.create",
                "lca.flows.read",
                "lca.runs.create",
                "lca.runs.read",
                "lca.assets.create",
                "lca.assets.read",
                "lca.evidence.read",
            }
        ),
    )
    return AccessPrincipal(**{**values, **changes})


@pytest.fixture
def services(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}")
    metadata.create_all(engine)
    browser_metadata.create_all(engine)
    browser = BrowserService(
        BrowserStore(engine), BrowserObjectStore(tmp_path / "objects")
    )
    return LcaService(LcaStore(engine), browser), browser


@pytest.fixture
def client(services):
    app = FastAPI()

    def authorized(request, project):
        return actor(
            project_id="project-a",
            principal_id=request.headers.get("x-test-actor", "alice"),
            actions=actor().actions
            | {"lca.assets.create", "lca.assets.read", "lca.evidence.read"},
        )

    app.include_router(create_lca_router(services[0], authorized))
    return TestClient(app)


def test_selected_upload_api_requires_owned_project_and_bounded_bytes(client):
    path = "/api/projects/project-a/lca/assets"
    payload = {
        "filename": "sample.txt",
        "content_base64": base64.b64encode(b"sample").decode(),
    }
    response = client.post(path, json=payload, headers={"Idempotency-Key": "selected"})
    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset["filename"] == "sample.txt"
    assert set(asset) == {
        "id",
        "filename",
        "content_type",
        "sha256",
        "size_bytes",
        "created_at",
    }
    assert client.get(path).json() == [asset]
    assert client.get(path, headers={"x-test-actor": "bob"}).json() == []
    assert client.get("/api/projects/other/lca/assets").status_code == 403
    assert client.post(path, json=payload).status_code == 400
    assert (
        client.post(
            path,
            json={**payload, "path": "/etc/passwd"},
            headers={"Idempotency-Key": "path"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            path,
            json={
                **payload,
                "content_base64": base64.b64encode(b"x" * 256001).decode(),
            },
            headers={"Idempotency-Key": "large"},
        ).status_code
        == 422
    )


def test_download_is_attachment_and_bound_to_run_actor_project(client, services):
    service, browser = services
    who = actor(actions=actor().actions | {"lca.evidence.read"})
    flow = service.create_flow(who, "flow", "Downloads", body()["definition"])
    run = service.debug_run(who, flow["id"], 1, {}, "run")
    ev = BrowserEvidenceSink(browser.store, browser.objects).persist(
        "project-a",
        flow["id"],
        run["execution_id"],
        "download",
        b"captured bytes",
        5_000_000,
    )
    browser.store.update(
        "execution",
        "project-a",
        run["execution_id"],
        result={
            "evidence": [
                {
                    "kind": "download",
                    "sha256": ev.sha256,
                    "filename": "../../result\r\n.txt",
                }
            ]
        },
    )
    path = f"/api/projects/project-a/lca/runs/{run['id']}/evidence/{ev.object_ref}"
    response = client.get(path)
    assert response.status_code == 200
    assert response.content == b"captured bytes"
    assert (
        response.headers["content-disposition"] == 'attachment; filename="result.txt"'
    )
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(path, headers={"x-test-actor": "bob"}).status_code == 404
    assert client.get(path.replace("project-a", "other")).status_code == 403
    another = service.debug_run(who, flow["id"], 1, {}, "other-run")
    assert client.get(path.replace(run["id"], another["id"])).status_code == 403
