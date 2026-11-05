from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.mysql_checkpointer import graph_run
from tap.modules.ai.domain.models import ModelGatewayUnavailable
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository, chat_turn
from tap.modules.chat.application.conversations import ConversationConflict, ConversationService
from tests.integration.test_conversation_persistence import _input
from tests.integration.test_graph_run_recovery import _completed_graph


class _CountingKnowledge:
    """A `ConversationAnswerSteps` stand-in that counts `prepare`/`generate`
    invocations and fails `generate_conversation_answer` the first
    `fail_times` calls with a transient `ModelGatewayUnavailable`, so tests
    can prove the retried turn resumes from the checkpointed `prepare` step
    instead of repeating retrieval."""

    def __init__(self, *, fail_times: int) -> None:
        self.prepare_calls = 0
        self.generate_calls = 0
        self._fail_times = fail_times

    async def prepare_conversation_answer(self, _request, _frozen_input, **_kwargs):
        self.prepare_calls += 1
        return {"kind": "prepared", "bundle": "stub-bundle"}

    async def generate_conversation_answer(self, step, _frozen_input, **_kwargs):
        self.generate_calls += 1
        if self.generate_calls <= self._fail_times:
            raise ModelGatewayUnavailable()
        return {"kind": "generated", "bundle": step["bundle"]}

    async def verify_conversation_answer(self, step, _frozen_input, **_kwargs):
        return {"kind": "verified", "bundle": step["bundle"]}

    async def finalize_conversation_answer(self, _step, _frozen_input, **_kwargs):
        return SimpleNamespace(
            answer="grounded",
            citations=(),
            abstained=False,
            trace_id="trace",
            model_dump=lambda **_: {"answer": "grounded", "citations": []},
        )


