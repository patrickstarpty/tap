"""MysqlSpanExporter integration tests: binding columns, truncation, failure isolation."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span, Status, StatusCode, Tracer
from scripts.migration_support import IsolatedMysql

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.platform.telemetry import TraceBindingProcessor, bind_trace
from tap.platform.telemetry.mysql_exporter import MysqlSpanExporter, sync_database_url
from tap.platform.telemetry.schema import trace_span
from tests.owned_mysql import owned_project_database_url

SERVICE_NAME = "tap-ai-test-exporter"


def _local_tracer() -> tuple[Tracer, InMemorySpanExporter]:
    """Build an isolated provider so tests never touch the process-wide TracerProvider."""
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    provider.add_span_processor(TraceBindingProcessor())
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test-mysql-span-exporter"), exporter


@contextmanager
def _recorded_span(
    tracer: Tracer, name: str, attributes: dict[str, object] | None = None
) -> Iterator[Span]:
    """Start a span that marks itself ERROR the same way `platform.telemetry.span` does."""
    with tracer.start_as_current_span(
        name,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as current:
        try:
            yield current
        except Exception as exc:
            current.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


def _sync_engine(owned_project_mysql: IsolatedMysql) -> sa.Engine:
    return sa.create_engine(sync_database_url(owned_project_database_url(owned_project_mysql)))


def test_scoped_span_is_written_with_binding_columns(
    owned_project_mysql: IsolatedMysql,
) -> None:
    tracer, capture = _local_tracer()
    with bind_trace(scope=VALIDATION_SCOPE, turn_id="t1", attempt=1):
        with _recorded_span(tracer, "outer") as outer:
            with _recorded_span(tracer, "inner"):
                pass
    recorded: list[ReadableSpan] = capture.get_finished_spans()
    assert len(recorded) == 2

    engine = _sync_engine(owned_project_mysql)
    try:
        exporter = MysqlSpanExporter(engine)
        assert exporter.export(recorded) == SpanExportResult.SUCCESS

        with engine.connect() as connection:
            rows = (
                connection.execute(sa.select(trace_span).order_by(trace_span.c.name))
                .mappings()
                .all()
            )
        assert len(rows) == 2
        by_name = {row["name"]: row for row in rows}
        assert by_name["outer"]["parent_span_id"] is None
        assert by_name["inner"]["parent_span_id"] == format(
            outer.get_span_context().span_id, "016x"
        )
        assert by_name["outer"]["trace_id"] == format(outer.get_span_context().trace_id, "032x")
        assert by_name["outer"]["turn_id"] == "t1"
        assert by_name["outer"]["service_name"] == SERVICE_NAME
        assert by_name["outer"]["status"] == "ok"
        assert "tap.scope.project_id" not in (by_name["outer"]["attributes"] or {})
    finally:
        engine.dispose()


def test_error_span_records_status_message(owned_project_mysql: IsolatedMysql) -> None:
    tracer, capture = _local_tracer()
    with bind_trace(scope=VALIDATION_SCOPE, turn_id="t2"):
        try:
            with _recorded_span(tracer, "boom"):
                raise ValueError("boom")
        except ValueError:
            pass
    recorded = capture.get_finished_spans()
    assert len(recorded) == 1

    engine = _sync_engine(owned_project_mysql)
    try:
        exporter = MysqlSpanExporter(engine)
        assert exporter.export(recorded) == SpanExportResult.SUCCESS

        with engine.connect() as connection:
            row = (
                connection.execute(sa.select(trace_span).where(trace_span.c.name == "boom"))
                .mappings()
                .one()
            )
        assert row["status"] == "error"
        assert row["status_message"] == "ValueError"
    finally:
        engine.dispose()


def test_oversized_attributes_are_truncated(owned_project_mysql: IsolatedMysql) -> None:
    tracer, capture = _local_tracer()
    with bind_trace(scope=VALIDATION_SCOPE, turn_id="t3"):
        with _recorded_span(tracer, "oversized", {"big": "x" * 70000, "small": "x"}):
            pass
    recorded = capture.get_finished_spans()

    engine = _sync_engine(owned_project_mysql)
    try:
        exporter = MysqlSpanExporter(engine)
        assert exporter.export(recorded) == SpanExportResult.SUCCESS

        with engine.connect() as connection:
            row = (
                connection.execute(sa.select(trace_span).where(trace_span.c.name == "oversized"))
                .mappings()
                .one()
            )
        attributes = row["attributes"]
        assert len(json.dumps(attributes).encode("utf-8")) <= 65536
        assert "big" not in attributes
        assert attributes["small"] == "x"
        assert attributes["tap.truncated"] is True
    finally:
        engine.dispose()


def test_unscoped_span_is_skipped(owned_project_mysql: IsolatedMysql) -> None:
    tracer, capture = _local_tracer()
    with _recorded_span(tracer, "unscoped"):
        pass
    recorded = capture.get_finished_spans()
    assert len(recorded) == 1

    engine = _sync_engine(owned_project_mysql)
    try:
        exporter = MysqlSpanExporter(engine)
        assert exporter.export(recorded) == SpanExportResult.SUCCESS

        with engine.connect() as connection:
            rows = connection.execute(sa.select(trace_span)).all()
        assert rows == []
    finally:
        engine.dispose()


def test_database_failure_returns_failure_without_raising(
    owned_project_mysql: IsolatedMysql,
    caplog,
) -> None:
    del owned_project_mysql  # gates the module behind TAP_RUN_MYSQL_INTEGRATION; unused here
    tracer, capture = _local_tracer()
    with bind_trace(scope=VALIDATION_SCOPE, turn_id="t4"):
        with _recorded_span(tracer, "unreachable"):
            pass
    recorded = capture.get_finished_spans()

    broken_engine = sa.create_engine("mysql+pymysql://tap:tap@127.0.0.1:1/tap")
    try:
        exporter = MysqlSpanExporter(broken_engine)
        with caplog.at_level(logging.WARNING):
            result = exporter.export(recorded)
        assert result == SpanExportResult.FAILURE
        assert any(record.levelno >= logging.WARNING for record in caplog.records)
    finally:
        broken_engine.dispose()
