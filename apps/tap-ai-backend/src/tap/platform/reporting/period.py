"""Reporting periods: a closed `[start, end]` date range and its query parsing.

`Period` captures a closed `[start, end]` date range used throughout the
reporting views (Agent team view spec sections 1.3/2.1/4). The codebase has no
per-Project timezone setting today, so `project_timezone` is pinned to
`"UTC"`; adding one later only changes that single function.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal


class PeriodInvalid(Exception):
    """`from` is after `to`, or either date lies outside the supported range."""


# The period selector the UI used (spec 2.1): decides the "previous period"
# (spec 6.4) when the date shape alone is ambiguous.
PeriodPreset = Literal["last7", "last30", "thisMonth", "lastMonth", "custom"]
PERIOD_PRESETS: tuple[PeriodPreset, ...] = ("last7", "last30", "thisMonth", "lastMonth", "custom")

# User-supplied dates outside this range are rejected (400 `period_invalid`)
# rather than reaching `datetime`/`timedelta` arithmetic that can overflow.
MIN_PERIOD_DATE = date(2000, 1, 1)
MAX_PERIOD_DATE = date(2100, 12, 31)


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _previous_month_start(day: date) -> date:
    return _month_start(_month_start(day) - timedelta(days=1))


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
        """Validates user input: `from <= to`, both within
        `[MIN_PERIOD_DATE, MAX_PERIOD_DATE]`."""
        for value in (from_, to):
            if not MIN_PERIOD_DATE <= value <= MAX_PERIOD_DATE:
                raise PeriodInvalid(
                    f"dates must be between {MIN_PERIOD_DATE.isoformat()} "
                    f"and {MAX_PERIOD_DATE.isoformat()}"
                )
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

    def previous(self, preset: PeriodPreset | None = None) -> Period:
        """The "previous period" per spec 6.4.

        With an explicit `preset` (the selector the UI used):

        - `lastMonth` -> the whole calendar month before `start`'s month.
        - `thisMonth` -> the 1st of the previous month through the same day
          number as `end`, clamped to that month's last day.
        - `last7`/`last30`/`custom` -> the adjacent, equal-length period
          immediately before this one.

        Without one (`None`), the preset is inferred from the shape -- a
        fallback only, since e.g. a 7-day range starting on the 1st looks
        like "month to date":

        - A full calendar month -> the previous calendar month.
        - The 1st of a month through a non-month-end day of the same month
          -> as `thisMonth`.
        - Anything else -> as `custom`.
        """
        if preset is None:
            preset = self._inferred_preset()
        if preset == "lastMonth":
            first = _previous_month_start(self.start)
            last_day = calendar.monthrange(first.year, first.month)[1]
            return Period(first, first.replace(day=last_day))
        if preset == "thisMonth":
            first = _previous_month_start(self.start)
            last_day = calendar.monthrange(first.year, first.month)[1]
            return Period(first, first.replace(day=min(self.end.day, last_day)))
        length = self.days
        new_end = self.start - timedelta(days=1)
        new_start = new_end - timedelta(days=length - 1)
        return Period(new_start, new_end)

    def _inferred_preset(self) -> PeriodPreset:
        same_month = self.start.year == self.end.year and self.start.month == self.end.month
        if self.start.day == 1 and same_month:
            last_day_of_start_month = calendar.monthrange(self.start.year, self.start.month)[1]
            if self.end.day == last_day_of_start_month:
                return "lastMonth"
            return "thisMonth"
        return "custom"


def trailing_30_days(today: date) -> Period:
    """The rolling 30-day period ending on `today` (inclusive)."""
    return Period(today - timedelta(days=29), today)


def project_timezone(project_id: str) -> str:
    """Always `"UTC"`: no Project carries its own timezone setting yet.

    Adding a per-Project timezone later only requires changing this function.
    """
    return "UTC"


def parse_period_query(from_: str, to: str) -> Period:
    """Parses the `from`/`to` ISO-date query parameters into a validated
    `Period`; raises `PeriodInvalid` when either is not an ISO date or the
    range fails `Period.parse`."""
    try:
        start = date.fromisoformat(from_)
        end = date.fromisoformat(to)
    except (ValueError, OverflowError) as exc:
        raise PeriodInvalid("from/to must be ISO dates") from exc
    return Period.parse(start, end)


def parse_preset(value: str | None) -> PeriodPreset | None:
    """An unknown `preset` is a malformed period selector: `PeriodInvalid`."""
    if value is None:
        return None
    for preset in PERIOD_PRESETS:
        if value == preset:
            return preset
    raise PeriodInvalid(f"invalid preset {value!r}")
