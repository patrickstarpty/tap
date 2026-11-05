"""CSV zip export for the Skill Hub (spec sections 3 and 5; Task 10).

Builds the two-file export `/ai/skill-hub/export` returns: `library.csv`
(one row per Skill: metadata, activations by mode, users, satisfaction, last
use, pinning-Agent count and the runtime cost/latency/failures) and
`usage.csv` (runs per bucket, Skill and mode). Same rules as the Agent team
export: every CSV starts with a UTF-8 BOM, free-text cells are neutralized
against formula injection, and a runtime outage puts `runtime=unavailable`
on the first line of every file (`# runtime=unavailable` in `library.csv`,
`# granularity=<g>; runtime=unavailable` in `usage.csv`) while the runtime
columns stay present but blank. The usage rows are MySQL data and are never
blanked. Cell, CSV and zip writers live in `tap.platform.reporting.csv_zip`.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from datetime import date, timedelta

from tap.modules.ai.domain.skill_metadata import ActivationMode
from tap.modules.skill_hub.application.service import SkillExportItem
from tap.modules.skill_hub.ports import UsagePoint
from tap.platform.reporting.csv_zip import (
    blank_cell,
    csv_bytes,
    iso_datetime_cell,
    text_cell,
    zip_bytes,
)
from tap.platform.reporting.period import Period
from tap.platform.reporting.runtime import RuntimeStatus

_MODES: tuple[ActivationMode, ...] = ("manual", "pinned", "auto")

_LIBRARY_HEADER: tuple[str, ...] = (
    "skill_id",
    "name",
    "description",
    "category",
    "version",
    "source_kind",
    "origin_kind",
    "origin_ref",
    "origin_imported_at",
    "owner_actor_id",
    "owner_assigned_at",
    "status",
    "applicable_tasks",
    "compatibility",
    "updated_at",
    *(f"activations_{mode}" for mode in _MODES),
    "users",
    "satisfaction",
    "satisfaction_sample",
    "last_used_at",
    "pinned_by_agents",
    "cost_usd",
    "latency_total_ms",
    "failures",
)

_USAGE_HEADER: tuple[str, ...] = ("bucket_start", "bucket_end", "skill_id", "mode", "runs")


def _library_row(item: SkillExportItem) -> list[str]:
    record, usage = item.record, item.usage
    runtime = usage.runtime
    return [
        text_cell(record.skill_id),
        text_cell(record.name),
        text_cell(record.description),
        record.category,
        text_cell(record.version),
        record.source_kind,
        record.origin_kind,
        text_cell(record.origin_ref),
        iso_datetime_cell(record.origin_imported_at),
        text_cell(record.owner_actor_id),
        iso_datetime_cell(record.owner_assigned_at),
        record.status,
        ";".join(record.applicable_tasks),
        text_cell(record.compatibility),
        iso_datetime_cell(record.updated_at),
        *(str(usage.by_mode.get(mode, 0)) for mode in _MODES),
        str(usage.users),
        blank_cell(usage.satisfaction),
        usage.satisfaction_sample,
        iso_datetime_cell(usage.last_used_at),
        str(usage.pinned_by_agents),
        blank_cell(runtime.cost_usd if runtime is not None else None),
        blank_cell(runtime.latency_total_ms if runtime is not None else None),
        blank_cell(runtime.failures if runtime is not None else None),
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


def _usage_row(skill_id: str, point: UsagePoint, period: Period) -> list[str]:
    # A week/month bucket can begin before or end after the period; label it
    # with the period bounds, as the Agent team export does.
    bucket_end = _bucket_end(point.bucket_start, period.granularity)
    return [
        max(point.bucket_start, period.start).isoformat(),
        min(bucket_end, period.end).isoformat(),
        text_cell(skill_id),
        point.mode,
        str(point.runs),
    ]


def build_skill_export(
    items: Sequence[SkillExportItem],
    usage_rows: Sequence[tuple[str, UsagePoint]],
    *,
    period: Period,
    runtime_status: RuntimeStatus,
) -> bytes:
    """The export zip's bytes: `library.csv` then `usage.csv`, each
    UTF-8-BOM-prefixed; `usage_rows` are `(skill_id, point)` pairs."""
    unavailable = runtime_status == "unavailable"
    library_csv = csv_bytes(
        _LIBRARY_HEADER,
        (_library_row(item) for item in items),
        comment_lines=["# runtime=unavailable"] if unavailable else [],
    )
    usage_comment = f"# granularity={period.granularity}"
    if unavailable:
        usage_comment += "; runtime=unavailable"
    usage_csv = csv_bytes(
        _USAGE_HEADER,
        (_usage_row(skill_id, point, period) for skill_id, point in usage_rows),
        comment_lines=[usage_comment],
    )
    return zip_bytes({"library.csv": library_csv, "usage.csv": usage_csv})
