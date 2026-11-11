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
                "browser.sessions.cancel",
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
    assert (
        service.create_execution(
            principal,
            {
                "task_id": "task-a",
                "bundle_id": bundle["bundle_id"],
                "input_sha256": bundle["input_sha256"],
            },
            "execution-key",
        )["status"]
        == "unknown"
    )
    assert (
        service.cancel_execution(principal, row["execution_id"])["status"] == "unknown"
    )


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
        async def start_inspector(
            self, task_id, allowed_origins, deadline, *, session_id
        ):
            return InspectorHandle(
                task_id, "browser", "network", "proxy", allowed_origins, session_id
            )

        async def observe(self, handle, action):
            image = self.sink.persist(
                "project-a",
                handle.task_id,
                handle.task_id,
                "screenshot",
                b"\x89PNG\r\n\x1a\nimage",
                1024,
                session_id=handle.session_id,
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
    screenshot = store.get(
        "evidence", "project-a", observed["observation"]["screenshot_ref"]
    )
    assert screenshot is not None
    assert screenshot["session_id"] == session["session_id"]
    assert screenshot["execution_id"] is None
    with pytest.raises(Forbidden):
        service.verify_inspector_evidence(
            "project-a", "task-a", "wrong-session", screenshot["id"]
        )


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


@pytest.mark.asyncio
async def test_cancel_at_execution_completion_preserves_finished_receipt(tmp_path):
    store, objects, service, principal, bundle = setup(tmp_path)
    row = service.create_execution(
        principal,
        {
            "task_id": "task-a",
            "bundle_id": bundle["bundle_id"],
            "input_sha256": bundle["input_sha256"],
        },
        "race-key",
    )

    class FinishCancel(Runtime):
        async def execute(self, *args, **kwargs):
            receipt = await super().execute(*args, **kwargs)
            service.cancel_execution(principal, row["execution_id"])
            return receipt

    runtime = FinishCancel(BrowserEvidenceSink(store, objects))
    runtime.release.set()
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    await worker.tick()
    await worker.drain()
    await worker.tick()
    result = service.get_execution(principal, row["execution_id"])
    assert result["status"] == "passed"
    assert result["result"] == {"passed": 1}
    assert len(result["evidence"]) == 3


@pytest.mark.asyncio
async def test_lost_owner_cannot_publish_after_evidence_verification(tmp_path):
    store, objects, service, principal, bundle = setup(tmp_path)
    row = service.create_execution(
        principal,
        {
            "task_id": "task-a",
            "bundle_id": bundle["bundle_id"],
            "input_sha256": bundle["input_sha256"],
        },
        "fence-key",
    )
    original = service.verify_evidence

    def expire_during_verify(*args, **kwargs):
        original(*args, **kwargs)
        store.update(
            "execution",
            "project-a",
            row["execution_id"],
            lease_until="2000-01-01T00:00:00+00:00",
        )
        store.expire_lost("project-a")

    service.verify_evidence = expire_during_verify
    runtime = Runtime(BrowserEvidenceSink(store, objects))
    runtime.release.set()
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    await worker.tick()
    await worker.drain()
    assert (
        store.get("execution", "project-a", row["execution_id"])["status"] == "unknown"
    )


@pytest.mark.asyncio
async def test_second_inspector_action_waits_for_first_observation(tmp_path):
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
    first = service.create_action(
        principal, session["session_id"], {"type": "snapshot"}, "first-key"
    )
    second = service.create_action(
        principal, session["session_id"], {"type": "wait"}, "second-key"
    )

    class BlockedRuntime(Runtime):
        def __init__(self, sink):
            super().__init__(sink)
            self.observed = 0
            self.observing = asyncio.Event()
            self.release_observation = asyncio.Event()

        async def start_inspector(
            self, task_id, allowed_origins, deadline, *, session_id
        ):
            return InspectorHandle(
                task_id, "browser", "network", "proxy", allowed_origins
            )

        async def observe(self, handle, action):
            self.observed += 1
            self.observing.set()
            if self.observed == 1:
                await self.release_observation.wait()
            return BrowserObservation(
                "internal",
                self.observed,
                "https://example.com",
                "Page",
                "snapshot",
                (),
                None,
                None,
            )

    runtime = BlockedRuntime(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    await worker.tick()
    await worker.drain()
    await worker.tick()
    await runtime.observing.wait()
    await worker.tick()
    assert runtime.observed == 1
    assert (
        service.get_action(principal, session["session_id"], second["action_id"])[
            "status"
        ]
        == "pending"
    )
    runtime.release_observation.set()
    await worker.drain()
    await worker.tick()
    await worker.drain()
    assert (
        service.get_action(principal, session["session_id"], first["action_id"])[
            "status"
        ]
        == "completed"
    )
    assert (
        service.get_action(principal, session["session_id"], second["action_id"])[
            "sequence"
        ]
        == 2
    )


@pytest.mark.asyncio
async def test_pending_session_cancel_settles_queued_action_without_runtime_start(
    tmp_path,
):
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
    assert (
        service.cancel_session(principal, session["session_id"])["status"] == "pending"
    )
    runtime = Runtime(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    await worker.tick()
    settled = service.get_session(principal, session["session_id"])
    assert settled["status"] == "canceled"
    assert settled["stop_receipt"]["stopped"] is True
    assert (
        service.get_action(principal, session["session_id"], action["action_id"])[
            "status"
        ]
        == "canceled"
    )


@pytest.mark.asyncio
async def test_active_session_cancel_requires_confirmed_stop_and_settles_actions(
    tmp_path,
):
    store, objects, service, principal, _ = setup(tmp_path)

    class InspectorRuntime(Runtime):
        def __init__(self, sink):
            super().__init__(sink)
            self.stops = 0

        async def start_inspector(
            self, task_id, allowed_origins, deadline, *, session_id
        ):
            return InspectorHandle(
                task_id, "browser", "network", "proxy", allowed_origins
            )

        async def cancel(self, handle):
            self.stops += 1
            return {"task_id": handle.task_id, "stopped": True, "status": "canceled"}

    runtime = InspectorRuntime(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    session = service.create_session(
        principal,
        {
            "task_id": "task-a",
            "allowed_origins": ["https://example.com"],
            "deadline_seconds": 60,
        },
        "session-key",
    )
    await worker.tick()
    await worker.drain()
    action = service.create_action(
        principal, session["session_id"], {"type": "snapshot"}, "action-key"
    )
    service.cancel_session(principal, session["session_id"])
    await worker.tick()
    settled = service.get_session(principal, session["session_id"])
    assert runtime.stops == 1
    assert settled["status"] == "canceled"
    assert settled["stop_receipt"]["stopped"] is True
    assert (
        service.get_action(principal, session["session_id"], action["action_id"])[
            "status"
        ]
        == "canceled"
    )


@pytest.mark.asyncio
async def test_worker_shutdown_stops_owned_inspector(tmp_path):
    store, objects, service, principal, _ = setup(tmp_path)

    class InspectorRuntime(Runtime):
        def __init__(self, sink):
            super().__init__(sink)
            self.stops = 0

        async def start_inspector(
            self, task_id, allowed_origins, deadline, *, session_id
        ):
            return InspectorHandle(
                task_id, "browser", "network", "proxy", allowed_origins
            )

        async def cancel(self, handle):
            self.stops += 1
            return {"task_id": handle.task_id, "stopped": True, "status": "canceled"}

    runtime = InspectorRuntime(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    session = service.create_session(
        principal,
        {
            "task_id": "task-a",
            "allowed_origins": ["https://example.com"],
            "deadline_seconds": 60,
        },
        "session-key",
    )
    await worker.tick()
    await worker.drain()
    await worker.shutdown()
    assert runtime.stops == 1
    assert service.get_session(principal, session["session_id"])["status"] == "canceled"


@pytest.mark.asyncio
async def test_canceling_worker_run_waits_for_session_teardown(tmp_path):
    store, objects, service, principal, _ = setup(tmp_path)

    class InspectorRuntime(Runtime):
        def __init__(self, sink):
            super().__init__(sink)
            self.stopped = asyncio.Event()

        async def start_inspector(
            self, task_id, allowed_origins, deadline, *, session_id
        ):
            return InspectorHandle(
                task_id, "browser", "network", "proxy", allowed_origins
            )

        async def cancel(self, handle):
            self.stopped.set()
            return {"task_id": handle.task_id, "stopped": True, "status": "canceled"}

    session = service.create_session(
        principal,
        {
            "task_id": "task-a",
            "allowed_origins": ["https://example.com"],
            "deadline_seconds": 60,
        },
        "session-key",
    )
    runtime = InspectorRuntime(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    running = asyncio.create_task(worker.run(0.01))
    try:
        for _ in range(100):
            if (
                service.get_session(principal, session["session_id"])["status"]
                == "active"
            ):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("session did not become active")
    finally:
        running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, 2)
    assert runtime.stopped.is_set()
    assert service.get_session(principal, session["session_id"])["status"] == "canceled"


@pytest.mark.asyncio
async def test_worker_run_cancel_during_stop_still_exits(tmp_path):
    store, objects, service, principal, _ = setup(tmp_path)

    class SlowStop(Runtime):
        def __init__(self, sink):
            super().__init__(sink)
            self.stopping = asyncio.Event()
            self.release_stop = asyncio.Event()
            self.stop_calls = 0

        async def start_inspector(
            self, task_id, allowed_origins, deadline, *, session_id
        ):
            return InspectorHandle(
                task_id, "browser", "network", "proxy", allowed_origins
            )

        async def cancel(self, handle):
            self.stop_calls += 1
            self.stopping.set()
            await self.release_stop.wait()
            return {"task_id": handle.task_id, "stopped": True, "status": "canceled"}

    session = service.create_session(
        principal,
        {
            "task_id": "task-a",
            "allowed_origins": ["https://example.com"],
            "deadline_seconds": 60,
        },
        "session-key",
    )
    runtime = SlowStop(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    running = asyncio.create_task(worker.run(0.01))
    for _ in range(100):
        if service.get_session(principal, session["session_id"])["status"] == "active":
            break
        await asyncio.sleep(0.01)
    else:
        running.cancel()
        pytest.fail("session did not become active")
    service.cancel_session(principal, session["session_id"])
    await asyncio.wait_for(runtime.stopping.wait(), 2)
    running.cancel()
    await asyncio.sleep(0)
    runtime.release_stop.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, 2)
    assert runtime.stop_calls >= 2
    assert service.get_session(principal, session["session_id"])["status"] == "unknown"


@pytest.mark.asyncio
async def test_cancel_during_session_start_waits_for_stop_receipt(tmp_path):
    store, objects, service, principal, _ = setup(tmp_path)

    class StartingRuntime(Runtime):
        def __init__(self, sink):
            super().__init__(sink)
            self.entered = asyncio.Event()
            self.release_start = asyncio.Event()
            self.stop_calls = 0

        async def start_inspector(
            self, task_id, allowed_origins, deadline, *, session_id
        ):
            self.entered.set()
            await self.release_start.wait()
            return InspectorHandle(
                task_id, "browser", "network", "proxy", allowed_origins
            )

        async def cancel_inspector(self, task_id, *, session_id):
            self.stop_calls += 1
            self.release_start.set()
            return {"task_id": task_id, "stopped": True, "status": "canceled"}

    runtime = StartingRuntime(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
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
    await worker.tick()
    await runtime.entered.wait()
    service.cancel_session(principal, session["session_id"])
    await worker.tick()
    await worker.drain()
    assert runtime.stop_calls == 1
    assert service.get_session(principal, session["session_id"])["status"] == "canceled"
    assert (
        service.get_action(principal, session["session_id"], action["action_id"])[
            "status"
        ]
        == "canceled"
    )


@pytest.mark.asyncio
async def test_unconfirmed_session_stop_is_unknown_and_late_action_cannot_publish(
    tmp_path,
):
    store, objects, service, principal, _ = setup(tmp_path)

    class UnconfirmedRuntime(Runtime):
        def __init__(self, sink):
            super().__init__(sink)
            self.observing = asyncio.Event()
            self.release_observation = asyncio.Event()

        async def start_inspector(
            self, task_id, allowed_origins, deadline, *, session_id
        ):
            return InspectorHandle(
                task_id, "browser", "network", "proxy", allowed_origins
            )

        async def observe(self, handle, action):
            self.observing.set()
            await self.release_observation.wait()
            return BrowserObservation(
                "internal", 1, None, None, "snapshot", (), None, None
            )

        async def cancel(self, handle):
            return {"task_id": handle.task_id, "stopped": False, "status": "unknown"}

    runtime = UnconfirmedRuntime(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    session = service.create_session(
        principal,
        {
            "task_id": "task-a",
            "allowed_origins": ["https://example.com"],
            "deadline_seconds": 60,
        },
        "session-key",
    )
    await worker.tick()
    await worker.drain()
    action = service.create_action(
        principal, session["session_id"], {"type": "snapshot"}, "action-key"
    )
    await worker.tick()
    await runtime.observing.wait()
    service.cancel_session(principal, session["session_id"])
    await worker.tick()
    assert service.get_session(principal, session["session_id"])["status"] == "unknown"
    assert (
        service.get_action(principal, session["session_id"], action["action_id"])[
            "status"
        ]
        == "unknown"
    )
    runtime.release_observation.set()
    await worker.drain()
    assert (
        service.get_action(principal, session["session_id"], action["action_id"])[
            "status"
        ]
        == "unknown"
    )


@pytest.mark.asyncio
async def test_lost_session_owner_cannot_publish_active(tmp_path):
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

    class ExpiringRuntime(Runtime):
        async def start_inspector(
            self, task_id, allowed_origins, deadline, *, session_id
        ):
            store.update(
                "session",
                "project-a",
                session["session_id"],
                lease_until="2000-01-01T00:00:00+00:00",
            )
            store.expire_lost("project-a")
            return InspectorHandle(
                task_id, "browser", "network", "proxy", allowed_origins
            )

        async def cancel(self, handle):
            return {"task_id": handle.task_id, "stopped": True, "status": "canceled"}

    runtime = ExpiringRuntime(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    await worker.tick()
    await worker.drain()
    assert service.get_session(principal, session["session_id"])["status"] == "unknown"


@pytest.mark.asyncio
async def test_wrong_session_screenshot_cannot_publish_observation(tmp_path):
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
    ref, sha, size = objects.persist(
        b"\x89PNG\r\n\x1a\nimage", kind="screenshot", max_bytes=1024
    )
    wrong = store.append_evidence(
        dict(
            project_id="project-a",
            task_id="task-a",
            session_id="other-session",
            execution_id=None,
            kind="screenshot",
            sha256=sha,
            object_ref=ref,
            size_bytes=size,
        )
    )

    class WrongScreenshot(Runtime):
        async def start_inspector(
            self, task_id, allowed_origins, deadline, *, session_id
        ):
            return InspectorHandle(
                task_id, "browser", "network", "proxy", allowed_origins
            )

        async def observe(self, handle, action):
            return BrowserObservation(
                "internal", 1, None, None, "snapshot", (), wrong["id"], None
            )

    runtime = WrongScreenshot(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    await worker.tick()
    await worker.drain()
    await worker.tick()
    await worker.drain()
    assert (
        service.get_action(principal, session["session_id"], action["action_id"])[
            "status"
        ]
        == "unknown"
    )


@pytest.mark.asyncio
async def test_cancel_claimed_unstarted_same_task_session_keeps_original_inspector(
    tmp_path,
):
    store, objects, service, principal, _ = setup(tmp_path)

    class SingleTaskRuntime(Runtime):
        def __init__(self, sink):
            super().__init__(sink)
            self.handles = {}
            self.stopped = []

        async def start_inspector(
            self, task_id, allowed_origins, deadline, *, session_id
        ):
            if task_id in self.handles:
                raise ValueError("inspector already active")
            handle = InspectorHandle(
                task_id, "browser-a", "network", "proxy", allowed_origins
            )
            self.handles[task_id] = handle
            return handle

        async def cancel(self, handle):
            self.stopped.append(handle)
            self.handles.pop(handle.task_id, None)
            return {"task_id": handle.task_id, "stopped": True, "status": "canceled"}

        async def cancel_inspector(self, task_id, *, session_id):
            handle = self.handles.get(task_id)
            if handle is not None:
                return await self.cancel(handle)
            return {"task_id": task_id, "stopped": False, "status": "unknown"}

    runtime = SingleTaskRuntime(BrowserEvidenceSink(store, objects))
    worker = BrowserWorker(service, runtime, "project-a", "owner-a")
    body = {
        "task_id": "task-a",
        "allowed_origins": ["https://example.com"],
        "deadline_seconds": 60,
    }
    first = service.create_session(principal, body, "session-first")
    await worker.tick()
    await worker.drain()
    original = worker._handles[first["session_id"]]
    second = service.create_session(principal, body, "session-second")
    await worker.tick()  # Claimed; its start task has not run yet.
    service.cancel_session(principal, second["session_id"])
    await worker.tick()
    await worker.drain()
    assert service.get_session(principal, first["session_id"])["status"] == "active"
    assert service.get_session(principal, second["session_id"])["status"] == "canceled"
    assert original in runtime.handles.values()
    assert original not in runtime.stopped


def test_late_inspector_evidence_keeps_originating_session(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    store, objects, service, principal, _ = setup(tmp_path)
    body = {
        "task_id": "task-a",
        "allowed_origins": ["https://example.com"],
        "deadline_seconds": 60,
    }
    first = service.create_session(principal, body, "session-first")
    second = service.create_session(principal, body, "session-second")
    entered = threading.Event()
    release = threading.Event()

    class PausedSink(BrowserEvidenceSink):
        def persist(self, *args, **kwargs):
            entered.set()
            assert release.wait(2)
            return super().persist(*args, **kwargs)

    sink = PausedSink(store, objects)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            sink.persist,
            "project-a",
            "task-a",
            "task-a",
            "screenshot",
            b"\x89PNG\r\n\x1a\nold-session",
            1024,
            session_id=first["session_id"],
        )
        assert entered.wait(2)
        service.cancel_session(principal, first["session_id"])
        store.update("session", "project-a", second["session_id"], status="active")
        release.set()
        evidence = pending.result()
    row = store.get("evidence", "project-a", evidence.object_ref)
    assert row is not None
    assert row["session_id"] == first["session_id"]
    assert row["session_id"] != second["session_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "write_started,expected",
    [(False, "failed"), (True, "unknown"), (None, "unknown"), ("exception", "unknown")],
)
async def test_legacy_write_failure_requires_explicit_no_write_proof(
    tmp_path, write_started, expected, caplog
):
    caplog.set_level("WARNING")
    store, objects, service, principal, _ = setup(tmp_path)

    class InspectorRuntime(Runtime):
        async def start_inspector(
            self, task_id, allowed_origins, deadline, *, session_id
        ):
            return InspectorHandle(
                task_id, "browser", "network", "proxy", allowed_origins, session_id
            )

        async def observe(self, handle, action):
            if write_started == "exception":
                raise TypeError("secret-test-password must never enter logs")
            return BrowserObservation(
                handle.session_id,
                1,
                "https://example.com",
                "Page",
                "current snapshot",
                (),
                None,
                "selector failed",
                write_started=write_started,
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
        principal,
        session["session_id"],
        {
            "type": "fill",
            "selector": "[",
            "value": "synthetic",
        },
        "action-key",
    )
    worker = BrowserWorker(
        service,
        InspectorRuntime(BrowserEvidenceSink(store, objects)),
        "project-a",
        "owner-a",
    )
    await worker.tick()
    await worker.drain()
    await worker.tick()
    await worker.drain()
    receipt = service.get_action(principal, session["session_id"], action["action_id"])
    assert receipt["status"] == expected
    if write_started is False:
        assert receipt["observation"]["bounded_snapshot"] == "current snapshot"
    if write_started == "exception":
        assert "phase=runtime_observe type=TypeError" in caplog.text
        assert "secret-test-password" not in caplog.text
