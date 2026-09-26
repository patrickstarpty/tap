from __future__ import annotations

from datetime import UTC, datetime

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

    def capture(sql: str, body: bytes | None = None) -> bytes:
        assert body is None
        statements.append(sql)
        return b""

    monkeypatch.setattr(store, "_execute", capture)

    assert (
        store.query_attempts(
            snapshot=ProjectionSnapshot(
                projection_version="insights-v1", visible_data_version=7
            ),
            project_id="project-a",
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
        assert "max_result_rows = 11" in sql
        assert "max_execution_time = 1.5" in sql
        assert "result_overflow_mode = 'throw'" in sql
