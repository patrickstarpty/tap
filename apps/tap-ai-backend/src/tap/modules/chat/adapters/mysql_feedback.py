"""MySQL-backed per-Turn answer feedback: one active row per (turn, actor)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import (
    CHAR,
    Column,
    ForeignKeyConstraint,
    Index,
    String,
    Table,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.adapters.mysql import chat_turn
from tap.modules.chat.domain.feedback import AnswerFeedback
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import augment_project_table, metadata

answer_feedback = Table(
    "answer_feedback",
    metadata,
    Column("feedback_id", CHAR(36), primary_key=True),
    Column("conversation_id", String(64), nullable=False),
    Column("turn_id", String(64), nullable=False),
    Column("trace_id", CHAR(32), nullable=True),
    Column("rating", String(8), nullable=True),
    Column("comment", String(500), nullable=True),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
)
augment_project_table(answer_feedback)
answer_feedback.append_constraint(
    UniqueConstraint("project_id", "turn_id", "actor_id", name="uq_answer_feedback_turn_actor")
)
answer_feedback.append_constraint(
    ForeignKeyConstraint(
        ["project_id", "turn_id"],
        [chat_turn.c.project_id, chat_turn.c.turn_id],
        name="fk_answer_feedback_turn",
    )
)
Index(
    "ix_answer_feedback_project_updated",
    answer_feedback.c.project_id,
    answer_feedback.c.updated_at,
)


def _naive(value: datetime) -> datetime:
    """MySQL DATETIME columns are naive; normalize aware inputs to naive UTC so
    they compare directly against values read back from the driver."""
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _from_row(row: RowMapping) -> AnswerFeedback:
    return AnswerFeedback(
        feedback_id=row["feedback_id"],
        project_id=row["project_id"],
        conversation_id=row["conversation_id"],
        turn_id=row["turn_id"],
        actor_id=row["actor_id"],
        trace_id=row["trace_id"],
        rating=row["rating"],
        comment=row["comment"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class MysqlFeedbackRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        scope: ProjectScopeContext,
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)

    async def upsert(self, feedback: AnswerFeedback) -> AnswerFeedback:
        """Insert a new row, or update the existing `(turn_id, actor_id)` row in place.

        A single atomic `INSERT ... ON DUPLICATE KEY UPDATE` (rather than a
        separate insert-then-update) means two concurrent writes for the same
        key never race to an earlier `updated_at`: MySQL serializes both
        statements against the same row, and each one's `updated_at` is
        floored at `<current row value> + 1 microsecond` via `GREATEST(...)`,
        so every write strictly increases it regardless of wall-clock drift
        or which caller's write lands first. `feedback_id` and `created_at`
        are never part of the UPDATE clause, so an existing row's identity
        and creation time survive every subsequent write untouched.
        """
        values = {
            **{**scope_values(self._scope), "actor_id": feedback.actor_id},
            "feedback_id": feedback.feedback_id,
            "conversation_id": feedback.conversation_id,
            "turn_id": feedback.turn_id,
            "trace_id": feedback.trace_id,
            "rating": feedback.rating,
            "comment": feedback.comment,
            "created_at": _naive(feedback.created_at),
            "updated_at": _naive(feedback.updated_at),
        }
        insert_stmt = mysql_insert(answer_feedback).values(**values)
        upsert_stmt = insert_stmt.on_duplicate_key_update(
            rating=insert_stmt.inserted.rating,
            comment=insert_stmt.inserted.comment,
            updated_at=func.greatest(
                answer_feedback.c.updated_at + text("INTERVAL 1 MICROSECOND"),
                insert_stmt.inserted.updated_at,
            ),
        )
        async with self._sessions() as session, session.begin():
            await session.execute(upsert_stmt)
            row = (
                (
                    await session.execute(
                        select(answer_feedback).where(
                            *scope_predicates(answer_feedback, self._scope),
                            answer_feedback.c.turn_id == feedback.turn_id,
                            answer_feedback.c.actor_id == feedback.actor_id,
                        )
                    )
                )
                .mappings()
                .one()
            )
        return _from_row(row)

    async def get(self, turn_id: str, actor_id: str) -> AnswerFeedback | None:
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(answer_feedback).where(
                            *scope_predicates(answer_feedback, self._scope),
                            answer_feedback.c.turn_id == turn_id,
                            answer_feedback.c.actor_id == actor_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _from_row(row)

    async def for_turns(self, turn_ids: Sequence[str], actor_id: str) -> dict[str, AnswerFeedback]:
        if not turn_ids:
            return {}
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(answer_feedback).where(
                            *scope_predicates(answer_feedback, self._scope),
                            answer_feedback.c.turn_id.in_(tuple(turn_ids)),
                            answer_feedback.c.actor_id == actor_id,
                            answer_feedback.c.rating.is_not(None),
                        )
                    )
                )
                .mappings()
                .all()
            )
        return {row["turn_id"]: _from_row(row) for row in rows}
