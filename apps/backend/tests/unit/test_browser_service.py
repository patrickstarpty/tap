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


def test_frozen_bundle_replay_keeps_input_and_script(tmp_path):
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
