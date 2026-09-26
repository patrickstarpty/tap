from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
import re
import time

import pytest

from sqlalchemy import create_engine

from tap_platform.insights.adapters.clickhouse import ClickHouseInsightsStore
from tap_platform.insights.adapters.mysql import SqlAlchemyReportLedger, metadata
from tap_platform.insights.application.queries import (
    AttemptDetail,
    FailureDetail,
    MetricQuery,
    QueryFilters,
    QueryLimits,
    QueryRecord,
    QueryTimedOut,
    QueryUnavailable,
    RunSummary,
)
from tap_platform.insights.domain.metrics import MetricId, MetricValue
from tap_platform.insights.domain.projection import ProjectionSnapshot


LIMITS = QueryLimits(
    max_rows_to_read=101,
    max_bytes_to_read=202,
    max_memory_bytes=303,
    max_concurrent_queries=2,
    max_output_rows=11,
    timeout_seconds=1.5,
)


def record() -> QueryRecord:
    return QueryRecord(
        query_id="query-immutable-1",
        project_id="project-a",
        metric_version="insights-metrics-v1",
        query=MetricQuery(
            metric_ids=(MetricId.FIRST_PASS_RATE,),
            filters=QueryFilters(source_ids=("ci-a",)),
            from_date="2026-09-24",
            to_date="2026-09-25",
            timezone="Asia/Shanghai",
            as_of=datetime(2026, 9, 25, tzinfo=UTC),
        ),
        snapshot=ProjectionSnapshot(
            projection_version="insights-v1", visible_data_version=7
        ),
        created_at=datetime(2026, 9, 25, 0, 0, 1, tzinfo=UTC),
        metrics=(
            MetricValue(
                metric_id=MetricId.FIRST_PASS_RATE,
                numerator=1,
                denominator=1,
                value=1.0,
                completeness="complete",
                missing_reasons=(),
                evidence_refs=("receipt-a",),
            ),
        ),
        trends=(),
        runs=(
            RunSummary(
                run_id="run-a",
                external_run_id="external-run-a",
                source_id="ci-a",
                environment="qa",
                configuration="browser=chromium",
                started_at=datetime(2026, 9, 24, 1, tzinfo=UTC),
                instance_count=1,
                evidence_refs=("receipt-a",),
            ),
        ),
        failures=(
            FailureDetail(
                fact_key="fact-a",
                run_id="run-a",
                external_run_id="external-run-a",
                source_id="ci-a",
                stable_test_id="test-a",
                source_test_identity="Checkout.test",
                data_row=None,
                result="fail",
                configuration="browser=chromium",
                evidence_refs=("receipt-a",),
            ),
        ),
        attempts=(
            AttemptDetail(
                fact_key="fact-a",
                run_id="run-a",
                external_run_id="external-run-a",
                stable_test_id="test-a",
                source_test_identity="Checkout.test",
                data_row=None,
                attempt=1,
                result="fail",
                duration_seconds=0.4,
                evidence_refs=("receipt-a",),
            ),
        ),
    )


def test_query_history_round_trips_every_immutable_scope_field() -> None:
    """Dropping a persisted field would silently reinterpret historical queries."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    ledger = SqlAlchemyReportLedger(engine)
    original = record()

    ledger.save_query(original)
    restored = ledger.get_query(original.query_id)

    assert restored == original
    engine.dispose()


def test_as_of_before_first_projection_activation_is_explicit_empty_snapshot() -> None:
    """Pre-activation history must not fall forward to the latest watermark."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    ledger = SqlAlchemyReportLedger(engine)

    snapshot = ledger.projection_snapshot_at(datetime(2000, 1, 1, tzinfo=UTC))

    assert snapshot.projection_version == "insights-v1"
    assert snapshot.visible_data_version == 0
    engine.dispose()


