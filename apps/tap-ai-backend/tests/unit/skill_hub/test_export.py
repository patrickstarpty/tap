"""Unit tests for the Skill Hub CSV zip export (Task 10, 2026-11-01 Skill Hub
phase 1): `library.csv` + `usage.csv`, UTF-8 BOM, formula escaping and the
`runtime=unavailable` rule shared with the Agent team export."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from tap.modules.skill_hub.application.export import build_skill_export
from tap.modules.skill_hub.application.service import SkillExportItem
from tap.modules.skill_hub.domain.models import SkillUsage
from tap.modules.skill_hub.ports import SkillRecord, SkillRuntimeStats, UsagePoint
from tap.platform.reporting.period import Period

_NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
# 152 days -> week granularity.
_PERIOD = Period(date(2026, 6, 3), date(2026, 11, 1))


def _record(skill_id: str, *, name: str = "Test design") -> SkillRecord:
    return SkillRecord(
        skill_id=skill_id,
        name=name,
        description="Designs cases",
        category="test-design",
        version="1.2.0",
        latest_revision_id=f"{skill_id}-r2",
        revision_number=2,
        source_kind="imported",
        origin_kind="zip",
        origin_ref="skills.zip",
        origin_imported_at=_NOW - timedelta(days=5),
        owner_actor_id="owner-1",
        owner_assigned_at=_NOW - timedelta(days=5),
        status="enabled",
        applicable_tasks=("knowledge.answer", "test-plan.generate"),
        compatibility="claude>=1",
        updated_at=_NOW - timedelta(days=1),
        built_in=False,
    )


def _usage(*, runtime: SkillRuntimeStats | None) -> SkillUsage:
    return SkillUsage(
        by_mode={"manual": 3, "pinned": 2, "auto": 1},
        users=4,
        satisfaction=0.75,
        satisfaction_sample="ok",
        last_used_at=_NOW - timedelta(hours=2),
        pinned_by_agents=1,
        runtime=runtime,
    )


_STATS = SkillRuntimeStats(
    skill_id="s-1", runs=6, failures=1, latency_total_ms=1200, cost_usd=Decimal("0.42")
)


def _export(name: str = "Test design", *, runtime_status: str = "ok") -> zipfile.ZipFile:
    runtime = None if runtime_status == "unavailable" else _STATS
    content = build_skill_export(
        [SkillExportItem(record=_record("s-1", name=name), usage=_usage(runtime=runtime))],
        [
            ("s-1", UsagePoint(bucket_start=date(2026, 6, 1), mode="manual", runs=2)),
            ("s-1", UsagePoint(bucket_start=date(2026, 10, 26), mode="auto", runs=1)),
        ],
        period=_PERIOD,
        runtime_status=runtime_status,  # type: ignore[arg-type]
    )
    return zipfile.ZipFile(io.BytesIO(content))


def _rows(archive: zipfile.ZipFile, name: str) -> list[list[str]]:
    text = archive.read(name).decode("utf-8-sig")
    return list(csv.reader(line for line in text.splitlines() if not line.startswith("#")))


def test_zip_has_two_bom_csv_files() -> None:
    with _export() as archive:
        assert archive.namelist() == ["library.csv", "usage.csv"]
        for name in archive.namelist():
            assert archive.read(name).startswith(b"\xef\xbb\xbf")

        header, row = _rows(archive, "library.csv")
        cells = dict(zip(header, row, strict=True))
        assert cells["skill_id"] == "s-1"
        assert cells["category"] == "test-design"
        assert cells["version"] == "1.2.0"
        assert cells["applicable_tasks"] == "knowledge.answer;test-plan.generate"
        assert (cells["activations_manual"], cells["activations_pinned"]) == ("3", "2")
        assert cells["activations_auto"] == "1"
        assert cells["users"] == "4"
        assert cells["satisfaction"] == "0.75"
        assert cells["last_used_at"] == "2026-11-01T10:00:00Z"
        assert cells["pinned_by_agents"] == "1"
        assert (cells["cost_usd"], cells["latency_total_ms"], cells["failures"]) == (
            "0.42",
            "1200",
            "1",
        )

        usage_text = archive.read("usage.csv").decode("utf-8-sig")
        assert usage_text.splitlines()[0] == "# granularity=week"
        usage_header, *usage_rows = _rows(archive, "usage.csv")
        assert usage_header == ["bucket_start", "bucket_end", "skill_id", "mode", "runs"]
        # Week buckets are clamped to the period bounds.
        assert usage_rows == [
            ["2026-06-03", "2026-06-07", "s-1", "manual", "2"],
            ["2026-10-26", "2026-11-01", "s-1", "auto", "1"],
        ]


def test_formula_like_skill_name_is_escaped() -> None:
    with _export("=HYPERLINK(1)") as archive:
        header, row = _rows(archive, "library.csv")

    assert row[header.index("name")] == "'=HYPERLINK(1)"


def test_unavailable_runtime_leaves_columns_blank() -> None:
    with _export(runtime_status="unavailable") as archive:
        library_text = archive.read("library.csv").decode("utf-8-sig")
        usage_text = archive.read("usage.csv").decode("utf-8-sig")
        header, row = _rows(archive, "library.csv")

    assert library_text.splitlines()[0] == "# runtime=unavailable"
    assert usage_text.splitlines()[0] == "# granularity=week; runtime=unavailable"
    cells = dict(zip(header, row, strict=True))
    assert (cells["cost_usd"], cells["latency_total_ms"], cells["failures"]) == ("", "", "")
    # MySQL usage columns are unaffected by the runtime outage.
    assert cells["activations_manual"] == "3"
    assert cells["users"] == "4"
