"""Per-process tracing initialization for TAP AI API and worker entrypoints.

Lives in `entrypoints/` rather than `platform/telemetry/` because it depends on
`TapperSettings`, and platform code must not import entrypoints settings.
"""

from __future__ import annotations

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from sqlalchemy import create_engine

from tap.entrypoints.tapper_runtime import TapperSettings
from tap.platform.telemetry import configure_tracing
from tap.platform.telemetry.mysql_exporter import MysqlSpanExporter, sync_database_url


def start_tracing(service_name: str, settings: TapperSettings) -> TracerProvider:
    """Register the process-wide `TracerProvider` with the MySQL exporter and,
    when `OTEL_EXPORTER_OTLP_ENDPOINT` is configured, an additional OTLP exporter.
    """
    exporters: list[SpanExporter] = [
        MysqlSpanExporter(
            create_engine(sync_database_url(settings.database_url), pool_pre_ping=True)
        )
    ]
    if settings.otel_exporter_otlp_endpoint:
        exporters.append(
            OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces")
        )
    return configure_tracing(service_name, exporters)


__all__ = ["start_tracing"]
