"""Durable controller fencing with the real SQLite ledger."""

from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy import create_engine
from tap_platform.browser_automation.store import BrowserStore, metadata


def ledger(tmp_path):
    store = BrowserStore(create_engine(f"sqlite:///{tmp_path / 'control.db'}"))
    metadata.create_all(store.engine)
    row = store.insert_idempotent(
        "session",
        "p",
        "s",
        "digest",
        dict(
            task_id="task",
            status="active",
            request={},
            observation=None,
            owner="worker",
            lease_until=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            control_version="browser-control-v1",
            controller_id="agent",
            deadline_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        ),
    )
    return store, row["id"]


def action(store, session, key="a", epoch=0):
    return store.insert_action_idempotent(
        "p",
        session,
        key,
        key,
        {"type": "click"},
        actor_kind="agent",
        controller_id="agent",
        expected_epoch=epoch,
    )


def takeover(store, session):
    return store.request_control("p", session, "takeover", 0, "take", "human", "panel")


def test_takeover_fences_admission_and_rejects_queued_dispatch(tmp_path):
    store, session = ledger(tmp_path)
    queued = action(store, session)
    receipt = takeover(store, session)
    assert receipt["control_state"] == "takeover_pending"
    assert takeover(store, session) == receipt
    with pytest.raises(ValueError, match="control"):
        action(store, session, "late")
    assert store.reserve_dispatch("p", queued["id"], "worker") is None
    assert store.get("action", "p", queued["id"])["status"] == "rejected"


def test_outstanding_reservation_drains_once_before_human_active(tmp_path):
    store, session = ledger(tmp_path)
    action(store, session)
    claimed = store.claim("action", "p", "worker", 60)
    reserved = store.reserve_dispatch("p", claimed["id"], "worker")
    assert reserved["status"] == "dispatching"
    assert store.reserve_dispatch("p", claimed["id"], "worker") is None
    takeover(store, session)
    assert (
        store.complete_control(
            "p",
            session,
            "worker",
            expected_epoch=store.get_control("p", session)["control_epoch"],
            write_channel_revoked=True,
        )["control_state"]
        == "takeover_pending"
    )
    assert store.publish_action(
        "p", reserved, "worker", status="completed", observation={"sequence": 1}
    )
    assert not store.publish_action("p", reserved, "worker", status="completed")
    receipt = store.complete_control(
        "p",
        session,
        "worker",
        expected_epoch=store.get_control("p", session)["control_epoch"],
        write_channel_revoked=True,
    )
    assert receipt["control_state"] == "human_active"
    assert receipt["control_epoch"] == 1
    assert takeover(store, session)["control_state"] == "takeover_pending"
    with pytest.raises(ValueError, match="control"):
        action(store, session, "old")


def test_unknown_blocks_handoff(tmp_path):
    store, session = ledger(tmp_path)
    action(store, session)
    claimed = store.claim("action", "p", "worker", 60)
    reserved = store.reserve_dispatch("p", claimed["id"], "worker")
    takeover(store, session)
    store.publish_action("p", reserved, "worker", status="unknown")
    assert (
        store.complete_control(
            "p",
            session,
            "worker",
            expected_epoch=store.get_control("p", session)["control_epoch"],
            write_channel_revoked=True,
        )["control_state"]
        == "blocked"
    )


def test_handback_requires_revocation_and_fresh_observation(tmp_path):
    store, session = ledger(tmp_path)
    takeover(store, session)
    assert (
        store.complete_control(
            "p",
            session,
            "worker",
            expected_epoch=store.get_control("p", session)["control_epoch"],
            write_channel_revoked=False,
        )["control_state"]
        == "takeover_pending"
    )
    store.complete_control(
        "p",
        session,
        "worker",
        expected_epoch=store.get_control("p", session)["control_epoch"],
        write_channel_revoked=True,
    )
    store.request_control("p", session, "handback", 1, "back", "human", "panel")
    assert (
        store.complete_control(
            "p",
            session,
            "worker",
            expected_epoch=store.get_control("p", session)["control_epoch"],
            write_channel_revoked=True,
        )["control_state"]
        == "handback_pending"
    )
    receipt = store.complete_control(
        "p",
        session,
        "worker",
        expected_epoch=store.get_control("p", session)["control_epoch"],
        write_channel_revoked=True,
        observation={"sequence": 1},
    )
    assert receipt["control_state"] == "agent_active"
    assert receipt["control_epoch"] == 2
    assert action(store, session, "new", 2)["control_epoch"] == 2


@pytest.mark.parametrize("reason", ["cancel", "deadline"])
def test_cancel_and_absolute_deadline_prevent_handoff(tmp_path, reason):
    store, session = ledger(tmp_path)
    takeover(store, session)
    if reason == "cancel":
        store.request_session_cancel("p", session)
    else:
        store.update("session", "p", session, deadline_at="2000-01-01T00:00:00+00:00")
    assert store.get_control("p", session)["control_state"] == "closed"
    assert (
        store.complete_control(
            "p",
            session,
            "worker",
            expected_epoch=store.get_control("p", session)["control_epoch"],
            write_channel_revoked=True,
        )["control_state"]
        == "closed"
    )
    with pytest.raises(ValueError):
        store.request_control("p", session, "takeover", 0, "other", "human", "panel")


def test_service_requires_explicit_epoch_and_ignores_no_forged_actor(tmp_path):
    from test_browser_service import service, principal
    from tap_platform.browser_automation.service import Invalid, Conflict
    from tap_platform.browser_automation.control import ControlActor

    browser = service(tmp_path)
    user = principal("p")
    session = browser.create_session(
        user,
        dict(
            task_id="t",
            allowed_origins=["https://example.com"],
            control_version="browser-control-v1",
        ),
        "s",
    )
    sid = session["session_id"]
    with pytest.raises(Conflict):
        browser.create_action(user, sid, {"type": "click"}, "missing")
    with pytest.raises(Invalid):
        browser.create_action(
            user,
            sid,
            {"type": "click", "expected_epoch": 0, "actor_kind": "human"},
            "forged",
        )
    receipt = browser.create_action(
        user, sid, {"type": "click", "expected_epoch": 0}, "ok"
    )
    assert receipt["actor_kind"] == "agent"
    assert receipt["control_epoch"] == 0
    assert receipt["command_id"] == "ok"
    assert receipt["operation"] == "click"
    with pytest.raises(Conflict):
        browser.create_action(
            user,
            sid,
            {"type": "click", "expected_epoch": 0},
            "human",
            actor=ControlActor("human", "panel"),
        )


def test_worker_reserves_and_executes_once(tmp_path):
    import asyncio
    from test_browser_service import service, principal
    from tap_platform.browser_automation.worker import BrowserWorker
    from tap_platform.browser_automation.contracts import BrowserObservation

    browser = service(tmp_path)
    sid = browser.create_session(
        principal("p"), dict(task_id="t", allowed_origins=["https://example.com"]), "s"
    )["session_id"]
    browser.store.update(
        "session",
        "p",
        sid,
        status="active",
        owner="worker",
        lease_until=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )
    browser.create_action(principal("p"), sid, {"type": "click"}, "click")
    row = browser.store.claim("action", "p", "worker", 60)

    class FakeRuntime:
        calls = 0

        async def observe(self, handle, request):
            self.calls += 1
            return BrowserObservation(
                sid, 1, "https://example.com", "title", "", (), None, None
            )

    runtime = FakeRuntime()
    worker = BrowserWorker(browser, runtime, "p", "worker")
    worker._handles[sid] = object()
    asyncio.run(worker._observe(row))
    asyncio.run(worker._observe(row))
    assert runtime.calls == 1
    assert browser.store.get("action", "p", row["id"])["status"] == "completed"


