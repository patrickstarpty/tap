"""CSV zip export for the Agent team view (spec section 5; Task 9).

Builds the three-file CSV export (`roster.csv`, `people.csv`, `trend.csv`)
`/ai/agent-team/export` returns. Each CSV starts with a UTF-8 BOM so Excel
opens Chinese Agent/Person names without mangling them, and each carries a
leading `# runtime=unavailable` comment line when the runtime backend could
not be queried (`trend.csv` additionally starts with a
`# granularity=<day|week|month>` comment line recording `period.granularity`,
the same rule the UI's trend view uses; `# runtime=unavailable`, if present,
follows it). Domain-level rows already carry `None` -- never `0` -- on every
run-derived field when the runtime backend is unavailable (controller ruling,
Task 9 review fix round 1: see `domain/models.py`'s `MemberRow`/`PersonRow`/
`TeamSummary` docstrings); this module renders `None` as an empty cell.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime

from tap.modules.agent_team.application.service import PeriodReport
from tap.modules.agent_team.domain.models import (
    MaintenanceKind,
    MemberRow,
    Period,
    PersonRow,
    RuntimeStatus,
)
from tap.modules.agent_team.ports import ServedActor, TrendPoint

_UTF8_BOM = "﻿"

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
    "agent_key",
    "runs",
    "failures",
    "latency_total_ms",
    "cost_usd",
)


def _blank(value: object | None) -> str:
    return "" if value is None else str(value)


def _iso_datetime(value: datetime | None) -> str:
    """ISO-8601 UTC, `Z`-suffixed. Domain datetimes are always timezone-aware
    UTC (ports.py's `AgentTeamStore`/`AgentRuntimeMetrics` docstrings)."""
    if value is None:
        return ""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_date(value: date) -> str:
    return value.isoformat()


def _top_served_cell(top_served: Sequence[ServedActor]) -> str:
    return ";".join(f"{actor.actor_id}:{actor.runs}" for actor in top_served)


def _maintenance_cell(maintenance: Mapping[MaintenanceKind, int], kind: MaintenanceKind) -> str:
    return str(maintenance.get(kind, 0))


def _calls_per_run_cell(value: float | None) -> str:
    """Rounded to 2 decimals for readability; `cost_usd` keeps its full
    `Decimal` string instead (money, not a derived ratio)."""
    return "" if value is None else f"{value:.2f}"


def _roster_row(row: MemberRow) -> list[str]:
    cells = [
        row.agent_key,
        row.name,
        row.kind,
        _blank(row.job_title),
        _blank(row.owner_actor_id),
        _iso_datetime(row.joined_at),
        _blank(row.status),
        _blank(row.runs),
        _blank(row.failures),
        _blank(row.abstained),
        _blank(row.canceled),
        _blank(row.latency_total_ms),
        _blank(row.latency_p50_ms),
        _blank(row.cost_usd),
        _blank(row.served_count),
        _top_served_cell(row.top_served),
        _iso_datetime(row.last_run_at),
        _blank(row.satisfaction),
        row.satisfaction_sample,
        _iso_datetime(row.last_maintenance_at),
        str(row.pending_feedback),
    ]
    cells.extend(_maintenance_cell(row.maintenance, kind) for kind in _MAINTENANCE_KINDS)
    cells.extend(
        [
            _blank(row.generation_calls),
            _blank(row.tool_calls),
            _calls_per_run_cell(row.calls_per_run),
        ]
    )
    return cells


def _people_row(row: PersonRow) -> list[str]:
    return [
        row.actor_id,
        str(row.owned_agents),
        _blank(row.owned_runs),
        _blank(row.served_by_agents),
        _blank(row.runs),
        str(row.maintenance),
    ]


def _trend_row(point: TrendPoint) -> list[str]:
    return [
        _iso_date(point.bucket_start),
        point.agent_key,
        str(point.runs),
        str(point.failures),
        str(point.latency_total_ms),
        str(point.cost_usd),
    ]


def _csv_bytes(
    header: Sequence[str], rows: Iterable[Sequence[str]], *, comment_lines: Sequence[str] = ()
) -> bytes:
    text = io.StringIO()
    text.write(_UTF8_BOM)
    for line in comment_lines:
        text.write(f"{line}\n")
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return text.getvalue().encode("utf-8")


def build_export(
    report: PeriodReport,
    trend: list[TrendPoint],
    *,
    period: Period,
    runtime_status: RuntimeStatus,
) -> bytes:
    """The export zip's bytes: `roster.csv`, `people.csv`, `trend.csv`, each
    UTF-8-BOM-prefixed. `trend.csv` additionally starts with a
    `# granularity=<day|week|month>` comment line recording
    `period.granularity` (the same rule the UI's trend view uses). When
    `runtime_status` is `"unavailable"`, every file gets a leading
    `# runtime=unavailable` comment line (after `# granularity=` in
    `trend.csv`) -- the run-derived cells are blank because the domain rows
    already carry `None` there, not because the column was dropped."""
    unavailable_comment = ["# runtime=unavailable"] if runtime_status == "unavailable" else []
    roster_csv = _csv_bytes(
        _ROSTER_HEADER,
        (_roster_row(row) for row in report.members),
        comment_lines=unavailable_comment,
    )
    people_csv = _csv_bytes(
        _PEOPLE_HEADER,
        (_people_row(row) for row in report.people),
        comment_lines=unavailable_comment,
    )
    trend_comments = [f"# granularity={period.granularity}", *unavailable_comment]
    trend_csv = _csv_bytes(
        _TREND_HEADER, (_trend_row(point) for point in trend), comment_lines=trend_comments
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("roster.csv", roster_csv)
        archive.writestr("people.csv", people_csv)
        archive.writestr("trend.csv", trend_csv)
    return buffer.getvalue()
