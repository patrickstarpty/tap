from __future__ import annotations

import pytest
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter

from tap.entrypoints import tracing_setup
from tap.entrypoints.tapper_runtime import TapperSettings
from tap.platform.telemetry.mysql_exporter import MysqlSpanExporter
from tests.object_settings import S3_SETTINGS


def test_start_tracing_adds_otlp_exporter_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[SpanExporter]] = []
    stub_provider = object()

    def fake_configure_tracing(service_name: str, exporters: list[SpanExporter]) -> TracerProvider:
        captured.append(list(exporters))
        return stub_provider  # type: ignore[return-value]

    monkeypatch.setattr(tracing_setup, "configure_tracing", fake_configure_tracing)

    without_otlp = TapperSettings.from_mapping(S3_SETTINGS)
    result = tracing_setup.start_tracing("tap-ai-api", without_otlp)

    assert result is stub_provider
    assert len(captured[-1]) == 1
    assert isinstance(captured[-1][0], MysqlSpanExporter)

    with_otlp = TapperSettings.from_mapping(
        S3_SETTINGS | {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:26006"}
    )
    tracing_setup.start_tracing("tap-ai-api", with_otlp)

    assert len(captured[-1]) == 2
    assert isinstance(captured[-1][0], MysqlSpanExporter)
    assert isinstance(captured[-1][1], OTLPSpanExporter)
