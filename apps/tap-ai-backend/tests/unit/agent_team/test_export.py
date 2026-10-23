"""Unit tests for `build_export` (Task 9, spec section 5): the CSV zip the
`/ai/agent-team/export` route returns. No HTTP or service wiring here --
`report`/`trend` are built by hand, but the "runtime unavailable" row goes
through the real domain builders (`build_member_row`/`build_person_row`) so
the blank-cell behavior is proven at the domain level, not merely asserted
on a hand-constructed `MemberRow`/`PersonRow` (Task 9 review, fix round 1)."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal

from tap.modules.agent_team.application.export import build_export
from tap.modules.agent_team.application.service import PeriodReport
from tap.modules.agent_team.domain.metrics import build_member_row, build_person_row
from tap.modules.agent_team.domain.models import MemberRow, Period, PersonRow, TeamSummary
from tap.modules.agent_team.ports import AgentRuntimeStats, ServedActor, TrendPoint

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
    generation_calls: int | None = 8,
    tool_calls: int | None = 4,
    calls_per_run: float | None = 2.4,
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
        generation_calls=generation_calls,
        tool_calls=tool_calls,
        calls_per_run=calls_per_run,
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


def _unavailable_member(agent_key: str = "agent-1") -> MemberRow:
    """Built through the real domain path (`build_member_row`) with
    `runtime_available=False` but real, nonzero `stats`/`trend` -- proves
    the outage forces every run-derived field to blank at the domain
    level, not merely that a hand-built `None` happens to render blank."""
    stats = AgentRuntimeStats(
        agent_key=agent_key,
        runs=5,
        failures=1,
        abstained=1,
        canceled=1,
        latency_total_ms=500,
        latency_p50_ms=100,
        cost_usd=Decimal("5.00"),
        served=(ServedActor(actor_id="actor-1", runs=3),),
        generation_calls=8,
        tool_calls=4,
    )
    return build_member_row(
        agent_key=agent_key,
        name="客服小助手",
        kind="custom",
        job_title="QA 负责人",
        owner_actor_id="owner-1",
        joined_at=_NOW,
        disabled=False,
        trailing_30d_runs=None,
        stats=stats,
        last_run_at=_NOW,
        up=0,
        down=0,
        last_maintenance_at=_NOW,
        pending_feedback=1,
        maintenance={"revised": 2},
        trend=(_trend_point(agent_key),),
        runtime_available=False,
    )


def _unavailable_person(actor_id: str = "owner-1") -> PersonRow:
    """Same real-domain-path proof as `_unavailable_member`, for `PersonRow`."""
    return build_person_row(
        actor_id=actor_id,
        owned_agent_keys=["agent-1"],
        stats_by_key={
            "agent-1": AgentRuntimeStats(
                agent_key="agent-1",
                runs=5,
                failures=0,
                abstained=0,
                canceled=0,
                latency_total_ms=50,
                latency_p50_ms=50,
                cost_usd=Decimal("1.00"),
                served=(ServedActor(actor_id=actor_id, runs=5),),
                generation_calls=1,
                tool_calls=1,
            )
        },
        maintenance_count=2,
        runtime_available=False,
    )


def _read_csv(archive: zipfile.ZipFile, name: str) -> tuple[bytes, list[list[str]]]:
    """Decodes a CSV entry and parses it, skipping any leading `#` comment
    lines so data-row assertions don't depend on how many comment lines a
    given file carries."""
    raw = archive.read(name)
    text = raw.decode("utf-8-sig")
    data_lines = [line for line in text.splitlines() if not line.startswith("#")]
    rows = list(csv.reader(data_lines))
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


def test_trend_granularity_is_week_for_a_long_period() -> None:
    long_period = Period(date(2026, 1, 1), date(2026, 6, 1))  # > 92 days
    assert long_period.granularity == "week"
    report = PeriodReport(summary=_summary(), members=(_member(),), people=(_person(),))
    content = build_export(report, [_trend_point()], period=long_period, runtime_status="ok")

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        text = archive.read("trend.csv").decode("utf-8-sig")
        assert text.splitlines()[0] == "# granularity=week"


def test_unavailable_runtime_leaves_columns_blank() -> None:
    member = _unavailable_member()
    person = _unavailable_person()
    report = PeriodReport(summary=_summary(), members=(member,), people=(person,))
    content = build_export(report, [], period=_PERIOD, runtime_status="unavailable")

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        roster_raw, roster_rows = _read_csv(archive, "roster.csv")
        assert roster_raw.decode("utf-8-sig").splitlines()[0] == "# runtime=unavailable"
        header, row = roster_rows[0], roster_rows[1]
        for column in (
            "status",
            "runs",
            "failures",
            "abstained",
            "canceled",
            "latency_total_ms",
            "latency_p50_ms",
            "cost_usd",
            "served_count",
            "last_run_at",
            "satisfaction",
            "generation_calls",
            "tool_calls",
            "calls_per_run",
        ):
            assert row[header.index(column)] == "", column
        assert row[header.index("top_served")] == ""

        people_raw, people_rows = _read_csv(archive, "people.csv")
        assert people_raw.decode("utf-8-sig").splitlines()[0] == "# runtime=unavailable"
        p_header, p_row = people_rows[0], people_rows[1]
        for column in ("owned_runs", "served_by_agents", "runs"):
            assert p_row[p_header.index(column)] == "", column
        # `owned_agents`/`maintenance` are MySQL facts, unaffected by the outage.
        assert p_row[p_header.index("owned_agents")] == "1"
        assert p_row[p_header.index("maintenance")] == "2"

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


def test_roster_row_includes_call_volume_columns() -> None:
    report = PeriodReport(summary=_summary(), members=(_member(),), people=(_person(),))
    content = build_export(report, [], period=_PERIOD, runtime_status="ok")

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        _, rows = _read_csv(archive, "roster.csv")
        header, row = rows[0], rows[1]
        assert row[header.index("generation_calls")] == "8"
        assert row[header.index("tool_calls")] == "4"
        # Rounded to 2 decimals; `cost_usd` keeps its full `Decimal` string.
        assert row[header.index("calls_per_run")] == "2.40"
        assert row[header.index("cost_usd")] == "5.00"


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
