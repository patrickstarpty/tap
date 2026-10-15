"""MySQL-backed `LangfuseSyncStore`: a system-wide reader, not Project-scoped.

The Langfuse sync worker runs outside any caller's Project — it walks every
Project's `trace_span`/`answer_feedback` rows by `recorded_at`/`updated_at`, so
every query here is deliberately unscoped (no `scope_predicates`). `trace_id`
and `feedback_id` are already globally unique, so this is safe.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import Literal, TypeVar, cast

import sqlalchemy as sa
from sqlalchemy import select
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

_Row = TypeVar("_Row")


async def _paginate(
    rows: Sequence[_Row],
    *,
    limit: int,
    time_of: Callable[[_Row], datetime],
    refetch_tied: Callable[[datetime], Awaitable[Sequence[_Row]]],
) -> tuple[Sequence[_Row], datetime | None]:
    """Turn a `LIMIT`-ed, time-ordered page into a lossless `(rows, next_position)`.

    A plain `ORDER BY time ASC LIMIT n` can split a group of rows that share the
    exact same timestamp across two pages, silently dropping whichever half
    lands after the cut if the cursor advances to the last row's timestamp. This
    never advances past a timestamp it cannot prove it has read in full:

    - a short page (fewer than `limit` rows) is the end of the window, so every
      row is safe and the position advances to the last row's timestamp;
    - a full page that is entirely one timestamp might still have more rows at
      that exact timestamp past the `LIMIT`, so every matching row is re-fetched
      without a limit and the position advances to that timestamp (all of it,
      not just this page, is now accounted for);
    - a full page spanning more than one timestamp drops the trailing group that
      shares the last row's timestamp (it might be incomplete) and advances only
      to the next distinct timestamp below it; the dropped group is picked up,
      complete, on the next scan.
    """
    if not rows:
        return (), None
    if len(rows) < limit:
        return rows, time_of(rows[-1])
    last_value = time_of(rows[-1])
    distinct_values = {time_of(row) for row in rows}
    if distinct_values == {last_value}:
        tied_rows = await refetch_tied(last_value)
        return tied_rows, last_value
    threshold = max(value for value in distinct_values if value != last_value)
    kept_rows = [row for row in rows if time_of(row) <= threshold]
    return kept_rows, threshold


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
        columns = (trace_span.c.recorded_at, trace_span.c.trace_id)
        statement = (
            select(*columns)
            .where(trace_span.c.recorded_at > position, trace_span.c.recorded_at <= cutoff)
            .order_by(trace_span.c.recorded_at.asc())
            .limit(limit)
        )

        async def refetch_tied(value: datetime) -> Sequence[sa.engine.Row]:
            tie_statement = select(*columns).where(trace_span.c.recorded_at == value)
            return (await session.execute(tie_statement)).all()

        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
            kept_rows, new_position = await _paginate(
                rows, limit=limit, time_of=lambda row: row[0], refetch_tied=refetch_tied
            )

        trace_ids: list[str] = []
        seen: set[str] = set()
        for row in kept_rows:
            trace_id = row[1]
            if trace_id not in seen:
                seen.add(trace_id)
                trace_ids.append(trace_id)
        return trace_ids, new_position

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

        async def refetch_tied(value: datetime) -> Sequence[RowMapping]:
            tie_statement = select(answer_feedback).where(answer_feedback.c.updated_at == value)
            return (await session.execute(tie_statement)).mappings().all()

        async with self._sessions() as session:
            rows = (await session.execute(statement)).mappings().all()
            kept_rows, new_position = await _paginate(
                rows,
                limit=limit,
                time_of=lambda row: row["updated_at"],
                refetch_tied=refetch_tied,
            )

        records = [_feedback_record(row) for row in kept_rows]
        return records, new_position

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
