"""Browser authority, replay and object invariants."""

import hashlib
import json

import pytest
from sqlalchemy import create_engine

from tap_platform.browser_automation.store import BrowserStore, metadata
from tap_platform.browser_automation.objects import BrowserObjectStore
from tap_platform.browser_automation.service import BrowserService, Conflict, Invalid
from tap_platform.access import AccessPrincipal
from datetime import UTC, datetime


def principal(project_id):
    return AccessPrincipal(
        project_id=project_id,
        principal_id="test",
        principal_type="service",
        audience="tap-browser",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        actions=frozenset(
            {
                "browser.sessions.create",
                "browser.actions.create",
                "browser.bundles.create",
                "browser.executions.create",
            }
        ),
        enabled=True,
    )


def service(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'browser.db'}")
    metadata.create_all(engine)
    return BrowserService(
        BrowserStore(engine), BrowserObjectStore(tmp_path / "objects")
    )


def test_same_key_same_body_is_idempotent_and_different_body_conflicts(tmp_path):
    browser = service(tmp_path)
    body = {
        "task_id": "task-a",
        "allowed_origins": ["https://example.com"],
        "deadline_seconds": 60,
    }
    first = browser.create_session(principal("project-a"), body, "key-a")
    assert browser.create_session(principal("project-a"), dict(body), "key-a") == first
    with pytest.raises(Conflict):
        browser.create_session(
            principal("project-a"), {**body, "deadline_seconds": 61}, "key-a"
        )
    assert (
        browser.create_session(principal("project-b"), body, "key-a")["session_id"]
        != first["session_id"]
    )


