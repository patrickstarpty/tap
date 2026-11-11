"""AA export authority and immutable source bindings."""

from dataclasses import replace
from datetime import UTC, datetime
import pytest
from sqlalchemy import create_engine
from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.service import BrowserService, Forbidden, Invalid
from tap_platform.browser_automation.store import BrowserStore, metadata
from tap_platform.browser_automation.objects import BrowserObjectStore
from tap_platform.lca.agent_export import export_agent_draft


def fixture(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'db'}")
    metadata.create_all(engine)
    browser = BrowserService(
        BrowserStore(engine), BrowserObjectStore(tmp_path / "objects")
    )

    def put(kind, ident, **values):
        return browser.store.insert_idempotent(
            kind, "project", ident, ident, {"id": ident, **values}
        )

    put(
        "session",
        "session",
        task_id="task",
        status="completed",
        request={},
        cancel_requested=False,
    )
    put(
        "bundle",
        "bundle",
        task_id="task",
        sha256="b" * 64,
        script_ref="script",
        input_sha256="a" * 64,
        input_ref="input",
        allowed_origins=["https://example.com"],
    )
    evidence = [
        {"kind": "result", "sha256": "c" * 64, "object_ref": "result", "size_bytes": 5}
    ]
    put(
        "execution",
        "run",
        task_id="task",
        bundle_id="bundle",
        input_sha256="a" * 64,
        status="passed",
        request={"mode": "live", "session_id": "session"},
        cancel_requested=False,
        result={"value": "done"},
        steps=[],
        evidence=evidence,
    )
    put(
        "action",
        "a1",
        session_id="session",
        sequence=1,
        status="completed",
        request={"type": "navigate", "url": "https://example.com"},
        observation={"error": None},
        actor_kind="agent",
    )
    put(
        "action",
        "a2",
        session_id="session",
        sequence=2,
        status="completed",
        request={"type": "finish"},
        observation={"result": {"value": "done"}, "evidence": evidence},
        actor_kind="agent",
    )
    principal = AccessPrincipal(
        project_id="project",
        principal_id="service",
        principal_type="service",
        audience="tap-browser",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        actions=frozenset({"browser.executions.read", "browser.actions.read"}),
        enabled=True,
    )
    body = {
        "task_id": "task",
        "bundle_id": "bundle",
        "input_sha256": "a" * 64,
        "plan": [{"id": "open", "keyword": "Given", "title": "Open"}],
        "action_steps": {"a1": "open"},
    }
    return browser, principal, body


def test_export_uses_existing_rows_and_never_creates_or_runs(tmp_path):
    browser, principal, body = fixture(tmp_path)
    result = export_agent_draft(browser, principal, "run", body)
    assert result["complete"] and result["source"]["execution_id"] == "run"
    assert result["definition"]["steps"][0]["arguments"] == {
        "url": "https://example.com"
    }
    assert (
        browser.store.get("session", "project", "session")["next_action_sequence"] == 1
    )


@pytest.mark.parametrize(
    "field,value",
    [("task_id", "other"), ("bundle_id", "other"), ("input_sha256", "f" * 64)],
)
def test_mismatched_source_is_denied(tmp_path, field, value):
    browser, principal, body = fixture(tmp_path)
    body[field] = value
    with pytest.raises(Forbidden):
        export_agent_draft(browser, principal, "run", body)


def test_cross_project_and_insufficient_read_scope_are_denied(tmp_path):
    browser, principal, body = fixture(tmp_path)
    for actor in (
        replace(principal, project_id="other"),
        replace(principal, actions=frozenset({"browser.executions.read"})),
    ):
        with pytest.raises(Forbidden):
            export_agent_draft(browser, actor, "run", body)


def test_unbound_or_failed_completion_cannot_be_imported(tmp_path):
    browser, principal, body = fixture(tmp_path)
    browser.store.update("execution", "project", "run", status="failed")
    with pytest.raises(Invalid):
        export_agent_draft(browser, principal, "run", body)
    browser.store.update(
        "execution", "project", "run", status="passed", result={"changed": True}
    )
    with pytest.raises(Invalid):
        export_agent_draft(browser, principal, "run", body)
