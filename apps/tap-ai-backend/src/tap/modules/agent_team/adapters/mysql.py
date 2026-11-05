"""`agent_maintenance_event` table definition (Task 1) and `MysqlAgentTeamStore`
(Task 6): MySQL read models and writes for the Agent team view.

`ai_agent`/`ai_agent_revision` are imported lazily, inside the methods that
need them, rather than at module scope: `tap.modules.ai.adapters.mysql`
imports `agent_maintenance_event` from this module at its own module scope,
so an eager top-level import here of anything from that module would
deadlock the cycle the first time either module loads before the other
finishes executing (whichever side starts first finds the other's symbol not
yet defined).
"""

from __future__ import annotations

import base64
import binascii
import json
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import Column, Index, String, Table, func, or_, select, update
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.adapters.mysql import actor_principal
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.agent_team.adapters.agent_keys import resolve_agent_keys
from tap.modules.agent_team.domain.models import (
    TAPPER_DEFAULT_AGENT_KEY,
    UNKNOWN_AGENT_KEY,
    MaintenanceEvent,
    MaintenanceKind,
    Period,
    is_pending_feedback,
)
from tap.modules.agent_team.ports import (
    AgentRecord,
    FeedbackNotFound,
    HistoryCursorInvalid,
    HistoryPage,
    PendingFeedback,
    RatingCounts,
    ResolvedFeedback,
)
from tap.modules.ai.domain.assets import AssetRevisionStatus
from tap.modules.ai.domain.langfuse_sync import SyncKind
from tap.modules.chat.adapters.mysql import chat_turn
from tap.modules.chat.adapters.mysql_conversations import turn_input_snapshot
from tap.modules.chat.adapters.mysql_feedback import answer_feedback
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import augment_project_table, metadata
from tap.platform.telemetry.schema import langfuse_sync_cursor, trace_span

agent_maintenance_event = Table(
    "agent_maintenance_event",
    metadata,
    Column("event_id", String(64), primary_key=True),
    Column("agent_id", String(64), nullable=False),
    Column("kind", String(24), nullable=False),
    Column("revision_id", String(64), nullable=True),
    Column("feedback_id", String(36), nullable=True),
    Column("note", String(500), nullable=True),
    Column("occurred_at", DATETIME(fsp=6), nullable=False),
)
# `agent_id -> ai_agent.agent_id` is registered in `PROJECT_PARENT_LINKS`
# (platform/db/schema.py) so `augment_project_table` wires the FK itself.
augment_project_table(agent_maintenance_event)
Index(
    "ix_agent_maintenance_event_project_agent_occurred",
    agent_maintenance_event.c.project_id,
    agent_maintenance_event.c.agent_id,
    agent_maintenance_event.c.occurred_at,
)
Index(
    "ix_agent_maintenance_event_project_occurred",
    agent_maintenance_event.c.project_id,
    agent_maintenance_event.c.occurred_at,
)


async def insert_maintenance_event(
    session: AsyncSession, scope: ProjectScopeContext, event: MaintenanceEvent
) -> None:
    """The one `agent_maintenance_event` insert, shared by every writer that
    records a maintenance event in the same transaction as its state change
    (revise, enable/disable, owner transfer, feedback resolution).

    An `IntegrityError` raised here (duplicate `event_id`, or the Agent FK)
    is only meaningful as a *transaction* failure: callers that map it to
    `RevisionConflict` do so because the whole transaction -- including the
    state change -- rolls back, never because the event alone conflicted."""
    await session.execute(
        agent_maintenance_event.insert().values(
            event_id=event.event_id,
            agent_id=event.agent_id,
            kind=event.kind,
            revision_id=event.revision_id,
            feedback_id=event.feedback_id,
            note=event.note,
            occurred_at=event.occurred_at,
            **scope_values(scope),
        )
    )


