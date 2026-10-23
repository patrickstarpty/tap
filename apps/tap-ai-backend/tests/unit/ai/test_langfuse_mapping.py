"""Mapping from MySQL trace bundles to Langfuse wire-format dataclasses."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tap.modules.ai.application.export_redaction import REDACTION_VERSION, ExportRedactor
from tap.modules.ai.application.langfuse_mapping import feedback_score_id, to_langfuse_trace
from tap.modules.ai.domain.langfuse_sync import CallRecord, LangfuseTrace, SpanRecord, TraceBundle

TRACE_ID = "trace-1"
_STARTED_AT = datetime(2026, 10, 14, 10, 0, 0)


def _span(
    *,
    span_id: str = "root-span",
    parent_span_id: str | None = None,
    name: str = "chat.turn",
    service_name: str = "tap-ai-backend",
    status: str = "ok",
    status_message: str | None = None,
    started_at: datetime = _STARTED_AT,
    duration_ms: int = 100,
    attributes: dict[str, object] | None = None,
) -> SpanRecord:
    return SpanRecord(
        span_id=span_id,
        parent_span_id=parent_span_id,
        name=name,
        service_name=service_name,
        status=status,
        status_message=status_message,
        started_at=started_at,
        duration_ms=duration_ms,
        attributes=attributes or {},
    )


def _call(
    *,
    call_id: str = "call-1",
    span_id: str | None = "root-span",
    operation: str = "chat.completion",
    model_name: str = "qwen",
    upstream_model: str | None = "qwen-upstream",
    provider: str | None = "litellm",
    input_tokens: int | None = 10,
    output_tokens: int | None = 20,
    cost_usd: Decimal | None = Decimal("0.0012"),
    status: str = "ok",
    error_code: str | None = None,
    request_json: str | None = '{"q":"hello"}',
    response_text: str | None = "hi there",
    reasoning_text: str | None = None,
) -> CallRecord:
    return CallRecord(
        call_id=call_id,
        span_id=span_id,
        operation=operation,
        model_name=model_name,
        upstream_model=upstream_model,
        provider=provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        status=status,
        error_code=error_code,
        request_json=request_json,
        response_text=response_text,
        reasoning_text=reasoning_text,
    )


def bundle(
    *,
    trace_id: str = TRACE_ID,
    project_id: str = "proj-1",
    actor_id: str = "actor-1",
    conversation_id: str | None = "conv-1",
    turn_id: str | None = "turn-1",
    job_id: str | None = None,
    spans: tuple[SpanRecord, ...] = (),
    calls: tuple[CallRecord, ...] = (),
    agent_key: str | None = None,
    turn_outcome: str | None = None,
) -> TraceBundle:
    return TraceBundle(
        trace_id=trace_id,
        project_id=project_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        job_id=job_id,
        spans=spans or (_span(),),
        calls=calls,
        agent_key=agent_key,
        turn_outcome=turn_outcome,
    )


def bundle_with_call(
    *,
    cost: Decimal | None = Decimal("0.0012"),
    request: str | None = '{"q":"hello"}',
    status: str = "ok",
    error_code: str | None = None,
) -> TraceBundle:
    span = _span()
    call = _call(cost_usd=cost, request_json=request, status=status, error_code=error_code)
    return bundle(spans=(span,), calls=(call,))


def generation_of(trace: LangfuseTrace):
    return next(
        observation for observation in trace.observations if observation.kind == "generation"
    )


def test_trace_identity_and_tags():
    t = to_langfuse_trace(
        bundle(conversation_id="c1", actor_id="a1", project_id="p1"),
        ExportRedactor(),
        environment="local",
    )
    assert (t.id, t.session_id, t.user_id, t.environment) == (TRACE_ID, "c1", "a1", "local")
    assert "project:p1" in t.tags
    assert t.metadata["tap.redaction_version"] == REDACTION_VERSION


def test_model_call_span_becomes_generation_with_usage_cost_and_redacted_io():
    t = to_langfuse_trace(
        bundle_with_call(cost=Decimal("0.0012"), request='{"q":"13812345678"}'),
        ExportRedactor(),
        environment="local",
    )
    g = generation_of(t)
    assert g.kind == "generation"
    assert g.model == "qwen-upstream"
    assert g.usage_details == {"input": 10, "output": 20}
    assert g.cost_details == {"total": 0.0012}
    assert "13812345678" not in json.dumps(g.input)


def test_failed_call_is_error_level():
    t = to_langfuse_trace(
        bundle_with_call(status="error", error_code="UPSTREAM_TIMEOUT"),
        ExportRedactor(),
        environment="local",
    )
    g = generation_of(t)
    assert g.level == "ERROR"
    assert g.status_message == "UPSTREAM_TIMEOUT"


def test_span_times_and_parents_preserved():
    root = _span(span_id="root", parent_span_id=None, started_at=_STARTED_AT, duration_ms=50)
    child = _span(
        span_id="child",
        parent_span_id="root",
        started_at=_STARTED_AT + timedelta(milliseconds=10),
        duration_ms=20,
    )
    t = to_langfuse_trace(bundle(spans=(root, child)), ExportRedactor(), environment="local")
    by_id = {observation.id: observation for observation in t.observations}

    assert by_id["child"].parent_id == "root"
    assert by_id["root"].start_time == _STARTED_AT.replace(tzinfo=timezone.utc)
    assert by_id["root"].end_time == (_STARTED_AT + timedelta(milliseconds=50)).replace(
        tzinfo=timezone.utc
    )
    assert by_id["child"].start_time == (_STARTED_AT + timedelta(milliseconds=10)).replace(
        tzinfo=timezone.utc
    )
    assert by_id["child"].end_time == (_STARTED_AT + timedelta(milliseconds=30)).replace(
        tzinfo=timezone.utc
    )


def test_string_attributes_are_redacted():
    span = _span(attributes={"user.email": "a@example.com"})
    t = to_langfuse_trace(bundle(spans=(span,)), ExportRedactor(), environment="local")
    observation = next(o for o in t.observations if o.kind == "span")
    assert observation.metadata["user.email"] == "[EMAIL]"


def test_span_without_call_has_no_model_or_usage():
    span = _span()
    t = to_langfuse_trace(bundle(spans=(span,)), ExportRedactor(), environment="local")
    observation = next(o for o in t.observations if o.kind == "span")
    assert observation.model is None
    assert observation.usage_details == {}
    assert observation.cost_details == {}


def test_cost_and_token_fields_omitted_when_absent():
    t = to_langfuse_trace(
        bundle_with_call(cost=None),
        ExportRedactor(),
        environment="local",
    )
    g = generation_of(t)
    assert g.cost_details == {}


def test_reasoning_text_is_redacted_into_metadata():
    span = _span()
    call = _call(reasoning_text="contact a@example.com for help")
    t = to_langfuse_trace(
        bundle(spans=(span,), calls=(call,)), ExportRedactor(), environment="local"
    )
    g = generation_of(t)
    assert g.metadata["tap.reasoning"] == "contact [EMAIL] for help"


def test_turn_trace_gets_agent_tag():
    t = to_langfuse_trace(
        bundle(project_id="proj-1", agent_key="agent-42", turn_outcome="completed"),
        ExportRedactor(),
        environment="local",
    )
    assert t.tags == ("project:proj-1", "agent:agent-42", "outcome:completed")


def test_default_turn_gets_tapper_default_tag():
    t = to_langfuse_trace(
        bundle(project_id="proj-1", agent_key="tapper_default"),
        ExportRedactor(),
        environment="local",
    )
    assert t.tags == ("project:proj-1", "agent:tapper_default")


def test_job_trace_has_no_agent_tag():
    t = to_langfuse_trace(
        bundle(
            project_id="proj-1",
            turn_id=None,
            conversation_id=None,
            agent_key=None,
            turn_outcome=None,
        ),
        ExportRedactor(),
        environment="local",
    )
    assert t.tags == ("project:proj-1",)


def test_terminal_turn_gets_outcome_tag():
    t = to_langfuse_trace(
        bundle(project_id="proj-1", turn_outcome="completed"), ExportRedactor(), environment="local"
    )
    assert t.tags == ("project:proj-1", "outcome:completed")


def test_running_turn_has_no_outcome_tag():
    t = to_langfuse_trace(
        bundle(project_id="proj-1", turn_outcome=None), ExportRedactor(), environment="local"
    )
    assert t.tags == ("project:proj-1",)


def test_feedback_score_id_is_deterministic_and_scoped_to_turn_and_actor():
    first = feedback_score_id("turn-1", "actor-1")
    again = feedback_score_id("turn-1", "actor-1")
    other_actor = feedback_score_id("turn-1", "actor-2")

    assert first == again
    assert first != other_actor
    assert len(first) == 32
