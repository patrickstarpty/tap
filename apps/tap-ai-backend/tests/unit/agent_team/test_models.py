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