def _naive(value: datetime) -> datetime:
    """MySQL DATETIME columns are naive (always UTC); strip tzinfo from an
    aware value before using it in a `WHERE` comparison against one."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _aware(value: datetime) -> datetime:
    """Attach UTC to a naive value read back from a DATETIME column."""
    return value.replace(tzinfo=UTC)


def _encode_history_cursor(occurred_at: datetime, event_id: str) -> str:
    payload = json.dumps(
        {"occurredAt": occurred_at.isoformat(timespec="microseconds"), "eventId": event_id},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_history_cursor(cursor: str) -> tuple[datetime, str]:
    """Inverse of `_encode_history_cursor`. Only its exact shape is accepted:
    a string `eventId` and a naive `occurredAt` (DATETIME columns are naive
    UTC); anything else is `HistoryCursorInvalid` (400 `cursor_invalid`)."""
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(decoded)
        occurred_at = datetime.fromisoformat(payload["occurredAt"])
        event_id = payload["eventId"]
    except (ValueError, TypeError, KeyError, binascii.Error) as error:
        raise HistoryCursorInvalid(cursor) from error
    if not isinstance(event_id, str) or occurred_at.tzinfo is not None:
        raise HistoryCursorInvalid(cursor)
    return occurred_at, event_id


# A Turn with no selected Agent (`tapper_default`) or whose frozen revision
# no longer resolves to one (`unknown`) has no `ai_agent` row to attach a
# `feedback_resolved` event to; `agent_maintenance_event.agent_id`'s FK would
# reject such an insert, so `resolve_feedback` skips writing an event (and
# does not call `event_factory`) for these.
_UNRESOLVABLE_AGENT_KEYS = frozenset({TAPPER_DEFAULT_AGENT_KEY, UNKNOWN_AGENT_KEY})
# The cursor row the ai-module Langfuse sync store writes for traces.
_TRACE_CURSOR: SyncKind = "trace"


def _to_event(row) -> MaintenanceEvent:
    return MaintenanceEvent(
        event_id=row["event_id"],
        project_id=row["project_id"],
        agent_id=row["agent_id"],
        actor_id=row["actor_id"],
        kind=row["kind"],
        revision_id=row["revision_id"],
        feedback_id=row["feedback_id"],
        note=row["note"],
        occurred_at=_aware(row["occurred_at"]),
    )


class MysqlAgentTeamStore:
    """`AgentTeamStore` over `ai_agent`/`ai_agent_revision`, `answer_feedback`,
    `agent_maintenance_event`, `actor_principal`, and `trace_span`/
    `langfuse_sync_cursor`, scoped to one Project."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)

    async def _agent_rows(self, session: AsyncSession, *, agent_id: str | None = None):
        from tap.modules.ai.adapters.mysql import ai_agent, ai_agent_revision

        latest_numbers = (
            select(
                ai_agent_revision.c.agent_id,
                func.max(ai_agent_revision.c.revision_number).label("revision_number"),
            )
            .where(
                *scope_predicates(ai_agent_revision, self._scope),
                ai_agent_revision.c.system_instruction.is_not(None),
                ai_agent_revision.c.output_schema_json.is_not(None),
            )
            .group_by(ai_agent_revision.c.agent_id)
            .subquery()
        )
        query = (
            select(
                ai_agent.c.agent_id,
                ai_agent.c.job_title,
                ai_agent.c.owner_actor_id,
                ai_agent.c.owner_assigned_at,
                ai_agent.c.created_at,
                ai_agent_revision.c.display_name,
                ai_agent_revision.c.source_kind,
                ai_agent_revision.c.status,
                ai_agent_revision.c.revision_id,
                ai_agent_revision.c.revision_number,
            )
            .select_from(
                ai_agent.join(
                    ai_agent_revision,
                    (ai_agent.c.agent_id == ai_agent_revision.c.agent_id)
                    & (ai_agent.c.project_id == ai_agent_revision.c.project_id),
                ).join(
                    latest_numbers,
                    (ai_agent_revision.c.agent_id == latest_numbers.c.agent_id)
                    & (ai_agent_revision.c.revision_number == latest_numbers.c.revision_number),
                )
            )
            .where(*scope_predicates(ai_agent, self._scope))
        )
        if agent_id is not None:
            query = query.where(ai_agent.c.agent_id == agent_id)
        return (await session.execute(query)).mappings().all()

    @staticmethod
    def _to_agent_record(row) -> AgentRecord:
        return AgentRecord(
            agent_id=row["agent_id"],
            name=row["display_name"],
            job_title=row["job_title"],
            owner_actor_id=row["owner_actor_id"],
            owner_assigned_at=_aware(row["owner_assigned_at"]),
            created_at=_aware(row["created_at"]),
            source_kind=row["source_kind"],
            enabled=row["status"] == AssetRevisionStatus.ENABLED.value,
            current_revision_id=row["revision_id"],
            current_revision_number=row["revision_number"],
        )

    async def agents(self) -> list[AgentRecord]:
        async with self._sessions() as session:
            rows = await self._agent_rows(session)
        return [self._to_agent_record(row) for row in rows]

    async def agent(self, agent_id: str) -> AgentRecord | None:
        async with self._sessions() as session:
            rows = await self._agent_rows(session, agent_id=agent_id)
        return self._to_agent_record(rows[0]) if rows else None

    async def ratings(self, period: Period) -> list[RatingCounts]:
        start, end_exclusive = period.utc_bounds()
        start, end_exclusive = _naive(start), _naive(end_exclusive)
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            chat_turn.c.turn_id,
                            turn_input_snapshot.c.snapshot,
                            answer_feedback.c.rating,
                        )
                        .select_from(
                            chat_turn.join(
                                answer_feedback,
                                (answer_feedback.c.turn_id == chat_turn.c.turn_id)
                                & (answer_feedback.c.project_id == chat_turn.c.project_id),
                            ).outerjoin(
                                turn_input_snapshot,
                                turn_input_snapshot.c.turn_id == chat_turn.c.turn_id,
                            )
                        )
                        .where(
                            *scope_predicates(chat_turn, self._scope),
                            chat_turn.c.created_at >= start,
                            chat_turn.c.created_at < end_exclusive,
                            answer_feedback.c.rating.in_(("up", "down")),
                        )
                    )
                )
                .mappings()
                .all()
            )
            snapshots = {row["turn_id"]: row["snapshot"] for row in rows}
            agent_keys = await resolve_agent_keys(session, self._scope.project_id, snapshots)
        counts: dict[str, dict[str, int]] = defaultdict(lambda: {"up": 0, "down": 0})
        for row in rows:
            counts[agent_keys[row["turn_id"]]][row["rating"]] += 1
        return [
            RatingCounts(agent_key=agent_key, up=value["up"], down=value["down"])
            for agent_key, value in counts.items()
        ]

    async def pending_feedback(self, agent_key: str | None = None) -> list[PendingFeedback]:
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            answer_feedback.c.feedback_id,
                            answer_feedback.c.turn_id,
                            answer_feedback.c.conversation_id,
                            answer_feedback.c.actor_id,
                            answer_feedback.c.rating,
                            answer_feedback.c.comment,
                            answer_feedback.c.updated_at,
                            turn_input_snapshot.c.snapshot,
                        )
                        .select_from(
                            answer_feedback.outerjoin(
                                turn_input_snapshot,
                                turn_input_snapshot.c.turn_id == answer_feedback.c.turn_id,
                            )
                        )
                        .where(
                            *scope_predicates(answer_feedback, self._scope),
                            answer_feedback.c.resolved_at.is_(None),
                            or_(
                                answer_feedback.c.rating == "down",
                                answer_feedback.c.comment.is_not(None),
                            ),
                        )
                        .order_by(
                            answer_feedback.c.updated_at.desc(),
                            answer_feedback.c.feedback_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
            snapshots = {row["turn_id"]: row["snapshot"] for row in rows}
            agent_keys = await resolve_agent_keys(session, self._scope.project_id, snapshots)
        results = [
            PendingFeedback(
                feedback_id=row["feedback_id"],
                agent_key=agent_keys[row["turn_id"]],
                turn_id=row["turn_id"],
                conversation_id=row["conversation_id"],
                actor_id=row["actor_id"],
                rating=row["rating"],
                comment=row["comment"],
                updated_at=_aware(row["updated_at"]),
            )
            for row in rows
        ]
        if agent_key is not None:
            results = [result for result in results if result.agent_key == agent_key]
        return results

    async def maintenance_counts(
        self, period: Period
    ) -> list[tuple[str, str, MaintenanceKind, int]]:
        start, end_exclusive = period.utc_bounds()
        start, end_exclusive = _naive(start), _naive(end_exclusive)
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        agent_maintenance_event.c.agent_id,
                        agent_maintenance_event.c.actor_id,
                        agent_maintenance_event.c.kind,
                        func.count().label("event_count"),
                    )
                    .where(
                        *scope_predicates(agent_maintenance_event, self._scope),
                        agent_maintenance_event.c.occurred_at >= start,
                        agent_maintenance_event.c.occurred_at < end_exclusive,
                    )
                    .group_by(
                        agent_maintenance_event.c.agent_id,
                        agent_maintenance_event.c.actor_id,
                        agent_maintenance_event.c.kind,
                    )
                )
            ).all()
        return [(row.agent_id, row.actor_id, row.kind, row.event_count) for row in rows]

    async def history(self, agent_id: str, cursor: str | None, limit: int = 50) -> HistoryPage:
        position = _decode_history_cursor(cursor) if cursor is not None else None
        async with self._sessions() as session:
            query = (
                select(agent_maintenance_event)
                .where(
                    *scope_predicates(agent_maintenance_event, self._scope),
                    agent_maintenance_event.c.agent_id == agent_id,
                )
                .order_by(
                    agent_maintenance_event.c.occurred_at.desc(),
                    agent_maintenance_event.c.event_id.desc(),
                )
                .limit(limit + 1)
            )
            if position is not None:
                occurred_at, event_id = position
                query = query.where(
                    or_(
                        agent_maintenance_event.c.occurred_at < occurred_at,
                        (
                            (agent_maintenance_event.c.occurred_at == occurred_at)
                            & (agent_maintenance_event.c.event_id < event_id)
                        ),
                    )
                )
            rows = (await session.execute(query)).mappings().all()
        events = tuple(_to_event(row) for row in rows[:limit])
        next_cursor = (
            _encode_history_cursor(rows[limit - 1]["occurred_at"], rows[limit - 1]["event_id"])
            if len(rows) > limit
            else None
        )
        return HistoryPage(events=events, next_cursor=next_cursor)

    async def last_maintenance(self) -> dict[str, datetime]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        agent_maintenance_event.c.agent_id,
                        func.max(agent_maintenance_event.c.occurred_at).label("occurred_at"),
                    )
                    .where(*scope_predicates(agent_maintenance_event, self._scope))
                    .group_by(agent_maintenance_event.c.agent_id)
                )
            ).all()
        return {row.agent_id: _aware(row.occurred_at) for row in rows}

    async def active_actors(self, period: Period) -> set[str]:
        start, end_exclusive = period.utc_bounds()
        start, end_exclusive = _naive(start), _naive(end_exclusive)
        async with self._sessions() as session:
            turn_actors = (
                (
                    await session.execute(
                        select(chat_turn.c.actor_id)
                        .where(
                            *scope_predicates(chat_turn, self._scope),
                            chat_turn.c.created_at >= start,
                            chat_turn.c.created_at < end_exclusive,
                        )
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            maintenance_actors = (
                (
                    await session.execute(
                        select(agent_maintenance_event.c.actor_id)
                        .where(
                            *scope_predicates(agent_maintenance_event, self._scope),
                            agent_maintenance_event.c.occurred_at >= start,
                            agent_maintenance_event.c.occurred_at < end_exclusive,
                        )
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
        return set(turn_actors) | set(maintenance_actors)

    async def oldest_unsynced_age(self, *, now: datetime, settle: timedelta) -> timedelta | None:
        settled_before = _naive(now - settle)
        async with self._sessions() as session:
            cursor_position = await session.scalar(
                select(langfuse_sync_cursor.c.position).where(
                    langfuse_sync_cursor.c.name == _TRACE_CURSOR
                )
            )
            query = select(func.min(trace_span.c.recorded_at)).where(
                *scope_predicates(trace_span, self._scope),
                trace_span.c.recorded_at <= settled_before,
            )
            if cursor_position is not None:
                query = query.where(trace_span.c.recorded_at > cursor_position)
            oldest = await session.scalar(query)
        if oldest is None:
            return None
        # How far past its settle deadline the oldest unsynced span is.
        return settled_before - oldest

    async def actor_exists(self, actor_id: str) -> bool:
        async with self._sessions() as session:
            row = await session.scalar(
                select(actor_principal.c.actor_id).where(
                    actor_principal.c.enterprise_id == self._scope.enterprise_id,
                    actor_principal.c.actor_id == actor_id,
                    actor_principal.c.enabled.is_(True),
                )
            )
        return row is not None

    async def set_job_title(self, agent_id: str, job_title: str | None) -> AgentRecord:
        from tap.modules.ai.adapters.mysql import ai_agent

        async with self._sessions.begin() as session:
            result = await session.execute(
                update(ai_agent)
                .where(*scope_predicates(ai_agent, self._scope), ai_agent.c.agent_id == agent_id)
                .values(job_title=job_title)
            )
            if result.rowcount != 1:
                raise LookupError(f"agent {agent_id!r} not found in this Project")
            rows = await self._agent_rows(session, agent_id=agent_id)
        return self._to_agent_record(rows[0])

    async def transfer_owner(
        self, agent_id: str, new_owner: str, event: MaintenanceEvent
    ) -> AgentRecord:
        from tap.modules.ai.adapters.mysql import ai_agent

        async with self._sessions.begin() as session:
            result = await session.execute(
                update(ai_agent)
                .where(*scope_predicates(ai_agent, self._scope), ai_agent.c.agent_id == agent_id)
                .values(owner_actor_id=new_owner, owner_assigned_at=event.occurred_at)
            )
            if result.rowcount != 1:
                raise LookupError(f"agent {agent_id!r} not found in this Project")
            await insert_maintenance_event(session, self._scope, event)
            rows = await self._agent_rows(session, agent_id=agent_id)
        return self._to_agent_record(rows[0])

    async def resolve_feedback(
        self,
        feedback_id: str,
        actor_id: str,
        note: str | None,
        event_factory: Callable[[str], MaintenanceEvent],
        *,
        now: datetime,
    ) -> ResolvedFeedback:
        async with self._sessions.begin() as session:
            # `FOR UPDATE` on the feedback row itself serializes concurrent
            # `resolve_feedback` calls for the same `feedback_id`: the second
            # caller blocks here until the first commits, then its own read
            # (a locking "current read", not the transaction's repeatable-read
            # snapshot) sees the first caller's committed resolution -- so at
            # most one event is ever written, and the loser still returns the
            # real (first) resolution rather than stale/duplicate data.
            row = (
                (
                    await session.execute(
                        select(
                            answer_feedback.c.feedback_id,
                            answer_feedback.c.turn_id,
                            answer_feedback.c.conversation_id,
                            answer_feedback.c.actor_id,
                            answer_feedback.c.rating,
                            answer_feedback.c.comment,
                            answer_feedback.c.updated_at,
                            answer_feedback.c.resolved_at,
                            answer_feedback.c.resolved_by,
                            answer_feedback.c.resolution_note,
                        )
                        .where(
                            *scope_predicates(answer_feedback, self._scope),
                            answer_feedback.c.feedback_id == feedback_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise FeedbackNotFound(feedback_id)
            snapshot = await session.scalar(
                select(turn_input_snapshot.c.snapshot).where(
                    turn_input_snapshot.c.turn_id == row["turn_id"]
                )
            )
            agent_keys = await resolve_agent_keys(
                session, self._scope.project_id, {row["turn_id"]: snapshot}
            )
            agent_key = agent_keys[row["turn_id"]]

            if row["resolved_at"] is not None or not is_pending_feedback(
                row["rating"], row["comment"]
            ):
                # Already resolved (by this call's own previous attempt, or
                # by whoever held the row lock before us): return the
                # existing resolution unchanged and write no event (spec
                # 5.1's "repeat submission returns the original result").
                # Not pending (plain thumbs-up, or withdrawn): nothing to
                # resolve, so likewise unchanged -- `resolved_*` stay NULL.
                resolved_at = row["resolved_at"]
                return ResolvedFeedback(
                    feedback_id=row["feedback_id"],
                    agent_key=agent_key,
                    turn_id=row["turn_id"],
                    conversation_id=row["conversation_id"],
                    actor_id=row["actor_id"],
                    rating=row["rating"],
                    comment=row["comment"],
                    updated_at=_aware(row["updated_at"]),
                    resolved_at=_aware(resolved_at) if resolved_at is not None else None,
                    resolved_by=row["resolved_by"],
                    resolution_note=row["resolution_note"],
                )

            # No real `ai_agent` row to attach an event to for these keys --
            # resolve the feedback but write no event, and never call
            # `event_factory`.
            event = None if agent_key in _UNRESOLVABLE_AGENT_KEYS else event_factory(agent_key)
            resolved_at = now if now.tzinfo else _aware(now)

            await session.execute(
                update(answer_feedback)
                .where(
                    *scope_predicates(answer_feedback, self._scope),
                    answer_feedback.c.feedback_id == feedback_id,
                )
                .values(
                    resolved_at=resolved_at,
                    resolved_by=actor_id,
                    resolution_note=note,
                )
            )
            if event is not None:
                await insert_maintenance_event(session, self._scope, event)
        return ResolvedFeedback(
            feedback_id=row["feedback_id"],
            agent_key=agent_key,
            turn_id=row["turn_id"],
            conversation_id=row["conversation_id"],
            actor_id=row["actor_id"],
            rating=row["rating"],
            comment=row["comment"],
            updated_at=_aware(row["updated_at"]),
            resolved_at=resolved_at,
            resolved_by=actor_id,
            resolution_note=note,
        )
