"""`MysqlAgentTeamStore` integration test (Task 6, 2026-10-23 agent team view):
the MySQL read models (roster, satisfaction, pending feedback, maintenance
counts/history, personnel scope, sync lag) and the three writes (job title,
owner transfer, feedback resolution), plus the `MysqlFeedbackRepository`
upsert reset-on-change behavior it depends on.

Requires `TAP_RUN_MYSQL_INTEGRATION=1`; uses the isolated `owned_project_mysql`
fixture (never the shared default MySQL service)."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from scripts.migration_support import IsolatedMysql

from tap.modules.access.adapters.mysql import actor_principal, project
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.agent_team.adapters.mysql import MysqlAgentTeamStore, agent_maintenance_event
from tap.modules.agent_team.domain.models import TAPPER_DEFAULT_AGENT_KEY, Period, new_event
from tap.modules.agent_team.ports import FeedbackNotFound, HistoryCursorInvalid
from tap.modules.ai.adapters.mysql import ai_agent, ai_agent_revision
from tap.modules.ai.domain.langfuse_sync import SyncKind
from tap.modules.chat.adapters.mysql import chat_turn
from tap.modules.chat.adapters.mysql_conversations import conversation, turn_input_snapshot
from tap.modules.chat.adapters.mysql_feedback import MysqlFeedbackRepository, answer_feedback
from tap.modules.chat.domain.feedback import AnswerFeedback
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.telemetry.schema import langfuse_sync_cursor, trace_span
from tests.owned_mysql import owned_project_database_url

_TRACE_CURSOR: SyncKind = "trace"  # the production sync store's real cursor name
_T0 = datetime(2026, 10, 1, 0, 0, 0)

OTHER_SCOPE = ProjectScopeContext(
    enterprise_id="local",
    project_id="other-project",
    actor_id=VALIDATION_SCOPE.actor_id,
    identity_mode=IdentityMode.VALIDATION,
)


def _scope_values(scope: ProjectScopeContext) -> dict[str, str]:
    return {
        "enterprise_id": scope.enterprise_id,
        "project_id": scope.project_id,
        "actor_id": scope.actor_id,
        "identity_mode": scope.identity_mode.value,
        "identity_origin": scope.identity_mode.value.upper(),
    }


async def _insert_agent(
    connection,
    scope: ProjectScopeContext,
    *,
    agent_id: str,
    revision_id: str,
    created_at: datetime = _T0,
    owner_actor_id: str | None = None,
    owner_assigned_at: datetime = _T0,
    status: str = "enabled",
) -> None:
    await connection.execute(
        sa.insert(ai_agent).values(
            agent_id=agent_id,
            display_name=agent_id,
            created_at=created_at,
            job_title=None,
            owner_actor_id=owner_actor_id or scope.actor_id,
            owner_assigned_at=owner_assigned_at,
            **_scope_values(scope),
        )
    )
    await connection.execute(
        sa.insert(ai_agent_revision).values(
            revision_id=revision_id,
            agent_id=agent_id,
            revision_number=1,
            display_name=agent_id,
            description=None,
            source_kind="custom",
            content_digest="sha256:" + uuid4().hex,
            system_instruction_digest="sha256:" + uuid4().hex,
            system_instruction="be helpful",
            tool_allowlist=[],
            output_schema_digest="sha256:" + uuid4().hex,
            output_schema_json={},
            skill_revision_ids=None,
            default_model_alias=None,
            status=status,
            adopted_from_revision_id=None,
            created_at=created_at,
            **_scope_values(scope),
        )
    )


async def _insert_turn(
    connection,
    scope: ProjectScopeContext,
    *,
    turn_id: str,
    created_at: datetime,
    agent_revision_id: str | None,
    actor_id: str | None = None,
) -> None:
    chat_id = f"chat-{uuid4().hex[:8]}"
    await connection.execute(
        sa.insert(conversation).values(
            conversation_id=chat_id,
            title="t",
            created_at=created_at,
            updated_at=created_at,
            **{**_scope_values(scope), "actor_id": actor_id or scope.actor_id},
        )
    )
    await connection.execute(
        sa.insert(chat_turn).values(
            turn_id=turn_id,
            chat_id=chat_id,
            client_request_id=f"req-{turn_id}",
            message="hi",
            state="completed",
            processing_attempt=0,
            last_sequence=1,
            created_at=created_at,
            traceparent=None,
            **{**_scope_values(scope), "actor_id": actor_id or scope.actor_id},
        )
    )
    await connection.execute(
        sa.insert(turn_input_snapshot).values(
            snapshot_id=f"snap-{uuid4().hex[:16]}",
            turn_id=turn_id,
            snapshot_digest="sha256:" + uuid4().hex,
            snapshot={"agent_revision_id": agent_revision_id},
            created_at=created_at,
            **_scope_values(scope),
        )
    )


async def _insert_feedback(
    connection,
    scope: ProjectScopeContext,
    *,
    feedback_id: str,
    turn_id: str,
    conversation_id: str,
    actor_id: str,
    rating: str | None,
    comment: str | None,
    created_at: datetime = _T0,
    updated_at: datetime | None = None,
    resolved_at: datetime | None = None,
    resolved_by: str | None = None,
    resolution_note: str | None = None,
) -> None:
    await connection.execute(
        sa.insert(answer_feedback).values(
            feedback_id=feedback_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            trace_id=None,
            rating=rating,
            comment=comment,
            created_at=created_at,
            updated_at=updated_at or created_at,
            resolved_at=resolved_at,
            resolved_by=resolved_by,
            resolution_note=resolution_note,
            **{**_scope_values(scope), "actor_id": actor_id},
        )
    )


async def _insert_maintenance_event(
    connection,
    scope: ProjectScopeContext,
    *,
    event_id: str,
    agent_id: str,
    kind: str,
    occurred_at: datetime,
    actor_id: str | None = None,
    revision_id: str | None = None,
    feedback_id: str | None = None,
    note: str | None = None,
) -> None:
    await connection.execute(
        sa.insert(agent_maintenance_event).values(
            event_id=event_id,
            agent_id=agent_id,
            kind=kind,
            revision_id=revision_id,
            feedback_id=feedback_id,
            note=note,
            occurred_at=occurred_at,
            **{**_scope_values(scope), "actor_id": actor_id or scope.actor_id},
        )
    )


async def _insert_actor(connection, *, actor_id: str, enterprise_id: str = "local") -> None:
    await connection.execute(
        sa.insert(actor_principal).values(
            actor_id=actor_id,
            enterprise_id=enterprise_id,
            principal_type="VALIDATION",
            enabled=True,
        )
    )


def _store(sessions, scope: ProjectScopeContext = VALIDATION_SCOPE) -> MysqlAgentTeamStore:
    return MysqlAgentTeamStore(sessions, scope=scope)


def test_ratings_attribute_by_frozen_agent(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-a", revision_id="rev-a"
                )
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-b", revision_id="rev-b"
                )

                turns = {
                    "a-up": ("rev-a", "up"),
                    "a-down": ("rev-a", "down"),
                    "b-up": ("rev-b", "up"),
                    "b-down": ("rev-b", "down"),
                    "default-up": (None, "up"),
                }
                for turn_id, (revision_id, rating) in turns.items():
                    await _insert_turn(
                        connection,
                        VALIDATION_SCOPE,
                        turn_id=turn_id,
                        created_at=_T0,
                        agent_revision_id=revision_id,
                    )
                    await _insert_feedback(
                        connection,
                        VALIDATION_SCOPE,
                        feedback_id=f"fb-{turn_id}",
                        turn_id=turn_id,
                        conversation_id="irrelevant",
                        actor_id=VALIDATION_SCOPE.actor_id,
                        rating=rating,
                        comment=None,
                        created_at=_T0,
                    )

            store = _store(sessions)
            counts = {
                row.agent_key: row
                for row in await store.ratings(Period.parse(_T0.date(), _T0.date()))
            }
            assert counts.keys() == {"agent-a", "agent-b", TAPPER_DEFAULT_AGENT_KEY}
            assert (counts["agent-a"].up, counts["agent-a"].down) == (1, 1)
            assert (counts["agent-b"].up, counts["agent-b"].down) == (1, 1)
            assert (counts[TAPPER_DEFAULT_AGENT_KEY].up, counts[TAPPER_DEFAULT_AGENT_KEY].down) == (
                1,
                0,
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_ratings_use_turn_created_time(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-a", revision_id="rev-a"
                )
                await _insert_turn(
                    connection,
                    VALIDATION_SCOPE,
                    turn_id="late-feedback-turn",
                    created_at=_T0,
                    agent_revision_id="rev-a",
                )
                # The rating itself lands well after the period ends; spec 2.3
                # says it still counts in the period the *Turn* ran in.
                await _insert_feedback(
                    connection,
                    VALIDATION_SCOPE,
                    feedback_id="fb-late",
                    turn_id="late-feedback-turn",
                    conversation_id="irrelevant",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    rating="up",
                    comment=None,
                    created_at=_T0 + timedelta(days=30),
                    updated_at=_T0 + timedelta(days=30),
                )

            store = _store(sessions)
            counts = {
                row.agent_key: row
                for row in await store.ratings(Period.parse(_T0.date(), _T0.date()))
            }
            assert counts["agent-a"].up == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_pending_feedback_includes_downvotes_and_comments(
    owned_project_mysql: IsolatedMysql,
) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-a", revision_id="rev-a"
                )
                for turn_id in ("t-down", "t-up-comment", "t-up-only", "t-resolved"):
                    await _insert_turn(
                        connection,
                        VALIDATION_SCOPE,
                        turn_id=turn_id,
                        created_at=_T0,
                        agent_revision_id="rev-a",
                    )
                await _insert_feedback(
                    connection,
                    VALIDATION_SCOPE,
                    feedback_id="fb-down",
                    turn_id="t-down",
                    conversation_id="c",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    rating="down",
                    comment=None,
                )
                await _insert_feedback(
                    connection,
                    VALIDATION_SCOPE,
                    feedback_id="fb-up-comment",
                    turn_id="t-up-comment",
                    conversation_id="c",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    rating="up",
                    comment="worked but slow",
                )
                await _insert_feedback(
                    connection,
                    VALIDATION_SCOPE,
                    feedback_id="fb-up-only",
                    turn_id="t-up-only",
                    conversation_id="c",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    rating="up",
                    comment=None,
                )
                await _insert_feedback(
                    connection,
                    VALIDATION_SCOPE,
                    feedback_id="fb-resolved",
                    turn_id="t-resolved",
                    conversation_id="c",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    rating="down",
                    comment=None,
                    resolved_at=_T0,
                    resolved_by=VALIDATION_SCOPE.actor_id,
                )

            store = _store(sessions)
            pending = {item.feedback_id for item in await store.pending_feedback()}
            assert pending == {"fb-down", "fb-up-comment"}
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_resolve_writes_feedback_and_event_atomically(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-a", revision_id="rev-a"
                )
                await _insert_turn(
                    connection,
                    VALIDATION_SCOPE,
                    turn_id="t1",
                    created_at=_T0,
                    agent_revision_id="rev-a",
                )
                await _insert_feedback(
                    connection,
                    VALIDATION_SCOPE,
                    feedback_id="fb-1",
                    turn_id="t1",
                    conversation_id="c1",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    rating="down",
                    comment=None,
                )

            store = _store(sessions)
            resolved_at = _T0 + timedelta(hours=1)
            result = await store.resolve_feedback(
                "fb-1",
                "resolver-actor",
                "fixed the prompt",
                lambda agent_id: new_event(
                    VALIDATION_SCOPE,
                    agent_id,
                    "feedback_resolved",
                    feedback_id="fb-1",
                    now=resolved_at,
                ),
                now=resolved_at,
            )
            assert result.feedback_id == "fb-1"
            assert result.agent_key == "agent-a"
            assert result.resolved_by == "resolver-actor"
            assert result.resolution_note == "fixed the prompt"
            assert result.resolved_at == resolved_at.replace(tzinfo=UTC)

            async with sessions() as session:
                row = (
                    await session.execute(
                        sa.select(
                            answer_feedback.c.resolved_at,
                            answer_feedback.c.resolved_by,
                            answer_feedback.c.resolution_note,
                        ).where(answer_feedback.c.feedback_id == "fb-1")
                    )
                ).one()
                assert row.resolved_at is not None
                assert row.resolved_by == "resolver-actor"
                assert row.resolution_note == "fixed the prompt"

                events = (
                    await session.execute(
                        sa.select(agent_maintenance_event).where(
                            agent_maintenance_event.c.feedback_id == "fb-1"
                        )
                    )
                ).all()
                assert len(events) == 1
                assert events[0].kind == "feedback_resolved"
                assert events[0].agent_id == "agent-a"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_resolve_twice_returns_first_resolution(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-a", revision_id="rev-a"
                )
                await _insert_turn(
                    connection,
                    VALIDATION_SCOPE,
                    turn_id="t1",
                    created_at=_T0,
                    agent_revision_id="rev-a",
                )
                await _insert_feedback(
                    connection,
                    VALIDATION_SCOPE,
                    feedback_id="fb-1",
                    turn_id="t1",
                    conversation_id="c1",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    rating="down",
                    comment=None,
                )

            store = _store(sessions)

            def factory(note: str, when: datetime):
                return lambda agent_id: new_event(
                    VALIDATION_SCOPE, agent_id, "feedback_resolved", feedback_id="fb-1", now=when
                )

            first = await store.resolve_feedback(
                "fb-1",
                "resolver-1",
                "first note",
                factory("first note", _T0 + timedelta(hours=1)),
                now=_T0 + timedelta(hours=1),
            )
            second = await store.resolve_feedback(
                "fb-1",
                "resolver-2",
                "second note",
                factory("second note", _T0 + timedelta(hours=2)),
                now=_T0 + timedelta(hours=2),
            )
            assert second.feedback_id == "fb-1"
            # Spec 5.1's "repeat submission returns the original result":
            # the second call's resolution fields must match the first's,
            # not the second caller's own (resolver-2/"second note") input.
            assert second.resolved_by == first.resolved_by == "resolver-1"
            assert second.resolution_note == first.resolution_note == "first note"
            assert second.resolved_at == first.resolved_at

            async with sessions() as session:
                row = (
                    await session.execute(
                        sa.select(
                            answer_feedback.c.resolved_by, answer_feedback.c.resolution_note
                        ).where(answer_feedback.c.feedback_id == "fb-1")
                    )
                ).one()
                assert row.resolved_by == "resolver-1"
                assert row.resolution_note == "first note"

                events = (
                    await session.execute(
                        sa.select(agent_maintenance_event).where(
                            agent_maintenance_event.c.feedback_id == "fb-1"
                        )
                    )
                ).all()
                assert len(events) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_transfer_updates_owner_and_assigned_at(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_actor(connection, actor_id="new-owner")
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-a", revision_id="rev-a"
                )

            store = _store(sessions)
            transferred_at = _T0 + timedelta(hours=3)
            event = new_event(VALIDATION_SCOPE, "agent-a", "owner_transferred", now=transferred_at)
            record = await store.transfer_owner("agent-a", "new-owner", event)
            assert record.owner_actor_id == "new-owner"
            assert record.owner_assigned_at == transferred_at.replace(tzinfo=UTC)

            async with sessions() as session:
                events = (
                    await session.execute(
                        sa.select(agent_maintenance_event).where(
                            agent_maintenance_event.c.agent_id == "agent-a",
                            agent_maintenance_event.c.kind == "owner_transferred",
                        )
                    )
                ).all()
                assert len(events) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_last_maintenance_per_agent(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-a", revision_id="rev-a"
                )
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-b", revision_id="rev-b"
                )
                await _insert_maintenance_event(
                    connection,
                    VALIDATION_SCOPE,
                    event_id="evt-a1",
                    agent_id="agent-a",
                    kind="revised",
                    occurred_at=_T0,
                )
                await _insert_maintenance_event(
                    connection,
                    VALIDATION_SCOPE,
                    event_id="evt-a2",
                    agent_id="agent-a",
                    kind="revised",
                    occurred_at=_T0 + timedelta(hours=5),
                )
                await _insert_maintenance_event(
                    connection,
                    VALIDATION_SCOPE,
                    event_id="evt-b1",
                    agent_id="agent-b",
                    kind="disabled",
                    occurred_at=_T0 + timedelta(hours=1),
                )

            store = _store(sessions)
            last = await store.last_maintenance()
            assert last["agent-a"] == (_T0 + timedelta(hours=5)).replace(tzinfo=UTC)
            assert last["agent-b"] == (_T0 + timedelta(hours=1)).replace(tzinfo=UTC)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_rerating_resolved_feedback_makes_it_pending(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-a", revision_id="rev-a"
                )
                await _insert_turn(
                    connection,
                    VALIDATION_SCOPE,
                    turn_id="t1",
                    created_at=_T0,
                    agent_revision_id="rev-a",
                )
                await _insert_feedback(
                    connection,
                    VALIDATION_SCOPE,
                    feedback_id="fb-1",
                    turn_id="t1",
                    conversation_id="c1",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    rating="down",
                    comment=None,
                    resolved_at=_T0,
                    resolved_by=VALIDATION_SCOPE.actor_id,
                )

            feedback_repo = MysqlFeedbackRepository(sessions, scope=VALIDATION_SCOPE)
            await feedback_repo.upsert(
                AnswerFeedback(
                    feedback_id="fb-1",
                    project_id=VALIDATION_SCOPE.project_id,
                    conversation_id="c1",
                    turn_id="t1",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    trace_id=None,
                    rating="down",
                    comment="actually still broken",
                    created_at=_T0,
                    updated_at=_T0 + timedelta(hours=2),
                )
            )

            store = _store(sessions)
            pending = {item.feedback_id for item in await store.pending_feedback()}
            assert "fb-1" in pending
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_same_rating_resubmit_keeps_resolution(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-a", revision_id="rev-a"
                )
                await _insert_turn(
                    connection,
                    VALIDATION_SCOPE,
                    turn_id="t1",
                    created_at=_T0,
                    agent_revision_id="rev-a",
                )
                await _insert_feedback(
                    connection,
                    VALIDATION_SCOPE,
                    feedback_id="fb-1",
                    turn_id="t1",
                    conversation_id="c1",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    rating="down",
                    comment=None,
                    resolved_at=_T0,
                    resolved_by=VALIDATION_SCOPE.actor_id,
                )

            feedback_repo = MysqlFeedbackRepository(sessions, scope=VALIDATION_SCOPE)
            await feedback_repo.upsert(
                AnswerFeedback(
                    feedback_id="fb-1",
                    project_id=VALIDATION_SCOPE.project_id,
                    conversation_id="c1",
                    turn_id="t1",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    trace_id=None,
                    rating="down",
                    comment=None,
                    created_at=_T0,
                    updated_at=_T0 + timedelta(hours=2),
                )
            )

            store = _store(sessions)
            pending = {item.feedback_id for item in await store.pending_feedback()}
            assert "fb-1" not in pending

            async with sessions() as session:
                resolved_at = await session.scalar(
                    sa.select(answer_feedback.c.resolved_at).where(
                        answer_feedback.c.feedback_id == "fb-1"
                    )
                )
                assert resolved_at is not None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_oldest_unsynced_age(owned_project_mysql: IsolatedMysql) -> None:
    """Not run locally (no isolated MySQL): covers B1's redefined lag."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            now = _T0 + timedelta(hours=10)
            settle = timedelta(seconds=60)
            store = _store(sessions)

            async def add_span(recorded_at) -> None:
                async with engine.begin() as connection:
                    await connection.execute(
                        sa.insert(trace_span).values(
                            trace_id=uuid4().hex,
                            span_id=uuid4().hex[:16],
                            parent_span_id=None,
                            turn_id=None,
                            job_id=None,
                            service_name="tap-ai",
                            name="turn.request",
                            status="DEFAULT",
                            status_message=None,
                            started_at=_T0,
                            duration_ms=10,
                            attributes=None,
                            recorded_at=recorded_at,
                            **_scope_values(VALIDATION_SCOPE),
                        )
                    )

            # No spans at all.
            assert await store.oldest_unsynced_age(now=now, settle=settle) is None

            # A span older than the settle window and no cursor row: unsynced.
            await add_span(now - timedelta(minutes=12))
            assert (
                await store.oldest_unsynced_age(now=now, settle=settle)
                == timedelta(minutes=12) - settle
            )

            # The cursor has caught up with it: nothing is behind.
            async with engine.begin() as connection:
                await connection.execute(
                    sa.insert(langfuse_sync_cursor).values(
                        name=_TRACE_CURSOR, position=now - timedelta(minutes=12)
                    )
                )
            assert await store.oldest_unsynced_age(now=now, settle=settle) is None

            # A span still inside the settle window is not behind yet...
            await add_span(now - timedelta(seconds=30))
            assert await store.oldest_unsynced_age(now=now, settle=settle) is None

            # ...but a settled span past the cursor is, measured past its settle deadline.
            await add_span(now - timedelta(minutes=7))
            assert (
                await store.oldest_unsynced_age(now=now, settle=settle)
                == timedelta(minutes=7) - settle
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_history_pages_with_cursor(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-a", revision_id="rev-a"
                )
                for i in range(120):
                    await _insert_maintenance_event(
                        connection,
                        VALIDATION_SCOPE,
                        event_id=f"evt-{i:04d}",
                        agent_id="agent-a",
                        kind="revised",
                        occurred_at=_T0 + timedelta(seconds=i),
                    )

            store = _store(sessions)
            seen_ids: list[str] = []
            cursor: str | None = None
            pages = 0
            while True:
                page = await store.history("agent-a", cursor, limit=50)
                pages += 1
                seen_ids.extend(event.event_id for event in page.events)
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
                assert pages <= 10  # guard against an infinite loop on a bug

            assert pages == 3
            assert len(seen_ids) == 120
            assert len(set(seen_ids)) == 120
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_queries_are_project_scoped(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    sa.insert(project).values(
                        project_id=OTHER_SCOPE.project_id,
                        enterprise_id=OTHER_SCOPE.enterprise_id,
                    )
                )
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-mine", revision_id="rev-mine"
                )
                await _insert_agent(
                    connection, OTHER_SCOPE, agent_id="agent-other", revision_id="rev-other"
                )

                await _insert_turn(
                    connection,
                    OTHER_SCOPE,
                    turn_id="other-turn",
                    created_at=_T0,
                    agent_revision_id="rev-other",
                )
                await _insert_feedback(
                    connection,
                    OTHER_SCOPE,
                    feedback_id="fb-other",
                    turn_id="other-turn",
                    conversation_id="c",
                    actor_id=OTHER_SCOPE.actor_id,
                    rating="down",
                    comment=None,
                )
                await _insert_maintenance_event(
                    connection,
                    OTHER_SCOPE,
                    event_id="evt-other",
                    agent_id="agent-other",
                    kind="revised",
                    occurred_at=_T0,
                )

            store = _store(sessions, VALIDATION_SCOPE)
            agent_ids = {record.agent_id for record in await store.agents()}
            assert agent_ids == {"agent-mine"}
            assert await store.agent("agent-other") is None

            pending_ids = {item.feedback_id for item in await store.pending_feedback()}
            assert "fb-other" not in pending_ids

            last = await store.last_maintenance()
            assert "agent-other" not in last

            counts = await store.maintenance_counts(Period.parse(_T0.date(), _T0.date()))
            assert all(agent_id != "agent-other" for agent_id, _, _, _ in counts)

            ratings = {
                row.agent_key for row in await store.ratings(Period.parse(_T0.date(), _T0.date()))
            }
            assert "agent-other" not in ratings

            history = await store.history("agent-other", cursor=None)
            assert history.events == ()

            # The only Turn/maintenance event in this scenario belong to
            # OTHER_SCOPE; VALIDATION_SCOPE's own active-actor set for the
            # same period must be empty, not leak OTHER_SCOPE's activity.
            actors = await store.active_actors(Period.parse(_T0.date(), _T0.date()))
            assert actors == set()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_resolve_feedback_not_found_raises(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            store = _store(sessions)
            with pytest.raises(FeedbackNotFound):
                await store.resolve_feedback(
                    "fb-missing",
                    "resolver",
                    None,
                    lambda agent_id: new_event(
                        VALIDATION_SCOPE,
                        agent_id,
                        "feedback_resolved",
                        feedback_id="fb-missing",
                        now=_T0,
                    ),
                    now=_T0,
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_resolve_feedback_skips_event_for_default_agent(owned_project_mysql: IsolatedMysql) -> None:
    """Feedback on a Turn with no selected Agent (`tapper_default`) must still
    resolve -- just without a `feedback_resolved` event, since there is no
    real `ai_agent` row to attach one to (the FK would reject it)."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_turn(
                    connection,
                    VALIDATION_SCOPE,
                    turn_id="default-turn",
                    created_at=_T0,
                    agent_revision_id=None,
                )
                await _insert_feedback(
                    connection,
                    VALIDATION_SCOPE,
                    feedback_id="fb-default",
                    turn_id="default-turn",
                    conversation_id="c1",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    rating="down",
                    comment=None,
                )

            store = _store(sessions)
            calls: list[str] = []

            def factory(agent_id: str):
                calls.append(agent_id)
                return new_event(
                    VALIDATION_SCOPE,
                    agent_id,
                    "feedback_resolved",
                    feedback_id="fb-default",
                    now=_T0,
                )

            injected_now = _T0 + timedelta(hours=5)
            result = await store.resolve_feedback(
                "fb-default", "resolver", "n/a", factory, now=injected_now
            )
            assert result.agent_key == TAPPER_DEFAULT_AGENT_KEY
            assert result.resolved_by == "resolver"
            # The injected clock, not wall-clock time.
            assert result.resolved_at == injected_now.replace(tzinfo=UTC)
            assert calls == []  # event_factory must not even be called

            async with sessions() as session:
                row = (
                    await session.execute(
                        sa.select(answer_feedback.c.resolved_at).where(
                            answer_feedback.c.feedback_id == "fb-default"
                        )
                    )
                ).one()
                assert row.resolved_at == injected_now.replace(tzinfo=None)

                events = (
                    await session.execute(
                        sa.select(agent_maintenance_event).where(
                            agent_maintenance_event.c.feedback_id == "fb-default"
                        )
                    )
                ).all()
                assert events == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("rating", "comment"),
    [("up", None), (None, None)],  # plain thumbs-up; withdrawn
)
def test_resolve_not_pending_feedback_is_unchanged_and_writes_no_event(
    owned_project_mysql: IsolatedMysql, rating: str | None, comment: str | None
) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-a", revision_id="rev-a"
                )
                await _insert_turn(
                    connection,
                    VALIDATION_SCOPE,
                    turn_id="t1",
                    created_at=_T0,
                    agent_revision_id="rev-a",
                )
                await _insert_feedback(
                    connection,
                    VALIDATION_SCOPE,
                    feedback_id="fb-1",
                    turn_id="t1",
                    conversation_id="c1",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    rating=rating,
                    comment=comment,
                )

            store = _store(sessions)
            calls: list[str] = []

            def factory(agent_id: str):
                calls.append(agent_id)
                return new_event(
                    VALIDATION_SCOPE, agent_id, "feedback_resolved", feedback_id="fb-1", now=_T0
                )

            result = await store.resolve_feedback(
                "fb-1", "resolver", "note", factory, now=_T0 + timedelta(hours=1)
            )
            assert result.feedback_id == "fb-1"
            assert result.agent_key == "agent-a"
            assert (result.rating, result.comment) == (rating, comment)
            assert result.resolved_at is None
            assert result.resolved_by is None
            assert result.resolution_note is None
            assert calls == []

            async with sessions() as session:
                row = (
                    await session.execute(
                        sa.select(
                            answer_feedback.c.resolved_at,
                            answer_feedback.c.resolved_by,
                            answer_feedback.c.resolution_note,
                        ).where(answer_feedback.c.feedback_id == "fb-1")
                    )
                ).one()
                assert tuple(row) == (None, None, None)
                events = (
                    await session.execute(
                        sa.select(agent_maintenance_event).where(
                            agent_maintenance_event.c.feedback_id == "fb-1"
                        )
                    )
                ).all()
                assert events == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_resolve_feedback_without_note_stores_null(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-a", revision_id="rev-a"
                )
                await _insert_turn(
                    connection,
                    VALIDATION_SCOPE,
                    turn_id="t1",
                    created_at=_T0,
                    agent_revision_id="rev-a",
                )
                await _insert_feedback(
                    connection,
                    VALIDATION_SCOPE,
                    feedback_id="fb-1",
                    turn_id="t1",
                    conversation_id="c1",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    rating="down",
                    comment=None,
                )

            store = _store(sessions)
            when = _T0 + timedelta(hours=1)
            result = await store.resolve_feedback(
                "fb-1",
                "resolver",
                None,
                lambda agent_id: new_event(
                    VALIDATION_SCOPE, agent_id, "feedback_resolved", feedback_id="fb-1", now=when
                ),
                now=when,
            )
            assert result.resolution_note is None
            async with sessions() as session:
                note = await session.scalar(
                    sa.select(answer_feedback.c.resolution_note).where(
                        answer_feedback.c.feedback_id == "fb-1"
                    )
                )
            assert note is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_resolve_feedback_concurrent_calls_write_one_event(
    owned_project_mysql: IsolatedMysql,
) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-a", revision_id="rev-a"
                )
                await _insert_turn(
                    connection,
                    VALIDATION_SCOPE,
                    turn_id="t1",
                    created_at=_T0,
                    agent_revision_id="rev-a",
                )
                await _insert_feedback(
                    connection,
                    VALIDATION_SCOPE,
                    feedback_id="fb-1",
                    turn_id="t1",
                    conversation_id="c1",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    rating="down",
                    comment=None,
                )

            store_a = _store(sessions)
            store_b = _store(sessions)

            def factory(resolver: str, when: datetime):
                return lambda agent_id: new_event(
                    VALIDATION_SCOPE, agent_id, "feedback_resolved", feedback_id="fb-1", now=when
                )

            result_a, result_b = await asyncio.gather(
                store_a.resolve_feedback(
                    "fb-1",
                    "resolver-a",
                    "note-a",
                    factory("resolver-a", _T0 + timedelta(hours=1)),
                    now=_T0 + timedelta(hours=1),
                ),
                store_b.resolve_feedback(
                    "fb-1",
                    "resolver-b",
                    "note-b",
                    factory("resolver-b", _T0 + timedelta(hours=2)),
                    now=_T0 + timedelta(hours=2),
                ),
            )

            assert result_a.resolved_by == result_b.resolved_by
            assert result_a.resolution_note == result_b.resolution_note
            assert result_a.resolved_at == result_b.resolved_at

            async with sessions() as session:
                events = (
                    await session.execute(
                        sa.select(agent_maintenance_event).where(
                            agent_maintenance_event.c.feedback_id == "fb-1"
                        )
                    )
                ).all()
                assert len(events) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_history_cursor_invalid_raises(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            store = _store(sessions)
            crafted = [
                base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
                for payload in (
                    {"occurredAt": "2026-10-23T12:00:00", "eventId": {"$gt": ""}},
                    {"occurredAt": "2026-10-23T12:00:00+00:00", "eventId": "mevt-1"},
                )
            ]
            for tampered in (
                "not-base64!!",
                "",
                "====",
                "YWJj",  # decodes to "abc", not JSON
                *crafted,
            ):
                with pytest.raises(HistoryCursorInvalid):
                    await store.history("agent-a", tampered)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_rerating_without_comment_change_makes_it_pending(
    owned_project_mysql: IsolatedMysql,
) -> None:
    """A rating-only change (comment stays the same) must still reset the
    resolution: here the new rating value ('down') on its own qualifies the
    feedback as pending regardless of the comment."""

    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await _insert_agent(
                    connection, VALIDATION_SCOPE, agent_id="agent-a", revision_id="rev-a"
                )
                await _insert_turn(
                    connection,
                    VALIDATION_SCOPE,
                    turn_id="t1",
                    created_at=_T0,
                    agent_revision_id="rev-a",
                )
                await _insert_feedback(
                    connection,
                    VALIDATION_SCOPE,
                    feedback_id="fb-1",
                    turn_id="t1",
                    conversation_id="c1",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    rating="up",
                    comment=None,
                    resolved_at=_T0,
                    resolved_by=VALIDATION_SCOPE.actor_id,
                )

            feedback_repo = MysqlFeedbackRepository(sessions, scope=VALIDATION_SCOPE)
            await feedback_repo.upsert(
                AnswerFeedback(
                    feedback_id="fb-1",
                    project_id=VALIDATION_SCOPE.project_id,
                    conversation_id="c1",
                    turn_id="t1",
                    actor_id=VALIDATION_SCOPE.actor_id,
                    trace_id=None,
                    rating="down",
                    comment=None,
                    created_at=_T0,
                    updated_at=_T0 + timedelta(hours=2),
                )
            )

            store = _store(sessions)
            pending = {item.feedback_id for item in await store.pending_feedback()}
            assert "fb-1" in pending
        finally:
            await engine.dispose()

    asyncio.run(scenario())
