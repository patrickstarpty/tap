"""Durable record of a single model call, bound to trace/turn/job context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.models import ModelOperation


@dataclass(frozen=True, slots=True)
class ModelCallRecord:
    call_id: str
    scope: ProjectScopeContext
    trace_id: str | None
    span_id: str | None
    turn_id: str | None
    job_id: str | None
    operation: ModelOperation
    model_name: str
    upstream_model: str | None
    provider: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    latency_ms: int
    attempts: int
    status: Literal["ok", "error"]
    error_code: str | None
    gateway_call_id: str | None
    provider_request_id: str | None
    created_at: datetime
    request_json: str
    response_text: str | None
    reasoning_text: str | None


__all__ = ["ModelCallRecord"]
