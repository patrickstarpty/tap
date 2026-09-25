import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.mysql_checkpointer import (
    MysqlGraphCheckpointer,
    graph_checkpoint,
    graph_checkpoint_write,
    graph_run,
    graph_settlement,
)
from tap.modules.ai.application.interaction_graph import (
    GraphVersionConflict,
    InteractionGraph,
    InteractionGraphState,
)
from tap.modules.ai.domain.graph_runs import GraphCheckpointUnavailable
from tap.modules.chat.adapters.mysql_conversations import (
    MysqlConversationRepository,
    turn_answer_evidence_snapshot,
)
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    GraphContextStatus,
    RetrievalSummary,
)
from tap.platform.db.schema import outbox
from tests.integration.test_conversation_persistence import _input


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
async def test_admission_wait_releases_worker_before_execute() -> None:
    executed = False

    async def authorized() -> bool:
        return True

    async def classify(_state):
        return {"reasoning_mode": "workflow"}

    async def admit(_state):
        return {"admitted": False, "waiting_reason": "human-confirmation"}

    async def execute(_state):
        nonlocal executed
        executed = True
        return {"result": {"unexpected": True}}

    state = await InteractionGraph(
        graph_version="wait-v1",
        state_schema_version=1,
        checkpointer=InMemorySaver(),
        classify=classify,
        admit=admit,
        execute=execute,
        authorize=authorized,
    ).start(run_id="run-wait", payload={}, execution_mode="durable")

    assert state["waiting_reason"] == "human-confirmation"
    assert executed is False


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


