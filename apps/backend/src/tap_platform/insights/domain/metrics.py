"""Versioned, pure metric formulas for TAP Insights."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import StrEnum
import math
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


METRIC_VERSION = "insights-metrics-v1"
_TERMINAL_RESULTS = frozenset({"pass", "fail", "error"})


class MetricId(StrEnum):
    FIRST_PASS_RATE = "first_pass_rate"
    FINAL_PASS_RATE = "final_pass_rate"
    RETRY_RECOVERY_RATE = "retry_recovery_rate"
    RECOVERY_CONTRIBUTION_RATE = "recovery_contribution_rate"
    SKIPPED_COUNT = "skipped_count"
    P95_DURATION_SECONDS = "p95_duration_seconds"


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportCoverage:
    source_id: str
    external_run_id: str
    report_batch_id: str
    expected_shards: int | None
    received_shards: int
    completeness: Literal["complete", "partial", "unknown"]
    missing_reasons: tuple[str, ...]
    local_dates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class MetricAttempt:
    fact_key: str
    receipt_id: str
    project_id: str
    source_id: str
    external_run_id: str
    stable_test_id: str | None
    source_test_identity: str
    data_row: str | None
    application_commit: str
    script_commit: str
    environment: str
    configuration: str
    attempt: int | None
    result: str
    duration_seconds: float | None
    first_attempt_eligible: bool
    missing_reasons: tuple[str, ...]
    run_started_at: datetime | None
    build_id: str | None = None
    branch: str | None = None

    def __post_init__(self) -> None:
        if self.run_started_at is not None and self.run_started_at.utcoffset() is None:
            raise ValueError("run_started_at must be timezone-aware")
        if self.result not in {
            "pass",
            "fail",
            "error",
            "skipped",
            "blocked",
            "canceled",
            "not-run",
            "unknown",
        }:
            raise ValueError("result is not a supported metric state")

    @property
    def instance_key(self) -> tuple[str, ...]:
        return (
            self.project_id,
            self.source_id,
            self.external_run_id,
            self.stable_test_id or self.source_test_identity,
            self.data_row or "",
            self.application_commit,
            self.script_commit,
            self.environment,
            self.configuration,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class MetricWindow:
    start: datetime
    end: datetime
    timezone: str

    def __post_init__(self) -> None:
        if self.start.utcoffset() is None or self.end.utcoffset() is None:
            raise ValueError("metric window bounds must be timezone-aware")
        if self.start >= self.end:
            raise ValueError("metric window start must precede end")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be an IANA timezone") from exc

    @classmethod
    def from_local_dates(
        cls, *, start_date: str, end_date: str, timezone: str
    ) -> MetricWindow:
        try:
            zone = ZoneInfo(timezone)
            start = datetime.combine(date.fromisoformat(start_date), time.min, zone)
            end = datetime.combine(date.fromisoformat(end_date), time.min, zone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("window requires ISO dates and an IANA timezone") from exc
        return cls(
            start=start.astimezone(UTC), end=end.astimezone(UTC), timezone=timezone
        )

    def contains(self, value: datetime) -> bool:
        return self.start <= value.astimezone(UTC) < self.end


@dataclass(frozen=True, slots=True, kw_only=True)
class MetricValue:
    metric_id: MetricId
    numerator: int | None
    denominator: int | None
    value: float | None
    completeness: Literal["complete", "empty", "unavailable"]
    missing_reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: MetricId
    label: str
    unit: Literal["ratio", "count", "seconds"]
    definition: str


METRIC_CATALOG: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        MetricId.FIRST_PASS_RATE,
        "First-pass rate",
        "ratio",
        "Instances passing on their first valid terminal attempt divided by D.",
    ),
    MetricDefinition(
        MetricId.FINAL_PASS_RATE,
        "Final-pass rate",
        "ratio",
        "Instances whose final valid attempt passes divided by the same D.",
    ),
    MetricDefinition(
        MetricId.RETRY_RECOVERY_RATE,
        "Retry recovery rate",
        "ratio",
        "Initially failing or errored instances that end passing divided by initially failing or errored instances.",
    ),
    MetricDefinition(
        MetricId.RECOVERY_CONTRIBUTION_RATE,
        "Recovery contribution rate",
        "ratio",
        "Recovered instances divided by D.",
    ),
    MetricDefinition(
        MetricId.SKIPPED_COUNT,
        "Skipped instances",
        "count",
        "Instances whose first observed state is skipped, reported outside D.",
    ),
    MetricDefinition(
        MetricId.P95_DURATION_SECONDS,
        "P95 execution duration",
        "seconds",
        "Nearest-rank P95 of summed attempt duration for each eligible instance.",
    ),
)


def calculate_metrics(
    attempts: list[MetricAttempt],
    *,
    metric_ids: tuple[MetricId, ...],
    window: MetricWindow,
    report_coverage: tuple[ReportCoverage, ...] = (),
) -> tuple[MetricValue, ...]:
    """Calculate allowlisted formulas from immutable effective facts."""
    deduplicated = {
        item.fact_key: item
        for item in attempts
        if item.run_started_at is None or window.contains(item.run_started_at)
    }
    grouped: dict[tuple[str, ...], list[MetricAttempt]] = defaultdict(list)
    for item in deduplicated.values():
        grouped[item.instance_key].append(item)
    ordered_groups = [
        sorted(items, key=lambda item: (item.attempt is None, item.attempt or 0))
        for items in grouped.values()
    ]
    missing_run_time = any(
        item.run_started_at is None for item in deduplicated.values()
    )
    missing_first = any(
        not items[0].first_attempt_eligible
        or any(item.attempt is None for item in items)
        for items in ordered_groups
    )
    evidence = tuple(sorted({item.receipt_id for item in deduplicated.values()}))
    coverage_reasons = {
        reason
        for coverage in report_coverage
        if coverage.completeness != "complete"
        for reason in coverage.missing_reasons
    }
    if missing_first or missing_run_time or coverage_reasons:
        missing_reasons = tuple(
            sorted(
                coverage_reasons
                | ({"missing-run-start-time"} if missing_run_time else set())
                | ({"missing-first-attempt-history"} if missing_first else set())
            )
        )
        paired_unavailable = MetricValue(
            metric_id=MetricId.FIRST_PASS_RATE,
            numerator=None,
            denominator=None,
            value=None,
            completeness="unavailable",
            missing_reasons=missing_reasons,
            evidence_refs=evidence,
        )
        values = {
            metric_id: MetricValue(
                metric_id=metric_id,
                numerator=paired_unavailable.numerator,
                denominator=paired_unavailable.denominator,
                value=paired_unavailable.value,
                completeness=paired_unavailable.completeness,
                missing_reasons=paired_unavailable.missing_reasons,
                evidence_refs=evidence,
            )
            for metric_id in MetricId
        }
    else:
        denominator_groups = [
            terminal_items
            for items in ordered_groups
            if (
                terminal_items := [
                    item for item in items if item.result in _TERMINAL_RESULTS
                ]
            )
        ]
        initial_failures = [
            items
            for items in denominator_groups
            if items[0].result in {"fail", "error"}
        ]
        recovered = [items for items in initial_failures if items[-1].result == "pass"]
        d = len(denominator_groups)
        values = {
            MetricId.FIRST_PASS_RATE: _ratio(
                MetricId.FIRST_PASS_RATE,
                sum(items[0].result == "pass" for items in denominator_groups),
                d,
                evidence,
            ),
            MetricId.FINAL_PASS_RATE: _ratio(
                MetricId.FINAL_PASS_RATE,
                sum(items[-1].result == "pass" for items in denominator_groups),
                d,
                evidence,
            ),
            MetricId.RETRY_RECOVERY_RATE: _ratio(
                MetricId.RETRY_RECOVERY_RATE,
                len(recovered),
                len(initial_failures),
                evidence,
            ),
            MetricId.RECOVERY_CONTRIBUTION_RATE: _ratio(
                MetricId.RECOVERY_CONTRIBUTION_RATE,
                len(recovered),
                d,
                evidence,
            ),
            MetricId.SKIPPED_COUNT: MetricValue(
                metric_id=MetricId.SKIPPED_COUNT,
                numerator=(
                    skipped := sum(
                        items[0].result == "skipped" for items in ordered_groups
                    )
                ),
                denominator=None,
                value=float(skipped),
                completeness="complete",
                missing_reasons=(),
                evidence_refs=evidence,
            ),
            MetricId.P95_DURATION_SECONDS: _duration_p95(denominator_groups, evidence),
        }
    return tuple(values[metric_id] for metric_id in metric_ids)


def _ratio(
    metric_id: MetricId,
    numerator: int,
    denominator: int,
    evidence_refs: tuple[str, ...],
) -> MetricValue:
    return MetricValue(
        metric_id=metric_id,
        numerator=numerator,
        denominator=denominator,
        value=numerator / denominator if denominator else None,
        completeness="complete" if denominator else "empty",
        missing_reasons=() if denominator else ("zero-denominator",),
        evidence_refs=evidence_refs,
    )


def _duration_p95(
    groups: list[list[MetricAttempt]], evidence_refs: tuple[str, ...]
) -> MetricValue:
    if not groups:
        return MetricValue(
            metric_id=MetricId.P95_DURATION_SECONDS,
            numerator=None,
            denominator=0,
            value=None,
            completeness="empty",
            missing_reasons=("zero-denominator",),
            evidence_refs=evidence_refs,
        )
    if any(item.duration_seconds is None for items in groups for item in items):
        return MetricValue(
            metric_id=MetricId.P95_DURATION_SECONDS,
            numerator=None,
            denominator=len(groups),
            value=None,
            completeness="unavailable",
            missing_reasons=("missing-duration",),
            evidence_refs=evidence_refs,
        )
    durations = sorted(
        sum(item.duration_seconds or 0.0 for item in items) for items in groups
    )
    return MetricValue(
        metric_id=MetricId.P95_DURATION_SECONDS,
        numerator=None,
        denominator=len(durations),
        value=durations[math.ceil(len(durations) * 0.95) - 1],
        completeness="complete",
        missing_reasons=(),
        evidence_refs=evidence_refs,
    )