def test_clickhouse_query_template_binds_project_watermark_and_hard_limits(
    monkeypatch,
) -> None:
    """Omitting any server-side bound would permit cross-scope or unbounded scans."""
    store = ClickHouseInsightsStore(
        "http://127.0.0.1:38123/?database=tap_insights",
        username="reader",
        password="non-secret-test-value",
    )
    statements: list[str] = []

    def capture(
        sql: str,
        body: bytes | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> bytes:
        assert body is None
        assert timeout_seconds is not None and 0 < timeout_seconds <= 1.5
        statements.append(sql)
        if "FROM report_batch_markers" in sql:
            return (
                json.dumps(
                    {
                        "project_id": "project-a",
                        "source_id": "ci-a",
                        "external_run_id": "run-a",
                        "report_batch_id": "batch-a",
                        "shard_id": "shard-a",
                        "correction_no": 0,
                        "marker_checksum": "a" * 64,
                    }
                )
                + "\n"
            ).encode()
        return b""

    monkeypatch.setattr(store, "_execute", capture)

    assert (
        store.query_attempts(
            snapshot=ProjectionSnapshot(
                projection_version="insights-v1", visible_data_version=7
            ),
            project_id="project-a",
            query=record().query,
            window=record().query.window(),
            limits=LIMITS,
        )
        == []
    )
    assert statements
    for sql in statements:
        assert sql.startswith("SELECT ")
        assert "project_id = 'project-a'" in sql
        assert "projection_version = 'insights-v1'" in sql
        assert "data_version <= 7" in sql
        assert "max_rows_to_read = 101" in sql
        assert "max_bytes_to_read = 202" in sql
        assert "max_memory_usage = 303" in sql
        assert "max_result_rows = 101" in sql
        assert "max_result_bytes = 202" in sql
        timeout = float(re.search(r"max_execution_time = ([0-9.]+)", sql).group(1))
        assert 0 < timeout <= 1.5
        assert "result_overflow_mode = 'throw'" in sql
    assert any("source_id IN ('ci-a')" in sql for sql in statements)
    assert any(
        "coalesce(fact.started_at, run.started_at) IS NULL OR" in sql
        for sql in statements
    )
    assert any("ANY LEFT JOIN run_dimensions" in sql for sql in statements)


def test_snapshot_resolution_is_inside_end_to_end_timeout() -> None:
    """A slow metadata lookup must consume the same deadline as ClickHouse."""

    class NeverCalledFacts:
        def query_attempts(self, **_: object):
            raise AssertionError("fact query must not start after deadline")

    from tap_platform.insights.application.queries import (
        InMemoryQueryHistory,
        InsightsQueryService,
    )

    def slow_snapshot(_: datetime) -> ProjectionSnapshot:
        time.sleep(0.02)
        return ProjectionSnapshot(
            projection_version="insights-v1", visible_data_version=0
        )

    service = InsightsQueryService(
        facts=NeverCalledFacts(),
        snapshots=slow_snapshot,
        history=InMemoryQueryHistory(),
        clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
        limits=QueryLimits(
            max_rows_to_read=100,
            max_bytes_to_read=1000,
            max_memory_bytes=1000,
            max_concurrent_queries=1,
            max_output_rows=10,
            timeout_seconds=0.001,
        ),
    )

    with pytest.raises(QueryTimedOut):
        service.execute(project_id="project-a", query=record().query)


def test_historical_details_refuse_unknown_metric_semantics_version() -> None:
    """A historical query must never be reinterpreted by current detail code."""
    from tap_platform.insights.application.queries import (
        InMemoryQueryHistory,
        InsightsQueryService,
    )

    history = InMemoryQueryHistory()
    original = replace(record(), metric_version="insights-metrics-v0")
    history.save_query(original)
    service = InsightsQueryService(
        facts=type("EmptyFacts", (), {"query_attempts": lambda self, **kwargs: []})(),
        snapshots=lambda _as_of: original.snapshot,
        history=history,
        clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
        limits=LIMITS,
    )

    with pytest.raises(QueryUnavailable, match="semantics"):
        service.run_page(
            project_id="project-a", query_id=original.query_id, cursor=0, limit=10
        )
