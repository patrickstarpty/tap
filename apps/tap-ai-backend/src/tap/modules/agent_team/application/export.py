"""CSV zip export for the Agent team view (spec section 5; Task 9).

Builds the three-file CSV export (`roster.csv`, `people.csv`, `trend.csv`)
`/ai/agent-team/export` returns. Each CSV starts with a UTF-8 BOM so Excel
opens Chinese Agent/Person names without mangling them, and each carries a
leading `# runtime=unavailable` comment line when the runtime backend could
not be queried (spec 5.1: on the first line of all three files). `trend.csv`
instead starts with one `# granularity=<day|week|month>` comment line
recording `period.granularity` (the same rule the UI's trend view uses),
with `; runtime=unavailable` appended on that same line during an outage.
Domain-level rows already carry `None` -- never `0` -- on every
run-derived field when the runtime backend is unavailable (controller ruling,
Task 9 review fix round 1: see `domain/models.py`'s `MemberRow`/`PersonRow`/
`TeamSummary` docstrings); this module renders `None` as an empty cell.
The cell, CSV and zip writers live in `tap.platform.reporting.csv_zip`
(shared with Skill Hub).
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from tap.modules.agent_team.application.service import PeriodReport
from tap.modules.agent_team.domain.models import (
    MemberRow,
    Period,
    PersonRow,
    RuntimeStatus,
)
from tap.modules.agent_team.ports import ServedActor, TrendPoint
from tap.modules.ai.domain.maintenance import MaintenanceKind
from tap.platform.reporting.csv_zip import (
    blank_cell,
    csv_bytes,
    iso_datetime_cell,
    text_cell,
    zip_bytes,
)

# Closed order matching `MaintenanceKind` (Global Constraints): one roster
# column per kind.
_MAINTENANCE_KINDS: tuple[MaintenanceKind, ...] = (
    "revised",
    "skill_upgraded",
    "disabled",
    "enabled",
    "feedback_resolved",
    "owner_transferred",
)

_ROSTER_HEADER: tuple[str, ...] = (
    "agent_key",
    "name",
    "kind",
    "job_title",
    "owner_actor_id",
    "joined_at",
    "status",
    "runs",
    "failures",
    "abstained",
    "canceled",
    "latency_total_ms",
    "latency_p50_ms",
    "cost_usd",
    "served_count",
    "top_served",
    "last_run_at",
    "satisfaction",
    "satisfaction_sample",
    "last_maintenance_at",
    "pending_feedback",
    *(f"maintenance_{kind}" for kind in _MAINTENANCE_KINDS),
    "generation_calls",
    "tool_calls",
    "calls_per_run",
)

_PEOPLE_HEADER: tuple[str, ...] = (
    "actor_id",
    "owned_agents",
    "owned_runs",
    "served_by_agents",
    "runs",
    "maintenance",
)

_TREND_HEADER: tuple[str, ...] = (
    "bucket_start",
    "bucket_end",
    "agent_key",
    "runs",
    "failures",
    "latency_total_ms",
    "cost_usd",
)


def _iso_date(value: date) -> str:
    return value.isoformat()


def _top_served_cell(top_served: Sequence[ServedActor]) -> str:
    return text_cell(";".join(f"{actor.actor_id}:{actor.runs}" for actor in top_served))


def _maintenance_cell(maintenance: Mapping[MaintenanceKind, int], kind: MaintenanceKind) -> str:
    return str(maintenance.get(kind, 0))


def _calls_per_run_cell(value: float | None) -> str:
    """Rounded to 2 decimals for readability; `cost_usd` keeps its full
    `Decimal` string instead (money, not a derived ratio)."""
    return "" if value is None else f"{value:.2f}"


def _roster_row(row: MemberRow) -> list[str]:
    cells = [
        row.agent_key,
        text_cell(row.name),
        row.kind,
        text_cell(row.job_title),
        text_cell(row.owner_actor_id),
        iso_datetime_cell(row.joined_at),
        blank_cell(row.status),
        blank_cell(row.runs),
        blank_cell(row.failures),
        blank_cell(row.abstained),
        blank_cell(row.canceled),
        blank_cell(row.latency_total_ms),
        blank_cell(row.latency_p50_ms),
        blank_cell(row.cost_usd),
        blank_cell(row.served_count),
        _top_served_cell(row.top_served),
        iso_datetime_cell(row.last_run_at),
        blank_cell(row.satisfaction),
        row.satisfaction_sample,
        iso_datetime_cell(row.last_maintenance_at),
        str(row.pending_feedback),
    ]
    cells.extend(_maintenance_cell(row.maintenance, kind) for kind in _MAINTENANCE_KINDS)
    cells.extend(
        [
            blank_cell(row.generation_calls),
            blank_cell(row.tool_calls),
            _calls_per_run_cell(row.calls_per_run),
        ]
    )
    return cells


def _people_row(row: PersonRow) -> list[str]:
    return [
        text_cell(row.actor_id),
        str(row.owned_agents),
        blank_cell(row.owned_runs),
        blank_cell(row.served_by_agents),
        blank_cell(row.runs),
        str(row.maintenance),
    ]


def _bucket_end(bucket_start: date, granularity: str) -> date:
    """Inclusive last day of the (Monday-start week / calendar month) bucket
    beginning on `bucket_start`, before clamping to the period."""
    if granularity == "week":
        return bucket_start + timedelta(days=6)
    if granularity == "month":
        last_day = calendar.monthrange(bucket_start.year, bucket_start.month)[1]
        return bucket_start.replace(day=last_day)
    return bucket_start


def _trend_row(point: TrendPoint, period: Period) -> list[str]:
    # A week/month bucket can begin before or end after the period; label it
    # with the period bounds (spec 2.1) rather than dates outside the chosen
    # range, so truncated first/last buckets are identifiable.
    bucket_end = _bucket_end(point.bucket_start, period.granularity)
    return [
        _iso_date(max(point.bucket_start, period.start)),
        _iso_date(min(bucket_end, period.end)),
        point.agent_key,
        str(point.runs),
        str(point.failures),
        str(point.latency_total_ms),
        str(point.cost_usd),
    ]


def build_export(
    report: PeriodReport,
    trend: list[TrendPoint],
    *,
    period: Period,
    runtime_status: RuntimeStatus,
) -> bytes:
    """The export zip's bytes: `roster.csv`, `people.csv`, `trend.csv`, each
    UTF-8-BOM-prefixed. `trend.csv` starts with a single
    `# granularity=<day|week|month>` comment line recording
    `period.granularity` (the same rule the UI's trend view uses). When
    `runtime_status` is `"unavailable"`, the first line of every file
    carries `runtime=unavailable` (`# runtime=unavailable` in
    `roster.csv`/`people.csv`, `# granularity=<g>; runtime=unavailable` in
    `trend.csv`) -- the run-derived cells are blank because the domain rows
    already carry `None` there, not because the column was dropped."""
    unavailable = runtime_status == "unavailable"
    unavailable_comment = ["# runtime=unavailable"] if unavailable else []
    roster_csv = csv_bytes(
        _ROSTER_HEADER,
        (_roster_row(row) for row in report.members),
        comment_lines=unavailable_comment,
    )
    people_csv = csv_bytes(
        _PEOPLE_HEADER,
        (_people_row(row) for row in report.people),
        comment_lines=unavailable_comment,
    )
    trend_comment = f"# granularity={period.granularity}"
    if unavailable:
        trend_comment += "; runtime=unavailable"
    trend_csv = csv_bytes(
        _TREND_HEADER,
        (_trend_row(point, period) for point in trend),
        comment_lines=[trend_comment],
    )

    return zip_bytes({"roster.csv": roster_csv, "people.csv": people_csv, "trend.csv": trend_csv})
