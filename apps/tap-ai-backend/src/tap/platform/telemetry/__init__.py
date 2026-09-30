"""Tracing core shared by TAP AI backend request, turn and job execution paths."""

from tap.platform.telemetry.tracing import (
    TraceBinding,
    TraceBindingProcessor,
    UsageTally,
    bind_trace,
    configure_tracing,
    current_binding,
    extract_traceparent,
    flush_traces,
    inject_traceparent,
    span,
    trace_id_of,
)

__all__ = [
    "TraceBinding",
    "TraceBindingProcessor",
    "UsageTally",
    "bind_trace",
    "configure_tracing",
    "current_binding",
    "extract_traceparent",
    "flush_traces",
    "inject_traceparent",
    "span",
    "trace_id_of",
]
