import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.interfaces.http.knowledge_service import KnowledgeHttpService
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
from tap.modules.ai.domain.graph_runs import GraphCheckpointRetryable, GraphCheckpointUnavailable
from tap.modules.chat.adapters.mysql_conversations import (
    MysqlConversationRepository,
    chat_turn,
    turn_answer_evidence_snapshot,
)
from tap.modules.chat.application.conversations import ConversationService
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    FrozenResource,
    GraphContextStatus,
    RetrievalSummary,
    content_digest,
)
from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.knowledge.adapters.mysql_review import MysqlKnowledgeReviewRepository
from tap.modules.knowledge.application.answers import AnswerService
from tap.modules.knowledge.application.citations import CitationResolver
from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority
from tap.modules.knowledge.application.review import KnowledgeReviewApplication
from tap.platform.db.schema import outbox
from tests.integration.test_conversation_persistence import _input
from tests.integration.test_knowledge_publication import (
    NOW,
    ReadyProjection,
    approved_review,
    seed_authority,
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
async def test_completed_checkpoint_resume_rechecks_authorization_without_reexecuting() -> None:
    calls: list[str] = []
    allowed = True

    async def authorized() -> bool:
        return allowed

    graph = _graph(checkpointer=InMemorySaver(), calls=calls, authorize=authorized)
    await graph.start(run_id="run-completed-revoked", payload={}, execution_mode="durable")
    allowed = False

    with pytest.raises(PermissionError, match="authorization changed"):
        await graph.resume(run_id="run-completed-revoked")

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
        MysqlConversationRepository(
            sessions, scope=VALIDATION_SCOPE, default_chat_model="qwen-plus"
        ),
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
            MysqlConversationRepository(
                sessions, scope=VALIDATION_SCOPE, default_chat_model="qwen-plus"
            ),
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


@pytest.mark.asyncio
async def test_reclaim_takes_over_completed_checkpoint_lease_without_repeating_provider(
    owned_project_mysql, monkeypatch
) -> None:
    failed = False
    original_aput_writes = MysqlGraphCheckpointer.aput_writes

    async def fail_once_after_durable_write(self, config, writes, task_id, task_path=""):
        nonlocal failed
        await original_aput_writes(self, config, writes, task_id, task_path)
        if not failed and any(channel == "result" for channel, _value in writes):
            failed = True
            raise GraphCheckpointRetryable("connection lost after durable task write")

    monkeypatch.setattr(MysqlGraphCheckpointer, "aput_writes", fail_once_after_durable_write)

    class Knowledge:
        calls = 0

        async def answer(self, _request):
            self.calls += 1
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
            MysqlConversationRepository(
                sessions, scope=VALIDATION_SCOPE, default_chat_model="qwen-plus"
            ),
            scope=VALIDATION_SCOPE,
        )
        await conversations.create("chat-takeover", "turn-takeover", "request-takeover", _input())
        knowledge = Knowledge()
        worker = GenerationWorker(conversations, knowledge)

        assert await worker.run_once(limit=1) == 1
        first = (await conversations.load("chat-takeover")).turns[0]
        assert first.state == "running"
        assert first.attempt == 1
        assert knowledge.calls == 1

        async with sessions() as session, session.begin():
            graph_before = (
                (
                    await session.execute(
                        select(graph_run).where(graph_run.c.run_id == "turn-takeover")
                    )
                )
                .mappings()
                .one()
            )
            assert graph_before["status"] == "RUNNING"
            assert graph_before["current_checkpoint_id"] is not None
            assert graph_before["lease_token"] == first.lease_token
            await session.execute(
                update(chat_turn)
                .where(chat_turn.c.turn_id == "turn-takeover")
                .values(
                    processing_lease_expires_at=func.utc_timestamp() - text("INTERVAL 1 SECOND")
                )
            )
            await session.execute(
                update(graph_run)
                .where(graph_run.c.run_id == "turn-takeover")
                .values(lease_until=func.utc_timestamp() - text("INTERVAL 1 SECOND"))
            )

        reclaimed = (await conversations.repository.claim_queued(limit=1))[0]
        assert reclaimed[1].attempt == 2
        async with sessions() as session:
            reclaimed_turn_token = await session.scalar(
                select(chat_turn.c.processing_lease_token).where(
                    chat_turn.c.turn_id == "turn-takeover"
                )
            )
            reclaimed_graph_token = await session.scalar(
                select(graph_run.c.lease_token).where(graph_run.c.run_id == "turn-takeover")
            )
        assert reclaimed_turn_token == reclaimed[1].lease_token
        assert reclaimed_graph_token == reclaimed[1].lease_token

        class ClaimedRepository:
            def __init__(self, delegate, claimed):
                self.delegate = delegate
                self.claimed = claimed

            async def claim_queued(self, *, limit):
                del limit
                claimed, self.claimed = self.claimed, None
                return () if claimed is None else (claimed,)

            def __getattr__(self, name):
                return getattr(self.delegate, name)

        durable_repository = conversations.repository
        conversations.repository = ClaimedRepository(durable_repository, reclaimed)
        assert await worker.run_once(limit=1) == 1
        completed = (await conversations.load("chat-takeover")).turns[0]
        assert completed.state == "completed"
        assert completed.attempt == 2
        assert knowledge.calls == 1
        assert await durable_repository.claim_queued(limit=1) == ()
        assert await worker.run_once(limit=1) == 0

        async with sessions() as session:
            graph_after = (
                (
                    await session.execute(
                        select(graph_run).where(graph_run.c.run_id == "turn-takeover")
                    )
                )
                .mappings()
                .one()
            )
            completion_outbox_count = await session.scalar(
                select(func.count())
                .select_from(outbox)
                .where(
                    outbox.c.aggregate_id == "turn-takeover",
                    outbox.c.message_type == "conversation.turn.completed",
                )
            )
        assert graph_after["status"] == "SUCCEEDED"
        assert graph_after["lease_token"] is None
        assert completion_outbox_count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("publication_state", ["withdrawn", "expired", "revision_changed", "valid"])
