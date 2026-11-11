import time
from datetime import UTC, datetime
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.http import create_browser_router


class Service:
    def create_action(self, principal, session_id, body, key, *, actor):
        return {"operation": body["type"], "actor_kind": actor.kind, "command_id": key}

    def list_actions(self, principal, session_id, *, after=0, limit=200):
        return {"actions": [], "next_after": after, "limit": limit}

    def stop_recording(self, principal, session_id, expected_epoch, key, *, actor):
        return {
            "session_id": session_id,
            "control_epoch": expected_epoch,
            "watermark": 4,
            "status": "pending",
            "controller_id": actor.controller_id,
        }

    def consume_input_for_dispatch(self, project_id, session_id, action_id):
        return {"value": "synthetic"}

    frames = None

    def request_observer(self, principal, session_id):
        return {"control_epoch": 0}

    def get_control(self, principal, session_id):
        return {"control_state": "agent_active", "control_epoch": 0}

    def request_control(
        self, principal, session_id, operation, expected_epoch, key, *, actor
    ):
        return {"controller_id": actor.controller_id, "operation": operation}


def client():
    principal = AccessPrincipal(
        project_id="p",
        principal_id="agent",
        principal_type="service",
        audience="tap-browser",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        actions=frozenset(),
        enabled=True,
    )
    app = FastAPI()
    app.include_router(
        create_browser_router(
            Service(),
            principal,
            "a" * 32,
            gateway_token="g" * 32,
            worker_token="w" * 32,
        )
    )
    return TestClient(app)


def test_human_control_requires_distinct_gateway_credential():
    c = client()
    path = "/api/projects/p/browser/sessions/s/control"
    body = {"operation": "takeover", "expected_epoch": 0}
    headers = {
        "Authorization": "Bearer " + "a" * 32,
        "Idempotency-Key": "k",
        "X-Tap-Controller": "c" * 32,
        "X-Tap-Controller-Expires": str(time.time() + 60),
    }
    assert c.post(path, headers=headers, json=body).status_code == 401
    headers["Authorization"] = "Bearer " + "g" * 32
    assert c.post(path, headers=headers, json=body).json()["controller_id"] == "c" * 32
    assert (
        c.post(
            path.replace("/projects/p/", "/projects/other/"), headers=headers, json=body
        ).status_code
        == 403
    )
    assert (
        c.post(path, headers=headers, json={**body, "actor": "forged"}).status_code
        == 422
    )


def test_control_read_uses_existing_agent_credential():
    response = client().get(
        "/api/projects/p/browser/sessions/s/control",
        headers={"Authorization": "Bearer " + "a" * 32},
    )
    assert response.status_code == 200
    assert response.json()["control_epoch"] == 0


def test_frame_read_is_authenticated_and_pending_until_worker_frame():
    c = client()
    path = "/api/projects/p/browser/sessions/s/frame"
    assert c.get(path).status_code == 401
    response = c.get(path, headers={"Authorization": "Bearer " + "a" * 32})
    assert response.status_code == 409


def test_ephemeral_input_is_worker_only_and_never_cacheable():
    c = client()
    path = "/api/projects/p/browser/sessions/s/actions/a/input"
    assert (
        c.post(path, headers={"Authorization": "Bearer " + "a" * 32}).status_code == 401
    )
    assert (
        c.post(path, headers={"Authorization": "Bearer " + "g" * 32}).status_code == 401
    )
    response = c.post(path, headers={"Authorization": "Bearer " + "w" * 32})
    assert response.status_code == 200
    assert response.json() == {"value": "synthetic"}
    assert response.headers["cache-control"] == "no-store"


def test_action_listing_is_authenticated_ordered_and_bounded():
    c = client()
    path = "/api/projects/p/browser/sessions/s/actions"
    assert c.get(path).status_code == 401
    headers = {"Authorization": "Bearer " + "a" * 32}
    response = c.get(path, headers=headers, params={"after": 4, "limit": 10})
    assert response.status_code == 200
    assert response.json() == {"actions": [], "next_after": 4, "limit": 10}
    assert c.get(path, headers=headers, params={"limit": 201}).status_code == 422
    assert c.get(path, headers=headers, params={"after": -1}).status_code == 422


def test_recording_stop_uses_trusted_gateway_and_strict_epoch():
    c = client()
    path = "/api/projects/p/browser/sessions/s/recording/stop"
    headers = {
        "Authorization": "Bearer " + "g" * 32,
        "Idempotency-Key": "stop",
        "X-Tap-Controller": "c" * 32,
        "X-Tap-Controller-Expires": str(time.time() + 60),
    }
    assert (
        c.post(
            path,
            headers={**headers, "Authorization": "Bearer " + "a" * 32},
            json={"expected_epoch": 1},
        ).status_code
        == 401
    )
    response = c.post(path, headers=headers, json={"expected_epoch": 1})
    assert response.status_code == 202
    assert response.json()["watermark"] == 4
    assert response.json()["controller_id"] == "c" * 32
    for body in (
        {"expected_epoch": True},
        {"expected_epoch": 1, "actor": "forged"},
        {},
    ):
        assert c.post(path, headers=headers, json=body).status_code == 422


def test_gateway_admits_controlled_native_select_and_explicit_navigation():
    c = client()
    headers = {
        "Authorization": "Bearer " + "g" * 32,
        "Idempotency-Key": "input",
        "X-Tap-Controller": "c" * 32,
        "X-Tap-Controller-Expires": str(time.time() + 60),
    }
    path = "/api/projects/p/browser/sessions/s/human-actions"
    for body in (
        {"type": "select", "value": "option-a", "expected_epoch": 1},
        {"type": "navigate", "url": "https://example.com/next", "expected_epoch": 1},
    ):
        response = c.post(path, headers=headers, json=body)
        assert response.status_code == 202, response.text
        assert response.json()["operation"] == body["type"]
        assert response.json()["actor_kind"] == "human"
