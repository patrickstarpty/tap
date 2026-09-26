"""Bounded Insights queries and immutable query history."""

from __future__ import annotations

import threading
import uuid
import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal, Protocol, cast

from tap_platform.insights.domain.metrics import (
    METRIC_VERSION,
    MetricAttempt,
    MetricId,
    MetricValue,
    MetricWindow,
    calculate_metrics,
)
from tap_platform.insights.domain.projection import ProjectionSnapshot


@dataclass(frozen=True, slots=True, kw_only=True)
class QueryLimits:
    max_rows_to_read: int
    max_bytes_to_read: int
    max_memory_bytes: int
    max_concurrent_queries: int
    max_output_rows: int
    timeout_seconds: float
    max_output_bytes: int = 4_000_000

    def __post_init__(self) -> None:
        values = (
            self.max_rows_to_read,
            self.max_bytes_to_read,
            self.max_memory_bytes,
            self.max_concurrent_queries,
            self.max_output_rows,
            self.max_output_bytes,
        )
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("query limits must be positive integers")
        if self.timeout_seconds <= 0:
            raise ValueError("query timeout must be positive")


@dataclass(frozen=True, slots=True, kw_only=True)
class QueryFilters:
    source_ids: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    configurations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class MetricQuery:
    metric_ids: tuple[MetricId, ...]
    filters: QueryFilters
    from_date: str
    to_date: str
    timezone: str
    as_of: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class RunSummary:
    run_id: str
    source_id: str
    environment: str
    configuration: str
    started_at: datetime | None
    instance_count: int
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class FailureDetail:
    fact_key: str
    run_id: str
    source_id: str
    stable_test_id: str | None
    source_test_identity: str
    data_row: str | None
    result: Literal["fail", "error"]
    configuration: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AttemptDetail:
    fact_key: str
    run_id: str
    stable_test_id: str | None
    source_test_identity: str
    data_row: str | None
    attempt: int | None
    result: str
    duration_seconds: float | None
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class TrendPoint:
    local_date: str
    metrics: tuple[MetricValue, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class QueryRecord:
    query_id: str
    project_id: str
    metric_version: str
    query: MetricQuery
    snapshot: ProjectionSnapshot
    created_at: datetime
    metrics: tuple[MetricValue, ...]
    trends: tuple[TrendPoint, ...]
    runs: tuple[RunSummary, ...]
    failures: tuple[FailureDetail, ...]
    attempts: tuple[AttemptDetail, ...]


class QueryFactSource(Protocol):
    def query_attempts(
        self,
        *,
        snapshot: ProjectionSnapshot,
        project_id: str,
        limits: QueryLimits,
    ) -> list[MetricAttempt]: ...


class QueryHistory(Protocol):
    def save_query(self, record: QueryRecord) -> None: ...
    def get_query(self, query_id: str) -> QueryRecord: ...


class InMemoryQueryHistory:
    def __init__(self) -> None:
        self._records: dict[str, QueryRecord] = {}

    def save_query(self, record: QueryRecord) -> None:
        if record.query_id in self._records:
            raise ValueError("query ID already exists")
        self._records[record.query_id] = record

    def get_query(self, query_id: str) -> QueryRecord:
        try:
            return self._records[query_id]
        except KeyError as exc:
            raise KeyError(query_id) from exc


class QueryUnavailable(RuntimeError):
    pass


class QueryLimitExceeded(RuntimeError):
    pass


class QueryTimedOut(RuntimeError):
    pass


class InsightsQueryService:
    def __init__(
        self,
        *,
        facts: QueryFactSource,
        snapshots: Callable[[datetime], ProjectionSnapshot],
        history: QueryHistory,
        clock: Callable[[], datetime],
        limits: QueryLimits,
    ) -> None:
        self._facts = facts
        self._snapshots = snapshots
        self._history = history
        self._clock = clock
        self._limits = limits
        self._capacity = threading.BoundedSemaphore(limits.max_concurrent_queries)

    def execute(self, *, project_id: str, query: MetricQuery) -> QueryRecord:
        if not self._capacity.acquire(blocking=False):
            raise QueryLimitExceeded("query concurrency limit exceeded")
        try:
            snapshot = self._snapshots(query.as_of)
            try:
                attempts = self._facts.query_attempts(
                    snapshot=snapshot,
                    project_id=project_id,
                    limits=self._limits,
                )
            except QueryLimitExceeded:
                raise
            except TimeoutError as exc:
                raise QueryTimedOut("insights query timed out") from exc
            except Exception as exc:
                raise QueryUnavailable("insights fact source unavailable") from exc
            if len(attempts) > self._limits.max_output_rows:
                raise QueryLimitExceeded("query output row limit exceeded")
            if any(item.project_id != project_id for item in attempts):
                raise QueryUnavailable("fact source returned cross-project data")
            selected = _apply_filters(attempts, query.filters)
            window = MetricWindow.from_local_dates(
                start_date=query.from_date,
                end_date=query.to_date,
                timezone=query.timezone,
            )
            metrics = calculate_metrics(
                selected, metric_ids=query.metric_ids, window=window
            )
            record = QueryRecord(
                query_id=str(uuid.uuid4()),
                project_id=project_id,
                metric_version=METRIC_VERSION,
                query=query,
                snapshot=snapshot,
                created_at=self._clock(),
                metrics=metrics,
                trends=_trend_points(selected, query=query),
                runs=_run_summaries(selected, window=window),
                failures=_failure_details(selected, window=window),
                attempts=_attempt_details(selected, window=window),
            )
            output_bytes = len(
                json.dumps(
                    asdict(record),
                    sort_keys=True,
                    separators=(",", ":"),
                    default=lambda value: value.isoformat(),
                ).encode()
            )
            if output_bytes > self._limits.max_output_bytes:
                raise QueryLimitExceeded("query output byte limit exceeded")
            self._history.save_query(record)
            return record
        finally:
            self._capacity.release()

    def historical(self, *, project_id: str, query_id: str) -> QueryRecord:
        record = self._history.get_query(query_id)
        if record.project_id != project_id:
            raise KeyError(query_id)
        return record


def _apply_filters(
    attempts: list[MetricAttempt], filters: QueryFilters
) -> list[MetricAttempt]:
    return [
        item
        for item in attempts
        if (not filters.source_ids or item.source_id in filters.source_ids)
        and (not filters.run_ids or item.external_run_id in filters.run_ids)
        and (not filters.environments or item.environment in filters.environments)
        and (not filters.configurations or item.configuration in filters.configurations)
    ]


def _run_summaries(
    attempts: list[MetricAttempt], *, window: MetricWindow
) -> tuple[RunSummary, ...]:
    grouped: dict[tuple[str, str, str, str, datetime], list[MetricAttempt]] = (
        defaultdict(list)
    )
    for item in attempts:
        if item.run_started_at is not None and window.contains(item.run_started_at):
            grouped[
                (
                    item.external_run_id,
                    item.source_id,
                    item.environment,
                    item.configuration,
                    item.run_started_at,
                )
            ].append(item)
    return tuple(
        RunSummary(
            run_id=key[0],
            source_id=key[1],
            environment=key[2],
            configuration=key[3],
            started_at=key[4],
            instance_count=len({item.instance_key for item in items}),
            evidence_refs=tuple(sorted({item.receipt_id for item in items})),
        )
        for key, items in sorted(grouped.items())
    )


def _failure_details(
    attempts: list[MetricAttempt], *, window: MetricWindow
) -> tuple[FailureDetail, ...]:
    grouped: dict[tuple[str, ...], list[MetricAttempt]] = defaultdict(list)
    for item in attempts:
        if item.run_started_at is not None and window.contains(item.run_started_at):
            grouped[item.instance_key].append(item)
    failures: list[FailureDetail] = []
    for items in grouped.values():
        final = max(
            items, key=lambda item: (item.attempt is not None, item.attempt or 0)
        )
        if final.result not in {"fail", "error"}:
            continue
        failures.append(
            FailureDetail(
                fact_key=final.fact_key,
                run_id=final.external_run_id,
                source_id=final.source_id,
                stable_test_id=final.stable_test_id,
                source_test_identity=final.source_test_identity,
                data_row=final.data_row,
                result=cast(Literal["fail", "error"], final.result),
                configuration=final.configuration,
                evidence_refs=tuple(sorted({item.receipt_id for item in items})),
            )
        )
    return tuple(sorted(failures, key=lambda item: (item.run_id, item.fact_key)))


def _trend_points(
    attempts: list[MetricAttempt], *, query: MetricQuery
) -> tuple[TrendPoint, ...]:
    window = MetricWindow.from_local_dates(
        start_date=query.from_date,
        end_date=query.to_date,
        timezone=query.timezone,
    )
    by_day: dict[str, list[MetricAttempt]] = defaultdict(list)
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(query.timezone)
    for item in attempts:
        if item.run_started_at is not None and window.contains(item.run_started_at):
            by_day[item.run_started_at.astimezone(zone).date().isoformat()].append(item)
    return tuple(
        TrendPoint(
            local_date=local_date,
            metrics=calculate_metrics(
                items,
                metric_ids=query.metric_ids,
                window=window,
            ),
        )
        for local_date, items in sorted(by_day.items())
    )


def _attempt_details(
    attempts: list[MetricAttempt], *, window: MetricWindow
) -> tuple[AttemptDetail, ...]:
    return tuple(
        AttemptDetail(
            fact_key=item.fact_key,
            run_id=item.external_run_id,
            stable_test_id=item.stable_test_id,
            source_test_identity=item.source_test_identity,
            data_row=item.data_row,
            attempt=item.attempt,
            result=item.result,
            duration_seconds=item.duration_seconds,
            evidence_refs=(item.receipt_id,),
        )
        for item in sorted(
            attempts,
            key=lambda value: (
                value.external_run_id,
                value.stable_test_id or value.source_test_identity,
                value.data_row or "",
                value.attempt is None,
                value.attempt or 0,
            ),
        )
        if item.run_started_at is not None and window.contains(item.run_started_at)
    )
