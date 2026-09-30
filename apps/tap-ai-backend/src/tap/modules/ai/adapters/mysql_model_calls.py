"""MySQL-backed ModelCallRecorder; one independent short transaction per call."""

from __future__ import annotations

import logging

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.modules.ai.domain.model_calls import ModelCallRecord
from tap.platform.db.project_scope import scope_values
from tap.platform.telemetry.schema import model_call, model_call_content

logger = logging.getLogger(__name__)


class MysqlModelCallRecorder:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._degraded = False

    def degraded(self) -> bool:
        return self._degraded

    async def record(self, call: ModelCallRecord) -> None:
        try:
            async with self._sessions() as session, session.begin():
                scope = scope_values(call.scope)
                await session.execute(
                    insert(model_call).values(
                        **scope,
                        call_id=call.call_id,
                        trace_id=call.trace_id,
                        span_id=call.span_id,
                        turn_id=call.turn_id,
                        job_id=call.job_id,
                        operation=call.operation.value,
                        model_name=call.model_name,
                        upstream_model=call.upstream_model,
                        provider=call.provider,
                        input_tokens=call.input_tokens,
                        output_tokens=call.output_tokens,
                        cost_usd=call.cost_usd,
                        latency_ms=call.latency_ms,
                        attempts=call.attempts,
                        status=call.status,
                        error_code=call.error_code,
                        gateway_call_id=call.gateway_call_id,
                        provider_request_id=call.provider_request_id,
                        created_at=call.created_at,
                    )
                )
                await session.execute(
                    insert(model_call_content).values(
                        **scope,
                        call_id=call.call_id,
                        request_json=call.request_json,
                        response_text=call.response_text,
                        reasoning_text=call.reasoning_text,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - observability must never raise
            logger.warning("model call recording failed: %s", exc)
            self._degraded = True
            return
        self._degraded = False


__all__ = ["MysqlModelCallRecorder"]
