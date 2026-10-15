"""Pure unit coverage for `_paginate`'s lossless cursor-paging rule.

No DB: this isolates the pagination arithmetic (short page / full single-value
page / full mixed page / multi-value-below-the-tie page) from the MySQL
integration tests in `tests/integration/test_mysql_langfuse_sync_store.py`,
which exercise the same rule end to end against real ties in `trace_span`/
`answer_feedback`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from tap.modules.ai.adapters.mysql_langfuse_sync import _paginate

_T0 = datetime(2026, 1, 1, 0, 0, 0)


def _row(value: datetime, label: str) -> tuple[datetime, str]:
    return (value, label)


def _time_of(row: tuple[datetime, str]) -> datetime:
    return row[0]


def test_empty_page_returns_no_rows_and_no_position() -> None:
    async def refetch_tied(_: datetime) -> list[tuple[datetime, str]]:
        raise AssertionError("must not be called for an empty page")

    kept, position = asyncio.run(
        _paginate([], limit=2, time_of=_time_of, refetch_tied=refetch_tied)
    )
    assert kept == ()
    assert position is None


def test_short_page_advances_to_last_row_without_refetching() -> None:
    rows = [_row(_T0, "a"), _row(_T0 + timedelta(seconds=1), "b")]

    async def refetch_tied(_: datetime) -> list[tuple[datetime, str]]:
        raise AssertionError("a short page must never need a refetch")

    kept, position = asyncio.run(
        _paginate(rows, limit=10, time_of=_time_of, refetch_tied=refetch_tied)
    )
    assert list(kept) == rows
    assert position == _T0 + timedelta(seconds=1)


def test_full_page_of_one_value_refetches_without_limit() -> None:
    """A full page where every row shares one timestamp might still have more
    rows at that timestamp past the page boundary, so every matching row must
    be re-fetched with no limit before the position advances past it."""
    rows = [_row(_T0, "a"), _row(_T0, "b")]
    refetch_calls: list[datetime] = []

    async def refetch_tied(value: datetime) -> list[tuple[datetime, str]]:
        refetch_calls.append(value)
        return [_row(_T0, "a"), _row(_T0, "b"), _row(_T0, "c")]

    kept, position = asyncio.run(
        _paginate(rows, limit=2, time_of=_time_of, refetch_tied=refetch_tied)
    )
    assert refetch_calls == [_T0]
    assert {label for _, label in kept} == {"a", "b", "c"}
    assert position == _T0


def test_full_mixed_page_drops_the_trailing_tied_group() -> None:
    """A full page spanning more than one timestamp must drop the trailing
    group sharing the last row's timestamp (it may be incomplete past the
    page boundary) and advance only to the next distinct timestamp below it —
    this is the one branch with no MySQL-level test prior to this round; a
    regression to `return rows, last_value` or `<` vs `<=` must fail here."""
    t1 = _T0
    t2 = _T0 + timedelta(minutes=1)
    rows = [_row(t1, "a"), _row(t2, "b"), _row(t2, "c")]

    async def refetch_tied(_: datetime) -> list[tuple[datetime, str]]:
        raise AssertionError("a mixed page must not trigger the all-tied refetch")

    kept, position = asyncio.run(
        _paginate(rows, limit=3, time_of=_time_of, refetch_tied=refetch_tied)
    )
    assert [label for _, label in kept] == ["a"]
    assert position == t1


def test_full_mixed_page_with_two_distinct_values_below_the_tie() -> None:
    """Three distinct timestamps in a full page, tied only at the very last
    one: the kept rows are everything up to and including the second-highest
    timestamp, and the position is that second-highest timestamp — not the
    lowest, and not the tied (dropped) one."""
    t1 = _T0
    t2 = _T0 + timedelta(minutes=1)
    t3 = _T0 + timedelta(minutes=2)
    rows = [_row(t1, "a"), _row(t2, "b"), _row(t3, "c"), _row(t3, "d")]

    async def refetch_tied(_: datetime) -> list[tuple[datetime, str]]:
        raise AssertionError("a mixed page must not trigger the all-tied refetch")

    kept, position = asyncio.run(
        _paginate(rows, limit=4, time_of=_time_of, refetch_tied=refetch_tied)
    )
    assert [label for _, label in kept] == ["a", "b"]
    assert position == t2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