def test_expired_dispatch_reservation_blocks_new_admission(tmp_path):
    store, session = ledger(tmp_path)
    action(store, session)
    row = store.claim("action", "p", "worker", 60)
    store.reserve_dispatch("p", row["id"], "worker")
    store.update("action", "p", row["id"], lease_until="2000-01-01T00:00:00+00:00")
    store.expire_lost("p")
    assert store.get_control("p", session)["control_state"] == "blocked"
    with pytest.raises(ValueError):
        action(store, session, "after-lost")


def test_receipt_requires_semantic_role_name_and_operation():
    from tap_platform.browser_automation.contracts import (
        BrowserActionReceipt,
        SemanticTarget,
    )

    with pytest.raises(ValueError):
        SemanticTarget("role_name", "button")
    target = SemanticTarget("role_name", "button", name="Submit")
    receipt = BrowserActionReceipt(
        command_id="c",
        action_id="a",
        sequence=1,
        actor_kind="human",
        control_epoch=1,
        status="completed",
        operation="click",
        pre_observation_sequence=0,
        target=target,
        args={},
        post_observation_sequence=1,
    )
    assert receipt.operation == "click"
    assert receipt.target.name == "Submit"
    with pytest.raises(ValueError):
        BrowserActionReceipt(
            command_id="c",
            action_id="a",
            sequence=1,
            actor_kind="human",
            control_epoch=1,
            status="completed",
            operation="unknown",
            pre_observation_sequence=0,
            target=None,
            args={},
            post_observation_sequence=1,
        )


def test_model_reservation_prevents_human_activation_and_duplicate_forward(tmp_path):
    store, session = ledger(tmp_path)
    assert store.reserve_model("p", session, "agent", 0, "m")["status"] == "reserved"
    assert store.reserve_model("p", session, "agent", 0, "m")["status"] == "duplicate"
    takeover(store, session)
    assert store.reserve_model("p", session, "agent", 0, "late")["status"] == "rejected"
    assert (
        store.complete_control(
            "p",
            session,
            "worker",
            expected_epoch=store.get_control("p", session)["control_epoch"],
            write_channel_revoked=True,
        )["control_state"]
        == "takeover_pending"
    )
    assert store.settle_model("p", session, "agent", "m")["status"] == "completed"
    assert store.settle_model("p", session, "agent", "m")["status"] == "completed"
    assert (
        store.complete_control(
            "p",
            session,
            "worker",
            expected_epoch=store.get_control("p", session)["control_epoch"],
            write_channel_revoked=True,
        )["control_state"]
        == "human_active"
    )


def test_unknown_model_blocks_and_cannot_be_rewritten(tmp_path):
    store, session = ledger(tmp_path)
    store.reserve_model("p", session, "agent", 0, "m")
    store.settle_model("p", session, "agent", "m", outcome="unknown")
    assert store.settle_model("p", session, "agent", "m")["status"] == "unknown"
    assert store.get_control("p", session)["control_state"] == "blocked"


def test_worker_waits_for_model_then_acknowledges_runtime_revocation(tmp_path):
    import asyncio
    from test_browser_service import service, principal
    from tap_platform.browser_automation.worker import BrowserWorker
    from tap_platform.browser_automation.contracts import BrowserObservation

    browser = service(tmp_path)
    sid = browser.create_session(
        principal("p"),
        dict(
            task_id="t",
            allowed_origins=["https://example.com"],
            control_version="browser-control-v1",
        ),
        "s",
    )["session_id"]
    browser.store.update(
        "session",
        "p",
        sid,
        status="active",
        owner="worker",
        lease_until=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )
    browser.store.reserve_model("p", sid, "test", 0, "m")
    browser.store.request_control("p", sid, "takeover", 0, "take", "human", "panel")

    class Runtime:
        calls = []

        async def synchronize_control(self, handle, kind, epoch):
            self.calls.append((kind, epoch))
            return BrowserObservation(
                sid, 1, "https://example.com", "title", "", (), None, None
            )

    runtime = Runtime()
    worker = BrowserWorker(browser, runtime, "p", "worker")
    worker._handles[sid] = object()
    asyncio.run(worker.tick())
    assert runtime.calls == []
    browser.store.settle_model("p", sid, "test", "m")
    asyncio.run(worker.tick())
    assert runtime.calls == [("human", 1)]
    assert browser.store.get_control("p", sid)["control_state"] == "human_active"


def test_error_observation_does_not_resume_agent(tmp_path):
    store, session = ledger(tmp_path)
    takeover(store, session)
    store.complete_control(
        "p",
        session,
        "worker",
        expected_epoch=store.get_control("p", session)["control_epoch"],
        write_channel_revoked=True,
    )
    store.request_control("p", session, "handback", 1, "back", "human", "panel")
    assert (
        store.complete_control(
            "p",
            session,
            "worker",
            expected_epoch=store.get_control("p", session)["control_epoch"],
            write_channel_revoked=True,
            observation={"sequence": 1, "error": "crashed"},
        )["control_state"]
        == "handback_pending"
    )


def test_observation_error_is_not_successful_action(tmp_path):
    import asyncio
    from test_browser_service import service, principal
    from tap_platform.browser_automation.worker import BrowserWorker
    from tap_platform.browser_automation.contracts import BrowserObservation

    browser = service(tmp_path)
    sid = browser.create_session(
        principal("p"), dict(task_id="t", allowed_origins=["https://example.com"]), "s"
    )["session_id"]
    browser.store.update(
        "session",
        "p",
        sid,
        status="active",
        owner="worker",
        lease_until=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )
    browser.create_action(principal("p"), sid, {"type": "click"}, "click")
    row = browser.store.claim("action", "p", "worker", 60)

    class Runtime:
        async def observe(self, handle, request):
            return BrowserObservation(
                sid, 1, "https://example.com", "title", "", (), None, "page crashed"
            )

    worker = BrowserWorker(browser, Runtime(), "p", "worker")
    worker._handles[sid] = object()
    asyncio.run(worker._observe(row))
    assert browser.store.get("action", "p", row["id"])["status"] == "unknown"


def test_delayed_ack_cannot_acknowledge_later_epoch(tmp_path):
    store, session = ledger(tmp_path)
    takeover(store, session)
    store.complete_control(
        "p", session, "worker", expected_epoch=0, write_channel_revoked=True
    )
    store.request_control("p", session, "handback", 1, "back", "human", "panel")
    receipt = store.complete_control(
        "p",
        session,
        "worker",
        expected_epoch=0,
        write_channel_revoked=True,
        observation={"sequence": 1},
    )
    assert receipt["control_state"] == "handback_pending"


def test_known_failed_read_does_not_block_queue(tmp_path):
    store, session = ledger(tmp_path)
    action(store, session)
    row = store.claim("action", "p", "worker", 60)
    reserved = store.reserve_dispatch("p", row["id"], "worker")
    store.publish_action("p", reserved, "worker", status="failed")
    next_action = action(store, session, "next")
    assert store.claim("action", "p", "worker", 60)["id"] == next_action["id"]


