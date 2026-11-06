"""Control and recovery transactions against an owned MySQL instance."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event as sa_event
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.workflows.adapters.mysql import MysqlWorkflowStore
from tap.modules.workflows.adapters.schema import (
    workflow_attempt,
    workflow_command,
    workflow_dependency,
    workflow_event,
    workflow_node,
    workflow_plan_revision,
    workflow_task,
)
from tap.modules.workflows.application.recovery import RecoveryService
from tap.modules.workflows.application.service import WorkflowService
from tap.modules.workflows.domain.commands import ControlCommand
from tap.modules.workflows.ports.runtime import RuntimeInspection
from tap.platform.db.project_scope import scope_values
from tests.owned_mysql import owned_project_database_url


@pytest_asyncio.fixture
async def workflow_db(owned_project_mysql):
    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = MysqlWorkflowStore(sessions)
    try:
        yield sessions, store, WorkflowService(store)
    finally:
        await engine.dispose()


async def _node(sessions, task_id: str, node_id: str, status: str = "running") -> None:
    async with sessions.begin() as session:
        await session.execute(
            workflow_node.insert().values(
                node_id=node_id,
                task_id=task_id,
                plan_revision=1,
                role="service",
                kind="service",
                runtime="native",
                status=status,
                attempt=1,
                created_at=datetime.now(UTC).replace(tzinfo=None),
                **scope_values(VALIDATION_SCOPE),
            )
        )
        await session.execute(
            workflow_attempt.insert().values(
                attempt_id=f"{node_id}-attempt-1",
                task_id=task_id,
                node_id=node_id,
                attempt=1,
                status=status,
                checkpoint_ref="checkpoint-1",
                created_at=datetime.now(UTC).replace(tzinfo=None),
                **scope_values(VALIDATION_SCOPE),
            )
        )


class FixedRuntime:
    def __init__(self, inspection: RuntimeInspection) -> None:
        self.inspection = inspection

    async def inspect(self, scope, episode) -> RuntimeInspection:
        return RuntimeInspection(
            self.inspection.outcome,
            self.inspection.checkpoint_compatible,
            self.inspection.side_effects,
            execution_token=episode.execution_token,
        )


@pytest.mark.asyncio
async def test_duplicate_and_concurrent_steer_have_one_ordered_application(workflow_db) -> None:
    sessions, store, service = workflow_db
    task = await store.create_task(VALIDATION_SCOPE, {"goal": "original"}, "controls-1")
    first = ControlCommand("steer-a", task.task_id, None, "steer", 1, "clarify output")
    second = ControlCommand("steer-b", task.task_id, None, "steer", 1, "add proof")
    receipts = await asyncio.gather(
        service.submit(VALIDATION_SCOPE, first),
        service.submit(VALIDATION_SCOPE, second),
    )
    assert [receipt["status"] for receipt in receipts] == ["accepted", "accepted"]
    assert await service.submit(VALIDATION_SCOPE, first) == receipts[0]
    with pytest.raises(ValueError):
        await service.submit(
            VALIDATION_SCOPE,
            ControlCommand("steer-a", task.task_id, None, "steer", 1, "different"),
        )
    pending = await store.pending_commands(VALIDATION_SCOPE, task.task_id)
    assert [item.command_id for item in pending] == ["steer-a", "steer-b"]
    token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-a")
    assert token

    def crash_before_receipt(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().startswith("UPDATE workflow_command"):
            raise RuntimeError("simulated crash after plan write")

    engine = sessions.kw["bind"]
    sa_event.listen(engine.sync_engine, "before_cursor_execute", crash_before_receipt)
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            await service.ack_command(VALIDATION_SCOPE, "steer-a", token, {"status": "applied"})
    finally:
        sa_event.remove(engine.sync_engine, "before_cursor_execute", crash_before_receipt)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(workflow_plan_revision)) == 1
    assert (await service.submit(VALIDATION_SCOPE, first))["status"] == "accepted"
    applied = await service.ack_command(VALIDATION_SCOPE, "steer-a", token, {"status": "applied"})
    assert applied == {"status": "applied", "reason": None, "applied_plan_revision": 2}
    assert (
        await service.ack_command(VALIDATION_SCOPE, "steer-a", token, {"status": "applied"})
        == applied
    )
    assert await service.ack_command(VALIDATION_SCOPE, "steer-b", token, {"status": "applied"}) == {
        "status": "stale",
        "reason": "plan revision changed",
        "applied_plan_revision": None,
    }
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(workflow_plan_revision)) == 2
        assert await session.scalar(select(func.count()).select_from(workflow_command)) == 2


@pytest.mark.asyncio
async def test_cancel_recovery_never_requeues_without_authoritative_stop(workflow_db) -> None:
    sessions, store, service = workflow_db
    task = await store.create_task(
        VALIDATION_SCOPE, {"goal": "run", "budget_limit": {"tokens": 10}}, "controls-2"
    )
    token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-a")
    await store.append_event(
        VALIDATION_SCOPE, task.task_id, token, "budget-1", "task.budget", {"tokens": 4}
    )
    await _node(sessions, task.task_id, "active")
    await _node(sessions, task.task_id, "still-queued", "queued")
    cancel = ControlCommand("cancel-1", task.task_id, None, "cancel", 1, None)
    assert (await service.submit(VALIDATION_SCOPE, cancel))["status"] == "accepted"
    assert (
        await service.ack_command(
            VALIDATION_SCOPE, "cancel-1", token, {"status": "applied", "stopped": False}
        )
    )["status"] == "applied"
    async with sessions.begin() as session:
        await session.execute(
            update(workflow_task)
            .where(workflow_task.c.task_id == task.task_id)
            .values(lease_until=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1))
        )
    recovery = RecoveryService(store, FixedRuntime(RuntimeInspection("unknown", False, "unknown")))
    assert await recovery.reconcile(VALIDATION_SCOPE, task.task_id) == "recovering"
    assert await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-b") is None
    assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))["budget_used"] == {"tokens": 4}
    with pytest.raises(ValueError):
        await store.append_event(
            VALIDATION_SCOPE, task.task_id, token, "stale-write", "task.budget", {"tokens": 1}
        )
    recovery = RecoveryService(store, FixedRuntime(RuntimeInspection("stopped", True, "clear")))
    assert await recovery.reconcile(VALIDATION_SCOPE, task.task_id) == "canceled"
    assert await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-b") is None
    assert await store.ready_nodes(VALIDATION_SCOPE, task.task_id) == ()


@pytest.mark.asyncio
async def test_recovered_running_node_gets_new_attempt_and_keeps_budget(workflow_db) -> None:
    sessions, store, _service = workflow_db
    task = await store.create_task(
        VALIDATION_SCOPE, {"goal": "run", "budget_limit": {"tokens": 10}}, "controls-3"
    )
    token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-a")
    await store.append_event(
        VALIDATION_SCOPE, task.task_id, token, "budget-2", "task.budget", {"tokens": 3}
    )
    await _node(sessions, task.task_id, "active")
    async with sessions.begin() as session:
        await session.execute(
            update(workflow_task)
            .where(workflow_task.c.task_id == task.task_id)
            .values(lease_until=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1))
        )
    recovery = RecoveryService(store, FixedRuntime(RuntimeInspection("stopped", True, "clear")))
    assert await recovery.reconcile(VALIDATION_SCOPE, task.task_id) == "queued"
    new_token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-b")
    assert new_token and new_token != token
    with pytest.raises(ValueError):
        await store.append_event(
            VALIDATION_SCOPE, task.task_id, token, "old-budget", "task.budget", {"tokens": 1}
        )
    snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
    assert snapshot["budget_used"] == {"tokens": 3}
    async with sessions() as session:
        attempts = (
            await session.execute(
                select(workflow_attempt.c.attempt, workflow_attempt.c.status)
                .where(workflow_attempt.c.task_id == task.task_id)
                .order_by(workflow_attempt.c.attempt)
            )
        ).all()
    assert attempts == [(1, "interrupted"), (2, "queued")]


@pytest.mark.asyncio
async def test_node_pause_blocks_dependents_but_not_independent_work(workflow_db) -> None:
    sessions, store, service = workflow_db
    task = await store.create_task(VALIDATION_SCOPE, {"goal": "branches"}, "controls-4")
    token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-a")
    await _node(sessions, task.task_id, "parent", "running")
    await _node(sessions, task.task_id, "dependent", "queued")
    await _node(sessions, task.task_id, "independent", "queued")
    await store.add_dependency(VALIDATION_SCOPE, task.task_id, token, "parent", "dependent", 1)
    command = ControlCommand("pause-node", task.task_id, "parent", "pause", 1, None)
    await service.submit(VALIDATION_SCOPE, command)
    assert (
        await service.ack_command(
            VALIDATION_SCOPE, "pause-node", token, {"status": "applied", "stopped": False}
        )
    )["status"] == "applied"
    assert await store.ready_nodes(VALIDATION_SCOPE, task.task_id) == ("independent",)
    async with sessions() as session:
        assert (
            await session.scalar(
                select(workflow_node.c.status).where(workflow_node.c.node_id == "parent")
            )
            == "pausing"
        )


@pytest.mark.asyncio
async def test_steer_reworks_completed_node_without_mutating_it(workflow_db) -> None:
    sessions, store, service = workflow_db
    task = await store.create_task(VALIDATION_SCOPE, {"goal": "review"}, "controls-5")
    token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-a")
    await _node(sessions, task.task_id, "finished", "succeeded")
    await _node(sessions, task.task_id, "successor", "queued")
    await _node(sessions, task.task_id, "independent", "queued")
    await store.add_dependency(VALIDATION_SCOPE, task.task_id, token, "finished", "successor", 1)
    command = ControlCommand("steer-rework", task.task_id, "finished", "steer", 1, "revise")
    await service.submit(VALIDATION_SCOPE, command)
    assert (
        await service.ack_command(VALIDATION_SCOPE, "steer-rework", token, {"status": "applied"})
    )["applied_plan_revision"] == 2
    async with sessions() as session:
        nodes = (
            await session.execute(
                select(
                    workflow_node.c.node_id,
                    workflow_node.c.parent_node_id,
                    workflow_node.c.status,
                    workflow_node.c.plan_revision,
                ).where(workflow_node.c.task_id == task.task_id)
            )
        ).all()
        edges = (
            await session.execute(
                select(
                    workflow_dependency.c.plan_revision,
                    workflow_dependency.c.from_node_id,
                    workflow_dependency.c.to_node_id,
                ).where(workflow_dependency.c.task_id == task.task_id)
            )
        ).all()
    assert ("finished", None, "succeeded", 1) in nodes
    rework = [node for node in nodes if node[1] == "finished"]
    assert len(rework) == 1
    rework_id = rework[0][0]
    assert rework[0][2:] == ("queued", 2)
    assert (1, "finished", "successor") in edges
    assert (2, "finished", rework_id) not in edges
    assert (2, rework_id, "successor") in edges
    assert {"independent", rework_id} == set(
        await store.ready_nodes(VALIDATION_SCOPE, task.task_id)
    )


@pytest.mark.asyncio
async def test_retry_uses_new_attempt_only_after_effect_and_budget_check(workflow_db) -> None:
    sessions, store, service = workflow_db
    task = await store.create_task(
        VALIDATION_SCOPE, {"goal": "retry", "budget_limit": {"tokens": 5}}, "controls-6"
    )
    token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-a")
    await _node(sessions, task.task_id, "failed-node", "failed")
    await store.append_event(
        VALIDATION_SCOPE, task.task_id, token, "retry-budget", "task.budget", {"tokens": 2}
    )
    command = ControlCommand("retry-node", task.task_id, "failed-node", "retry", 1, None)
    await service.submit(VALIDATION_SCOPE, command)
    with pytest.raises(ValueError):
        await service.ack_command(
            VALIDATION_SCOPE,
            "retry-node",
            token,
            {"status": "applied", "side_effects": "unknown", "checkpoint_compatible": True},
        )
    assert (
        await service.ack_command(
            VALIDATION_SCOPE,
            "retry-node",
            token,
            {"status": "applied", "side_effects": "clear", "checkpoint_compatible": True},
        )
    )["status"] == "applied"
    async with sessions() as session:
        attempts = (
            await session.execute(
                select(
                    workflow_attempt.c.attempt,
                    workflow_attempt.c.status,
                    workflow_attempt.c.checkpoint_ref,
                )
                .where(workflow_attempt.c.task_id == task.task_id)
                .order_by(workflow_attempt.c.attempt)
            )
        ).all()
    assert attempts == [(1, "failed", "checkpoint-1"), (2, "queued", "checkpoint-1")]
    assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))["budget_used"] == {"tokens": 2}


@pytest.mark.asyncio
async def test_confirmed_pause_releases_lease_and_resume_uses_control_claim(workflow_db) -> None:
    _sessions, store, service = workflow_db
    task = await store.create_task(VALIDATION_SCOPE, {"goal": "pause"}, "controls-7")
    first_token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-a")
    await service.submit(
        VALIDATION_SCOPE, ControlCommand("pause-task", task.task_id, None, "pause", 1, None)
    )
    assert (
        await service.ack_command(
            VALIDATION_SCOPE,
            "pause-task",
            first_token,
            {"status": "applied", "stopped": True, "checkpoint_compatible": True},
        )
    )["status"] == "applied"
    assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))["status"] == "paused"
    await service.submit(
        VALIDATION_SCOPE, ControlCommand("resume-task", task.task_id, None, "resume", 1, None)
    )
    second_token = await store.claim_control(VALIDATION_SCOPE, task.task_id, "worker-b")
    assert second_token and second_token != first_token
    assert (
        await service.ack_command(
            VALIDATION_SCOPE, "resume-task", second_token, {"status": "applied"}
        )
    )["status"] == "applied"
    assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))["status"] == "queued"
    assert await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-c")


@pytest.mark.asyncio
async def test_completed_task_rerun_creates_new_task_with_source(workflow_db) -> None:
    sessions, store, service = workflow_db
    original = await store.create_task(VALIDATION_SCOPE, {"goal": "deliver"}, "controls-8")
    token = await store.claim_task(VALIDATION_SCOPE, original.task_id, "worker-a")
    await store.append_event(
        VALIDATION_SCOPE,
        original.task_id,
        token,
        "done",
        "task.status",
        {"signal": "runtime_succeeded"},
    )
    rerun = await service.rerun(VALIDATION_SCOPE, original.task_id, "rerun-1")
    assert rerun.task_id != original.task_id
    assert (await store.snapshot(VALIDATION_SCOPE, original.task_id))["status"] == "succeeded"
    async with sessions() as session:
        source = await session.scalar(
            select(workflow_task.c.request_json).where(workflow_task.c.task_id == rerun.task_id)
        )
    assert source["rerun_from_task_id"] == original.task_id


@pytest.mark.asyncio
async def test_concurrent_duplicate_ack_keeps_first_applied_receipt(workflow_db) -> None:
    sessions, store, service = workflow_db
    task = await store.create_task(VALIDATION_SCOPE, {"goal": "race"}, "controls-race")
    token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-a")
    command = ControlCommand("race-steer", task.task_id, None, "steer", 1, "clarify")
    await service.submit(VALIDATION_SCOPE, command)
    both_read = asyncio.Event()
    reads = 0

    def after_command_read(_conn, _cursor, statement, _params, _context, _many):
        nonlocal reads
        if "FROM workflow_command" in statement and "workflow_command.command_id =" in statement:
            reads += 1
            if reads == 2:
                both_read.set()

    engine = sessions.kw["bind"]
    sa_event.listen(engine.sync_engine, "after_cursor_execute", after_command_read)
    try:
        async with sessions.begin() as blocker:
            await blocker.execute(
                select(workflow_task.c.task_id)
                .where(workflow_task.c.task_id == task.task_id)
                .with_for_update()
            )
            first = asyncio.create_task(
                service.ack_command(VALIDATION_SCOPE, "race-steer", token, {"status": "applied"})
            )
            second = asyncio.create_task(
                service.ack_command(VALIDATION_SCOPE, "race-steer", token, {"status": "applied"})
            )
            await asyncio.wait_for(both_read.wait(), timeout=10)
        receipts = await asyncio.gather(first, second)
    finally:
        sa_event.remove(engine.sync_engine, "after_cursor_execute", after_command_read)
    expected = {"status": "applied", "reason": None, "applied_plan_revision": 2}
    assert receipts == [expected, expected]
    async with sessions() as session:
        stored = await session.scalar(
            select(workflow_command.c.receipt).where(workflow_command.c.command_id == "race-steer")
        )
        revisions = await session.scalar(
            select(func.count())
            .select_from(workflow_plan_revision)
            .where(workflow_plan_revision.c.task_id == task.task_id)
        )
        receipts_count = await session.scalar(
            select(func.count())
            .select_from(workflow_event)
            .where(
                workflow_event.c.task_id == task.task_id,
                workflow_event.c.kind == "command.receipted",
            )
        )
    assert stored == expected
    assert revisions == 2
    assert receipts_count == 1


@pytest.mark.asyncio
async def test_old_recovery_observation_cannot_resolve_new_episode(workflow_db) -> None:
    sessions, store, _service = workflow_db
    task = await store.create_task(VALIDATION_SCOPE, {"goal": "recover"}, "controls-episode")
    first_token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-a")
    async with sessions.begin() as session:
        await session.execute(
            update(workflow_task)
            .where(workflow_task.c.task_id == task.task_id)
            .values(lease_until=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1))
        )
    first_episode = await store.mark_lease_lost(VALIDATION_SCOPE, task.task_id)
    old_observation = RuntimeInspection(
        "stopped", True, "clear", execution_token=first_episode.execution_token
    )
    assert await store.resolve_recovery(VALIDATION_SCOPE, task.task_id, old_observation) == "queued"
    second_token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-b")
    assert second_token and second_token != first_token
    async with sessions.begin() as session:
        await session.execute(
            update(workflow_task)
            .where(workflow_task.c.task_id == task.task_id)
            .values(lease_until=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1))
        )
    second_episode = await store.mark_lease_lost(VALIDATION_SCOPE, task.task_id)
    assert second_episode.execution_token != first_episode.execution_token
    with pytest.raises(ValueError, match="recovery episode"):
        await store.resolve_recovery(VALIDATION_SCOPE, task.task_id, old_observation)
    assert (await store.snapshot(VALIDATION_SCOPE, task.task_id))["status"] == "recovering"


@pytest.mark.asyncio
async def test_steer_accepted_before_terminal_completion_cannot_revise_task(workflow_db) -> None:
    sessions, store, service = workflow_db
    task = await store.create_task(VALIDATION_SCOPE, {"goal": "finish"}, "controls-terminal")
    token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-a")
    await service.submit(
        VALIDATION_SCOPE,
        ControlCommand("late-steer", task.task_id, None, "steer", 1, "change"),
    )
    await store.append_event(
        VALIDATION_SCOPE,
        task.task_id,
        token,
        "finished-event",
        "task.status",
        {"signal": "runtime_succeeded"},
    )
    receipt = await service.ack_command(
        VALIDATION_SCOPE, "late-steer", token, {"status": "applied"}
    )
    assert receipt == {
        "status": "rejected",
        "reason": "terminal task requires a new task",
        "applied_plan_revision": None,
    }
    snapshot = await store.snapshot(VALIDATION_SCOPE, task.task_id)
    assert snapshot["status"] == "succeeded"
    assert snapshot["plan_revision"] == 1
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(workflow_plan_revision)
                .where(workflow_plan_revision.c.task_id == task.task_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(workflow_command.c.receipt).where(
                    workflow_command.c.command_id == "late-steer"
                )
            )
            == receipt
        )


@pytest.mark.asyncio
async def test_failed_node_rework_inherits_prerequisites_without_blocking_on_failure(
    workflow_db,
) -> None:
    sessions, store, service = workflow_db
    task = await store.create_task(VALIDATION_SCOPE, {"goal": "repair"}, "controls-failed-rework")
    token = await store.claim_task(VALIDATION_SCOPE, task.task_id, "worker-a")
    await _node(sessions, task.task_id, "prerequisite", "succeeded")
    await _node(sessions, task.task_id, "failed-target", "failed")
    await _node(sessions, task.task_id, "successor", "queued")
    await _node(sessions, task.task_id, "independent", "queued")
    await store.add_dependency(
        VALIDATION_SCOPE, task.task_id, token, "prerequisite", "failed-target", 1
    )
    await store.add_dependency(
        VALIDATION_SCOPE, task.task_id, token, "failed-target", "successor", 1
    )
    await service.submit(
        VALIDATION_SCOPE,
        ControlCommand("repair-steer", task.task_id, "failed-target", "steer", 1, "repair"),
    )
    assert (
        await service.ack_command(VALIDATION_SCOPE, "repair-steer", token, {"status": "applied"})
    )["applied_plan_revision"] == 2
    async with sessions() as session:
        rework_id = await session.scalar(
            select(workflow_node.c.node_id).where(
                workflow_node.c.task_id == task.task_id,
                workflow_node.c.parent_node_id == "failed-target",
            )
        )
        edges = (
            await session.execute(
                select(
                    workflow_dependency.c.plan_revision,
                    workflow_dependency.c.from_node_id,
                    workflow_dependency.c.to_node_id,
                ).where(workflow_dependency.c.task_id == task.task_id)
            )
        ).all()
        original_status = await session.scalar(
            select(workflow_node.c.status).where(workflow_node.c.node_id == "failed-target")
        )
    assert rework_id
    assert original_status == "failed"
    assert (1, "prerequisite", "failed-target") in edges
    assert (1, "failed-target", "successor") in edges
    assert (2, "prerequisite", rework_id) in edges
    assert (2, rework_id, "successor") in edges
    assert (2, "failed-target", rework_id) not in edges
    assert set(await store.ready_nodes(VALIDATION_SCOPE, task.task_id)) == {
        "independent",
        rework_id,
    }
