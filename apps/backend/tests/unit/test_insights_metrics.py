from datetime import UTC, datetime
import threading
import time

import pytest

from tap_platform.insights.application.queries import (
    InMemoryQueryHistory,
    InsightsQueryService,
    MetricQuery,
    QueryFilters,
    QueryLimitExceeded,
    QueryLimits,
)
from tap_platform.insights.domain.projection import ProjectionSnapshot

from tap_platform.insights.domain.metrics import (
    MetricAttempt,
    MetricId,
    MetricWindow,
    calculate_metrics,
)


def attempt(
    *,
    test_id: str,
    attempt_no: int | None,
    result: str,
    title: str | None = None,
    configuration: str = "browser=chromium",
    first_attempt_eligible: bool = True,
    run_id: str = "run-1",
    started_at: datetime = datetime(2026, 9, 23, 16, tzinfo=UTC),
    duration_seconds: float = 0.25,
    build_id: str | None = "build-1",
    branch: str | None = "main",
) -> MetricAttempt:
    return MetricAttempt(
        fact_key=f"{run_id}:{test_id}:{configuration}:{attempt_no}",
        receipt_id=f"receipt-{run_id}",
        project_id="project-a",
        source_id="ci-a",
        external_run_id=run_id,
        stable_test_id=test_id,
        source_test_identity=title or test_id,
        data_row=None,
        application_commit="app-1",
        script_commit="script-1",
        environment="qa",
        configuration=configuration,
        attempt=attempt_no,
        result=result,
        duration_seconds=duration_seconds,
        first_attempt_eligible=first_attempt_eligible,
        missing_reasons=() if first_attempt_eligible else ("missing-attempt-identity",),
        run_started_at=started_at,
        build_id=build_id,
        branch=branch,
    )


def metric_map(attempts: list[MetricAttempt]):
    results = calculate_metrics(
        attempts,
        metric_ids=tuple(MetricId),
        window=MetricWindow(
            start=datetime(2026, 9, 23, tzinfo=UTC),
            end=datetime(2026, 9, 25, tzinfo=UTC),
            timezone="Asia/Shanghai",
        ),
    )
    return {result.metric_id: result for result in results}


def test_failure_details_sort_attempts_before_checking_first_eligibility() -> None:
    from tap_platform.insights.application.queries import _failure_details_v1

    facts = [
        attempt(test_id="a", attempt_no=2, result="fail", first_attempt_eligible=False),
        attempt(test_id="a", attempt_no=1, result="fail"),
    ]
    assert metric_map(facts)[MetricId.FINAL_PASS_RATE].value == 0.0
    details = _failure_details_v1(
        facts,
        window=MetricWindow(
            start=datetime(2026, 9, 23, tzinfo=UTC),
            end=datetime(2026, 9, 25, tzinfo=UTC),
            timezone="UTC",
        ),
        deadline=time.monotonic() + 5,
    )
    assert [(item.fact_key, item.result) for item in details] == [
        ("run-1:a:browser=chromium:2", "fail")
    ]


def test_oracle_uses_distinct_first_final_and_recovery_denominators() -> None:
    """Merging recovery denominator with D would change the literal 1/2."""
    results = metric_map(
        [
            attempt(test_id="a", attempt_no=1, result="pass"),
            attempt(test_id="b", attempt_no=1, result="fail"),
            attempt(test_id="b", attempt_no=2, result="pass"),
            attempt(test_id="c", attempt_no=1, result="error"),
            attempt(test_id="d", attempt_no=1, result="skipped"),
        ]
    )

    assert (
        results[MetricId.FIRST_PASS_RATE].numerator,
        results[MetricId.FIRST_PASS_RATE].denominator,
        results[MetricId.FIRST_PASS_RATE].value,
    ) == (1, 3, 1 / 3)
    assert (
        results[MetricId.FINAL_PASS_RATE].numerator,
        results[MetricId.FINAL_PASS_RATE].denominator,
        results[MetricId.FINAL_PASS_RATE].value,
    ) == (2, 3, 2 / 3)
    assert (
        results[MetricId.RETRY_RECOVERY_RATE].numerator,
        results[MetricId.RETRY_RECOVERY_RATE].denominator,
        results[MetricId.RETRY_RECOVERY_RATE].value,
    ) == (1, 2, 0.5)
    assert (
        results[MetricId.RECOVERY_CONTRIBUTION_RATE].numerator,
        results[MetricId.RECOVERY_CONTRIBUTION_RATE].denominator,
        results[MetricId.RECOVERY_CONTRIBUTION_RATE].value,
    ) == (1, 3, 1 / 3)
    assert results[MetricId.SKIPPED_COUNT].numerator == 1