def test_sensitive_input_requires_trusted_preflight_before_persistence(tmp_path):
    from sqlalchemy import select
    from tap_platform.browser_automation.store import actions

    store, session = ledger(tmp_path)
    secret = "synthetic-secret-never-save"
    with pytest.raises(ValueError, match="preflight"):
        store.insert_action_idempotent(
            "p",
            session,
            "fill",
            "fill",
            {"type": "fill", "selector": "#password", "value": secret},
            actor_kind="agent",
            controller_id="agent",
            expected_epoch=0,
        )
    with store.engine.connect() as connection:
        assert secret not in repr(connection.execute(select(actions)).all())


def test_preflight_proof_is_bound_to_last_action_target_and_epoch(tmp_path):
    store, session = ledger(tmp_path)
    preflight = store.insert_action_idempotent(
        "p",
        session,
        "pre",
        "pre",
        {"type": "preflight", "operation": "fill", "selector": "#name"},
        actor_kind="agent",
        controller_id="agent",
        expected_epoch=0,
    )
    claim = store.claim("action", "p", "worker", 60)
    reserved = store.reserve_dispatch("p", claim["id"], "worker")
    store.publish_action(
        "p",
        reserved,
        "worker",
        status="completed",
        observation={
            "sequence": 1,
            "extracted": {
                "preflight": {
                    "safe": True,
                    "operation": "fill",
                    "selector": "#name",
                    "target": {"kind": "test_id", "value": "name"},
                }
            },
        },
    )
    body = {
        "type": "fill",
        "selector": "#name",
        "value": "synthetic",
        "preflight_action_id": preflight["id"],
    }
    with pytest.raises(ValueError, match="preflight"):
        store.insert_action_idempotent(
            "p",
            session,
            "wrong",
            "wrong",
            {**body, "selector": "#password"},
            actor_kind="agent",
            controller_id="agent",
            expected_epoch=0,
        )
    actual = store.insert_action_idempotent(
        "p",
        session,
        "fill",
        "fill",
        body,
        actor_kind="agent",
        controller_id="agent",
        expected_epoch=0,
    )
    assert actual["request"]["target"] == {"kind": "test_id", "value": "name"}
    with pytest.raises(ValueError, match="preflight"):
        store.insert_action_idempotent(
            "p",
            session,
            "again",
            "again",
            body,
            actor_kind="agent",
            controller_id="agent",
            expected_epoch=0,
        )


def test_independent_store_instances_serialize_takeover_and_dispatch(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store, session = ledger(tmp_path)
    action(store, session)
    row = store.claim("action", "p", "worker", 60)
    other = BrowserStore(create_engine(str(store.engine.url)))
    barrier = Barrier(2)

    def dispatch():
        barrier.wait()
        return store.reserve_dispatch("p", row["id"], "worker")

    def transfer():
        barrier.wait()
        return takeover(other, session)

    with ThreadPoolExecutor(2) as pool:
        reserved = pool.submit(dispatch)
        taken = pool.submit(transfer)
        result = reserved.result()
        assert taken.result()["control_state"] == "takeover_pending"
    receipt = store.complete_control(
        "p", session, "worker", expected_epoch=0, write_channel_revoked=True
    )
    assert receipt["control_state"] == (
        "takeover_pending" if result else "human_active"
    )
    if result:
        assert store.get("action", "p", row["id"])["status"] == "dispatching"
    else:
        assert store.get("action", "p", row["id"])["status"] == "rejected"


def test_explicit_migration_preserves_legacy_session(tmp_path):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import MetaData, Table, text, inspect
    from tap_platform.browser_automation.store import sessions, actions

    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    old = MetaData()
    new_session = {
        "control_version",
        "controller_kind",
        "controller_id",
        "agent_controller_id",
        "control_epoch",
        "control_state",
        "pending_controller_id",
        "deadline_at",
        "handoff_observation_sequence",
        "observer_until",
        "recording_stopped",
        "recording_watermark",
        "recording_start_sequence",
        "recording_epoch",
    }
    new_action = {
        "actor_kind",
        "controller_id",
        "control_epoch",
        "command_id",
        "actor_expires_at",
    }
    for source, excluded in ((sessions, new_session), (actions, new_action)):
        Table(
            source.name,
            old,
            *(
                column._copy()
                for column in source.columns
                if column.name not in excluded
            ),
        )
    old.create_all(engine)
    path = Path(__file__).parents[2] / "migrations/versions/0009_browser_control.py"
    spec = importlib.util.spec_from_file_location("control_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tap_browser_sessions (id,project_id,task_id,status,request,created_at) VALUES ('s','p','t','active','{}','2026-01-01')"
            )
        )
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        row = connection.execute(
            text(
                "SELECT control_version,controller_kind,control_epoch FROM tap_browser_sessions WHERE id='s'"
            )
        ).one()
        assert tuple(row) == (None, "agent", 0)
        assert "tap_browser_model_reservations" in inspect(connection).get_table_names()
        for table_name in (
            "tap_browser_sessions",
            "tap_browser_actions",
            "tap_browser_control_requests",
            "tap_browser_model_reservations",
        ):
            expected = metadata.tables[table_name]
            actual = {
                column["name"]: column
                for column in inspect(connection).get_columns(table_name)
            }
            assert set(actual) == set(expected.columns.keys())
            for column in expected.columns:
                assert actual[column.name]["nullable"] == column.nullable
                assert str(actual[column.name]["type"]) == str(column.type)
        legacy = connection.execute(
            text(
                "SELECT control_state,recording_stopped,recording_start_sequence,recording_epoch FROM tap_browser_sessions WHERE id='s'"
            )
        ).one()
        assert tuple(legacy) == ("agent_active", 0, None, None)
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        assert {
            column["name"]
            for column in inspect(connection).get_columns("tap_browser_sessions")
        } == set(old.tables["tap_browser_sessions"].columns.keys())
        assert {
            column["name"]
            for column in inspect(connection).get_columns("tap_browser_actions")
        } == set(old.tables["tap_browser_actions"].columns.keys())
        assert (
            "tap_browser_control_requests" not in inspect(connection).get_table_names()
        )
        assert (
            "tap_browser_model_reservations"
            not in inspect(connection).get_table_names()
        )
        assert (
            connection.execute(text("SELECT id FROM tap_browser_sessions")).scalar()
            == "s"
        )


def test_receipt_uses_runtime_bound_target_before_navigation():
    from tap_platform.browser_automation.service import BrowserService

    row = {
        "id": "a",
        "session_id": "s",
        "sequence": 2,
        "status": "completed",
        "command_id": "c",
        "actor_kind": "human",
        "control_epoch": 1,
        "request": {
            "type": "click",
            "target": {"kind": "css", "value": "forged"},
            "args": {"forged": True},
        },
        "observation": {
            "sequence": 9,
            "extracted": {
                "control_receipt": {
                    "target": {
                        "kind": "role_name",
                        "value": "button",
                        "name": "Submit",
                    },
                    "pre_observation_sequence": 8,
                    "args": {},
                }
            },
        },
    }
    receipt = BrowserService._action(row)
    assert receipt["target"] == {
        "kind": "role_name",
        "value": "button",
        "name": "Submit",
    }
    assert receipt["pre_observation_sequence"] == 8
    assert receipt["args"] == {}