@pytest.mark.asyncio
async def test_deferred_turn_is_not_claimed_before_next_attempt_at(owned_project_mysql) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
            scope=VALIDATION_SCOPE,
        )
        await conversations.create("chat-defer", "turn-defer", "request-defer", _input())
        claimed = (await conversations.repository.claim_queued(limit=1))[0][1]
        assert claimed.attempt == 1

        future = datetime.now(timezone.utc) + timedelta(hours=1)
        await conversations.repository.defer_turn(
            "chat-defer",
            "turn-defer",
            claimed.lease_token,
            next_attempt_at=future,
            failure="transient:ModelGatewayUnavailable",
        )

        assert await conversations.repository.claim_queued(limit=10) == ()

        async with sessions() as session, session.begin():
            await session.execute(
                update(chat_turn)
                .where(chat_turn.c.turn_id == "turn-defer")
                .values(next_attempt_at=func.utc_timestamp(6) - text("INTERVAL 1 SECOND"))
            )

        reclaimed = (await conversations.repository.claim_queued(limit=10))[0][1]
        assert reclaimed.turn_id == "turn-defer"
        assert reclaimed.attempt == 2

        async with sessions() as session:
            row = (
                (
                    await session.execute(
                        select(chat_turn).where(chat_turn.c.turn_id == "turn-defer")
                    )
                )
                .mappings()
                .one()
            )
        assert row["next_attempt_at"] is None
        # `claim_queued` clears `last_failure` along with `next_attempt_at`: this
        # new attempt's own outcome (another defer, success, or a silent crash)
        # should decide what the row reports next, not a stale class carried over
        # from the previous attempt (a silent crash with no explicit `defer_turn`
        # call must read as `lease-expired`, not an old transient reason).
        assert row["last_failure"] is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_defer_requires_the_current_lease_token(owned_project_mysql) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
            scope=VALIDATION_SCOPE,
        )
        await conversations.create(
            "chat-defer-wrong", "turn-defer-wrong", "request-defer-wrong", _input()
        )
        await conversations.repository.claim_queued(limit=1)

        with pytest.raises(ConversationConflict):
            await conversations.repository.defer_turn(
                "chat-defer-wrong",
                "turn-defer-wrong",
                "not-the-current-lease-token",
                next_attempt_at=datetime.now(timezone.utc) + timedelta(hours=1),
                failure="transient:ModelGatewayUnavailable",
            )

        async with sessions() as session:
            row = (
                (
                    await session.execute(
                        select(chat_turn).where(chat_turn.c.turn_id == "turn-defer-wrong")
                    )
                )
                .mappings()
                .one()
            )
        assert row["state"] == "running"
        assert row["next_attempt_at"] is None
        assert row["last_failure"] is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_defer_expires_the_graph_run_lease(owned_project_mysql) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
            scope=VALIDATION_SCOPE,
        )
        await conversations.create(
            "chat-defer-graph", "turn-defer-graph", "request-defer-graph", _input()
        )
        claimed = (await conversations.repository.claim_queued(limit=1))[0][1]
        await _completed_graph(
            conversations.repository.graph_checkpointer(claimed), claimed.turn_id
        )

        await conversations.repository.defer_turn(
            "chat-defer-graph",
            "turn-defer-graph",
            claimed.lease_token,
            next_attempt_at=datetime.now(timezone.utc) + timedelta(hours=1),
            failure="transient:ModelGatewayUnavailable",
        )

        async with sessions() as session:
            graph = (
                (
                    await session.execute(
                        select(graph_run).where(graph_run.c.run_id == "turn-defer-graph")
                    )
                )
                .mappings()
                .one()
            )
        assert graph["lease_until"] <= datetime.now(timezone.utc).replace(tzinfo=None)

        async with sessions() as session, session.begin():
            await session.execute(
                update(chat_turn)
                .where(chat_turn.c.turn_id == "turn-defer-graph")
                .values(
                    next_attempt_at=func.utc_timestamp(6) - text("INTERVAL 1 SECOND"),
                    processing_lease_expires_at=(func.utc_timestamp(6) - text("INTERVAL 1 SECOND")),
                )
            )

        reclaimed = await conversations.repository.claim_queued(limit=10)
        assert len(reclaimed) == 1
        assert reclaimed[0][1].turn_id == "turn-defer-graph"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_claim_fails_turn_whose_attempts_are_exhausted(owned_project_mysql) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        repository = MysqlConversationRepository(
            sessions, scope=VALIDATION_SCOPE, max_turn_attempts=3
        )
        conversations = ConversationService(repository, scope=VALIDATION_SCOPE)
        await conversations.create(
            "chat-exhausted", "turn-exhausted", "request-exhausted", _input()
        )
        await repository.claim_queued(limit=1)

        async with sessions() as session, session.begin():
            await session.execute(
                update(chat_turn)
                .where(chat_turn.c.turn_id == "turn-exhausted")
                .values(
                    processing_attempt=3,
                    processing_lease_expires_at=(func.utc_timestamp(6) - text("INTERVAL 1 SECOND")),
                )
            )

        assert await repository.claim_queued(limit=10) == ()

        async with sessions() as session:
            row = (
                (
                    await session.execute(
                        select(chat_turn).where(chat_turn.c.turn_id == "turn-exhausted")
                    )
                )
                .mappings()
                .one()
            )
            event_count = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM chat_event "
                    "WHERE turn_id='turn-exhausted' AND event_type='turn.failed'"
                )
            )
            problem_type = await session.scalar(
                text(
                    "SELECT JSON_EXTRACT(payload, '$.problem.type') FROM chat_event "
                    "WHERE turn_id='turn-exhausted' AND event_type='turn.failed'"
                )
            )
        assert row["state"] == "failed"
        assert event_count == 1
        assert problem_type is not None and "answer-unavailable" in problem_type

        assert await repository.claim_queued(limit=10) == ()
        async with sessions() as session:
            event_count_after = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM chat_event "
                    "WHERE turn_id='turn-exhausted' AND event_type='turn.failed'"
                )
            )
        assert event_count_after == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_canceled_deferred_turn_is_not_reclaimed(owned_project_mysql) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE),
            scope=VALIDATION_SCOPE,
        )
        await conversations.create(
            "chat-defer-cancel", "turn-defer-cancel", "request-defer-cancel", _input()
        )
        claimed = (await conversations.repository.claim_queued(limit=1))[0][1]
        await conversations.repository.defer_turn(
            "chat-defer-cancel",
            "turn-defer-cancel",
            claimed.lease_token,
            next_attempt_at=datetime.now(timezone.utc) + timedelta(hours=1),
            failure="transient:ModelGatewayUnavailable",
        )

        await conversations.cancel("chat-defer-cancel", "turn-defer-cancel")

        async with sessions() as session, session.begin():
            await session.execute(
                update(chat_turn)
                .where(chat_turn.c.turn_id == "turn-defer-cancel")
                .values(next_attempt_at=func.utc_timestamp(6) - text("INTERVAL 1 SECOND"))
            )

        assert await conversations.repository.claim_queued(limit=10) == ()
    finally:
        await engine.dispose()


