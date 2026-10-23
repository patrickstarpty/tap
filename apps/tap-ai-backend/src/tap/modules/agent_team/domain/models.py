"""Agent team domain model: reporting periods and maintenance events.

`Period` captures a closed `[start, end]` date range used throughout the
Agent team view (spec sections 1.3/2.1/4). The codebase has no per-Project
timezone setting today, so `project_timezone` is pinned to `"UTC"`; adding one
later only changes that single function.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import uuid4

from tap.modules.access.domain.context import ProjectScopeContext

TAPPER_DEFAULT_AGENT_KEY = "tapper_default"
UNKNOWN_AGENT_KEY = "unknown"

MaintenanceKind = Literal[
    "revised",
    "skill_upgraded",
    "disabled",
    "enabled",
    "feedback_resolved",
    "owner_transferred",
]


class PeriodInvalid(Exception):
    """`from` is after `to`."""


@dataclass(frozen=True)
class Period:
    """A closed `[start, end]` date range (both dates included)."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise PeriodInvalid("period start must not be after end")

    @staticmethod
    def parse(from_: date, to: date) -> Period:
        if from_ > to:
            raise PeriodInvalid("period start must not be after end")
        return Period(from_, to)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def granularity(self) -> Literal["day", "week", "month"]:
        n = self.days
        if n <= 92:
            return "day"
        if n <= 731:
            return "week"
        return "month"

    def utc_bounds(self) -> tuple[datetime, datetime]:
        """`[start 00:00Z, end+1 00:00Z)` -- a half-open range covering every
        instant of every included day."""
        start = datetime(self.start.year, self.start.month, self.start.day, tzinfo=UTC)
        end_exclusive = datetime(
            self.end.year, self.end.month, self.end.day, tzinfo=UTC
        ) + timedelta(days=1)
        return start, end_exclusive

    def previous(self) -> Period:
        """The "previous period" per spec 6.4, judged by shape:

        - A full calendar month -> the previous calendar month.
        - The 1st of a month through a non-month-end day of the same month
          ("month to date") -> the 1st of the previous month through the same
          day number, clamped to the previous month's last day if it is
          shorter.
        - Anything else -> the adjacent, equal-length period immediately
          before this one.
        """
        same_month = self.start.year == self.end.year and self.start.month == self.end.month
        if self.start.day == 1 and same_month:
            last_day_of_start_month = calendar.monthrange(self.start.year, self.start.month)[1]
            previous_month_last_day = self.start - timedelta(days=1)
            previous_month_first_day = previous_month_last_day.replace(day=1)
            if self.end.day == last_day_of_start_month:
                return Period(previous_month_first_day, previous_month_last_day)
            clamped_day = min(self.end.day, previous_month_last_day.day)
            return Period(
                previous_month_first_day, previous_month_first_day.replace(day=clamped_day)
            )
        length = self.days
        new_end = self.start - timedelta(days=1)
        new_start = new_end - timedelta(days=length - 1)
        return Period(new_start, new_end)


def trailing_30_days(today: date) -> Period:
    """The rolling 30-day period ending on `today` (inclusive)."""
    return Period(today - timedelta(days=29), today)


def project_timezone(project_id: str) -> str:
    """Always `"UTC"`: no Project carries its own timezone setting yet.

    Adding a per-Project timezone later only requires changing this function.
    """
    return "UTC"


@dataclass(frozen=True, slots=True)
class MaintenanceEvent:
    event_id: str
    project_id: str
    agent_id: str
    actor_id: str
    kind: MaintenanceKind
    revision_id: str | None
    feedback_id: str | None
    note: str | None
    occurred_at: datetime


def new_event(
    scope: ProjectScopeContext,
    agent_id: str,
    kind: MaintenanceKind,
    *,
    revision_id: str | None = None,
    feedback_id: str | None = None,
    note: str | None = None,
    now: datetime,
) -> MaintenanceEvent:
    return MaintenanceEvent(
        event_id=f"mevt-{uuid4().hex[:16]}",
        project_id=scope.project_id,
        agent_id=agent_id,
        actor_id=scope.actor_id,
        kind=kind,
        revision_id=revision_id,
        feedback_id=feedback_id,
        note=note,
        occurred_at=now,
    )
