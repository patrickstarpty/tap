"""Pure mapping from a MySQL `TraceBundle` to Langfuse's wire-format dataclasses.

No SDK and no DB here: redaction runs through `ExportRedactor`, and the result is a
plain `LangfuseTrace` the sync service hands to a `LangfuseSink`. A model-call span
(one with a matching `CallRecord`) becomes a `generation` observation carrying the
call's model/usage/cost/IO; every other span stays a plain `span` observation.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from tap.modules.ai.application.export_redaction import REDACTION_VERSION, ExportRedactor
from tap.modules.ai.domain.langfuse_sync import (
    CallRecord,
    LangfuseObservation,
    LangfuseTrace,
    SpanRecord,
    TraceBundle,
)

_ERROR_STATUS = "error"

ObservationLevel = Literal["DEFAULT", "ERROR"]


def _level_for(status: str) -> ObservationLevel:
    return "ERROR" if status == _ERROR_STATUS else "DEFAULT"


def feedback_score_id(turn_id: str, actor_id: str) -> str:
    """Deterministic Langfuse score id for one `(turn_id, actor_id)` feedback row.

    Stable across re-pushes (retract/re-react must hit the same score), so this is
    derived from identity alone, never from the feedback row's own id or timing.
    """
    return uuid5(NAMESPACE_URL, f"tap:user_feedback:{turn_id}:{actor_id}").hex


def _as_utc(value: datetime) -> datetime:
    """MySQL gives naive UTC; Langfuse needs timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _redact_request(request_json: str | None, redactor: ExportRedactor) -> object:
    if request_json is None:
        return None
    try:
        parsed = json.loads(request_json)
    except (TypeError, ValueError):
        return redactor.redact(request_json)
    return redactor.redact_value(parsed)


def _observation_for(
    span: SpanRecord, call: CallRecord | None, redactor: ExportRedactor
) -> LangfuseObservation:
    start_time = _as_utc(span.started_at)
    end_time = start_time + timedelta(milliseconds=span.duration_ms)

    attributes_metadata = redactor.redact_value(dict(span.attributes))
    metadata: dict[str, object] = (
        dict(attributes_metadata) if isinstance(attributes_metadata, dict) else {}
    )

    if call is None:
        level = _level_for(span.status)
        return LangfuseObservation(
            id=span.span_id,
            parent_id=span.parent_span_id,
            kind="span",
            name=span.name,
            start_time=start_time,
            end_time=end_time,
            level=level,
            status_message=redactor.redact(span.status_message),
            input=None,
            output=None,
            model=None,
            usage_details={},
            cost_details={},
            metadata=metadata,
        )

    level = _level_for(call.status)
    usage_details: dict[str, int] = {}
    if call.input_tokens is not None:
        usage_details["input"] = call.input_tokens
    if call.output_tokens is not None:
        usage_details["output"] = call.output_tokens
    cost_details: dict[str, float] = {}
    if call.cost_usd is not None:
        cost_details["total"] = float(call.cost_usd)
    if call.reasoning_text is not None:
        metadata["tap.reasoning"] = redactor.redact(call.reasoning_text)

    return LangfuseObservation(
        id=span.span_id,
        parent_id=span.parent_span_id,
        kind="generation",
        name=span.name,
        start_time=start_time,
        end_time=end_time,
        level=level,
        status_message=call.error_code,
        input=_redact_request(call.request_json, redactor),
        output=redactor.redact(call.response_text),
        model=call.upstream_model or call.model_name,
        usage_details=usage_details,
        cost_details=cost_details,
        metadata=metadata,
    )


def to_langfuse_trace(
    bundle: TraceBundle, redactor: ExportRedactor, *, environment: str
) -> LangfuseTrace:
    calls_by_span: dict[str, CallRecord] = {
        call.span_id: call for call in bundle.calls if call.span_id is not None
    }
    root = next(
        (span for span in bundle.spans if span.parent_span_id is None),
        bundle.spans[0] if bundle.spans else None,
    )
    observations = tuple(
        _observation_for(span, calls_by_span.get(span.span_id), redactor) for span in bundle.spans
    )
    metadata: dict[str, object] = {
        "tap.redaction_version": REDACTION_VERSION,
        "tap.service_name": root.service_name if root is not None else "",
    }
    return LangfuseTrace(
        id=bundle.trace_id,
        name=root.name if root is not None else bundle.trace_id,
        session_id=bundle.conversation_id,
        user_id=bundle.actor_id,
        environment=environment,
        tags=(f"project:{bundle.project_id}",),
        metadata=metadata,
        observations=observations,
    )


__all__ = ["feedback_score_id", "to_langfuse_trace"]