def test_p95_duration_is_a_server_metric_over_summed_instance_attempts() -> None:
    """A browser percentile or per-attempt percentile would publish a different value."""
    results = metric_map(
        [
            attempt(test_id="a", attempt_no=1, result="pass", duration_seconds=1.0),
            attempt(test_id="b", attempt_no=1, result="fail", duration_seconds=2.0),
            attempt(test_id="b", attempt_no=2, result="pass", duration_seconds=3.0),
            attempt(test_id="c", attempt_no=1, result="error", duration_seconds=4.0),
        ]
    )

    duration = results[MetricId.P95_DURATION_SECONDS]
    assert (duration.value, duration.denominator, duration.completeness) == (
        5.0,
        3,
        "complete",
    )


def test_build_and_branch_filters_are_authoritative_query_scope() -> None:
    """Ignoring either dimension would leak a second run into every metric denominator."""
    facts = [
        attempt(test_id="a", attempt_no=1, result="pass"),
        attempt(
            test_id="b",
            attempt_no=1,
            result="fail",
            run_id="run-2",
            build_id="build-2",
            branch="release",
        ),
    ]
    service = InsightsQueryService(
        facts=type("Facts", (), {"query_attempts": lambda self, **_: facts})(),
        snapshots=lambda _as_of: ProjectionSnapshot(
            projection_version="insights-v1", visible_data_version=2
        ),
        history=InMemoryQueryHistory(),
        clock=lambda: datetime(2026, 9, 24, tzinfo=UTC),
        limits=QueryLimits(
            max_rows_to_read=10,
            max_bytes_to_read=100_000,
            max_memory_bytes=100_000,
            max_concurrent_queries=1,
            max_output_rows=10,
            timeout_seconds=1,
        ),
    )
    record = service.execute(
        project_id="project-a",
        query=MetricQuery(
            metric_ids=(MetricId.FINAL_PASS_RATE,),
            filters=QueryFilters(build_ids=("build-1",), branches=("main",)),
            from_date="2026-09-23",
            to_date="2026-09-25",
            timezone="UTC",
            as_of=datetime(2026, 9, 24, tzinfo=UTC),
        ),
    )

    assert (record.metrics[0].numerator, record.metrics[0].denominator) == (1, 1)


def test_oracle_uses_first_and_final_valid_terminal_attempts() -> None:
    """Nonterminal observations must not change the paired terminal formula."""
    results = metric_map(
        [
            attempt(test_id="a", attempt_no=1, result="skipped"),
            attempt(test_id="a", attempt_no=2, result="pass"),
            attempt(test_id="b", attempt_no=1, result="fail"),
            attempt(test_id="b", attempt_no=2, result="pass"),
            attempt(test_id="b", attempt_no=3, result="canceled"),
        ]
    )

    assert (
        results[MetricId.FIRST_PASS_RATE].numerator,
        results[MetricId.FIRST_PASS_RATE].denominator,
    ) == (1, 2)
    assert (
        results[MetricId.FINAL_PASS_RATE].numerator,
        results[MetricId.FINAL_PASS_RATE].denominator,
    ) == (2, 2)
    assert (
        results[MetricId.RETRY_RECOVERY_RATE].numerator,
        results[MetricId.RETRY_RECOVERY_RATE].denominator,
    ) == (1, 1)
    assert results[MetricId.SKIPPED_COUNT].numerator == 1


def test_missing_first_history_makes_paired_metrics_unavailable_not_zero() -> None:
    """Treating a final-only pass as attempt one would fabricate first-pass data."""
    results = metric_map(
        [
            attempt(test_id="a", attempt_no=1, result="pass"),
            attempt(
                test_id="b",
                attempt_no=None,
                result="pass",
                first_attempt_eligible=False,
            ),
        ]
    )

    for metric_id in (
        MetricId.FIRST_PASS_RATE,
        MetricId.FINAL_PASS_RATE,
        MetricId.RETRY_RECOVERY_RATE,
        MetricId.RECOVERY_CONTRIBUTION_RATE,
    ):
        result = results[metric_id]
        assert result.completeness == "unavailable"
        assert result.value is None
        assert result.numerator is None
        assert result.denominator is None
        assert "missing-first-attempt-history" in result.missing_reasons


def test_unknown_later_attempt_number_still_makes_paired_metrics_unavailable() -> None:
    """A known attempt one does not make an unorderable later result safe to pair."""
    results = metric_map(
        [
            attempt(test_id="b", attempt_no=1, result="fail"),
            attempt(
                test_id="b",
                attempt_no=None,
                result="pass",
                first_attempt_eligible=False,
            ),
        ]
    )

    assert results[MetricId.FINAL_PASS_RATE].completeness == "unavailable"
    assert results[MetricId.FINAL_PASS_RATE].value is None


