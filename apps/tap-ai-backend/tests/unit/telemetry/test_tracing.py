"""Trace binding propagation, span error marking and traceparent round-trips."""

from opentelemetry.trace import StatusCode

from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.platform.telemetry import (
    bind_trace,
    current_binding,
    extract_traceparent,
    inject_traceparent,
    span,
    trace_id_of,
)


def _scope() -> ProjectScopeContext:
    return ProjectScopeContext(
        enterprise_id="local",
        project_id="tapper-demo",
        actor_id="tapper-local-user",
        identity_mode=IdentityMode.VALIDATION,
    )


def test_binding_attributes_are_stamped_on_every_span(span_recorder) -> None:
    scope = _scope()
    with bind_trace(scope=scope, turn_id="t1", attempt=2):
        with span("outer"):
            pass
        with span("inner"):
            pass

    spans = span_recorder.get_finished_spans()
    assert len(spans) == 2
    for recorded in spans:
        attributes = recorded.attributes
        assert attributes["tap.scope.project_id"] == scope.project_id
        assert attributes["tap.turn_id"] == "t1"
        assert attributes["tap.attempt"] == 2
        assert "tap.job_id" not in attributes


def test_nested_binding_inherits_outer_fields(span_recorder) -> None:
    with bind_trace(turn_id="outer-turn"):
        with bind_trace(attempt=3):
            assert current_binding().turn_id == "outer-turn"
            assert current_binding().attempt == 3


def test_span_marks_error_and_reraises(span_recorder) -> None:
    try:
        with span("boom"):
            raise ValueError("bad")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError to propagate")

    spans = span_recorder.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR


def test_traceparent_round_trip(span_recorder) -> None:
    with span("a") as outer:
        traceparent = inject_traceparent()
        assert traceparent is not None
        assert len(traceparent) == 55
        outer_trace_id = outer.get_span_context().trace_id
        outer_span_id = outer.get_span_context().span_id

    context = extract_traceparent(traceparent)
    assert context is not None
    with span("b", context=context) as inner:
        inner_context = inner.get_span_context()
        assert inner_context.trace_id == outer_trace_id

    spans = span_recorder.get_finished_spans()
    child = next(recorded for recorded in spans if recorded.name == "b")
    assert child.parent is not None
    assert child.parent.span_id == outer_span_id

    trace_id = trace_id_of(traceparent)
    assert trace_id is not None
    assert len(trace_id) == 32
    int(trace_id, 16)


def test_invalid_traceparent_yields_none() -> None:
    assert extract_traceparent("garbage") is None
    assert extract_traceparent(None) is None
