"""Read-side shapes for the Langfuse sync worker: a trace bundle and a feedback row.

These are plain, scope-free carriers (the store that produces them reads across
all Projects); mapping to Langfuse's wire format happens downstream of this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

SyncKind = Literal["trace", "feedback"]


@dataclass(frozen=True, slots=True)
class SpanRecord:
    span_id: str
    parent_span_id: str | None
    name: str
    service_name: str
    status: str
    status_message: str | None
    started_at: datetime
    duration_ms: int
    attributes: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class CallRecord:
    call_id: str
    span_id: str | None
    operation: str
    model_name: str
    upstream_model: str | None
    provider: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    status: str
    error_code: str | None
    request_json: str | None
    response_text: str | None
    reasoning_text: str | None


@dataclass(frozen=True, slots=True)
class TraceBundle:
    trace_id: str
    project_id: str
    actor_id: str
    conversation_id: str | None
    turn_id: str | None
    job_id: str | None
    spans: tuple[SpanRecord, ...]
    calls: tuple[CallRecord, ...]


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    feedback_id: str
    trace_id: str | None
    turn_id: str
    actor_id: str
    rating: Literal["up", "down"] | None
    comment: str | None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "CallRecord",
    "FeedbackRecord",
    "SpanRecord",
    "SyncKind",
    "TraceBundle",
]
