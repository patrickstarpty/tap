"""MySQL-backed TraceHttpService: turn trace summaries and model-call detail reads."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.contracts.http import (
    ModelCallDetail,
    ModelCallView,
    TraceSpanView,
    TurnTrace,
    TurnTraceSummary,
)
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.adapters.mysql import chat_turn
from tap.modules.chat.adapters.mysql_conversations import conversation
from tap.platform.db.project_scope import scope_predicates
from tap.platform.telemetry import trace_id_of
from tap.platform.telemetry.schema import model_call, model_call_content, trace_span


def _span_view(row: RowMapping) -> TraceSpanView:
    attributes = dict(row["attributes"] or {})
    attempt = attributes.get("tap.attempt")
    return TraceSpanView(
        span_id=row["span_id"],
        parent_span_id=row["parent_span_id"],
        name=row["name"],
        status=row["status"],
        started_at=row["started_at"],
        duration_ms=row["duration_ms"],
        attributes=attributes,
        attempt=attempt if isinstance(attempt, int) else None,
    )


def _call_view(row: RowMapping) -> ModelCallView:
    return ModelCallView(
        call_id=row["call_id"],
        span_id=row["span_id"],
        operation=row["operation"],
        model_name=row["model_name"],
        upstream_model=row["upstream_model"],
        provider=row["provider"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost_usd=row["cost_usd"],
        latency_ms=row["latency_ms"],
        attempts=row["attempts"],
        status=row["status"],
        error_code=row["error_code"],
        created_at=row["created_at"],
    )


def _summary(spans: Sequence[TraceSpanView], calls: Sequence[ModelCallView]) -> TurnTraceSummary:
    if spans:
        starts = [item.started_at for item in spans]
        ends = [item.started_at + timedelta(milliseconds=item.duration_ms) for item in spans]
        total_duration_ms = int((max(ends) - min(starts)).total_seconds() * 1000)
    else:
        total_duration_ms = 0
    ok_calls = [item for item in calls if item.status == "ok"]
    input_tokens = sum(item.input_tokens or 0 for item in ok_calls)
    output_tokens = sum(item.output_tokens or 0 for item in ok_calls)
    costs = [item.cost_usd for item in ok_calls if item.cost_usd is not None]
    cost_incomplete = any(item.cost_usd is None for item in ok_calls)
    requested_models: list[str] = []
    for item in calls:
        if item.model_name not in requested_models:
            requested_models.append(item.model_name)
    upstream_models: list[str] = []
    for item in calls:
        if item.upstream_model is not None and item.upstream_model not in upstream_models:
            upstream_models.append(item.upstream_model)
    attempt_count = len({item.attempt for item in spans if item.attempt is not None})
    return TurnTraceSummary(
        total_duration_ms=total_duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=sum(costs, Decimal("0")) if costs else None,
        cost_incomplete=cost_incomplete,
        requested_models=requested_models,
        upstream_models=upstream_models,
        attempt_count=attempt_count,
    )


class MysqlTraceHttpService:
    """Reads persisted trace spans and model calls scoped to the caller's Project."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def turn_trace(
        self, scope: ProjectScopeContext, conversation_id: str, turn_id: str
    ) -> TurnTrace | None:
        async with self._sessions() as session:
            traceparent = (
                await session.execute(
                    select(chat_turn.c.traceparent)
                    .select_from(
                        chat_turn.join(
                            conversation,
                            (chat_turn.c.project_id == conversation.c.project_id)
                            & (chat_turn.c.chat_id == conversation.c.conversation_id),
                        )
                    )
                    .where(
                        *scope_predicates(chat_turn, scope),
                        chat_turn.c.turn_id == turn_id,
                        chat_turn.c.chat_id == conversation_id,
                        conversation.c.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if traceparent is None:
                return None
            trace_id = trace_id_of(traceparent)
            if trace_id is None:
                return None

            span_rows = (
                (
                    await session.execute(
                        select(trace_span)
                        .where(
                            *scope_predicates(trace_span, scope),
                            trace_span.c.trace_id == trace_id,
                        )
                        .order_by(trace_span.c.started_at.asc())
                    )
                )
                .mappings()
                .all()
            )
            call_rows = (
                (
                    await session.execute(
                        select(model_call)
                        .where(
                            *scope_predicates(model_call, scope),
                            model_call.c.trace_id == trace_id,
                        )
                        .order_by(model_call.c.created_at.asc())
                    )
                )
                .mappings()
                .all()
            )

        spans = [_span_view(row) for row in span_rows]
        calls = [_call_view(row) for row in call_rows]
        return TurnTrace(
            trace_id=trace_id,
            summary=_summary(spans, calls),
            spans=spans,
            model_calls=calls,
        )

    async def model_call(self, scope: ProjectScopeContext, call_id: str) -> ModelCallDetail | None:
        async with self._sessions() as session:
            call_row = (
                (
                    await session.execute(
                        select(model_call).where(
                            *scope_predicates(model_call, scope),
                            model_call.c.call_id == call_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if call_row is None:
                return None
            content_row = (
                (
                    await session.execute(
                        select(model_call_content).where(
                            *scope_predicates(model_call_content, scope),
                            model_call_content.c.call_id == call_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )

        view = _call_view(call_row)
        return ModelCallDetail(
            **view.model_dump(),
            request=content_row["request_json"] if content_row is not None else "",
            response=content_row["response_text"] if content_row is not None else None,
            reasoning=content_row["reasoning_text"] if content_row is not None else None,
        )


__all__ = ["MysqlTraceHttpService"]