@pytest.mark.parametrize("source_status", ["passed", "failed", "canceled", "unknown"])
def test_frozen_bundle_replay_keeps_input_and_script(tmp_path, source_status):
    browser = service(tmp_path)
    inputs = {"北京": "值", "a": 1}
    digest = hashlib.sha256(
        json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    script = "import { test } from '@playwright/test'; test('flow', async () => {});"
    bundle = browser.create_bundle(
        principal("project-a"),
        {
            "task_id": "task-a",
            "script": script,
            "sha256": hashlib.sha256(script.encode()).hexdigest(),
            "input_data": inputs,
            "input_sha256": digest,
            "allowed_origins": ["https://example.com"],
        },
        "bundle-key",
    )
    first = browser.create_execution(
        principal("project-a"),
        {"task_id": "task-a", "bundle_id": bundle["bundle_id"], "input_sha256": digest},
        "execution-key",
    )
    browser.store.update(
        "execution", "project-a", first["execution_id"], status=source_status
    )
    rerun = browser.create_execution(
        principal("project-a"),
        {"task_id": "task-b", "rerun_of": first["execution_id"]},
        "rerun-key",
    )
    assert rerun["execution_id"] != first["execution_id"]
    assert rerun["bundle_id"] == first["bundle_id"]
    assert rerun["input_sha256"] == digest
    with pytest.raises(Invalid):
        browser.create_execution(
            principal("project-a"),
            {
                "task_id": "task-c",
                "bundle_id": bundle["bundle_id"],
                "input_sha256": "0" * 64,
            },
            "bad-key",
        )
    with pytest.raises(Invalid):
        browser.create_bundle(
            principal("project-a"),
            {
                "task_id": "task-a",
                "script": script,
                "sha256": "0" * 64,
                "input_data": inputs,
                "input_sha256": digest,
                "allowed_origins": ["https://example.com"],
            },
            "bad-bundle-key",
        )


def test_conflicting_bundle_key_does_not_write_new_task_files(tmp_path):
    browser = service(tmp_path)
    actor = principal("project-a")
    inputs = {"a": 1}
    input_sha = hashlib.sha256(
        json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    first_script = "test('first', async () => {});"
    body = {
        "task_id": "task-a",
        "script": first_script,
        "sha256": hashlib.sha256(first_script.encode()).hexdigest(),
        "input_data": inputs,
        "input_sha256": input_sha,
        "allowed_origins": ["https://example.com"],
    }
    browser.create_bundle(actor, body, "one-key")
    before = sorted(p.name for p in (tmp_path / "objects").rglob("*") if p.is_file())
    changed_script = "test('changed', async () => {});"
    with pytest.raises(Conflict):
        browser.create_bundle(
            actor,
            {
                **body,
                "script": changed_script,
                "sha256": hashlib.sha256(changed_script.encode()).hexdigest(),
            },
            "one-key",
        )
    after = sorted(p.name for p in (tmp_path / "objects").rglob("*") if p.is_file())
    assert after == before


def test_distinct_concurrent_actions_get_unique_ordered_sequences(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    browser = service(tmp_path)
    actor = principal("project-a")
    session = browser.create_session(
        actor,
        {
            "task_id": "task-a",
            "allowed_origins": ["https://example.com"],
            "deadline_seconds": 60,
        },
        "session-key",
    )

    def create(i):
        return browser.create_action(
            actor,
            session["session_id"],
            {"type": "snapshot", "marker": i},
            f"action-{i}",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, (1, 2)))
    assert sorted(item["sequence"] for item in results) == [1, 2]
    assert (
        browser.create_action(
            actor, session["session_id"], {"type": "snapshot", "marker": 1}, "action-1"
        )["sequence"]
        == results[0]["sequence"]
    )


def test_action_idempotency_route_cannot_be_overwritten_by_body(tmp_path):
    browser = service(tmp_path)
    actor = principal("project-a")
    body = {
        "task_id": "task-a",
        "allowed_origins": ["https://example.com"],
        "deadline_seconds": 60,
    }
    first_session = browser.create_session(actor, body, "session-one")["session_id"]
    second_session = browser.create_session(actor, body, "session-two")["session_id"]
    action = {"type": "snapshot", "session_id": first_session}
    first = browser.create_action(actor, first_session, action, "same-action-key")
    with pytest.raises(Conflict, match="idempotency conflict"):
        browser.create_action(actor, second_session, action, "same-action-key")
    assert first["session_id"] == first_session


def test_public_web_policy_is_persisted_for_session_and_frozen_replay(tmp_path):
    browser = service(tmp_path)
    body = {
        "task_id": "task-a",
        "allowed_origins": ["https://example.com"],
        "network_mode": "public_web",
    }
    session = browser.create_session(principal("project-a"), body, "session-public")
    saved = browser.store.get("session", "project-a", session["session_id"])
    assert saved["request"]["network_mode"] == "public_web"
    script = "// frozen"
    bundle = browser.create_bundle(
        principal("project-a"),
        {
            **body,
            "script": script,
            "sha256": hashlib.sha256(script.encode()).hexdigest(),
            "input_data": {},
            "input_sha256": hashlib.sha256(b"{}").hexdigest(),
        },
        "bundle-public",
    )
    frozen, _ = browser.frozen_bundle("project-a", bundle["bundle_id"])
    assert frozen.network_mode == "public_web"
    assert bundle["network_mode"] == "public_web"
    with pytest.raises(Invalid):
        browser.create_session(
            principal("project-a"), {**body, "network_mode": "unrestricted"}, "bad-mode"
        )


def test_live_receipt_uses_finished_session_and_never_queues_an_execution(tmp_path):
    from tap_platform.browser_automation.objects import BrowserEvidenceSink
    from tap_platform.browser_automation.store import actions, sessions
    from sqlalchemy import insert, update
    from dataclasses import asdict

    browser = service(tmp_path)
    actor = principal("project-a")
    session = browser.create_session(
        actor,
        {
            "task_id": "task-a",
            "allowed_origins": ["https://example.com"],
            "deadline_seconds": 60,
        },
        "live-session",
    )
    sid = session["session_id"]
    inputs = {"goal": "read quote"}
    sha = hashlib.sha256(
        json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    script = "import {test} from '@playwright/test'; test('task',()=>{});"
    bundle = browser.create_bundle(
        actor,
        {
            "task_id": "task-a",
            "script": script,
            "sha256": hashlib.sha256(script.encode()).hexdigest(),
            "input_data": inputs,
            "input_sha256": sha,
            "allowed_origins": ["https://example.com"],
        },
        "live-bundle",
    )
    sink = BrowserEvidenceSink(browser.store, browser.objects)
    result = {"outputs": [{"quote": "10.62"}]}
    refs = [
        asdict(
            sink.persist(
                "project-a", "task-a", "task-a", kind, data, 1000, session_id=sid
            )
        )
        for kind, data in [
            ("screenshot", b"\x89PNG\r\n\x1a\nimage"),
            ("trace", b"PK\x03\x04trace"),
            ("result", json.dumps(result).encode()),
        ]
    ]
    with browser.store.engine.begin() as connection:
        connection.execute(
            update(sessions).where(sessions.c.id == sid).values(status="active")
        )
        connection.execute(
            insert(actions).values(
                id="finish-action",
                project_id="project-a",
                session_id=sid,
                sequence=1,
                status="completed",
                request={"type": "finish"},
                observation={"error": None, "result": result, "evidence": refs},
                created_at=datetime.now(UTC).isoformat(),
            )
        )
    body = {
        "task_id": "task-a",
        "bundle_id": bundle["bundle_id"],
        "input_sha256": sha,
        "live_session_id": sid,
        "finish_action_id": "finish-action",
        "steps": [{"title": "Then read quote", "status": "passed", "duration": 1}],
    }
    receipt = browser.create_execution(actor, body, "live-receipt")
    assert receipt["status"] == "passed"
    assert receipt["result"] == result
    assert {r["kind"] for r in receipt["evidence"]} == {"screenshot", "trace", "result"}
    assert browser.store.claim("execution", "project-a", "worker", 60) is None
    assert browser.create_execution(actor, body, "live-receipt") == receipt
    with pytest.raises(PermissionError):
        browser.create_execution(actor, {**body, "task_id": "other"}, "foreign-live")


@pytest.mark.parametrize("source_status", ["pending", "running", "invalid-state"])
def test_rerun_rejects_nonterminal_source_without_creating_execution(
    tmp_path, source_status
):
    browser = service(tmp_path)
    actor = principal("project-a")
    script = "// frozen"
    input_sha = hashlib.sha256(b"{}").hexdigest()
    bundle = browser.create_bundle(
        actor,
        {
            "task_id": "task-a",
            "script": script,
            "sha256": hashlib.sha256(script.encode()).hexdigest(),
            "input_data": {},
            "input_sha256": input_sha,
            "allowed_origins": ["https://example.com"],
        },
        "bundle-key",
    )
    first = browser.create_execution(
        actor,
        {
            "task_id": "task-a",
            "bundle_id": bundle["bundle_id"],
            "input_sha256": input_sha,
            "limits": {"deadline_seconds": 30},
        },
        "first-key",
    )
    browser.store.update(
        "execution", "project-a", first["execution_id"], status=source_status
    )
    body = {"task_id": "task-b", "rerun_of": first["execution_id"]}
    with pytest.raises(Conflict):
        browser.create_execution(actor, body, "rerun-key")
    from sqlalchemy import func, select
    from tap_platform.browser_automation.store import executions, idempotency

    with browser.store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(executions)) == 1
        assert (
            connection.scalar(
                select(func.count())
                .select_from(idempotency)
                .where(idempotency.c.key == "rerun-key")
            )
            == 0
        )
    browser.store.update(
        "execution", "project-a", first["execution_id"], status="failed"
    )
    rerun = browser.create_execution(actor, body, "rerun-key")
    assert rerun["execution_id"] != first["execution_id"]
    saved = browser.store.get("execution", "project-a", rerun["execution_id"])
    assert saved["request"]["limits"]["deadline_seconds"] == 30
    assert browser.create_execution(actor, body, "rerun-key") == rerun


def test_recording_cleanup_is_trusted_only_and_part_of_identity(tmp_path):
    browser = service(tmp_path)
    user = principal("p")
    body = {
        "task_id": "flow",
        "allowed_origins": ["https://example.com"],
        "control_version": "browser-control-v1",
    }
    with pytest.raises(Invalid, match="trusted"):
        browser.create_session(user, {**body, "close_on_recording_stop": True}, "bad")
    first = browser.create_session(user, body, "key", close_on_recording_stop=True)
    assert (
        browser.store.get("session", "p", first["session_id"])["request"][
            "close_on_recording_stop"
        ]
        is True
    )
    with pytest.raises(Conflict):
        browser.create_session(user, body, "key")