def test_observer_lease_and_ephemeral_worker_frame(tmp_path):
    import asyncio
    from dataclasses import replace
    from test_browser_service import service, principal
    from tap_platform.browser_automation.worker import BrowserWorker

    browser = service(tmp_path)
    user = replace(
        principal("p"), actions=principal("p").actions | {"browser.sessions.read"}
    )
    sid = browser.create_session(
        user,
        dict(
            task_id="t",
            allowed_origins=["https://example.com"],
            control_version="browser-control-v1",
        ),
        "s",
    )["session_id"]
    browser.store.update(
        "session",
        "p",
        sid,
        status="active",
        owner="worker",
        lease_until=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )

    class Frames:
        content = {}

        def publish(self, sid, frame, *, control_epoch):
            self.content[sid] = frame

        def delete(self, sid):
            self.content.pop(sid, None)

        def purge_expired(self):
            pass

    class Runtime:
        calls = 0

        async def read_frame(self, handle):
            self.calls += 1
            return {"image_base64": "synthetic-frame", "control_epoch": 0}

    browser.frames = Frames()
    worker = BrowserWorker(browser, Runtime(), "p", "worker")
    worker._handles[sid] = object()
    browser.request_observer(user, sid)
    asyncio.run(worker.tick())
    assert browser.frames.content[sid]["image_base64"] == "synthetic-frame"
    asyncio.run(worker.tick())
    assert worker.runtime.calls == 1
    assert "synthetic-frame" not in repr(browser.store.get("session", "p", sid))
    browser.store.update(
        "session", "p", sid, observer_until="2000-01-01T00:00:00+00:00"
    )
    asyncio.run(worker.tick())
    assert not browser.frames.content


@pytest.mark.parametrize(
    "change",
    [
        {"x": 21},
        {"y": 31},
        {"frame_sequence": 8},
        {"viewport": {"width": 801, "height": 600}},
        {"type": "press"},
        {"selector": "#other"},
    ],
)
def test_coordinate_preflight_binds_exact_target_without_persisting_mismatch(
    tmp_path, change
):
    from sqlalchemy import select
    from tap_platform.browser_automation.store import actions

    store, session = ledger(tmp_path)
    target = {
        "x": 20,
        "y": 30,
        "frame_sequence": 7,
        "viewport": {"width": 800, "height": 600},
    }
    pre = store.insert_action_idempotent(
        "p",
        session,
        "pre",
        "pre",
        {"type": "preflight", "operation": "fill", **target},
        actor_kind="agent",
        controller_id="agent",
        expected_epoch=0,
    )
    row = store.claim("action", "p", "worker", 60)
    reserved = store.reserve_dispatch("p", row["id"], "worker")
    store.publish_action(
        "p",
        reserved,
        "worker",
        status="completed",
        observation={
            "sequence": 1,
            "extracted": {
                "preflight": {
                    "safe": True,
                    "operation": "fill",
                    "target": {"kind": "test_id", "value": "name"},
                }
            },
        },
    )
    body = {
        "type": "fill",
        **target,
        "value": "synthetic-unpersisted-mismatch",
        "preflight_action_id": pre["id"],
    }
    with pytest.raises(ValueError, match="preflight"):
        store.insert_action_idempotent(
            "p",
            session,
            "bad",
            "bad",
            {**body, **change},
            actor_kind="agent",
            controller_id="agent",
            expected_epoch=0,
        )
    with store.engine.connect() as connection:
        assert "synthetic-unpersisted-mismatch" not in repr(
            connection.execute(select(actions)).all()
        )
    admitted = store.insert_action_idempotent(
        "p",
        session,
        "ok",
        "ok",
        body,
        actor_kind="agent",
        controller_id="agent",
        expected_epoch=0,
    )
    assert admitted["request"]["target"] == {"kind": "test_id", "value": "name"}


def test_service_admits_scroll_for_controlled_human(tmp_path):
    from test_browser_service import service, principal
    from tap_platform.browser_automation.control import ControlActor

    browser = service(tmp_path)
    sid = browser.create_session(
        principal("p"),
        dict(
            task_id="t",
            allowed_origins=["https://example.com"],
            control_version="browser-control-v1",
        ),
        "s",
    )["session_id"]
    browser.store.update(
        "session",
        "p",
        sid,
        controller_kind="human",
        controller_id="panel",
        control_state="human_active",
        control_epoch=1,
    )
    receipt = browser.create_action(
        principal("p"),
        sid,
        {"type": "scroll", "expected_epoch": 1, "delta_y": 100},
        "scroll",
        actor=ControlActor("human", "panel"),
    )
    assert receipt["operation"] == "scroll"