@pytest.mark.asyncio
async def test_mysql_checkpoint_rolls_back_when_outbox_write_fails(
    owned_project_mysql, monkeypatch
) -> None:
    async def fail_event(*_args, **_kwargs):
        raise RuntimeError("injected outbox failure")

    monkeypatch.setattr(
        "tap.modules.ai.adapters.mysql_checkpointer.write_project_event", fail_event
    )
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:

        async def authorized() -> bool:
            return True

        graph = _graph(
            checkpointer=MysqlGraphCheckpointer(sessions, scope=VALIDATION_SCOPE),
            calls=[],
            authorize=authorized,
        )

        with pytest.raises(RuntimeError, match="injected outbox failure"):
            await graph.start(
                run_id="run-outbox-rollback",
                payload={"objective": "Prove atomic graph persistence"},
                execution_mode="durable",
            )

        async with sessions() as session:
            run_count = await session.scalar(
                select(func.count())
                .select_from(graph_run)
                .where(graph_run.c.run_id == "run-outbox-rollback")
            )
            checkpoint_count = await session.scalar(
                select(func.count())
                .select_from(graph_checkpoint)
                .where(graph_checkpoint.c.run_id == "run-outbox-rollback")
            )
        assert run_count == 0
        assert checkpoint_count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_checkpoint_accepts_task_writes_before_parent_commit(
    owned_project_mysql, monkeypatch
) -> None:
    """LangGraph may submit task writes while the parent put is still in flight."""
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = ConversationService(
        MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
        scope=VALIDATION_SCOPE,
    )
    await service.create("chat-parent-race", "turn-parent-race", "request-parent-race", _input())
    claim = (await service.repository.claim_queued(limit=1))[0][1]
    saver = service.repository.graph_checkpointer(claim)
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {
        "graph_version": "fast-chat-v1",
        "state_schema_version": 1,
        "execution_mode": "inline",
        "reasoning_mode": "direct",
    }
    base_config = {
        "configurable": {
            "thread_id": claim.turn_id,
            "graph_version": "fast-chat-v1",
            "state_schema_version": 1,
            "execution_mode": "inline",
        }
    }
    write_config = {
        "configurable": {
            **base_config["configurable"],
            "checkpoint_id": checkpoint["id"],
        }
    }
    parent_missing = asyncio.Event()
    retry_parent = asyncio.Event()
    write_task = None
    missing_task = None

    async def controlled_parent_wait(_delay: float) -> None:
        parent_missing.set()
        await retry_parent.wait()

    monkeypatch.setattr(asyncio, "sleep", controlled_parent_wait)
    try:
        write_task = asyncio.create_task(
            saver.aput_writes(
                write_config,
                [("result", {"revisionId": "revision-race"})],
                "task-race",
            )
        )
        missing_task = asyncio.create_task(parent_missing.wait())
        completed, _ = await asyncio.wait(
            {write_task, missing_task},
            timeout=2,
            return_when=asyncio.FIRST_COMPLETED,
        )
        assert missing_task in completed, (
            "task writes failed instead of waiting for their in-flight parent: "
            f"{write_task.exception()!r}"
        )

        saved_config = await saver.aput(base_config, checkpoint, {"step": -1}, {})
        retry_parent.set()
        await asyncio.wait_for(write_task, timeout=2)

        saved = await saver.aget_tuple(saved_config)
        assert saved is not None
        assert saved.pending_writes == [("task-race", "result", {"revisionId": "revision-race"})]
        async with sessions() as session:
            write_count = await session.scalar(
                select(func.count())
                .select_from(graph_checkpoint_write)
                .where(graph_checkpoint_write.c.run_id == claim.turn_id)
            )
        assert write_count == 1
    finally:
        retry_parent.set()
        for task in (write_task, missing_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (write_task, missing_task) if task is not None),
            return_exceptions=True,
        )
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_checkpoint_replay_deduplicates_concurrent_task_writes_after_restart(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    first = MysqlGraphCheckpointer(sessions, scope=VALIDATION_SCOPE)
    second = MysqlGraphCheckpointer(sessions, scope=VALIDATION_SCOPE)
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {
        "graph_version": "concurrent-replay-v1",
        "state_schema_version": 1,
        "execution_mode": "durable",
        "reasoning_mode": "direct",
    }
    base_config = {
        "configurable": {
            "thread_id": "run-concurrent-replay",
            "graph_version": "concurrent-replay-v1",
            "state_schema_version": 1,
            "execution_mode": "durable",
        }
    }
    try:
        saved_config = await first.aput(base_config, checkpoint, {"step": -1}, {})
        writes = [("result", {"revisionId": "revision-once"})]

        await asyncio.gather(
            first.aput_writes(saved_config, writes, "task-replayed"),
            second.aput_writes(saved_config, writes, "task-replayed"),
        )

        restarted = MysqlGraphCheckpointer(sessions, scope=VALIDATION_SCOPE)
        saved = await restarted.aget_tuple(saved_config)
        assert saved is not None
        assert saved.pending_writes == [
            ("task-replayed", "result", {"revisionId": "revision-once"})
        ]
        async with sessions() as session:
            write_count = await session.scalar(
                select(func.count())
                .select_from(graph_checkpoint_write)
                .where(graph_checkpoint_write.c.run_id == "run-concurrent-replay")
            )
        assert write_count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_permanent_checkpoint_error_terminalizes_once_and_does_not_reclaim(
    owned_project_mysql,
) -> None:
    class PermanentResultFailure(InMemorySaver):
        async def aput_writes(self, config, writes, task_id, task_path=""):
            if config["configurable"]["thread_id"] == "turn-permanent" and any(
                channel == "result" for channel, _value in writes
            ):
                raise GraphCheckpointUnavailable("checkpoint row is invalid")
            await super().aput_writes(config, writes, task_id, task_path)

    class Knowledge:
        calls = []

        async def answer(self, request):
            self.calls.append(request.query)
            return SimpleNamespace(
                answer="grounded",
                citations=(),
                abstained=False,
                trace_id="trace",
                model_dump=lambda **_: {"answer": "grounded", "citations": []},
            )

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
            scope=VALIDATION_SCOPE,
        )
        await conversations.create(
            "chat-permanent", "turn-permanent", "request-permanent", _input("first")
        )
        await conversations.create(
            "chat-healthy", "turn-healthy", "request-healthy", _input("second")
        )
        knowledge = Knowledge()
        worker = GenerationWorker(
            conversations,
            knowledge,
            checkpointer=PermanentResultFailure(),
        )

        assert await worker.run_once(limit=2) == 2
        assert (await conversations.load("chat-permanent")).turns[0].state == "failed"
        assert (await conversations.load("chat-healthy")).turns[0].state == "completed"
        assert await conversations.repository.claim_queued(limit=2) == ()
        assert await worker.run_once(limit=2) == 0
        assert knowledge.calls == ["first", "second"]

        async with sessions() as session:
            failure_outbox_count = await session.scalar(
                select(func.count())
                .select_from(outbox)
                .where(
                    outbox.c.aggregate_id == "turn-permanent",
                    outbox.c.message_type == "conversation.turn.completed",
                )
            )
            leaked_answer_count = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM chat_event "
                    "WHERE turn_id='turn-permanent' AND event_type='answer.delta'"
                )
            )
            healthy_answer_count = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM chat_event "
                    "WHERE turn_id='turn-healthy' AND event_type='answer.delta'"
                )
            )
        assert failure_outbox_count == 1
        assert leaked_answer_count == 0
        assert healthy_answer_count == 1
    finally:
        await engine.dispose()


