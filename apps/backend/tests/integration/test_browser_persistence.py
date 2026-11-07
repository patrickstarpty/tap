"""Durable worker ownership and evidence publication against a real SQL ledger."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.contracts import (
    BrowserExecution,
    BrowserObservation,
)
from tap_platform.browser_automation.docker import InspectorHandle
from tap_platform.browser_automation.objects import (
    BrowserEvidenceSink,
    BrowserObjectStore,
)
from tap_platform.browser_automation.service import BrowserService, Forbidden
from tap_platform.browser_automation.store import BrowserStore, metadata
from tap_platform.browser_automation.worker import BrowserWorker


class Runtime:
    def __init__(self, sink):
        self.sink = sink
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.canceled = 0
        self.received_limits = None

    async def execute(self, bundle, inputs, limits, *, execution_id):
        self.calls += 1
        self.received_limits = limits
        self.started.set()
        await self.release.wait()
        task = inputs["task_id"]
        refs = []
        for kind, data in (
            ("screenshot", b"\x89PNG\r\n\x1a\nimage"),
            ("trace", b"PK\x03\x04zip"),
            ("result", b'{"passed":1}'),
        ):
            refs.append(
                self.sink.persist(
                    bundle.project_id, task, execution_id, kind, data, 1_000_000
                )
            )
        return BrowserExecution(
            execution_id,
            bundle.project_id,
            task,
            bundle.bundle_id,
            bundle.input_sha256,
            "passed",
            datetime.now(UTC).isoformat(),
            datetime.now(UTC).isoformat(),
            {"passed": 1},
            (),
            tuple(refs),
            None,
        )

    async def cancel_execution(self, execution_id, task_id):
        self.canceled += 1
        self.release.set()
        return {"execution_id": execution_id, "stopped": True, "status": "canceled"}


def setup(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'browser.db'}",
        connect_args={"check_same_thread": False},
    )
    metadata.create_all(engine)
    store = BrowserStore(engine)
    objects = BrowserObjectStore(tmp_path / "objects")
    service = BrowserService(store, objects)
    principal = AccessPrincipal(
        project_id="project-a",
        principal_id="worker-test",
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
                "browser.executions.create",
                "browser.executions.read",
                "browser.executions.cancel",
            }
        ),
        enabled=True,
    )
    inputs = {"x": "值"}
    script = "import { test } from '@playwright/test'; test('flow', async () => {});"
    bundle = service.create_bundle(
        principal,
        {
            "task_id": "task-a",
            "script": script,
            "sha256": hashlib.sha256(script.encode()).hexdigest(),
            "input_data": inputs,
            "input_sha256": hashlib.sha256(
                json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "allowed_origins": ["https://example.com"],
        },
        "bundle-key",
    )
    return store, objects, service, principal, bundle


@pytest.mark.asyncio
async def test_pending_cancel_never_starts_side_effect(tmp_path):
    store, objects, service, principal, bundle = setup(tmp_path)
    row = service.create_execution(
        principal,
        {
            "task_id": "task-a",
            "bundle_id": bundle["bundle_id"],
            "input_sha256": bundle["input_sha256"],
        },
        "execution-key",
    )
    service.cancel_execution(principal, row["execution_id"])
    runtime = Runtime(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    await worker.tick()
    assert runtime.calls == 0
    assert service.get_execution(principal, row["execution_id"])["status"] == "canceled"


@pytest.mark.asyncio
async def test_active_cancel_waits_for_stop_receipt_and_old_evidence_is_immutable(
    tmp_path,
):
    store, objects, service, principal, bundle = setup(tmp_path)
    row = service.create_execution(
        principal,
        {
            "task_id": "task-a",
            "bundle_id": bundle["bundle_id"],
            "input_sha256": bundle["input_sha256"],
        },
        "execution-key",
    )
    runtime = Runtime(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    await worker.tick()
    await runtime.started.wait()
    service.cancel_execution(principal, row["execution_id"])
    await worker.tick()
    assert runtime.canceled == 1
    assert service.get_execution(principal, row["execution_id"])["status"] == "canceled"
    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_success_requires_readable_complete_evidence(tmp_path):
    store, objects, service, principal, bundle = setup(tmp_path)
    row = service.create_execution(
        principal,
        {
            "task_id": "task-a",
            "bundle_id": bundle["bundle_id"],
            "input_sha256": bundle["input_sha256"],
        },
        "execution-key",
    )
    runtime = Runtime(BrowserEvidenceSink(store, objects))
    runtime.release.set()
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    await worker.tick()
    await worker.drain()
    final = service.get_execution(principal, row["execution_id"])
    assert final["status"] == "passed"
    assert len(final["evidence"]) == 3
    with pytest.raises(Forbidden):
        service.verify_evidence(
            "project-a",
            final["evidence"],
            task_id="task-other",
            execution_id=row["execution_id"],
        )
    evidence_id = final["evidence"][0]["object_ref"]
    stored = store.get("evidence", "project-a", evidence_id)
    assert stored is not None
    (objects.root / stored["object_ref"]).unlink()
    assert service.get_execution(principal, row["execution_id"])["status"] == "unknown"


@pytest.mark.asyncio
async def test_expired_owner_becomes_unknown_without_reexecution(tmp_path):
    store, objects, service, principal, bundle = setup(tmp_path)
    row = service.create_execution(
        principal,
        {
            "task_id": "task-a",
            "bundle_id": bundle["bundle_id"],
            "input_sha256": bundle["input_sha256"],
        },
        "execution-key",
    )
    store.update(
        "execution",
        "project-a",
        row["execution_id"],
        status="running",
        owner="lost-owner",
        lease_until="2000-01-01T00:00:00+00:00",
    )
    runtime = Runtime(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "new-owner")
    await worker.tick()
    assert runtime.calls == 0
    assert service.get_execution(principal, row["execution_id"])["status"] == "unknown"


@pytest.mark.asyncio
async def test_worker_uses_frozen_execution_limits(tmp_path):
    store, objects, service, principal, bundle = setup(tmp_path)
    limits = {
        "deadline_seconds": 7,
        "memory_mb": 256,
        "cpus": 0.5,
        "pids": 64,
        "max_output_bytes": 100_000,
    }
    service.create_execution(
        principal,
        {
            "task_id": "task-a",
            "bundle_id": bundle["bundle_id"],
            "input_sha256": bundle["input_sha256"],
            "limits": limits,
        },
        "execution-key",
    )
    runtime = Runtime(BrowserEvidenceSink(store, objects))
    runtime.release.set()
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    await worker.tick()
    await worker.drain()
    assert runtime.received_limits.deadline_seconds == 7
    assert runtime.received_limits.max_output_bytes == 100_000


@pytest.mark.asyncio
async def test_worker_owns_session_handle_and_publishes_observation(tmp_path):
    store, objects, service, principal, _ = setup(tmp_path)

    class InspectorRuntime(Runtime):
        async def start_inspector(self, task_id, allowed_origins, deadline):
            return InspectorHandle(
                task_id, "browser", "network", "proxy", allowed_origins
            )

        async def observe(self, handle, action):
            image = self.sink.persist(
                "project-a",
                handle.task_id,
                handle.task_id,
                "screenshot",
                b"\x89PNG\r\n\x1a\nimage",
                1024,
            )
            return BrowserObservation(
                "session-a",
                1,
                "https://example.com",
                "Page",
                "snapshot",
                (),
                image.object_ref,
                None,
            )

    session = service.create_session(
        principal,
        {
            "task_id": "task-a",
            "allowed_origins": ["https://example.com"],
            "deadline_seconds": 60,
        },
        "session-key",
    )
    action = service.create_action(
        principal, session["session_id"], {"type": "snapshot"}, "action-key"
    )
    runtime = InspectorRuntime(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    await worker.tick()
    await worker.drain()
    assert service.get_session(principal, session["session_id"])["status"] == "active"
    await worker.tick()
    await worker.drain()
    observed = service.get_action(principal, session["session_id"], action["action_id"])
    assert observed["status"] == "completed"
    assert observed["observation"]["session_id"] == session["session_id"]
    assert observed["observation"]["bounded_snapshot"] == "snapshot"
    assert observed["observation"]["screenshot_ref"] is not None


@pytest.mark.asyncio
async def test_lost_inspector_does_not_replay_queued_action(tmp_path):
    store, objects, service, principal, _ = setup(tmp_path)
    session = service.create_session(
        principal,
        {
            "task_id": "task-a",
            "allowed_origins": ["https://example.com"],
            "deadline_seconds": 60,
        },
        "session-key",
    )
    action = service.create_action(
        principal, session["session_id"], {"type": "snapshot"}, "action-key"
    )
    store.update(
        "session",
        "project-a",
        session["session_id"],
        status="active",
        owner="lost-owner",
        lease_until="2000-01-01T00:00:00+00:00",
    )
    runtime = Runtime(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "new-owner")
    await worker.tick()
    assert service.get_session(principal, session["session_id"])["status"] == "unknown"
    assert (
        service.get_action(principal, session["session_id"], action["action_id"])[
            "status"
        ]
        == "unknown"
    )