def test_zero_denominator_returns_null_and_empty_not_normal_zero() -> None:
    """An all-skipped run must not be reported as a 0% pass rate."""
    results = metric_map([attempt(test_id="d", attempt_no=1, result="skipped")])

    first = results[MetricId.FIRST_PASS_RATE]
    assert (first.numerator, first.denominator, first.value) == (0, 0, None)
    assert first.completeness == "empty"
    assert first.missing_reasons == ("zero-denominator",)
    assert results[MetricId.SKIPPED_COUNT].value == 1.0


def test_undated_fact_makes_every_requested_metric_unavailable() -> None:
    """A time-scoped query cannot claim a complete count for an undated fact."""
    undated = attempt(test_id="a", attempt_no=1, result="pass")
    undated = MetricAttempt(
        **{
            field: getattr(undated, field)
            for field in undated.__dataclass_fields__
            if field != "run_started_at"
        },
        run_started_at=None,
    )

    results = metric_map([undated])

    assert all(item.completeness == "unavailable" for item in results.values())
    assert all(item.value is None for item in results.values())
    assert all(
        "missing-run-start-time" in item.missing_reasons for item in results.values()
    )


def test_same_title_and_different_configurations_remain_distinct_instances() -> None:
    """Grouping by display title or omitting configuration would collapse instances."""
    results = metric_map(
        [
            attempt(
                test_id="stable-a",
                title="same title",
                attempt_no=1,
                result="pass",
            ),
            attempt(
                test_id="stable-b",
                title="same title",
                attempt_no=1,
                result="fail",
            ),
            attempt(
                test_id="stable-a",
                title="same title",
                configuration="browser=firefox",
                attempt_no=1,
                result="error",
            ),
        ]
    )

    first = results[MetricId.FIRST_PASS_RATE]
    assert (first.numerator, first.denominator) == (1, 3)


def test_same_external_run_id_in_distinct_configurations_has_distinct_run_keys() -> (
    None
):
    """External run labels are not globally unique drilldown identities."""
    rows = [
        attempt(test_id="a", attempt_no=1, result="pass"),
        attempt(
            test_id="a",
            configuration="browser=firefox",
            attempt_no=1,
            result="fail",
        ),
    ]

    class Facts:
        def query_attempts(self, **_: object) -> list[MetricAttempt]:
            return rows

    service = InsightsQueryService(
        facts=Facts(),
        snapshots=lambda _as_of: ProjectionSnapshot(
            projection_version="insights-v1", visible_data_version=1
        ),
        history=InMemoryQueryHistory(),
        clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
        limits=QueryLimits(
            max_rows_to_read=100,
            max_bytes_to_read=1000,
            max_memory_bytes=10_000,
            max_concurrent_queries=1,
            max_output_rows=10,
            timeout_seconds=1,
        ),
    )
    query = MetricQuery(
        metric_ids=(MetricId.FIRST_PASS_RATE,),
        filters=QueryFilters(),
        from_date="2026-09-23",
        to_date="2026-09-25",
        timezone="UTC",
        as_of=datetime(2026, 9, 25, tzinfo=UTC),
    )

    record = service.execute(project_id="project-a", query=query)
    _, runs, _ = service.run_page(
        project_id="project-a", query_id=record.query_id, cursor=0, limit=10
    )

    assert {item.external_run_id for item in runs} == {"run-1"}
    assert len({item.run_id for item in runs}) == 2


def test_timezone_window_is_start_inclusive_and_end_exclusive() -> None:
    """Comparing date strings instead of instants would move UTC boundary runs."""
    results = calculate_metrics(
        [
            attempt(
                test_id="before",
                attempt_no=1,
                result="fail",
                started_at=datetime(2026, 9, 23, 15, 59, 59, tzinfo=UTC),
            ),
            attempt(
                test_id="start",
                attempt_no=1,
                result="pass",
                started_at=datetime(2026, 9, 23, 16, tzinfo=UTC),
            ),
            attempt(
                test_id="end",
                attempt_no=1,
                result="fail",
                started_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
            ),
        ],
        metric_ids=(MetricId.FIRST_PASS_RATE,),
        window=MetricWindow.from_local_dates(
            start_date="2026-09-24",
            end_date="2026-09-25",
            timezone="Asia/Shanghai",
        ),
    )

    assert (results[0].numerator, results[0].denominator) == (1, 1)