@pytest.mark.parametrize("after_claim", [False, True])
def test_human_capability_expiry_fences_queued_runtime_dispatch(tmp_path, after_claim):
    from tap_platform.browser_automation.control import ControlActor
    from tap_platform.browser_automation.service import (
        BrowserService,
        Conflict,
        Invalid,
    )
    from tap_platform.browser_automation.objects import BrowserObjectStore
    from test_browser_service import principal

    store, session = ledger(tmp_path)
    store.update(
        "session",
        "p",
        session,
        controller_kind="human",
        controller_id="panel",
        control_state="human_active",
        control_epoch=1,
    )
    browser = BrowserService(store, BrowserObjectStore(tmp_path / "objects"))
    actor = ControlActor(
        "human",
        "panel",
        expires_at=(datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
    )
    accepted = browser.create_action(
        principal("p"),
        session,
        {"type": "click", "expected_epoch": 1},
        "human",
        actor=actor,
    )
    if after_claim:
        assert store.claim("action", "p", "worker", 60)["id"] == accepted["action_id"]
    store.update(
        "action",
        "p",
        accepted["action_id"],
        actor_expires_at="2000-01-01T00:00:00+00:00",
    )
    if not after_claim:
        assert store.claim("action", "p", "worker", 60) is None
    assert store.reserve_dispatch("p", accepted["action_id"], "worker") is None
    assert store.get("action", "p", accepted["action_id"])["status"] == "rejected"
    assert store.get_control("p", session)["control_state"] == "human_active"
    expired = ControlActor("human", "panel", expires_at="2000-01-01T00:00:00+00:00")
    with pytest.raises(Conflict, match="expired"):
        browser.create_action(
            principal("p"),
            session,
            {"type": "click", "expected_epoch": 1},
            "expired",
            actor=expired,
        )
    with pytest.raises(Invalid):
        browser.create_action(
            principal("p"),
            session,
            {
                "type": "click",
                "expected_epoch": 1,
                "actor_expires_at": "2099-01-01T00:00:00+00:00",
            },
            "forged",
            actor=actor,
        )


def test_expired_control_actor_cannot_request_transition(tmp_path):
    from dataclasses import replace
    from tap_platform.browser_automation.control import ControlActor
    from tap_platform.browser_automation.service import BrowserService, Conflict
    from tap_platform.browser_automation.objects import BrowserObjectStore
    from test_browser_service import principal

    store, session = ledger(tmp_path)
    browser = BrowserService(store, BrowserObjectStore(tmp_path / "objects"))
    user = replace(
        principal("p"), actions=principal("p").actions | {"browser.sessions.control"}
    )
    with pytest.raises(Conflict, match="expired"):
        browser.request_control(
            user,
            session,
            "takeover",
            0,
            "expired",
            actor=ControlActor(
                "human", "panel", expires_at="2000-01-01T00:00:00+00:00"
            ),
        )
    assert store.get_control("p", session)["control_state"] == "agent_active"


def controlled_input_fixture(tmp_path):
    from test_browser_service import service, principal

    browser = service(tmp_path)
    sid = browser.create_session(
        principal("p"),
        dict(
            task_id="t",
            allowed_origins=["https://example.com"],
            control_version="browser-control-v1",
        ),
        "s",
    )["session_id"]
    browser.store.update(
        "session",
        "p",
        sid,
        status="active",
        owner="worker",
        lease_until=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )
    proof = browser.create_action(
        principal("p"),
        sid,
        {
            "type": "preflight",
            "operation": "fill",
            "selector": "#name",
            "expected_epoch": 0,
        },
        "proof",
    )
    claimed = browser.store.claim("action", "p", "worker", 60)
    reserved = browser.store.reserve_dispatch("p", claimed["id"], "worker")
    browser.store.publish_action(
        "p",
        reserved,
        "worker",
        status="completed",
        observation={
            "sequence": 1,
            "extracted": {
                "preflight": {
                    "safe": True,
                    "operation": "fill",
                    "selector": "#name",
                    "target": {"kind": "test_id", "value": "name"},
                }
            },
        },
    )
    return browser, sid, proof["action_id"]


def test_controlled_input_value_never_enters_durable_intent_and_consumes_once(tmp_path):
    from test_browser_service import principal

    browser, sid, proof = controlled_input_fixture(tmp_path)
    secret = "synthetic-ephemeral-value"
    body = {
        "type": "fill",
        "selector": "#name",
        "expected_epoch": 0,
        "preflight_action_id": proof,
        "value": secret,
    }
    receipt = browser.create_action(principal("p"), sid, body, "input")
    assert secret not in repr(browser.store.get("action", "p", receipt["action_id"]))
    assert browser.create_action(principal("p"), sid, body, "input") == receipt
    with pytest.raises(ValueError):
        browser.consume_input_for_dispatch("p", sid, receipt["action_id"])
    claimed = browser.store.claim("action", "p", "worker", 60)
    browser.store.reserve_dispatch("p", claimed["id"], "worker")
    assert browser.consume_input_for_dispatch("p", sid, claimed["id"]) == {
        "value": secret
    }
    with pytest.raises(ValueError):
        browser.consume_input_for_dispatch("p", sid, claimed["id"])


def test_password_flip_after_admission_does_not_persist_or_forward_value(tmp_path):
    import asyncio
    from test_browser_service import principal
    from tap_platform.browser_automation.worker import BrowserWorker
    from tap_platform.browser_automation.contracts import BrowserObservation

    browser, sid, proof = controlled_input_fixture(tmp_path)
    secret = "synthetic-password-flipped-after-proof"
    receipt = browser.create_action(
        principal("p"),
        sid,
        {
            "type": "fill",
            "selector": "#name",
            "expected_epoch": 0,
            "preflight_action_id": proof,
            "value": secret,
        },
        "input",
    )

    class Runtime:
        requests = []

        async def observe(self, handle, request):
            self.requests.append(request)
            return BrowserObservation(
                sid,
                2,
                "https://example.com",
                "",
                "",
                (),
                None,
                "sensitive input target",
            )

    runtime = Runtime()
    worker = BrowserWorker(browser, runtime, "p", "worker")
    worker._handles[sid] = object()
    claimed = browser.store.claim("action", "p", "worker", 60)
    asyncio.run(worker._observe(claimed))
    assert len(runtime.requests) == 1
    assert runtime.requests[0]["type"] == "preflight"
    assert secret not in repr(runtime.requests)
    assert secret not in repr(browser.store.get("action", "p", receipt["action_id"]))
    assert browser.store.get("action", "p", receipt["action_id"])["status"] == "failed"
    for path in browser.objects.root.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


def test_missing_ephemeral_input_becomes_unknown_without_reexecution(tmp_path):
    import asyncio
    from test_browser_service import principal
    from tap_platform.browser_automation.worker import BrowserWorker
    from tap_platform.browser_automation.contracts import BrowserObservation
    from tap_platform.browser_automation.input_vault import InputVault

    browser, sid, proof = controlled_input_fixture(tmp_path)
    receipt = browser.create_action(
        principal("p"),
        sid,
        {
            "type": "fill",
            "selector": "#name",
            "expected_epoch": 0,
            "preflight_action_id": proof,
            "value": "volatile",
        },
        "input",
    )
    browser.inputs = InputVault()  # API process restart loses unsent plaintext.

    class Runtime:
        requests = []

        async def observe(self, handle, request):
            self.requests.append(request)
            return BrowserObservation(
                sid,
                2,
                "https://example.com",
                "",
                "",
                (),
                None,
                None,
                extracted={"preflight": {"safe": True}},
            )

    runtime = Runtime()
    worker = BrowserWorker(browser, runtime, "p", "worker")
    worker._handles[sid] = object()
    claimed = browser.store.claim("action", "p", "worker", 60)
    asyncio.run(worker._observe(claimed))
    asyncio.run(worker._observe(claimed))
    assert [x["type"] for x in runtime.requests] == ["preflight"]
    assert browser.store.get("action", "p", receipt["action_id"])["status"] == "unknown"


def test_worker_input_transport_is_required_when_control_enabled(monkeypatch):
    from tap_platform.browser_automation.worker import input_fetcher_from_environment

    monkeypatch.setenv("TAP_BROWSER_CONTROL_ENABLED", "true")
    monkeypatch.delenv("TAP_BROWSER_API_ORIGIN", raising=False)
    monkeypatch.delenv("TAP_BROWSER_WORKER_TOKEN", raising=False)
    with pytest.raises(ValueError):
        input_fetcher_from_environment()
    monkeypatch.setenv("TAP_BROWSER_API_ORIGIN", "http://127.0.0.1:8123")
    monkeypatch.setenv("TAP_BROWSER_WORKER_TOKEN", "w" * 32)
    assert callable(input_fetcher_from_environment())


def test_stop_recording_fences_new_admission_but_drains_captured_watermark(tmp_path):
    store, session = ledger(tmp_path)
    store.update(
        "session",
        "p",
        session,
        controller_kind="human",
        controller_id="panel",
        control_state="human_active",
        control_epoch=1,
    )
    admitted = store.insert_action_idempotent(
        "p",
        session,
        "first",
        "first",
        {"type": "click"},
        actor_kind="human",
        controller_id="panel",
        expected_epoch=1,
    )
    stopped = store.stop_recording("p", session, 1, "stop", "panel")
    assert stopped["watermark"] == admitted["sequence"]
    assert stopped["status"] == "pending"
    assert store.get_control("p", session)["control_state"] == "human_active"
    assert store.get_control("p", session)["recording_stopped"] is True
    with pytest.raises(ValueError, match="recording stopped"):
        store.insert_action_idempotent(
            "p",
            session,
            "late",
            "late",
            {"type": "click"},
            actor_kind="human",
            controller_id="panel",
            expected_epoch=1,
        )
    claimed = store.claim("action", "p", "worker", 60)
    reserved = store.reserve_dispatch("p", claimed["id"], "worker")
    assert reserved["id"] == admitted["id"]
    store.publish_action("p", reserved, "worker", status="completed")
    final = store.stop_recording("p", session, 1, "stop", "panel")
    assert final["status"] == "completed"
    assert final["watermark"] == stopped["watermark"]
    assert store.get_control("p", session)["control_epoch"] == 1
    with pytest.raises(ValueError, match="idempotency"):
        store.stop_recording("p", session, 1, "stop", "another-panel")


def test_stop_recording_unknown_is_not_completed_and_listing_is_ordered(tmp_path):
    store, session = ledger(tmp_path)
    store.update(
        "session",
        "p",
        session,
        controller_kind="human",
        controller_id="panel",
        control_state="human_active",
        control_epoch=1,
    )
    for key in ("one", "two"):
        store.insert_action_idempotent(
            "p",
            session,
            key,
            key,
            {"type": "click"},
            actor_kind="human",
            controller_id="panel",
            expected_epoch=1,
        )
    store.stop_recording("p", session, 1, "stop", "panel")
    row = store.claim("action", "p", "worker", 60)
    reserved = store.reserve_dispatch("p", row["id"], "worker")
    store.publish_action("p", reserved, "worker", status="unknown")
    assert store.stop_recording("p", session, 1, "stop", "panel")["status"] == "unknown"
    page = store.list_actions("p", session, after=0, limit=1)
    assert [row["sequence"] for row in page] == [1]
    assert [
        row["sequence"] for row in store.list_actions("p", session, after=1, limit=200)
    ] == [2]
    with pytest.raises(ValueError):
        store.list_actions("p", session, after=0, limit=201)


@pytest.mark.parametrize(
    "other", ["TAP_BROWSER_ACCESS_TOKEN", "TAP_BROWSER_GATEWAY_TOKEN"]
)
def test_worker_input_token_cannot_reuse_other_service_credentials(monkeypatch, other):
    from tap_platform.browser_automation.worker import input_fetcher_from_environment

    monkeypatch.setenv("TAP_BROWSER_CONTROL_ENABLED", "true")
    monkeypatch.setenv("TAP_BROWSER_API_ORIGIN", "http://127.0.0.1:8123")
    monkeypatch.setenv("TAP_BROWSER_WORKER_TOKEN", "w" * 32)
    monkeypatch.setenv(other, "w" * 32)
    with pytest.raises(ValueError, match="distinct"):
        input_fetcher_from_environment()


def test_capability_expiring_during_input_fetch_cannot_write(tmp_path):
    import asyncio
    from test_browser_service import principal
    from tap_platform.browser_automation.worker import BrowserWorker
    from tap_platform.browser_automation.contracts import BrowserObservation
    from tap_platform.browser_automation.control import ControlActor

    browser, sid, proof = controlled_input_fixture(tmp_path)
    browser.store.update(
        "session",
        "p",
        sid,
        controller_kind="human",
        controller_id="panel",
        control_state="human_active",
        control_epoch=1,
    )
    browser.store.update(
        "action", "p", proof, actor_kind="human", controller_id="panel", control_epoch=1
    )
    actor = ControlActor(
        "human",
        "panel",
        expires_at=(datetime.now(UTC) + timedelta(seconds=0.5)).isoformat(),
    )
    receipt = browser.create_action(
        principal("p"),
        sid,
        {
            "type": "fill",
            "selector": "#name",
            "expected_epoch": 1,
            "preflight_action_id": proof,
            "value": "volatile",
        },
        "input",
        actor=actor,
    )

    class Runtime:
        requests = []

        async def observe(self, handle, request):
            self.requests.append(request)
            return BrowserObservation(
                sid,
                2,
                "https://example.com",
                "",
                "",
                (),
                None,
                None,
                extracted={"preflight": {"safe": True}},
            )

    async def fetch(project, session, action):
        payload = browser.consume_input_for_dispatch(project, session, action)
        await asyncio.sleep(0.6)
        return payload

    runtime = Runtime()
    worker = BrowserWorker(browser, runtime, "p", "worker", input_fetcher=fetch)
    worker._handles[sid] = object()
    claimed = browser.store.claim("action", "p", "worker", 60)
    asyncio.run(worker._observe(claimed))
    assert [x["type"] for x in runtime.requests] == ["preflight"]
    assert (
        browser.store.get("action", "p", receipt["action_id"])["status"] == "rejected"
    )


def test_controlled_receipt_preserves_runtime_observation_sequence(tmp_path):
    import asyncio
    from test_browser_service import service, principal
    from tap_platform.browser_automation.worker import BrowserWorker
    from tap_platform.browser_automation.contracts import BrowserObservation

    browser = service(tmp_path)
    sid = browser.create_session(
        principal("p"),
        {
            "task_id": "t",
            "allowed_origins": ["https://example.com"],
            "control_version": "browser-control-v1",
        },
        "session",
    )["session_id"]
    browser.store.update(
        "session",
        "p",
        sid,
        status="active",
        owner="worker",
        lease_until=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )
    receipt = browser.create_action(
        principal("p"), sid, {"type": "click", "expected_epoch": 0}, "click"
    )

    class Runtime:
        async def observe(self, handle, request):
            return BrowserObservation(
                sid,
                99,
                "https://example.com",
                "",
                "",
                (),
                None,
                None,
                extracted={
                    "control_receipt": {
                        "pre_observation_sequence": 98,
                        "target": {"kind": "test_id", "value": "submit"},
                        "args": {},
                    }
                },
            )

    worker = BrowserWorker(browser, Runtime(), "p", "worker")
    worker._handles[sid] = object()
    row = browser.store.claim("action", "p", "worker", 60)
    asyncio.run(worker._observe(row))
    result = browser._action(browser.store.get("action", "p", receipt["action_id"]))
    assert result["sequence"] == 1
    assert result["pre_observation_sequence"] == 98
    assert result["post_observation_sequence"] == 99


def test_recording_start_is_durable_at_takeover_and_zero_action_stop(tmp_path):
    store, session = ledger(tmp_path)
    takeover(store, session)
    receipt = store.complete_control(
        "p",
        session,
        "worker",
        expected_epoch=0,
        write_channel_revoked=True,
        observation={"sequence": 7},
    )
    assert receipt["recording_start_sequence"] == 8
    assert receipt["recording_epoch"] == 1
    assert store.get_control("p", session)["recording_start_sequence"] == 8
    stopped = store.stop_recording("p", session, 1, "stop", "panel")
    assert stopped["recording_start_sequence"] == 8
    assert stopped["recording_epoch"] == 1
    assert stopped["watermark"] == 7
    assert stopped["status"] == "completed"


def test_handback_freshness_uses_runtime_sequence_not_action_sequence(tmp_path):
    store, session = ledger(tmp_path)
    takeover(store, session)
    store.complete_control(
        "p",
        session,
        "worker",
        expected_epoch=0,
        write_channel_revoked=True,
        observation={"sequence": 1},
    )
    store.insert_action_idempotent(
        "p",
        session,
        "human",
        "human",
        {"type": "click"},
        actor_kind="human",
        controller_id="panel",
        expected_epoch=1,
    )
    row = store.claim("action", "p", "worker", 60)
    reserved = store.reserve_dispatch("p", row["id"], "worker")
    store.publish_action(
        "p", reserved, "worker", status="completed", observation={"sequence": 99}
    )
    store.request_control("p", session, "handback", 1, "back", "human", "panel")
    stale = store.complete_control(
        "p",
        session,
        "worker",
        expected_epoch=1,
        write_channel_revoked=True,
        observation={"sequence": 98},
    )
    assert stale["control_state"] == "handback_pending"
    fresh = store.complete_control(
        "p",
        session,
        "worker",
        expected_epoch=1,
        write_channel_revoked=True,
        observation={"sequence": 100},
    )
    assert fresh["control_state"] == "agent_active"


@pytest.mark.parametrize(
    "url",
    [
        "https://other.test/path",
        "http://127.0.0.1/private",
        "javascript:alert(1)",
        "https://user:password@example.com/path",
    ],
)
def test_human_navigation_rejects_disallowed_urls_before_admission(tmp_path, url):
    from test_browser_service import service, principal
    from tap_platform.browser_automation.control import ControlActor
    from tap_platform.browser_automation.service import Invalid
    from sqlalchemy import select, func
    from tap_platform.browser_automation.store import actions

    browser = service(tmp_path)
    sid = browser.create_session(
        principal("p"),
        {
            "task_id": "t",
            "allowed_origins": ["https://example.com"],
            "control_version": "browser-control-v1",
        },
        "session",
    )["session_id"]
    browser.store.update(
        "session",
        "p",
        sid,
        controller_kind="human",
        controller_id="panel",
        control_state="human_active",
        control_epoch=1,
    )
    with pytest.raises(Invalid):
        browser.create_action(
            principal("p"),
            sid,
            {
                "type": "navigate",
                "url": url,
                "expected_epoch": 1,
                "frame_sequence": 1,
                "viewport": {"width": 800, "height": 600},
            },
            "navigate",
            actor=ControlActor("human", "panel"),
        )
    with browser.store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(actions)) == 0


