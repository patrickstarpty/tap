"""Workflow transactions against fixture-owned MySQL sessions."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy import event as sa_event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.workflows.adapters.mysql import MysqlWorkflowStore
from tap.modules.workflows.adapters.schema import workflow_event, workflow_task
from tap.modules.workflows.domain.commands import ControlCommand
from tests.owned_mysql import owned_project_database_url


async def _lease(
    sessions,
    task_id: str,
    *,
    token: str = "fence-1",
    owner: str | None = "worker",
    until: datetime | None = None,
) -> None:
    async with sessions.begin() as session:
        await session.execute(
            update(workflow_task)
            .where(workflow_task.c.task_id == task_id)
            .values(
                lease_owner=owner,
                lease_token=token,
                lease_until=(until or datetime.now(UTC) + timedelta(minutes=5)).replace(
                    tzinfo=None
                ),
            )
        )


@pytest_asyncio.fixture
async def workflow_db(owned_project_mysql):
    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions, MysqlWorkflowStore(sessions)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_is_idempotent_and_rejects_changed_payload(workflow_db) -> None:
    sessions, store = workflow_db
    scope = VALIDATION_SCOPE
    original = await store.create_task(
        scope, {"goal": "summarize", "budget_limit": {"model_tokens": 10}}, "create-1"
    )
    repeated = await store.create_task(
        scope, {"budget_limit": {"model_tokens": 10}, "goal": "summarize"}, "create-1"
    )
    assert repeated == original
    with pytest.raises(ValueError):
        await store.create_task(scope, {"goal": "changed"}, "create-1")
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(workflow_task)) == 1


@pytest.mark.asyncio
async def test_status_budget_and_event_commit_together(workflow_db) -> None:
    sessions, store = workflow_db
    task = await store.create_task(
        VALIDATION_SCOPE, {"goal": "build", "budget_limit": {"model_tokens": 5}}, "create-2"
    )
    await _lease(sessions, task.task_id)
    claim = await store.append_event(
        VALIDATION_SCOPE, task.task_id, "fence-1", "event-1", "task.status", {"signal": "claim"}
    )
    assert claim.sequence == 2
    await store.append_event(
        VALIDATION_SCOPE, task.task_id, "fence-1", "event-2", "task.budget", {"model_tokens": 3}
    )
    before = await store.snapshot(VALIDATION_SCOPE, task.task_id)
    assert before["status"] == "running"
    assert before["budget_used"] == {"model_tokens": 3}
    assert before["last_sequence"] == 3
    assert [event.sequence for event in before["events"]] == [1, 2, 3]
    with pytest.raises(ValueError):
        await store.append_event(
            VALIDATION_SCOPE, task.task_id, "fence-1", "event-3", "task.budget", {"model_tokens": 3}
        )
    with pytest.raises(ValueError):
        await store.append_event(
            VALIDATION_SCOPE,
            task.task_id,
            "fence-1",
            "event-1",
            "task.status",
            {"signal": "runtime_failed"},
        )
    after = await store.snapshot(VALIDATION_SCOPE, task.task_id)
    assert after["last_sequence"] == 3
    assert after["budget_used"] == {"model_tokens": 3}
    assert [
        event.event_id for event in await store.events_after(VALIDATION_SCOPE, task.task_id, 1, 2)
    ] == ["event-1", "event-2"]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(workflow_event)) == 3
    # Force a database failure after the task update: both the new status and
    # event must roll back, even though next_sequence was corrupted externally.
    async with sessions.begin() as session:
        await session.execute(
            update(workflow_task)
            .where(workflow_task.c.task_id == task.task_id)
            .values(next_sequence=3)
        )
    with pytest.raises(IntegrityError):
        await store.append_event(
            VALIDATION_SCOPE,
            task.task_id,
            "fence-1",
            "failed-insert",
            "task.status",
            {"signal": "runtime_succeeded"},
        )
    rolled_back = await store.snapshot(VALIDATION_SCOPE, task.task_id)
    assert rolled_back["status"] == "running"
    assert [event.sequence for event in rolled_back["events"]] == [1, 2]


@pytest.mark.asyncio
async def test_two_sessions_allocate_unique_sequences_and_old_fence_is_rejected(
    workflow_db,
) -> None:
    sessions, store = workflow_db
    task = await store.create_task(VALIDATION_SCOPE, {"goal": "parallel"}, "create-3")
    await _lease(sessions, task.task_id, token="fence-2")
    with pytest.raises(ValueError):
        await store.append_event(
            VALIDATION_SCOPE, task.task_id, "fence-1", "stale", "task.note", {}
        )
    events = await asyncio.gather(
        *(
            store.append_event(
                VALIDATION_SCOPE, task.task_id, "fence-2", f"parallel-{n}", "task.note", {"n": n}
            )
            for n in range(8)
        )
    )
    assert sorted(event.sequence for event in events) == list(range(2, 10))
    assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))["last_sequence"] == 9


@pytest.mark.asyncio
async def test_snapshot_keeps_one_waterline_under_read_committed(owned_project_mysql) -> None:
    async_engine = create_async_engine(
        owned_project_database_url(owned_project_mysql), isolation_level="READ COMMITTED"
    )
    sync_engine = create_engine(owned_project_mysql.url)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    store = MysqlWorkflowStore(sessions)
    task = await store.create_task(VALIDATION_SCOPE, {"goal": "consistent view"}, "snapshot-1")
    task_read = threading.Event()
    writer_done = threading.Event()
    writer_errors: list[BaseException] = []

    def writer() -> None:
        try:
            if not task_read.wait(10):
                raise TimeoutError("snapshot did not read the task")
            with sync_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO workflow_node (node_id, task_id, plan_revision, kind, "
                        "status, attempt, role, runtime, created_at, enterprise_id, project_id, "
                        "actor_id, identity_mode, identity_origin) SELECT 'node-after-read', "
                        "task_id, 1, 'service', 'queued', 0, 'service', 'native', "
                        "UTC_TIMESTAMP(6), enterprise_id, project_id, actor_id, identity_mode, "
                        "identity_origin FROM workflow_task WHERE task_id=:task_id"
                    ),
                    {"task_id": task.task_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO workflow_event (event_id, task_id, sequence, attempt, kind, "
                        "payload, payload_digest, occurred_at, enterprise_id, "
                        "project_id, actor_id, "
                        "identity_mode, identity_origin) SELECT 'event-after-read', task_id, 2, 0, "
                        "'node.created', '{}', 'sha256:0', UTC_TIMESTAMP(6), enterprise_id, "
                        "project_id, actor_id, identity_mode, identity_origin FROM workflow_task "
                        "WHERE task_id=:task_id"
                    ),
                    {"task_id": task.task_id},
                )
                connection.execute(
                    update(workflow_task)
                    .where(workflow_task.c.task_id == task.task_id)
                    .values(next_sequence=3)
                )
        except BaseException as error:
            writer_errors.append(error)
        finally:
            writer_done.set()

    def after_task_read(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        if "FROM workflow_task" in statement and statement.lstrip().startswith("SELECT"):
            task_read.set()
            if not writer_done.wait(10):
                raise TimeoutError("concurrent writer did not finish")

    thread = threading.Thread(target=writer)
    sa_event.listen(async_engine.sync_engine, "after_cursor_execute", after_task_read)
    try:
        thread.start()
        observed = await store.snapshot(VALIDATION_SCOPE, task.task_id)
        assert not writer_errors
        assert observed["last_sequence"] == 1
        assert observed["nodes"] == ()
        assert [item.sequence for item in observed["events"]] == [1]
    finally:
        sa_event.remove(async_engine.sync_engine, "after_cursor_execute", after_task_read)
        thread.join(timeout=10)
        await async_engine.dispose()
        sync_engine.dispose()


@pytest.mark.asyncio
async def test_task_scope_and_cross_task_node_are_rejected(workflow_db) -> None:
    sessions, store = workflow_db
    first = await store.create_task(VALIDATION_SCOPE, {"goal": "first"}, "create-4")
    second = await store.create_task(VALIDATION_SCOPE, {"goal": "second"}, "create-5")
    await _lease(sessions, first.task_id)
    from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext

    other = ProjectScopeContext(
        enterprise_id="local",
        project_id="another",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode=IdentityMode.VALIDATION,
    )
    with pytest.raises(LookupError):
        await store.snapshot(other, first.task_id)
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO workflow_node (node_id, task_id, plan_revision, kind, status, "
                "attempt, role, runtime, created_at, enterprise_id, project_id, actor_id, "
                "identity_mode, identity_origin) SELECT 'node-foreign', task_id, 1, "
                "'service', 'queued', 0, 'service', 'native', UTC_TIMESTAMP(6), "
                "enterprise_id, project_id, actor_id, identity_mode, identity_origin "
                "FROM workflow_task WHERE task_id=:task_id"
            ),
            {"task_id": second.task_id},
        )
    with pytest.raises(ValueError):
        await store.append_event(
            VALIDATION_SCOPE,
            first.task_id,
            "fence-1",
            "wrong-node",
            "node.note",
            {"node_id": "node-foreign"},
        )


@pytest.mark.asyncio
async def test_expired_or_ownerless_lease_cannot_append_budget_or_event(workflow_db) -> None:
    sessions, store = workflow_db
    task = await store.create_task(VALIDATION_SCOPE, {"goal": "fenced"}, "create-6")
    await _lease(sessions, task.task_id, owner=None)
    with pytest.raises(ValueError):
        await store.append_event(
            VALIDATION_SCOPE, task.task_id, "fence-1", "event-ownerless", "task.note", {}
        )
    await _lease(sessions, task.task_id, until=datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(ValueError):
        await store.append_event(
            VALIDATION_SCOPE,
            task.task_id,
            "fence-1",
            "event-expired",
            "task.budget",
            {"model_tokens": 1},
        )
    assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))["last_sequence"] == 1


@pytest.mark.asyncio
async def test_canceling_lease_loss_does_not_requeue_without_confirmation(workflow_db) -> None:
    sessions, store = workflow_db
    task = await store.create_task(VALIDATION_SCOPE, {"goal": "cancel"}, "create-7")
    await _lease(sessions, task.task_id)
    for event_id, signal in (
        ("claim", "claim"),
        ("cancel", "cancel_requested"),
        ("lost", "lease_lost"),
    ):
        await store.append_event(
            VALIDATION_SCOPE, task.task_id, "fence-1", event_id, "task.status", {"signal": signal}
        )
    with pytest.raises(ValueError):
        await store.append_event(
            VALIDATION_SCOPE,
            task.task_id,
            "fence-1",
            "blind-resume",
            "task.status",
            {"signal": "reconciled"},
        )
    assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))["status"] == "recovering"


@pytest.mark.asyncio
async def test_dependency_edges_stay_within_task_and_acyclic(workflow_db) -> None:
    sessions, store = workflow_db
    task = await store.create_task(VALIDATION_SCOPE, {"goal": "plan"}, "create-8")
    other = await store.create_task(VALIDATION_SCOPE, {"goal": "other"}, "create-9")
    await _lease(sessions, task.task_id)
    async with sessions.begin() as session:
        for node_id, task_id in (
            ("a", task.task_id),
            ("b", task.task_id),
            ("c", task.task_id),
            ("foreign", other.task_id),
        ):
            await session.execute(
                text(
                    "INSERT INTO workflow_node (node_id, task_id, plan_revision, kind, "
                    "status, attempt, role, runtime, created_at, enterprise_id, project_id, "
                    "actor_id, identity_mode, identity_origin) SELECT :node_id, task_id, 1, "
                    "'service', 'queued', 0, 'service', 'native', UTC_TIMESTAMP(6), "
                    "enterprise_id, project_id, actor_id, identity_mode, identity_origin "
                    "FROM workflow_task WHERE task_id=:task_id"
                ),
                {"node_id": node_id, "task_id": task_id},
            )
    await store.add_dependency(VALIDATION_SCOPE, task.task_id, "fence-1", "a", "b", 1)
    await store.add_dependency(VALIDATION_SCOPE, task.task_id, "fence-1", "b", "c", 1)
    with pytest.raises(ValueError):
        await store.add_dependency(VALIDATION_SCOPE, task.task_id, "fence-1", "c", "a", 1)
    with pytest.raises(ValueError):
        await store.add_dependency(VALIDATION_SCOPE, task.task_id, "fence-1", "a", "foreign", 1)
    assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))["last_sequence"] == 3


@pytest.mark.asyncio
async def test_observed_task_is_read_only_and_never_native_claimed(workflow_db) -> None:
    _, store = workflow_db
    task = await store.create_task(
        VALIDATION_SCOPE,
        {"origin": "observed", "goal": "legacy chat turn"},
        "observed-never-claim",
    )
    assert (task.task_id, "queued") not in await store.scan_work(VALIDATION_SCOPE, 10)
    assert await store.claim_task(VALIDATION_SCOPE, task.task_id, "native-worker") is None
    with pytest.raises(ValueError, match="observed"):
        await store.submit_command(
            VALIDATION_SCOPE,
            ControlCommand("observed-command", task.task_id, None, "cancel", 1, None),
        )
    with pytest.raises(ValueError, match="observed"):
        await store.rerun_task(VALIDATION_SCOPE, task.task_id, "observed-rerun")
