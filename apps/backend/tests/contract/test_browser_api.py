"""Project-bound browser HTTP contract."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.objects import BrowserObjectStore
from tap_platform.browser_automation.service import BrowserService
from tap_platform.browser_automation.store import BrowserStore, metadata
from tap_platform.browser_automation.http import create_browser_router
from fastapi import FastAPI


def test_browser_http_rejects_cross_project_and_conflicting_keys(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'browser.db'}")
    metadata.create_all(engine)
    principal = AccessPrincipal(
        project_id="project-a",
        principal_id="service",
        principal_type="service",
        audience="tap-browser",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        actions=frozenset(
            {
                "browser.sessions.create",
                "browser.sessions.read",
                "browser.actions.create",
                "browser.actions.read",
                "browser.bundles.create",
                "browser.bundles.read",
                "browser.executions.create",
                "browser.executions.read",
                "browser.executions.cancel",
                "browser.evidence.read",
            }
        ),
        enabled=True,
    )
    app = FastAPI()
    app.include_router(
        create_browser_router(
            BrowserService(
                BrowserStore(engine), BrowserObjectStore(tmp_path / "objects")
            ),
            principal,
            "a" * 32,
        )
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {'a' * 32}", "Idempotency-Key": "same-key"}
    body = {
        "task_id": "task-a",
        "allowed_origins": ["https://example.com"],
        "deadline_seconds": 60,
    }
    first = client.post(
        "/api/projects/project-a/browser/sessions", headers=headers, json=body
    )
    again = client.post(
        "/api/projects/project-a/browser/sessions", headers=headers, json=body
    )
    conflict = client.post(
        "/api/projects/project-a/browser/sessions",
        headers=headers,
        json={**body, "deadline_seconds": 61},
    )
    cross = client.get(
        f"/api/projects/project-b/browser/sessions/{first.json()['session_id']}",
        headers=headers,
    )
    assert first.status_code == 202
    assert again.json() == first.json()
    assert conflict.status_code == 409
    assert cross.status_code == 403