async def _event_count(sessions, turn_id: str, event_type: str) -> int:
    async with sessions() as session:
        return await session.scalar(
            text(
                "SELECT COUNT(*) FROM chat_event WHERE turn_id=:turn_id AND event_type=:event_type"
            ),
            {"turn_id": turn_id, "event_type": event_type},
        )


async def _set_next_attempt_at_to_past(sessions, turn_id: str) -> None:
    async with sessions() as session, session.begin():
        await session.execute(
            update(chat_turn)
            .where(chat_turn.c.turn_id == turn_id)
            .values(next_attempt_at=func.utc_timestamp(6) - text("INTERVAL 1 SECOND"))
        )


@pytest.mark.asyncio
async def test_model_failure_resumes_from_checkpoint_without_repeating_retrieval(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE, max_turn_attempts=3),
            scope=VALIDATION_SCOPE,
        )
        await conversations.create("chat-resume", "turn-resume", "request-resume", _input())
        knowledge = _CountingKnowledge(fail_times=1)
        worker = GenerationWorker(conversations, knowledge, max_turn_attempts=3)

        assert await worker.run_once(limit=1) == 1

        async with sessions() as session:
            row = (
                (
                    await session.execute(
                        select(chat_turn).where(chat_turn.c.turn_id == "turn-resume")
                    )
                )
                .mappings()
                .one()
            )
        assert row["state"] == "running"
        assert row["next_attempt_at"] is not None
        assert row["next_attempt_at"] > datetime.now(timezone.utc).replace(tzinfo=None)
        assert knowledge.prepare_calls == 1
        assert knowledge.generate_calls == 1
        assert await _event_count(sessions, "turn-resume", "turn.completed") == 0
        assert await _event_count(sessions, "turn-resume", "turn.failed") == 0

        await _set_next_attempt_at_to_past(sessions, "turn-resume")

        assert await worker.run_once(limit=1) == 1
        completed = (await conversations.load("chat-resume")).turns[0]
        assert completed.state == "completed"
        assert completed.attempt == 2
        assert knowledge.prepare_calls == 1
        assert knowledge.generate_calls == 2
        assert await _event_count(sessions, "turn-resume", "turn.completed") == 1
        assert await _event_count(sessions, "turn-resume", "turn.failed") == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_three_transient_failures_fail_the_turn_once(owned_project_mysql) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        conversations = ConversationService(
            MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE, max_turn_attempts=3),
            scope=VALIDATION_SCOPE,
        )
        await conversations.create(
            "chat-exhaust-retry", "turn-exhaust-retry", "request-exhaust-retry", _input()
        )
        knowledge = _CountingKnowledge(fail_times=1_000)
        worker = GenerationWorker(conversations, knowledge, max_turn_attempts=3)

        for _ in range(3):
            await _set_next_attempt_at_to_past(sessions, "turn-exhaust-retry")
            assert await worker.run_once(limit=1) == 1

        failed = (await conversations.load("chat-exhaust-retry")).turns[0]
        assert failed.state == "failed"
        assert knowledge.prepare_calls == 1
        assert knowledge.generate_calls == 3
        assert await _event_count(sessions, "turn-exhaust-retry", "turn.failed") == 1
        assert await _event_count(sessions, "turn-exhaust-retry", "turn.completed") == 0

        await _set_next_attempt_at_to_past(sessions, "turn-exhaust-retry")
        assert await worker.run_once(limit=1) == 0
    finally:
        await engine.dispose()