async def test_reclaimed_result_requires_current_publication_before_delivery(
    owned_project_mysql, publication_state: str
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await seed_authority(sessions)
        review = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
        await review.create_review(approved_review())
        publication = await KnowledgeReviewApplication(review, ReadyProjection()).publish_review(
            "krv_mysql_001",
            generation="generation-001",
            idempotency_key="publish-recovery",
            actor_id="synthetic-reviewer-02",
            expected_version=4,
            now=NOW,
        )
        documents = MysqlDocumentRepository(
            sessions, scope=VALIDATION_SCOPE, audit_factory=lambda _: None
        )
        authority = PublishedKnowledgeAuthority(review)

        class Knowledge(KnowledgeHttpService):
            calls = 0

            async def answer_conversation(self, *_args, **_kwargs):
                self.calls += 1
                return SimpleNamespace(
                    answer="grounded secret",
                    citations=(),
                    abstained=False,
                    trace_id="trace-recovery",
                    model_dump=lambda **_: {"answer": "grounded secret", "citations": []},
                )

        knowledge = Knowledge(
            documents=documents,
            answers=AnswerService(
                repository=documents, knowledge=object(), publication_authority=authority
            ),
            citations=CitationResolver(
                repository=documents,
                artifacts=object(),
                publication_authority=authority,
            ),
        )
        conversations = ConversationService(
            MysqlConversationRepository(
                sessions, scope=VALIDATION_SCOPE, default_chat_model="qwen-plus"
            ),
            scope=VALIDATION_SCOPE,
        )
        _revisions, policy = await knowledge.resolve_conversation_selection(("rev_mysql_001",))
        frozen = replace(
            _input(),
            acl_digest=policy.acl_digest,
            retrieval_policy_digest=content_digest(
                {
                    "decisionId": policy.decision_id,
                    "policyVersion": policy.policy_version,
                    "corpusVersion": policy.active_corpus_version,
                }
            ),
            source_revision_ids=("rev_mysql_001",),
            document_revision_ids=("rev_mysql_001",),
            resolved_resources=(
                FrozenResource(
                    source_id="src_" + "2" * 32,
                    document_id="doc_mysql_publication",
                    revision_id="rev_mysql_001",
                    source_content_hash="sha256:" + "a" * 64,
                ),
            ),
        )
        await conversations.create(
            "chat-publication-recovery",
            "turn-publication-recovery",
            "request-publication-recovery",
            frozen,
        )
        worker = GenerationWorker(conversations, knowledge)
        settle = conversations.complete_evidence

        async def crash_before_settle(*_args, **_kwargs):
            conversations.complete_evidence = settle
            raise RuntimeError("worker stopped after result checkpoint")

        conversations.complete_evidence = crash_before_settle
        with pytest.raises(RuntimeError, match="worker stopped"):
            await worker.run_once(limit=1)
        first = (await conversations.load("chat-publication-recovery")).turns[0]
        assert first.state == "running"
        persisted = await conversations.repository.graph_checkpointer(first).aget_tuple(
            {"configurable": {"thread_id": first.turn_id}}
        )
        assert persisted is not None
        assert persisted.checkpoint["channel_values"]["result"]["evidence"]["answer"] == (
            "grounded secret"
        )
        assert knowledge.calls == 1

        if publication_state == "withdrawn":
            await KnowledgeReviewApplication(review, ReadyProjection()).withdraw_publication(
                publication.publication_id,
                idempotency_key="withdraw-recovery",
                actor_id="synthetic-reviewer-02",
                expected_version=1,
                now=NOW + timedelta(seconds=1),
            )
        elif publication_state == "expired":
            from tap.modules.knowledge.adapters.mysql_review import knowledge_publication

            async with sessions() as session, session.begin():
                await session.execute(
                    update(knowledge_publication)
                    .where(knowledge_publication.c.publication_id == publication.publication_id)
                    .values(expires_at=NOW + timedelta(seconds=1))
                )
        elif publication_state == "revision_changed":
            from tap.modules.knowledge.adapters.mysql_documents import knowledge_document

            async with sessions() as session, session.begin():
                await session.execute(
                    update(knowledge_document)
                    .where(knowledge_document.c.document_id == "doc_mysql_publication")
                    .values(current_revision_id=None)
                )
        async with sessions() as session, session.begin():
            await session.execute(
                update(chat_turn)
                .where(chat_turn.c.turn_id == "turn-publication-recovery")
                .values(
                    processing_lease_expires_at=func.utc_timestamp() - text("INTERVAL 1 SECOND")
                )
            )
            await session.execute(
                update(graph_run)
                .where(graph_run.c.run_id == "turn-publication-recovery")
                .values(lease_until=func.utc_timestamp() - text("INTERVAL 1 SECOND"))
            )

        if publication_state != "valid":
            await conversations.create(
                "chat-after-denial", "turn-after-denial", "request-after-denial", _input("next")
            )
        assert await worker.run_once(limit=2) == (1 if publication_state == "valid" else 2)
        resumed = (await conversations.load("chat-publication-recovery")).turns[0]
        assert resumed.state == ("completed" if publication_state == "valid" else "failed")
        if publication_state != "valid":
            assert (await conversations.load("chat-after-denial")).turns[0].state == "completed"
        assert knowledge.calls == (1 if publication_state == "valid" else 2)
        async with sessions() as session:
            events = (
                (
                    await session.execute(
                        text(
                            "SELECT event_type FROM chat_event "
                            "WHERE turn_id='turn-publication-recovery'"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert ("answer.delta" in events) == (publication_state == "valid")
        assert ("turn.completed" in events) == (publication_state == "valid")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_graph_lease_reclaim_and_cancel_have_one_terminal_winner(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        conversations = ConversationService(
            MysqlConversationRepository(
                sessions, scope=VALIDATION_SCOPE, default_chat_model="qwen-plus"
            ),
            scope=VALIDATION_SCOPE,
        )
        await conversations.create("chat-race", "turn-race", "request-race", _input())
        first = (await conversations.repository.claim_queued(limit=1))[0][1]
        await _completed_graph(conversations.repository.graph_checkpointer(first), first.turn_id)
        async with sessions() as session, session.begin():
            await session.execute(
                update(chat_turn)
                .where(chat_turn.c.turn_id == first.turn_id)
                .values(
                    processing_lease_expires_at=func.utc_timestamp() - text("INTERVAL 1 SECOND")
                )
            )
            await session.execute(
                update(graph_run)
                .where(graph_run.c.run_id == first.turn_id)
                .values(lease_until=func.utc_timestamp() - text("INTERVAL 1 SECOND"))
            )

        reclaimed, canceled = await asyncio.gather(
            conversations.repository.claim_queued(limit=1),
            conversations.cancel("chat-race", first.turn_id),
        )
        assert len(reclaimed) <= 1
        assert canceled.state == "canceled"
        assert (await conversations.load("chat-race")).turns[0].state == "canceled"
        assert await conversations.repository.claim_queued(limit=1) == ()
        async with sessions() as session:
            graph = (
                (
                    await session.execute(
                        select(graph_run).where(graph_run.c.run_id == first.turn_id)
                    )
                )
                .mappings()
                .one()
            )
        assert graph["status"] == "CANCELLED"
        assert graph["lease_token"] is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_waiting_graph_stays_parked_without_blocking_later_queued_turn(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        conversations = ConversationService(
            MysqlConversationRepository(
                sessions, scope=VALIDATION_SCOPE, default_chat_model="qwen-plus"
            ),
            scope=VALIDATION_SCOPE,
        )
        await conversations.create("chat-wait", "turn-wait", "request-wait", _input())
        first = (await conversations.repository.claim_queued(limit=1))[0][1]

        async def authorized() -> bool:
            return True

        async def classify(_state):
            return {"reasoning_mode": "workflow"}

        async def admit(_state):
            return {"admitted": False, "waiting_reason": "human-confirmation"}

        async def execute(_state):
            raise AssertionError("waiting graph must not execute")

        state = await InteractionGraph(
            graph_version="wait-takeover-v1",
            state_schema_version=1,
            checkpointer=conversations.repository.graph_checkpointer(first),
            classify=classify,
            admit=admit,
            execute=execute,
            authorize=authorized,
        ).start(run_id=first.turn_id, payload={}, execution_mode="durable")
        assert state["waiting_reason"] == "human-confirmation"

        async with sessions() as session, session.begin():
            waiting = (
                (
                    await session.execute(
                        select(graph_run).where(graph_run.c.run_id == first.turn_id)
                    )
                )
                .mappings()
                .one()
            )
            assert waiting["status"] == "WAITING"
            assert waiting["lease_token"] is None
            await session.execute(
                update(chat_turn)
                .where(chat_turn.c.turn_id == first.turn_id)
                .values(
                    processing_lease_expires_at=func.utc_timestamp() - text("INTERVAL 1 SECOND")
                )
            )
        await conversations.create(
            "chat-after-wait", "turn-after-wait", "request-after-wait", _input()
        )
        claimed = await conversations.repository.claim_queued(limit=1)
        assert len(claimed) == 1
        assert claimed[0][1].turn_id == "turn-after-wait"
        async with sessions() as session:
            parked = (
                (
                    await session.execute(
                        select(graph_run).where(graph_run.c.run_id == first.turn_id)
                    )
                )
                .mappings()
                .one()
            )
        assert parked["status"] == "WAITING"
        assert parked["waiting_reason"] == "human-confirmation"
        assert parked["lease_token"] is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_active_graph_candidate_does_not_starve_later_queued_turn(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        conversations = ConversationService(
            MysqlConversationRepository(
                sessions, scope=VALIDATION_SCOPE, default_chat_model="qwen-plus"
            ),
            scope=VALIDATION_SCOPE,
        )
        await conversations.create("chat-blocked", "turn-blocked", "request-blocked", _input())
        blocked = (await conversations.repository.claim_queued(limit=1))[0][1]
        await _completed_graph(
            conversations.repository.graph_checkpointer(blocked), blocked.turn_id
        )
        await conversations.create("chat-healthy", "turn-healthy", "request-healthy", _input())
        async with sessions() as session, session.begin():
            await session.execute(
                update(chat_turn)
                .where(chat_turn.c.turn_id == blocked.turn_id)
                .values(
                    processing_lease_expires_at=func.utc_timestamp() - text("INTERVAL 1 SECOND")
                )
            )

        claimed = await conversations.repository.claim_queued(limit=1)
        assert len(claimed) == 1
        assert claimed[0][1].turn_id == "turn-healthy"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_reclaimers_transfer_expired_graph_lease_once(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        first_repository = MysqlConversationRepository(
            sessions, scope=VALIDATION_SCOPE, default_chat_model="qwen-plus"
        )
        second_repository = MysqlConversationRepository(
            sessions, scope=VALIDATION_SCOPE, default_chat_model="qwen-plus"
        )
        conversations = ConversationService(first_repository, scope=VALIDATION_SCOPE)
        await conversations.create(
            "chat-reclaimers", "turn-reclaimers", "request-reclaimers", _input()
        )
        first = (await first_repository.claim_queued(limit=1))[0][1]
        await _completed_graph(first_repository.graph_checkpointer(first), first.turn_id)
        async with sessions() as session, session.begin():
            await session.execute(
                update(chat_turn)
                .where(chat_turn.c.turn_id == first.turn_id)
                .values(
                    processing_lease_expires_at=func.utc_timestamp() - text("INTERVAL 1 SECOND")
                )
            )
            await session.execute(
                update(graph_run)
                .where(graph_run.c.run_id == first.turn_id)
                .values(lease_until=func.utc_timestamp() - text("INTERVAL 1 SECOND"))
            )

        claims = await asyncio.gather(
            first_repository.claim_queued(limit=1),
            second_repository.claim_queued(limit=1),
        )
        winners = tuple(item for batch in claims for item in batch)
        assert len(winners) == 1
        assert winners[0][1].attempt == 2
        async with sessions() as session:
            graph_token = await session.scalar(
                select(graph_run.c.lease_token).where(graph_run.c.run_id == first.turn_id)
            )
        assert graph_token == winners[0][1].lease_token
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
            MysqlConversationRepository(
                sessions, scope=VALIDATION_SCOPE, default_chat_model="qwen-plus"
            ),
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
            MysqlConversationRepository(
                sessions, scope=VALIDATION_SCOPE, default_chat_model="qwen-plus"
            ),
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
