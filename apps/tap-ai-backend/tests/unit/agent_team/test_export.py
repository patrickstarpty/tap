"""Unit tests for `build_export` (Task 9, spec section 5): the CSV zip the
`/ai/agent-team/export` route returns. No HTTP or service wiring here --
`report`/`trend` are built by hand."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal

from tap.modules.agent_team.application.export import build_export
from tap.modules.agent_team.application.service import PeriodReport
from tap.modules.agent_team.domain.models import MemberRow, Period, PersonRow, TeamSummary
from tap.modules.agent_team.ports import ServedActor, TrendPoint

_NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
_PERIOD = Period(date(2026, 10, 1), date(2026, 10, 23))


def _summary() -> TeamSummary:
    return TeamSummary(
        agent_total=1,
        active=1,
        idle=0,
        owners_avg=1.0,
        served_avg=1.0,
        runs=5,
        latency_total_ms=500,
        cost_usd=Decimal("5.00"),
        maintenance_total=0,
    )


def _member(
    agent_key: str = "agent-1",
    *,
    name: str = "客服小助手",
    status: str | None = "active",
    satisfaction: float | None = 0.9,
    latency_p50_ms: int | None = 100,
    job_title: str | None = "QA 负责人",
    owner_actor_id: str | None = "owner-1",
) -> MemberRow:
    return MemberRow(
        agent_key=agent_key,
        name=name,
        kind="custom",
        job_title=job_title,
        owner_actor_id=owner_actor_id,
        joined_at=_NOW,
        status=status,
        runs=5,
        failures=1,
        abstained=0,
        canceled=0,
        latency_total_ms=500,
        latency_p50_ms=latency_p50_ms,
        cost_usd=Decimal("5.00"),
        served_count=2,
        top_served=(
            ServedActor(actor_id="actor-1", runs=3),
            ServedActor(actor_id="actor-2", runs=2),
        ),
        last_run_at=_NOW,
        satisfaction=satisfaction,
        satisfaction_sample="ok",
        last_maintenance_at=_NOW,
        pending_feedback=1,
        maintenance={"revised": 2},
        trend=(),
    )


def _person() -> PersonRow:
    return PersonRow(
        actor_id="owner-1",
        owned_agents=1,
        owned_runs=5,
        served_by_agents=1,
        runs=5,
        maintenance=2,
    )


def _trend_point(agent_key: str = "agent-1") -> TrendPoint:
    return TrendPoint(
        bucket_start=date(2026, 10, 1),
        agent_key=agent_key,
        runs=5,
        failures=1,
        latency_total_ms=500,
        cost_usd=Decimal("5.00"),
    )


def _read_csv(archive: zipfile.ZipFile, name: str) -> tuple[bytes, list[list[str]]]:
    raw = archive.read(name)
    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    return raw, rows


def test_zip_has_three_bom_csv_files() -> None:
    report = PeriodReport(summary=_summary(), members=(_member(),), people=(_person(),))
    content = build_export(report, [_trend_point()], period=_PERIOD, runtime_status="ok")

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert set(archive.namelist()) == {"roster.csv", "people.csv", "trend.csv"}
        for name in ("roster.csv", "people.csv", "trend.csv"):
            raw = archive.read(name)
            assert raw.startswith(b"\xef\xbb\xbf"), f"{name} missing UTF-8 BOM"


def test_trend_granularity_matches_ui_rule() -> None:
    report = PeriodReport(summary=_summary(), members=(_member(),), people=(_person(),))
    content = build_export(report, [_trend_point()], period=_PERIOD, runtime_status="ok")

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        text = archive.read("trend.csv").decode("utf-8-sig")
        first_line = text.splitlines()[0]
        assert first_line == f"# granularity={_PERIOD.granularity}"
        assert _PERIOD.granularity == "day"


def test_unavailable_runtime_leaves_columns_blank() -> None:
    member = _member(status=None, satisfaction=None, latency_p50_ms=None)
    report = PeriodReport(summary=_summary(), members=(member,), people=(_person(),))
    content = build_export(report, [], period=_PERIOD, runtime_status="unavailable")

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        roster_text = archive.read("roster.csv").decode("utf-8-sig")
        roster_rows = list(csv.reader(io.StringIO(roster_text)))
        header, row = roster_rows[0], roster_rows[1]
        assert row[header.index("status")] == ""
        assert row[header.index("satisfaction")] == ""
        assert row[header.index("latency_p50_ms")] == ""

        trend_text = archive.read("trend.csv").decode("utf-8-sig")
        trend_lines = trend_text.splitlines()
        assert trend_lines[0] == f"# granularity={_PERIOD.granularity}"
        assert trend_lines[1] == "# runtime=unavailable"


def test_chinese_names_round_trip() -> None:
    report = PeriodReport(
        summary=_summary(), members=(_member(name="客服小助手"),), people=(_person(),)
    )
    content = build_export(report, [_trend_point()], period=_PERIOD, runtime_status="ok")

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        roster_text = archive.read("roster.csv").decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(roster_text)))
        header, row = rows[0], rows[1]
        assert row[header.index("name")] == "客服小助手"


def test_roster_row_covers_every_member_scalar_and_maintenance_kind() -> None:
    report = PeriodReport(summary=_summary(), members=(_member(),), people=(_person(),))
    content = build_export(report, [], period=_PERIOD, runtime_status="ok")

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        _, rows = _read_csv(archive, "roster.csv")
        header, row = rows[0], rows[1]
        assert row[header.index("agent_key")] == "agent-1"
        assert row[header.index("top_served")] == "actor-1:3;actor-2:2"
        assert row[header.index("maintenance_revised")] == "2"
        assert row[header.index("maintenance_skill_upgraded")] == "0"
        assert row[header.index("joined_at")] == "2026-10-23T12:00:00Z"


def test_people_row_covers_every_person_field() -> None:
    report = PeriodReport(summary=_summary(), members=(_member(),), people=(_person(),))
    content = build_export(report, [], period=_PERIOD, runtime_status="ok")

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        _, rows = _read_csv(archive, "people.csv")
        header, row = rows[0], rows[1]
        assert row[header.index("actor_id")] == "owner-1"
        assert row[header.index("owned_agents")] == "1"
        assert row[header.index("owned_runs")] == "5"
        assert row[header.index("served_by_agents")] == "1"
        assert row[header.index("runs")] == "5"
        assert row[header.index("maintenance")] == "2"