def test_human_navigation_allowed_url_is_idempotent_and_target_free(tmp_path):
    from test_browser_service import service, principal
    from tap_platform.browser_automation.control import ControlActor

    browser = service(tmp_path)
    sid = browser.create_session(
        principal("p"),
        {
            "task_id": "t",
            "allowed_origins": ["https://example.com"],
            "control_version": "browser-control-v1",
        },
        "session",
    )["session_id"]
    browser.store.update(
        "session",
        "p",
        sid,
        controller_kind="human",
        controller_id="panel",
        control_state="human_active",
        control_epoch=1,
    )
    body = {
        "type": "navigate",
        "url": "https://example.com/next?part=2",
        "expected_epoch": 1,
        "frame_sequence": 1,
        "viewport": {"width": 800, "height": 600},
    }
    actor = ControlActor("human", "panel")
    first = browser.create_action(principal("p"), sid, body, "navigate", actor=actor)
    assert (
        browser.create_action(principal("p"), sid, body, "navigate", actor=actor)
        == first
    )
    row = browser.store.get("action", "p", first["action_id"])
    assert "target" not in row["request"]
    assert first["target"] is None


@pytest.mark.parametrize("operation", ["navigate", "select"])
@pytest.mark.parametrize("uncertain", [False, True])
def test_recording_stop_drains_inflight_navigation_or_select_once(
    tmp_path, operation, uncertain
):
    import asyncio
    from dataclasses import replace
    from test_browser_service import service, principal
    from tap_platform.browser_automation.control import ControlActor
    from tap_platform.browser_automation.worker import BrowserWorker
    from tap_platform.browser_automation.contracts import BrowserObservation

    browser = service(tmp_path)
    user = replace(
        principal("p"), actions=principal("p").actions | {"browser.sessions.control"}
    )
    sid = browser.create_session(
        user,
        {
            "task_id": "t",
            "allowed_origins": ["https://example.com"],
            "control_version": "browser-control-v1",
        },
        "session",
    )["session_id"]
    browser.store.update(
        "session",
        "p",
        sid,
        status="active",
        owner="worker",
        lease_until=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        controller_kind="human",
        controller_id="panel",
        control_state="human_active",
        control_epoch=1,
    )
    actor = ControlActor("human", "panel")
    body = {
        "type": operation,
        "expected_epoch": 1,
        "frame_sequence": 1,
        "viewport": {"width": 800, "height": 600},
    }
    if operation == "select":
        body.update(selector="#plan", value="premium")
        preflight = browser.create_action(
            user,
            sid,
            {
                key: value
                for key, value in {
                    **body,
                    "type": "preflight",
                    "operation": "select",
                }.items()
                if key != "value"
            },
            "preflight",
            actor=actor,
        )
        claimed = browser.store.claim("action", "p", "worker", 60)
        reserved = browser.store.reserve_dispatch("p", claimed["id"], "worker")
        browser.store.publish_action(
            "p",
            reserved,
            "worker",
            status="completed",
            observation={
                "sequence": 1,
                "extracted": {
                    "preflight": {
                        "safe": True,
                        "operation": "select",
                        "selector": "#plan",
                        "target": {"kind": "test_id", "value": "plan"},
                    }
                },
            },
        )
        body["preflight_action_id"] = preflight["action_id"]
    else:
        body["url"] = "https://example.com/next"
    accepted = browser.create_action(user, sid, body, "command", actor=actor)
    row = browser.store.get("action", "p", accepted["action_id"])
    if operation == "select":
        assert "premium" not in repr(row["request"])
        assert "input_ref" in row["request"]

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        class Runtime:
            writes = 0

            async def observe(self, handle, request):
                if request["type"] == "preflight":
                    return BrowserObservation(
                        sid,
                        2,
                        "https://example.com",
                        "",
                        "",
                        (),
                        None,
                        None,
                        extracted={"preflight": {"safe": True}},
                    )
                self.writes += 1
                started.set()
                await release.wait()
                if operation == "select":
                    assert request["value"] == "premium"
                return BrowserObservation(
                    sid,
                    3,
                    "https://example.com/next",
                    "",
                    "",
                    (),
                    None,
                    "outcome unknown" if uncertain else None,
                    extracted={
                        "control_receipt": {
                            "target": None
                            if operation == "navigate"
                            else {"kind": "test_id", "value": "plan"},
                            "args": {"url": body["url"]}
                            if operation == "navigate"
                            else {"value": "premium"},
                            "pre_observation_sequence": 2,
                        }
                    },
                )

        runtime = Runtime()
        worker = BrowserWorker(browser, runtime, "p", "worker")
        worker._handles[sid] = object()
        claimed = browser.store.claim("action", "p", "worker", 60)
        running = asyncio.create_task(worker._observe(claimed))
        await started.wait()
        stopped = browser.stop_recording(user, sid, 1, "stop", actor=actor)
        assert stopped["status"] == "pending"
        assert stopped["watermark"] == accepted["sequence"]
        release.set()
        await running
        final = browser.stop_recording(user, sid, 1, "stop", actor=actor)
        assert final["status"] == ("unknown" if uncertain else "completed")
        assert final["watermark"] == stopped["watermark"]
        duplicate = browser.create_action(user, sid, body, "command", actor=actor)
        assert duplicate["action_id"] == accepted["action_id"]
        await worker._observe(claimed)
        assert runtime.writes == 1
        if not uncertain:
            assert duplicate["args"] == (
                {"url": body["url"]}
                if operation == "navigate"
                else {"value": "premium"}
            )
            assert duplicate["target"] == (
                None
                if operation == "navigate"
                else {"kind": "test_id", "value": "plan"}
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("blocked", [False, True])
def test_cancel_atomically_freezes_recording_and_preserves_receipts(tmp_path, blocked):
    store, sid = ledger(tmp_path)
    takeover(store, sid)
    store.complete_control(
        "p", sid, "worker", expected_epoch=0, write_channel_revoked=True
    )
    row = store.insert_action_idempotent(
        "p",
        sid,
        "human",
        "human",
        {"type": "click"},
        actor_kind="human",
        controller_id="panel",
        expected_epoch=1,
    )
    if blocked:
        claimed = store.claim("action", "p", "worker", 60)
        reserved = store.reserve_dispatch("p", claimed["id"], "worker")
        store.publish_action("p", reserved, "worker", status="unknown")
    store.request_session_cancel("p", sid)
    state = store.get_control("p", sid)
    assert state["recording_stopped"] is True
    assert state["recording_watermark"] == row["sequence"]
    assert state["recording_start_sequence"] == 1
    assert state["control_state"] == "closed"
    assert store.get("action", "p", row["id"])["status"] == (
        "unknown" if blocked else "pending"
    )
    store.request_session_cancel("p", sid)
    assert store.get_control("p", sid)["recording_watermark"] == row["sequence"]
    with pytest.raises(ValueError):
        store.insert_action_idempotent(
            "p",
            sid,
            "late",
            "late",
            {"type": "click"},
            actor_kind="human",
            controller_id="panel",
            expected_epoch=1,
        )


def test_cancel_preserves_already_stopped_recording_watermark(tmp_path):
    store, sid = ledger(tmp_path)
    takeover(store, sid)
    store.complete_control(
        "p", sid, "worker", expected_epoch=0, write_channel_revoked=True
    )
    receipt = store.stop_recording("p", sid, 1, "stop", "panel")
    store.request_session_cancel("p", sid, owner="not-owner")
    assert store.get("session", "p", sid)["cancel_requested"] is False
    store.request_session_cancel("p", sid)
    state = store.get_control("p", sid)
    assert state["recording_watermark"] == receipt["watermark"] == 0
    assert state["recording_start_sequence"] == receipt["recording_start_sequence"] == 1


@pytest.mark.parametrize("confirmed", [True, False])
def test_lca_cleanup_waits_for_drain_and_ack_preserves_receipts(tmp_path, confirmed):
    from sqlalchemy import update
    from tap_platform.browser_automation.store import sessions

    store, sid = ledger(tmp_path)
    with store.engine.begin() as c:
        c.execute(
            update(sessions)
            .where(sessions.c.id == sid)
            .values(request={"close_on_recording_stop": True})
        )
    takeover(store, sid)
    store.complete_control(
        "p", sid, "worker", expected_epoch=0, write_channel_revoked=True
    )
    row = store.insert_action_idempotent(
        "p",
        sid,
        "human",
        "human",
        {"type": "click"},
        actor_kind="human",
        controller_id="panel",
        expected_epoch=1,
    )
    store.stop_recording("p", sid, 1, "stop", "panel")
    assert store.claim_recording_cleanup("p", sid, "worker") is None
    claimed = store.claim("action", "p", "worker", 60)
    reserved = store.reserve_dispatch("p", claimed["id"], "worker")
    store.publish_action(
        "p", reserved, "worker", status="completed", observation={"sequence": 1}
    )
    before = store.get("action", "p", row["id"])
    cleanup = store.claim_recording_cleanup("p", sid, "worker")
    assert cleanup["cancel_requested"] is True
    assert store.claim_recording_cleanup("p", sid, "worker") is None
    assert store.settle_session(
        "p",
        sid,
        "worker",
        {"stopped": confirmed, "status": "canceled" if confirmed else "unknown"},
    )
    stopped = store.get("session", "p", sid)
    assert stopped["status"] == ("completed" if confirmed else "unknown")
    assert stopped["stop_receipt"]["reason"] == "recording_drained"
    assert store.get("action", "p", row["id"]) == before


def test_generic_aa_stop_does_not_claim_lca_cleanup(tmp_path):
    store, sid = ledger(tmp_path)
    takeover(store, sid)
    store.complete_control(
        "p", sid, "worker", expected_epoch=0, write_channel_revoked=True
    )
    store.stop_recording("p", sid, 1, "stop", "panel")
    assert store.claim_recording_cleanup("p", sid, "worker") is None
    assert store.get_control("p", sid)["control_state"] == "human_active"


@pytest.mark.parametrize("operation", ["click", "fill"])
def test_safe_click_diagnostic_keeps_unknown_fence_and_vaulted_input_redaction(
    tmp_path, operation
):
    import asyncio
    from dataclasses import replace
    from test_browser_service import principal
    from tap_platform.browser_automation.contracts import BrowserObservation
    from tap_platform.browser_automation.worker import BrowserWorker

    browser, sid, proof = controlled_input_fixture(tmp_path)
    user = replace(
        principal("p"), actions=principal("p").actions | {"browser.actions.read"}
    )
    diagnostic = "controlled browser request rejected: frame_revision_changed"
    secret = "synthetic-private-input-for-redaction-check"
    body = {"type": operation, "selector": "#name", "expected_epoch": 0}
    if operation == "fill":
        body.update(value=secret, preflight_action_id=proof)
    admitted = browser.create_action(user, sid, body, "diagnostic-command")

    class Runtime:
        requests = []

        async def observe(self, handle, request):
            self.requests.append(request)
            if request["type"] == "preflight":
                return BrowserObservation(
                    sid,
                    2,
                    "https://example.com",
                    "",
                    "",
                    (),
                    None,
                    None,
                    extracted={"preflight": {"safe": True}},
                )
            return BrowserObservation(
                sid,
                3,
                "https://example.com",
                "",
                secret if operation == "fill" else "",
                (),
                None,
                diagnostic,
                extracted={"control_receipt": {"args": {"value": secret}}}
                if operation == "fill"
                else None,
            )

    runtime = Runtime()
    worker = BrowserWorker(browser, runtime, "p", "worker")
    worker._handles[sid] = object()
    claimed = browser.store.claim("action", "p", "worker", 60)
    asyncio.run(worker._observe(claimed))
    receipt = browser.get_action(user, sid, admitted["action_id"])
    assert receipt["status"] == "unknown"
    assert browser.store.get_control("p", sid)["control_state"] == "blocked"
    assert receipt["observation"]["error"] == (
        "controlled input outcome unavailable" if operation == "fill" else diagnostic
    )
    assert secret not in repr(receipt)
    assert secret not in repr(browser.store.get("action", "p", admitted["action_id"]))
    duplicate = browser.create_action(user, sid, body, "diagnostic-command")
    assert duplicate == receipt
    asyncio.run(worker._observe(claimed))
    assert [request["type"] for request in runtime.requests] == (
        ["preflight", "fill"] if operation == "fill" else ["click"]
    )
    assert browser.store.claim("action", "p", "worker", 60) is None
