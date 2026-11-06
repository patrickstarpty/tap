"""Owned-MySQL checks for public workflow scope and cursor reads."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.mysql import project
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.adapters.mysql import MysqlWorkflowStore
from tap.modules.workflows.adapters.schema import (
    workflow_artifact,
    workflow_attempt,
    workflow_event,
    workflow_node,
    workflow_task,
)
from tap.modules.workflows.application.queries import SnapshotRequired, WorkflowQueries
from tap.platform.db.project_scope import scope_values
from tests.owned_mysql import owned_project_database_url


@pytest.mark.asyncio
async def test_scoped_agent_page_and_node_attempt_artifact_visibility(owned_project_mysql):
    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    other = ProjectScopeContext(
        enterprise_id=VALIDATION_SCOPE.enterprise_id,
        project_id="other-project",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode=VALIDATION_SCOPE.identity_mode,
    )
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = MysqlWorkflowStore(sessions)
        first = await store.create_task(
            VALIDATION_SCOPE, {"goal": "Review", "origin": "native"}, "http-1"
        )
        second = await store.create_task(
            VALIDATION_SCOPE, {"goal": "Review", "origin": "native"}, "http-2"
        )
        async with sessions.begin() as session:
            await session.execute(
                project.insert().values(enterprise_id="local", project_id="other-project")
            )
        hidden = await store.create_task(other, {"goal": "Hidden", "origin": "native"}, "http-3")
        now = datetime.now(UTC).replace(tzinfo=None)
        async with sessions.begin() as session:
            for task, node_id, role, scope in (
                (first, "n1", "qae", VALIDATION_SCOPE),
                (first, "n2", "qae", VALIDATION_SCOPE),
                (second, "n3", "developer", VALIDATION_SCOPE),
                (hidden, "n4", "qae", other),
            ):
                await session.execute(
                    workflow_node.insert().values(
                        node_id=node_id,
                        task_id=task.task_id,
                        plan_revision=1,
                        parent_node_id=None,
                        agent_revision_id=None,
                        role=role,
                        kind="agent",
                        runtime="native",
                        status="succeeded",
                        attempt=1,
                        created_at=now,
                        **scope_values(scope),
                    )
                )
                await session.execute(
                    workflow_attempt.insert().values(
                        attempt_id=f"attempt-{node_id}",
                        task_id=task.task_id,
                        node_id=node_id,
                        attempt=1,
                        status="succeeded",
                        created_at=now,
                        **scope_values(scope),
                    )
                )
            await session.execute(
                workflow_artifact.insert().values(
                    artifact_id="artifact-n1",
                    task_id=first.task_id,
                    node_id="n1",
                    attempt=1,
                    object_ref="/private/secret/path",
                    content_digest="sha256:abc",
                    version_ref="version-1",
                    created_at=now,
                    **scope_values(VALIDATION_SCOPE),
                )
            )
        rows = await WorkflowQueries(store).list(VALIDATION_SCOPE, 20, None, "qae")
        assert [row["taskId"] for row in rows["items"]] == [first.task_id]
        assert {run["nodeId"] for run in rows["items"][0]["matchingRuns"]} == {"n1", "n2"}
        detail = await WorkflowQueries(store).node(VALIDATION_SCOPE, first.task_id, "n1")
        assert detail["artifacts"][0]["contentDigest"] == "sha256:abc"
        assert "/private/secret/path" not in str(detail)
        with pytest.raises(LookupError):
            await WorkflowQueries(store).snapshot(VALIDATION_SCOPE, hidden.task_id)
        with pytest.raises(LookupError):
            await WorkflowQueries(store).node(VALIDATION_SCOPE, first.task_id, "n3")
        with pytest.raises(LookupError):
            await WorkflowQueries(store).logs(VALIDATION_SCOPE, first.task_id, "n3", 1, 0, 50)
        with pytest.raises(LookupError):
            await WorkflowQueries(store).logs(VALIDATION_SCOPE, first.task_id, "n1", 2, 0, 50)
        assert (await store.node_logs(VALIDATION_SCOPE, first.task_id, "n1", 1, 0, 50))[0] == ()
        queries = WorkflowQueries(store)
        baseline = await queries.snapshot(VALIDATION_SCOPE, first.task_id)
        assert baseline["lastSequence"] == 1
        async with sessions.begin() as session:
            await session.execute(
                workflow_event.insert().values(
                    event_id="private-event",
                    task_id=first.task_id,
                    sequence=2,
                    node_id="n1",
                    attempt=1,
                    kind="action.settled",
                    payload={"result": {"output": "secret"}},
                    payload_digest="sha256:" + "a" * 64,
                    occurred_at=now,
                    **scope_values(VALIDATION_SCOPE),
                )
            )
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == first.task_id)
                .values(next_sequence=3)
            )
            assert await queries.events(VALIDATION_SCOPE, first.task_id, 1) == ()
        replay = await queries.events(VALIDATION_SCOPE, first.task_id, baseline["lastSequence"])
        assert [(item["sequence"], item["kind"], item["payload"]) for item in replay] == [
            (2, "activity", {})
        ]
        assert len((await queries.snapshot(VALIDATION_SCOPE, first.task_id))["nodes"]) == 2
        logs = await queries.logs(VALIDATION_SCOPE, first.task_id, "n1", 1, 1, 50)
        assert [item["sequence"] for item in logs["items"]] == [2]
        assert "secret" not in str(logs)
        async with sessions.begin() as session:
            await session.execute(
                delete(workflow_event).where(workflow_event.c.event_id == "private-event")
            )
        with pytest.raises(SnapshotRequired):
            await queries.events(VALIDATION_SCOPE, first.task_id, 1)
    finally:
        await engine.dispose()
