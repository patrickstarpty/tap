"""Unit tests for the `agent_team` domain model: `Period` arithmetic and
`MaintenanceEvent` construction (Task 1, 2026-10-23 agent team view)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tap.modules.agent_team.domain.models import (
    TAPPER_DEFAULT_AGENT_KEY,
    UNKNOWN_AGENT_KEY,
    MaintenanceEvent,
    Period,
    PeriodInvalid,
    is_pending_feedback,
    new_event,
    project_timezone,
    trailing_30_days,
)


def test_constants():
    assert TAPPER_DEFAULT_AGENT_KEY == "tapper_default"
    assert UNKNOWN_AGENT_KEY == "unknown"


def test_granularity_boundaries():
    d = date(2026, 1, 1)
    assert Period(d, d + timedelta(days=91)).granularity == "day"  # 92 天
    assert Period(d, d + timedelta(days=92)).granularity == "week"  # 93 天
    assert Period(d, d + timedelta(days=730)).granularity == "week"  # 731 天
    assert Period(d, d + timedelta(days=731)).granularity == "month"


def test_parse_rejects_reversed_period():
    with pytest.raises(PeriodInvalid):
        Period.parse(date(2026, 2, 2), date(2026, 2, 1))


def test_previous_of_full_month_is_previous_month():
    assert Period(date(2026, 9, 1), date(2026, 9, 30)).previous() == Period(
        date(2026, 8, 1), date(2026, 8, 31)
    )


def test_previous_of_month_to_date_clamps_day():
    assert Period(date(2026, 3, 1), date(2026, 3, 30)).previous() == Period(
        date(2026, 2, 1), date(2026, 2, 28)
    )


def test_previous_of_rolling_period_is_adjacent_equal_length():
    assert Period(date(2026, 10, 17), date(2026, 10, 23)).previous() == Period(
        date(2026, 10, 10), date(2026, 10, 16)
    )


def test_utc_bounds_are_half_open_days():
    assert Period(date(2026, 9, 1), date(2026, 9, 1)).utc_bounds() == (
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 9, 2, tzinfo=UTC),
    )


def test_days_is_inclusive_count():
    assert Period(date(2026, 9, 1), date(2026, 9, 1)).days == 1
    assert Period(date(2026, 9, 1), date(2026, 9, 30)).days == 30


def test_trailing_30_days_ends_on_today():
    period = trailing_30_days(date(2026, 10, 23))
    assert period.end == date(2026, 10, 23)
    assert period.days == 30


def test_project_timezone_is_always_utc():
    assert project_timezone("tapper-demo") == "UTC"
    assert project_timezone("any-other-project") == "UTC"


def test_new_event_builds_immutable_maintenance_event():
    from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext

    scope = ProjectScopeContext(
        enterprise_id="local",
        project_id="tapper-demo",
        actor_id="tapper-local-user",
        identity_mode=IdentityMode.VALIDATION,
    )
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)
    event = new_event(
        scope,
        "agent-1",
        "revised",
        revision_id="rev-1",
        note="updated instructions",
        now=now,
    )
    assert isinstance(event, MaintenanceEvent)
    assert event.event_id.startswith("mevt-")
    assert len(event.event_id) == len("mevt-") + 16
    assert event.project_id == "tapper-demo"
    assert event.agent_id == "agent-1"
    assert event.actor_id == "tapper-local-user"
    assert event.kind == "revised"
    assert event.revision_id == "rev-1"
    assert event.feedback_id is None
    assert event.note == "updated instructions"
    assert event.occurred_at == now
    with pytest.raises(AttributeError):
        event.kind = "disabled"  # type: ignore[misc]


# -- review fix: explicit presets (spec 6.4) and a sane date range ----------


@pytest.mark.parametrize(
    ("preset", "start", "end", "expected_start", "expected_end"),
    [
        # 近 7 天 / 近 30 天 / 自定义 starting on the 1st: adjacent equal length.
        ("last7", date(2026, 10, 1), date(2026, 10, 7), date(2026, 9, 24), date(2026, 9, 30)),
        ("last30", date(2026, 9, 1), date(2026, 9, 30), date(2026, 8, 2), date(2026, 8, 31)),
        ("custom", date(2026, 10, 1), date(2026, 10, 7), date(2026, 9, 24), date(2026, 9, 30)),
        ("custom", date(2026, 9, 1), date(2026, 9, 30), date(2026, 8, 2), date(2026, 8, 31)),
        # 本月: the 1st of last month through the same day, clamped to month end.
        ("thisMonth", date(2026, 10, 1), date(2026, 10, 23), date(2026, 9, 1), date(2026, 9, 23)),
        ("thisMonth", date(2026, 3, 1), date(2026, 3, 30), date(2026, 2, 1), date(2026, 2, 28)),
        ("thisMonth", date(2026, 10, 1), date(2026, 10, 31), date(2026, 9, 1), date(2026, 9, 30)),
        ("thisMonth", date(2026, 1, 1), date(2026, 1, 5), date(2025, 12, 1), date(2025, 12, 5)),
        # 上月: the whole calendar month before.
        ("lastMonth", date(2026, 9, 1), date(2026, 9, 30), date(2026, 8, 1), date(2026, 8, 31)),
        ("lastMonth", date(2026, 3, 1), date(2026, 3, 31), date(2026, 2, 1), date(2026, 2, 28)),
        ("lastMonth", date(2026, 1, 1), date(2026, 1, 31), date(2025, 12, 1), date(2025, 12, 31)),
    ],
)
def test_previous_follows_explicit_preset(preset, start, end, expected_start, expected_end):
    assert Period(start, end).previous(preset) == Period(expected_start, expected_end)


def test_previous_without_preset_still_infers_from_shape():
    assert Period(date(2026, 10, 1), date(2026, 10, 7)).previous(None) == Period(
        date(2026, 9, 1), date(2026, 9, 7)
    )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(1, 1, 5), date(2026, 1, 1)),
        (date(1999, 12, 31), date(2026, 1, 1)),
        (date(2026, 1, 1), date(2101, 1, 1)),
        (date(2026, 1, 1), date(9999, 12, 31)),
    ],
)
def test_parse_rejects_dates_outside_supported_range(start, end):
    with pytest.raises(PeriodInvalid):
        Period.parse(start, end)


def test_parse_accepts_range_edges():
    period = Period.parse(date(2000, 1, 1), date(2100, 12, 31))
    assert period.previous("custom").end == date(1999, 12, 31)
    assert period.utc_bounds()[1] == datetime(2101, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("rating", "comment", "expected"),
    [
        ("down", None, True),
        ("down", "bad", True),
        ("up", "nice but", True),
        (None, "just a comment", True),
        ("up", None, False),  # plain thumbs-up
        (None, None, False),  # withdrawn
    ],
)
def test_is_pending_feedback_matches_spec_2_4(rating, comment, expected) -> None:
    assert is_pending_feedback(rating, comment) is expected
