from dataclasses import replace

import pytest
from sqlalchemy import update

from test_lca_service import actor, definition, services  # noqa: F401, F811
from tap_platform.browser_automation.store import sessions
from tap_platform.lca.recorder import LcaRecorder
from tap_platform.lca.store import LcaNotFound


def recording_actor():
    user = actor()
    return replace(
        user,
        actions=user.actions
        | {"lca.recordings.create", "lca.recordings.read", "lca.recordings.cancel"},
    )


def test_session_creation_is_owned_idempotent_declared_and_not_execution(services):  # noqa: F811
    svc, browser = services
    user = recording_actor()
    flow = svc.create_flow(user, "flow", "Recorded flow", definition())
    recorder = LcaRecorder(svc)
    first = recorder.start(user, flow["id"], 1, "record-one")
    again = recorder.start(user, flow["id"], 1, "record-one")
    assert first["session_id"] == again["session_id"]
    row = browser.store.get("session", user.project_id, first["session_id"])
    assert row["request"]["network_mode"] == "declared_origins"
    assert row["task_id"] == flow["id"]
    assert len(recorder.list(user, flow["id"])) == 1
    with pytest.raises(LcaNotFound):
        recorder.get(replace(user, principal_id="bob"), flow["id"], first["session_id"])


def test_other_flow_session_cannot_be_adopted(services):  # noqa: F811
    svc, _ = services
    user = recording_actor()
    first = svc.create_flow(user, "one", "One", definition())
    second = svc.create_flow(user, "two", "Two", definition())
    recorder = LcaRecorder(svc)
    session = recorder.start(user, first["id"], 1, "record")
    with pytest.raises(LcaNotFound):
        recorder.get(user, second["id"], session["session_id"])


def test_empty_stopped_recording_is_readable_without_dispatch(services):  # noqa: F811
    svc, browser = services
    user = recording_actor()
    flow = svc.create_flow(user, "flow", "Recorded flow", definition())
    recorder = LcaRecorder(svc)
    recorded = recorder.start(user, flow["id"], 1, "record")
    with browser.store.engine.begin() as c:
        c.execute(
            update(sessions)
            .where(sessions.c.id == recorded["session_id"])
            .values(
                recording_stopped=True,
                recording_watermark=0,
                recording_start_sequence=1,
                recording_epoch=1,
            )
        )
    state = recorder.get(user, flow["id"], recorded["session_id"])
    assert state["status"] == "draining"
    assert state["projection"]["ready"] is True
    assert state["projection"]["definition"] is None


def test_lca_app_reuses_injected_browser_input_vault(services, monkeypatch):  # noqa: F811
    from tap_platform import app as app_module

    svc, browser = services
    captured = []
    monkeypatch.setenv("TAP_LCA_GATEWAY_TOKEN", "synthetic-test-token")
    monkeypatch.setattr(
        app_module,
        "browser_service_from_environment",
        lambda: pytest.fail("must not construct second BrowserService"),
    )
    original = app_module.LcaService

    def record(store, shared):
        captured.append(shared)
        return original(store, shared)

    monkeypatch.setattr(app_module, "LcaService", record)
    app_module.create_app(
        browser_service=browser,
        browser_principal=svc._browser_principal(recording_actor()),
        browser_token="browser-test-token",
    )
    assert captured == [browser]


def test_cancel_is_terminal_only_after_actual_runtime_ack(services):  # noqa: F811
    svc, browser = services
    user = recording_actor()
    user = replace(user, actions=user.actions | {"lca.recordings.cancel"})
    flow = svc.create_flow(user, "flow", "Recorded flow", definition())
    recorder = LcaRecorder(svc)
    recorded = recorder.start(user, flow["id"], 1, "record")
    sid = recorded["session_id"]
    with browser.store.engine.begin() as c:
        c.execute(
            update(sessions)
            .where(sessions.c.id == sid)
            .values(
                status="active",
                control_state="blocked",
                controller_kind="human",
                recording_start_sequence=1,
                recording_epoch=1,
            )
        )
    result = recorder.cancel(user, flow["id"], sid)
    assert result["status"] == "unknown"
    assert result["cancel_requested"] is True
    with browser.store.engine.begin() as c:
        c.execute(
            update(sessions).where(sessions.c.id == sid).values(status="canceled")
        )
    settled = recorder.get(user, flow["id"], sid)
    assert settled["status"] == "canceled"
    assert settled["projection"]["ready"] is True


def test_new_recording_waits_for_confirmed_cleanup(services):  # noqa: F811
    from tap_platform.lca.store import LcaConflict

    svc, browser = services
    user = recording_actor()
    flow = svc.create_flow(user, "flow", "Recorded flow", definition())
    recorder = LcaRecorder(svc)
    first = recorder.start(user, flow["id"], 1, "first")
    with pytest.raises(LcaConflict, match="teardown"):
        recorder.start(user, flow["id"], 1, "second")
    with browser.store.engine.begin() as c:
        c.execute(
            update(sessions)
            .where(sessions.c.id == first["session_id"])
            .values(
                status="completed",
                stop_receipt={"stopped": True, "reason": "recording_drained"},
            )
        )
    second = recorder.start(user, flow["id"], 1, "second")
    assert second["session_id"] != first["session_id"]
