"""Tracing core: binding-aware spans, traceparent propagation and provider setup.

`bind_trace` merges scope/turn/job identifiers into a context-local `TraceBinding`
that `TraceBindingProcessor` stamps onto every span started while it is active.
`configure_tracing` registers the process-wide `TracerProvider` exactly once,
since OpenTelemetry only allows a single global provider per process.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TypeAlias

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace import Span as SdkSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Span, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from tap.modules.access.domain.context import ProjectScopeContext
from tap.platform.db.project_scope import scope_values

logger = logging.getLogger(__name__)

_TRACER_NAME = "tap"
_PROPAGATOR = TraceContextTextMapPropagator()

# Fixed batching behavior for exported spans; passed explicitly so OTEL_BSP_* environment
# variables (which BatchSpanProcessor otherwise reads as defaults) cannot silently override
# this platform-wide constraint.
_BATCH_SCHEDULE_DELAY_MILLIS = 5000
_BATCH_MAX_EXPORT_BATCH_SIZE = 512


def _batch_span_processor(exporter: SpanExporter) -> BatchSpanProcessor:
    return BatchSpanProcessor(
        exporter,
        schedule_delay_millis=_BATCH_SCHEDULE_DELAY_MILLIS,
        max_export_batch_size=_BATCH_MAX_EXPORT_BATCH_SIZE,
    )


# opentelemetry.util.types.AttributeValue is a recursive alias assigned without an
# explicit `TypeAlias` annotation, which mypy --strict rejects as "not valid as a
# type". Redeclare the equivalent shape here so span attribute signatures type-check.
AttributeValue: TypeAlias = (
    str
    | bool
    | int
    | float
    | bytes
    | Sequence["AttributeValue"]
    | Mapping[str, "AttributeValue"]
    | None
)


@dataclass(slots=True)
class UsageTally:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class TraceBinding:
    scope: ProjectScopeContext | None = None
    turn_id: str | None = None
    job_id: str | None = None
    job_kind: str | None = None
    attempt: int | None = None
    usage: UsageTally = field(default_factory=UsageTally)


_current_binding: ContextVar[TraceBinding | None] = ContextVar("tap_trace_binding", default=None)


def current_binding() -> TraceBinding:
    """Return the `TraceBinding` active in the current execution context.

    Returns a fresh `TraceBinding` (with its own `UsageTally`) when no binding is
    active, so usage accumulated outside `bind_trace` is never shared across calls
    or requests.
    """
    binding = _current_binding.get()
    return binding if binding is not None else TraceBinding()


@contextmanager
def bind_trace(
    *,
    scope: ProjectScopeContext | None = None,
    turn_id: str | None = None,
    job_id: str | None = None,
    job_kind: str | None = None,
    attempt: int | None = None,
    fresh_usage: bool = False,
) -> Iterator[TraceBinding]:
    """Merge the given fields onto the outer binding; unset fields are inherited."""
    outer = current_binding()
    merged = TraceBinding(
        scope=scope if scope is not None else outer.scope,
        turn_id=turn_id if turn_id is not None else outer.turn_id,
        job_id=job_id if job_id is not None else outer.job_id,
        job_kind=job_kind if job_kind is not None else outer.job_kind,
        attempt=attempt if attempt is not None else outer.attempt,
        usage=UsageTally() if fresh_usage else outer.usage,
    )
    token = _current_binding.set(merged)
    try:
        yield merged
    finally:
        _current_binding.reset(token)


class TraceBindingProcessor(SpanProcessor):
    """Stamps the active `TraceBinding` onto every span as it starts."""

    def on_start(self, span: SdkSpan, parent_context: Context | None = None) -> None:
        binding = current_binding()
        if binding.scope is not None:
            values = scope_values(binding.scope)
            span.set_attribute("tap.scope.enterprise_id", values["enterprise_id"])
            span.set_attribute("tap.scope.project_id", values["project_id"])
            span.set_attribute("tap.scope.actor_id", values["actor_id"])
            span.set_attribute("tap.scope.identity_mode", values["identity_mode"])
        if binding.turn_id is not None:
            span.set_attribute("tap.turn_id", binding.turn_id)
        if binding.job_id is not None:
            span.set_attribute("tap.job_id", binding.job_id)
        if binding.job_kind is not None:
            span.set_attribute("tap.job_kind", binding.job_kind)
        if binding.attempt is not None:
            span.set_attribute("tap.attempt", binding.attempt)

    def on_end(self, span: ReadableSpan) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


@contextmanager
def span(
    name: str,
    attributes: Mapping[str, AttributeValue] | None = None,
    *,
    context: Context | None = None,
) -> Iterator[Span]:
    """Start a span; on exception, mark it ERROR, record the exception, and reraise."""
    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(
        name,
        context=context,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as current_span:
        try:
            yield current_span
        except Exception as exc:
            current_span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            current_span.record_exception(exc)
            raise


def inject_traceparent() -> str | None:
    """Return the current span's `traceparent` header value, or None if invalid."""
    if not trace.get_current_span().get_span_context().is_valid:
        return None
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return carrier.get("traceparent")


def extract_traceparent(value: str | None) -> Context | None:
    """Parse a `traceparent` header value into a Context, or None if invalid."""
    if not value:
        return None
    extracted = _PROPAGATOR.extract({"traceparent": value})
    if not trace.get_current_span(extracted).get_span_context().is_valid:
        return None
    return extracted


def trace_id_of(traceparent: str | None) -> str | None:
    """Return the 32-hex-digit trace id encoded in a `traceparent` value, if valid."""
    context = extract_traceparent(traceparent)
    if context is None:
        return None
    return format(trace.get_current_span(context).get_span_context().trace_id, "032x")


def configure_tracing(service_name: str, exporters: Sequence[SpanExporter]) -> TracerProvider:
    """Register the process-wide `TracerProvider` once; later calls return it unchanged."""
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        return current
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(TraceBindingProcessor())
    for exporter in exporters:
        provider.add_span_processor(_batch_span_processor(exporter))
    trace.set_tracer_provider(provider)
    return provider


async def flush_traces(timeout_ms: int = 5000) -> None:
    """Force-flush the global provider's span processors; log and swallow failures."""
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        return
    try:
        await asyncio.to_thread(provider.force_flush, timeout_ms)
    except Exception:
        logger.warning("failed to flush traces", exc_info=True)


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
