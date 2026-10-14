"""MySQL-backed `LangfuseSyncStore`: a system-wide reader, not Project-scoped.

The Langfuse sync worker runs outside any caller's Project — it walks every
Project's `trace_span`/`answer_feedback` rows by `recorded_at`/`updated_at`, so
every query here is deliberately unscoped (no `scope_predicates`). `trace_id`
and `feedback_id` are already globally unique, so this is safe.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, cast

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.ai.domain.langfuse_sync import (
    CallRecord,
    FeedbackRecord,
    SpanRecord,
    SyncKind,
    TraceBundle,
)
from tap.modules.chat.adapters.mysql import chat_turn
from tap.modules.chat.adapters.mysql_feedback import answer_feedback
from tap.platform.telemetry.schema import (
    langfuse_sync_cursor,
    langfuse_sync_failure,
    model_call,
    model_call_content,
    trace_span,
)

_EPOCH = datetime(1970, 1, 1)
_LAST_ERROR_LIMIT = 1000


def _span_record(row: RowMapping) -> SpanRecord:
    return SpanRecord(
        span_id=row["span_id"],
        parent_span_id=row["parent_span_id"],
        name=row["name"],
        service_name=row["service_name"],
        status=row["status"],
        status_message=row["status_message"],
        started_at=row["started_at"],
        duration_ms=row["duration_ms"],
        attributes=dict(row["attributes"] or {}),
    )


def _call_record(row: RowMapping) -> CallRecord:
    return CallRecord(
        call_id=row["call_id"],
        span_id=row["span_id"],
        operation=row["operation"],
        model_name=row["model_name"],
        upstream_model=row["upstream_model"],
        provider=row["provider"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost_usd=row["cost_usd"],
        status=row["status"],
        error_code=row["error_code"],
        request_json=row["request_json"],
        response_text=row["response_text"],
        reasoning_text=row["reasoning_text"],
    )


def _feedback_record(row: RowMapping) -> FeedbackRecord:
    return FeedbackRecord(
        feedback_id=row["feedback_id"],
        trace_id=row["trace_id"],
        turn_id=row["turn_id"],
        actor_id=row["actor_id"],
        rating=cast("Literal['up', 'down'] | None", row["rating"]),
        comment=row["comment"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class MysqlLangfuseSyncStore:
    """Not Project-scoped: a system worker reading trace/feedback data for every Project."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def _cursor_position(self, name: SyncKind) -> datetime:
        async with self._sessions() as session:
            position = (
                await session.execute(
                    select(langfuse_sync_cursor.c.position).where(
                        langfuse_sync_cursor.c.name == name
                    )
                )
            ).scalar_one_or_none()
        return _EPOCH if position is None else position

    async def scan_traces(
        self, *, cutoff: datetime, limit: int
    ) -> tuple[list[str], datetime | None]:
        position = await self._cursor_position("trace")
        min_recorded_at = func.min(trace_span.c.recorded_at)
        max_recorded_at = func.max(trace_span.c.recorded_at)
        statement = (
            select(trace_span.c.trace_id, min_recorded_at, max_recorded_at)
            .where(trace_span.c.recorded_at > position, trace_span.c.recorded_at <= cutoff)
            .group_by(trace_span.c.trace_id)
            .order_by(min_recorded_at.asc())
            .limit(limit)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        if not rows:
            return [], None
        trace_ids = [row[0] for row in rows]
        batch_max = max(row[2] for row in rows)
        return trace_ids, batch_max

    async def scan_feedback(
        self, *, cutoff: datetime, limit: int
    ) -> tuple[list[FeedbackRecord], datetime | None]:
        position = await self._cursor_position("feedback")
        statement = (
            select(answer_feedback)
            .where(
                answer_feedback.c.updated_at > position,
                answer_feedback.c.updated_at <= cutoff,
            )
            .order_by(answer_feedback.c.updated_at.asc())
            .limit(limit)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).mappings().all()
        if not rows:
            return [], None
        records = [_feedback_record(row) for row in rows]
        batch_max = max(row["updated_at"] for row in rows)
        return records, batch_max

    async def advance(self, name: SyncKind, position: datetime) -> None:
        insert_stmt = mysql_insert(langfuse_sync_cursor).values(name=name, position=position)
        upsert_stmt = insert_stmt.on_duplicate_key_update(position=insert_stmt.inserted.position)
        async with self._sessions() as session, session.begin():
            await session.execute(upsert_stmt)

    async def load_trace(self, trace_id: str) -> TraceBundle | None:
        async with self._sessions() as session:
            span_rows = (
                (
                    await session.execute(
                        select(trace_span)
                        .where(trace_span.c.trace_id == trace_id)
                        .order_by(trace_span.c.started_at.asc())
                    )
                )
                .mappings()
                .all()
            )
            if not span_rows:
                return None
            call_rows = (
                (
                    await session.execute(
                        select(
                            model_call,
                            model_call_content.c.request_json,
                            model_call_content.c.response_text,
                            model_call_content.c.reasoning_text,
                        )
                        .select_from(
                            model_call.outerjoin(
                                model_call_content,
                                model_call.c.call_id == model_call_content.c.call_id,
                            )
                        )
                        .where(model_call.c.trace_id == trace_id)
                    )
                )
                .mappings()
                .all()
            )
            root = next((row for row in span_rows if row["parent_span_id"] is None), span_rows[0])
            turn_id = root["turn_id"]
            conversation_id: str | None = None
            if turn_id is not None:
                conversation_id = (
                    await session.execute(
                        select(chat_turn.c.chat_id).where(chat_turn.c.turn_id == turn_id)
                    )
                ).scalar_one_or_none()

        spans = tuple(_span_record(row) for row in span_rows)
        calls = tuple(_call_record(row) for row in call_rows)
        return TraceBundle(
            trace_id=trace_id,
            project_id=root["project_id"],
            actor_id=root["actor_id"],
            conversation_id=conversation_id,
            turn_id=turn_id,
            job_id=root["job_id"],
            spans=spans,
            calls=calls,
        )

    async def load_feedback(self, feedback_id: str) -> FeedbackRecord | None:
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(answer_feedback).where(answer_feedback.c.feedback_id == feedback_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _feedback_record(row)

    async def failures(self, kind: SyncKind, limit: int) -> list[str]:
        statement = (
            select(langfuse_sync_failure.c.subject_id)
            .where(langfuse_sync_failure.c.kind == kind)
            .order_by(langfuse_sync_failure.c.last_failed_at.asc())
            .limit(limit)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).scalars().all()
        return list(rows)

    async def record_failure(self, kind: SyncKind, subject_id: str, error: str) -> None:
        last_error = error[:_LAST_ERROR_LIMIT]
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        insert_stmt = mysql_insert(langfuse_sync_failure).values(
            kind=kind,
            subject_id=subject_id,
            failure_count=1,
            last_error=last_error,
            last_failed_at=now,
        )
        upsert_stmt = insert_stmt.on_duplicate_key_update(
            failure_count=langfuse_sync_failure.c.failure_count + 1,
            last_error=insert_stmt.inserted.last_error,
            last_failed_at=insert_stmt.inserted.last_failed_at,
        )
        async with self._sessions() as session, session.begin():
            await session.execute(upsert_stmt)

    async def clear_failure(self, kind: SyncKind, subject_id: str) -> None:
        statement = sa.delete(langfuse_sync_failure).where(
            langfuse_sync_failure.c.kind == kind,
            langfuse_sync_failure.c.subject_id == subject_id,
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)


__all__ = ["MysqlLangfuseSyncStore"]
