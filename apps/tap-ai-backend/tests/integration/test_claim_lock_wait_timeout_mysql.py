"""A real InnoDB row lock bounds a job claim by the claim lock-wait timeout.

A second connection holds the conversation row that the chat Turn claim reads
with a blocking `FOR UPDATE`. Each claim attempt must give up after the
claim-scoped `innodb_lock_wait_timeout` (default 5 s) instead of the server
default (50 s), so the exhausted poll claims nothing within about 3 x 5 s plus
backoff rather than about 150 s. Afterwards the same pooled connection runs
non-claim work at the server default. Requires `TAP_RUN_MYSQL_INTEGRATION=1`.
"""

from __future__ import annotations

import asyncio
import time

from sqlalchemy import select, text

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql import chat_turn
from tap.modules.chat.adapters.mysql_conversations import (
    MysqlConversationRepository,
    conversation,
)
from tap.modules.chat.application.conversations import ConversationService
from tap.platform.db.lock_contention import CLAIM_ATTEMPTS, DEFAULT_CLAIM_LOCK_WAIT_SECONDS
from tap.platform.db.project_scope import scope_predicates
from tap.platform.db.session import create_engine_and_session_factory
from tests.integration.test_claim_loop_contention import _turn_input
from tests.owned_mysql import owned_project_database_url

_SESSION_STATE = text(
    "SELECT CONNECTION_ID(), @@SESSION.innodb_lock_wait_timeout, @@GLOBAL.innodb_lock_wait_timeout"
)


async def _session_state(sessions):  # type: ignore[no-untyped-def]
    async with sessions() as session, session.begin():
        connection_id, session_wait, global_wait = (await session.execute(_SESSION_STATE)).one()
    return connection_id, session_wait, global_wait


def test_a_locked_row_bounds_each_claim_attempt_and_leaks_no_timeout(owned_project_mysql) -> None:  # type: ignore[no-untyped-def]
    async def scenario() -> None:
        database_url = owned_project_database_url(owned_project_mysql)
        engine, sessions = create_engine_and_session_factory(database_url)
        holder_engine, _ = create_engine_and_session_factory(database_url)
        try:
            repository = MysqlConversationRepository(sessions, scope=VALIDATION_SCOPE)
            conversations = ConversationService(repository, scope=VALIDATION_SCOPE)
            await conversations.create(
                "chat-lock-wait", "turn-lock-wait", "request-1", _turn_input()
            )
            pooled_id, before_wait, server_default = await _session_state(sessions)
            assert before_wait == server_default
            assert server_default > CLAIM_ATTEMPTS * DEFAULT_CLAIM_LOCK_WAIT_SECONDS

            async with holder_engine.connect() as holder:
                await holder.begin()
                held = await holder.scalar(
                    select(conversation.c.conversation_id)
                    .where(
                        *scope_predicates(conversation, VALIDATION_SCOPE),
                        conversation.c.conversation_id == "chat-lock-wait",
                    )
                    .with_for_update()
                )
                assert held == "chat-lock-wait"

                started = time.monotonic()
                batch = await repository.claim_queued(limit=5)
                elapsed = time.monotonic() - started
                await holder.rollback()

            assert not batch
            # Three attempts each timed out after the claim bound, plus at most
            # 2 x 0.2 s backoff and statement overhead -- never the 50 s default.
            bound = CLAIM_ATTEMPTS * DEFAULT_CLAIM_LOCK_WAIT_SECONDS
            assert bound - 1 <= elapsed < bound + 5, elapsed

            # The one pooled connection that ran every attempt is back at the
            # server default for non-claim work.
            assert engine.sync_engine.pool.checkedin() == 1
            connection_id, after_wait, _ = await _session_state(sessions)
            assert connection_id == pooled_id
            assert after_wait == server_default

            async with sessions() as session:
                state = await session.scalar(
                    select(chat_turn.c.state).where(chat_turn.c.turn_id == "turn-lock-wait")
                )
            assert state == "queued"
            ((_, claimed),) = await repository.claim_queued(limit=5)
            assert claimed.turn_id == "turn-lock-wait" and claimed.attempt == 1
        finally:
            await holder_engine.dispose()
            await engine.dispose()

    asyncio.run(scenario())