async def _completed_graph(checkpointer, run_id: str) -> None:
    async def authorized() -> bool:
        return True

    async def classify(_state):
        return {"reasoning_mode": "direct"}

    async def admit(_state):
        return {"admitted": True}

    async def execute(_state):
        return {"result": {"evidence": "ready"}}

    await InteractionGraph(
        graph_version="fast-chat-v1",
        state_schema_version=1,
        checkpointer=checkpointer,
        classify=classify,
        admit=admit,
        execute=execute,
        authorize=authorized,
    ).start(run_id=run_id, payload={"turnId": run_id}, execution_mode="inline")


@pytest.mark.asyncio
async def test_chat_terminal_business_graph_and_outbox_settle_atomically(
    owned_project_mysql, monkeypatch
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        service = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
            scope=VALIDATION_SCOPE,
        )
        await service.create("chat-atomic", "turn-atomic", "request-atomic", _input())
        claim = (await service.repository.claim_queued(limit=1))[0][1]
        await _completed_graph(service.repository.graph_checkpointer(claim), claim.turn_id)

        async def fail_event(*_args, **_kwargs):
            raise RuntimeError("injected chat settlement outbox failure")

        monkeypatch.setattr(
            "tap.modules.chat.adapters.mysql_conversations.write_project_event", fail_event
        )
        with pytest.raises(RuntimeError, match="chat settlement"):
            await service.complete_evidence(
                "chat-atomic",
                claim.turn_id,
                AnswerEvidence(
                    "grounded",
                    "completed",
                    RetrievalSummary("completed"),
                    GraphContextStatus.NOT_REQUESTED,
                ),
                lease_token=claim.lease_token,
                terminal_event=("turn.completed", {"answer": {"answer": "grounded"}}),
            )

        async with sessions() as session:
            turn_state = await session.scalar(
                text("SELECT state FROM chat_turn WHERE turn_id='turn-atomic'")
            )
            snapshot_count = await session.scalar(
                select(func.count())
                .select_from(turn_answer_evidence_snapshot)
                .where(turn_answer_evidence_snapshot.c.turn_id == "turn-atomic")
            )
            graph_state = await session.scalar(
                select(graph_run.c.status).where(graph_run.c.run_id == "turn-atomic")
            )
            settlement_count = await session.scalar(
                select(func.count())
                .select_from(graph_settlement)
                .where(graph_settlement.c.run_id == "turn-atomic")
            )
        assert turn_state == "running"
        assert snapshot_count == 0
        assert graph_state == "RUNNING"
        assert settlement_count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_chat_checkpoint_is_fenced_and_cancel_releases_graph_lease(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        service = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
            scope=VALIDATION_SCOPE,
        )
        await service.create("chat-fence", "turn-fence", "request-fence", _input())
        stale = (await service.repository.claim_queued(limit=1))[0][1]
        stale_checkpointer = service.repository.graph_checkpointer(stale)
        async with sessions() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE chat_turn SET processing_lease_expires_at="
                    "UTC_TIMESTAMP(6)-INTERVAL 1 SECOND WHERE turn_id='turn-fence'"
                )
            )
        current = (await service.repository.claim_queued(limit=1))[0][1]

        with pytest.raises(PermissionError, match="fencing token"):
            await _completed_graph(stale_checkpointer, stale.turn_id)

        await _completed_graph(service.repository.graph_checkpointer(current), current.turn_id)
        await service.cancel("chat-fence", current.turn_id)
        async with sessions() as session:
            row = (
                (
                    await session.execute(
                        select(graph_run).where(graph_run.c.run_id == current.turn_id)
                    )
                )
                .mappings()
                .one()
            )
        assert row["status"] == "CANCELLED"
        assert row["lease_owner"] is None
        assert row["lease_token"] is None
        assert row["lease_until"] is None
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(graph_settlement)
                    .where(graph_settlement.c.run_id == current.turn_id)
                )
                == 1
            )
    finally:
        await engine.dispose()