def test_concurrency_limit_rejects_second_query_instead_of_queueing_unbounded() -> None:
    """Replacing nonblocking capacity with an unbounded wait would hide overload."""
    entered = threading.Event()
    release = threading.Event()

    class BlockingFacts:
        def query_attempts(self, **_: object) -> list[MetricAttempt]:
            entered.set()
            assert release.wait(timeout=5)
            return []

    service = InsightsQueryService(
        facts=BlockingFacts(),
        snapshots=lambda _as_of: ProjectionSnapshot(
            projection_version="insights-v1", visible_data_version=0
        ),
        history=InMemoryQueryHistory(),
        clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
        limits=QueryLimits(
            max_rows_to_read=100,
            max_bytes_to_read=1000,
            max_memory_bytes=1000,
            max_concurrent_queries=1,
            max_output_rows=10,
            timeout_seconds=1,
        ),
    )
    query = MetricQuery(
        metric_ids=(MetricId.FIRST_PASS_RATE,),
        filters=QueryFilters(),
        from_date="2026-09-24",
        to_date="2026-09-25",
        timezone="UTC",
        as_of=datetime(2026, 9, 25, tzinfo=UTC),
    )
    first = threading.Thread(
        target=service.execute, kwargs={"project_id": "project-a", "query": query}
    )
    first.start()
    assert entered.wait(timeout=2)
    try:
        with pytest.raises(QueryLimitExceeded, match="concurrency"):
            service.execute(project_id="project-a", query=query)
    finally:
        release.set()
        first.join(timeout=5)
    assert not first.is_alive()


def test_as_of_is_passed_to_snapshot_authority() -> None:
    """Taking the latest watermark would make a historical as-of include late facts."""
    seen: list[datetime] = []
    as_of = datetime(2026, 9, 24, 12, tzinfo=UTC)
    service = InsightsQueryService(
        facts=type(
            "EmptyFacts",
            (),
            {"query_attempts": lambda self, **kwargs: []},
        )(),
        snapshots=lambda requested: (
            seen.append(requested)
            or ProjectionSnapshot(
                projection_version="insights-v1", visible_data_version=4
            )
        ),
        history=InMemoryQueryHistory(),
        clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
        limits=QueryLimits(
            max_rows_to_read=100,
            max_bytes_to_read=1000,
            max_memory_bytes=1000,
            max_concurrent_queries=1,
            max_output_rows=10,
            timeout_seconds=1,
        ),
    )
    query = MetricQuery(
        metric_ids=(MetricId.FIRST_PASS_RATE,),
        filters=QueryFilters(),
        from_date="2026-09-24",
        to_date="2026-09-25",
        timezone="UTC",
        as_of=as_of,
    )

    result = service.execute(project_id="project-a", query=query)

    assert seen == [as_of]
    assert result.snapshot.visible_data_version == 4


def test_daily_trends_scope_marker_coverage_and_include_zero_attempt_days(monkeypatch):
    """An incomplete empty report must affect its own local day, not every day."""
    import json
    from tap_platform.insights.adapters.clickhouse import ClickHouseInsightsStore
    from tap_platform.insights.application.queries import _trend_points

    query = MetricQuery(
        metric_ids=(MetricId.FIRST_PASS_RATE,),
        filters=QueryFilters(),
        from_date="2026-09-24",
        to_date="2026-09-27",
        timezone="Asia/Shanghai",
        as_of=datetime(2026, 9, 27, tzinfo=UTC),
    )
    markers = []
    for run_id, started_at, expected in (
        ("run-1", "2026-09-23T16:00:00Z", 1),
        ("empty-run", "2026-09-24T16:00:00Z", 2),
        ("undated-run", None, 2),
    ):
        markers.append(
            dict(
                project_id="project-a",
                source_id="ci-a",
                external_run_id=run_id,
                report_batch_id=run_id,
                shard_id="1",
                correction_no=0,
                marker_checksum=run_id,
                expected_shards=expected,
                contains_complete_attempts=1,
                started_at=started_at,
            )
        )
    store = ClickHouseInsightsStore(
        "http://127.0.0.1:38123/?database=tap_insights",
        username="reader",
        password="test",
    )
    monkeypatch.setattr(
        store,
        "_execute",
        lambda *args, **kwargs: "\n".join(json.dumps(row) for row in markers).encode(),
    )
    coverage = store.query_report_coverage(
        snapshot=ProjectionSnapshot(
            projection_version="insights-v1", visible_data_version=3
        ),
        project_id="project-a",
        query=query,
        window=query.window(),
        limits=QueryLimits(
            max_rows_to_read=100,
            max_bytes_to_read=100000,
            max_memory_bytes=100000,
            max_concurrent_queries=1,
            max_output_rows=100,
            timeout_seconds=1,
        ),
    )
    trends = {
        point.local_date: point.metrics[0]
        for point in _trend_points(
            [attempt(test_id="a", attempt_no=1, result="pass")],
            query=query,
            report_coverage=coverage,
        )
    }
    assert trends["2026-09-24"].value == 1.0
    assert trends["2026-09-24"].completeness == "complete"
    assert trends["2026-09-25"].completeness == "unavailable"
    assert "report-shards-missing" in trends["2026-09-25"].missing_reasons
    assert trends["undated"].completeness == "unavailable"
