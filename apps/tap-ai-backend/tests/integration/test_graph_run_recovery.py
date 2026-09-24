from collections.abc import Awaitable, Callable

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.mysql_checkpointer import (
    MysqlGraphCheckpointer,
    graph_checkpoint,
    graph_run,
)
from tap.modules.ai.application.interaction_graph import (
    GraphVersionConflict,
    InteractionGraph,
    InteractionGraphState,
)


def _graph(
    *,
    checkpointer: InMemorySaver,
    calls: list[str],
    authorize: Callable[[], Awaitable[bool]],
    fail_execute_once: bool = False,
    graph_version: str = "test-design-v1",
) -> InteractionGraph:
    failed = False

    async def classify(state: InteractionGraphState) -> dict[str, object]:
        calls.append("classify")
        return {"reasoning_mode": "workflow"}

    async def admit(state: InteractionGraphState) -> dict[str, object]:
        calls.append("admit")
        return {"admitted": True}

    async def execute(state: InteractionGraphState) -> dict[str, object]:
        nonlocal failed
        calls.append("execute")
        if fail_execute_once and not failed:
            failed = True
            raise RuntimeError("worker interrupted after durable admission")
        return {"result": {"revisionId": "revision-1"}}

    return InteractionGraph(
        graph_version=graph_version,
        state_schema_version=1,
        checkpointer=checkpointer,
        classify=classify,
        admit=admit,
        execute=execute,
        authorize=authorize,
    )


@pytest.mark.asyncio
async def test_failed_node_resumes_without_repeating_completed_nodes() -> None:
    calls: list[str] = []

    async def authorized() -> bool:
        return True

    graph = _graph(
        checkpointer=InMemorySaver(),
        calls=calls,
        authorize=authorized,
        fail_execute_once=True,
    )
    with pytest.raises(RuntimeError, match="worker interrupted"):
        await graph.start(
            run_id="run-restart",
            payload={"objective": "Generate the governed draft"},
            execution_mode="durable",
        )

    result = await graph.resume(run_id="run-restart")

    assert result["result"] == {"revisionId": "revision-1"}
    assert calls == ["classify", "admit", "execute", "execute"]


@pytest.mark.asyncio
async def test_resume_rechecks_authorization_before_retrying_failed_node() -> None:
    calls: list[str] = []
    allowed = True

    async def authorized() -> bool:
        return allowed

    graph = _graph(
        checkpointer=InMemorySaver(),
        calls=calls,
        authorize=authorized,
        fail_execute_once=True,
    )
    with pytest.raises(RuntimeError, match="worker interrupted"):
        await graph.start(
            run_id="run-revoked",
            payload={"objective": "Generate the governed draft"},
            execution_mode="durable",
        )
    allowed = False

    with pytest.raises(PermissionError, match="authorization changed"):
        await graph.resume(run_id="run-revoked")

    assert calls == ["classify", "admit", "execute"]


@pytest.mark.asyncio
async def test_new_graph_version_cannot_take_over_existing_checkpoint() -> None:
    calls: list[str] = []

    async def authorized() -> bool:
        return True

    checkpointer = InMemorySaver()
    first = _graph(
        checkpointer=checkpointer,
        calls=calls,
        authorize=authorized,
        fail_execute_once=True,
    )
    with pytest.raises(RuntimeError, match="worker interrupted"):
        await first.start(
            run_id="run-versioned",
            payload={"objective": "Generate the governed draft"},
            execution_mode="durable",
        )

    replacement = _graph(
        checkpointer=checkpointer,
        calls=calls,
        authorize=authorized,
        graph_version="test-design-v2",
    )
    with pytest.raises(GraphVersionConflict, match="test-design-v1"):
        await replacement.resume(run_id="run-versioned")


@pytest.mark.asyncio
async def test_mysql_checkpoint_and_outbox_survive_runtime_recreation(
    owned_project_mysql,
) -> None:
    calls: list[str] = []

    async def authorized() -> bool:
        return True

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        first = _graph(
            checkpointer=MysqlGraphCheckpointer(sessions, scope=VALIDATION_SCOPE),
            calls=calls,
            authorize=authorized,
            fail_execute_once=True,
        )
        with pytest.raises(RuntimeError, match="worker interrupted"):
            await first.start(
                run_id="run-mysql-restart",
                payload={"objective": "Generate the governed draft"},
                execution_mode="durable",
            )

        resumed = _graph(
            checkpointer=MysqlGraphCheckpointer(sessions, scope=VALIDATION_SCOPE),
            calls=calls,
            authorize=authorized,
        )
        result = await resumed.resume(run_id="run-mysql-restart")

        assert result["result"] == {"revisionId": "revision-1"}
        assert calls == ["classify", "admit", "execute", "execute"]
        async with sessions() as session:
            run_row = (
                (
                    await session.execute(
                        select(graph_run).where(graph_run.c.run_id == "run-mysql-restart")
                    )
                )
                .mappings()
                .one()
            )
            checkpoint_count = await session.scalar(
                select(func.count())
                .select_from(graph_checkpoint)
                .where(graph_checkpoint.c.run_id == "run-mysql-restart")
            )
        assert run_row["graph_version"] == "test-design-v1"
        assert run_row["state_schema_version"] == 1
        assert run_row["status"] == "SUCCEEDED"
        assert run_row["current_checkpoint_id"]
        assert checkpoint_count and checkpoint_count >= 4
    finally:
        await engine.dispose()
