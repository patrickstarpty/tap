"""MySQL-backed OpenTelemetry span exporter; failures degrade to a warning, never raise.

Spans without a `tap.scope.project_id` attribute (i.e. never wrapped in `bind_trace`
with a scope) are skipped here; they may still reach an OTLP exporter configured
alongside this one.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import StatusCode

from tap.platform.telemetry.schema import trace_span

logger = logging.getLogger(__name__)

_ATTRIBUTES_LIMIT_BYTES = 65536
_SCOPE_PREFIX = "tap.scope."


def sync_database_url(url: str) -> str:
    """Return the synchronous (`pymysql`) equivalent of an `asyncmy` database URL."""
    return url.replace("mysql+asyncmy:", "mysql+pymysql:", 1)


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, default=str).encode("utf-8"))


def _bounded_attributes(attributes: Mapping[str, object]) -> dict[str, object]:
    """Drop `tap.scope.*` keys and truncate the largest attributes to fit the byte cap."""
    cleaned = {key: value for key, value in attributes.items() if not key.startswith(_SCOPE_PREFIX)}
    if _json_bytes(cleaned) <= _ATTRIBUTES_LIMIT_BYTES:
        return cleaned
    remaining = dict(cleaned)
    while remaining:
        candidate: dict[str, object] = {**remaining, "tap.truncated": True}
        if _json_bytes(candidate) <= _ATTRIBUTES_LIMIT_BYTES:
            return candidate
        largest_key = max(remaining, key=lambda key: _json_bytes({key: remaining[key]}))
        del remaining[largest_key]
    return {"tap.truncated": True}


def _row(span: ReadableSpan) -> dict[str, object] | None:
    attributes: Mapping[str, object] = span.attributes or {}
    project_id = attributes.get("tap.scope.project_id")
    if project_id is None:
        return None
    context = span.context
    assert context is not None
    identity_mode = attributes.get("tap.scope.identity_mode")
    start_time = span.start_time or 0
    end_time = span.end_time or start_time
    started_at = datetime.fromtimestamp(start_time / 1_000_000_000, tz=timezone.utc)
    resource_attributes = span.resource.attributes if span.resource is not None else {}
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
        "parent_span_id": format(span.parent.span_id, "016x") if span.parent else None,
        "enterprise_id": attributes.get("tap.scope.enterprise_id"),
        "project_id": project_id,
        "actor_id": attributes.get("tap.scope.actor_id"),
        "identity_mode": identity_mode,
        "identity_origin": str(identity_mode).upper() if identity_mode is not None else None,
        "turn_id": attributes.get("tap.turn_id"),
        "job_id": attributes.get("tap.job_id"),
        "service_name": resource_attributes.get("service.name"),
        "name": span.name,
        "status": "error" if span.status.status_code == StatusCode.ERROR else "ok",
        "status_message": span.status.description,
        "started_at": started_at.replace(tzinfo=None),
        "duration_ms": (end_time - start_time) // 1_000_000,
        "attributes": _bounded_attributes(attributes),
    }


class MysqlSpanExporter(SpanExporter):
    """Persist scoped spans to `trace_span` in one transaction per export batch."""

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        rows = [row for span in spans if (row := _row(span)) is not None]
        if not rows:
            return SpanExportResult.SUCCESS
        try:
            statement = sa.insert(trace_span).prefix_with("IGNORE")
            with self._engine.begin() as connection:
                connection.execute(statement, rows)
            return SpanExportResult.SUCCESS
        except Exception:
            logger.warning("failed to export spans to mysql", exc_info=True)
            return SpanExportResult.FAILURE


__all__ = ["MysqlSpanExporter", "sync_database_url"]
