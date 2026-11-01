"""Unit tests for the shared reporting helpers in `tap.platform.reporting`
(extracted from `agent_team` for reuse by Skill Hub)."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from tap.platform.reporting.csv_zip import blank_cell, csv_bytes, text_cell
from tap.platform.reporting.period import Period, PeriodInvalid, parse_period_query
from tap.platform.reporting.runtime import RuntimeMetricsUnavailable
from tap.platform.reporting.ttl_cache import TtlSingleFlightCache


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


@pytest.mark.asyncio
async def test_ttl_cache_single_flight_and_negative_cache() -> None:
    clock = _FakeClock()
    cache = TtlSingleFlightCache(clock=clock)
    release = asyncio.Event()
    calls = {"ok": 0, "fail": 0}

    async def gated() -> str:
        calls["ok"] += 1
        await release.wait()
        return "value"

    callers = [asyncio.create_task(cache.get("k", gated, 5.0)) for _ in range(4)]
    await asyncio.sleep(0)
    release.set()
    assert await asyncio.gather(*callers) == ["value"] * 4
    assert calls["ok"] == 1  # one shared in-flight fetch
    assert await cache.get("k", gated) == "value"  # served from the TTL cache
    assert calls["ok"] == 1
    assert cache.computed_at("k") is not None

    async def failing() -> str:
        calls["fail"] += 1
        raise RuntimeMetricsUnavailable("boom")

    for _ in range(2):
        with pytest.raises(RuntimeMetricsUnavailable):
            await cache.get("bad", failing)
    assert calls["fail"] == 1  # the failure is negative-cached for 30 s
    clock.value += 31
    with pytest.raises(RuntimeMetricsUnavailable):
        await cache.get("bad", failing)
    assert calls["fail"] == 2

    clock.value += 300
    assert cache.computed_at("k") is None  # the 300 s TTL elapsed


def test_csv_bytes_bom_and_formula_escape() -> None:
    body = csv_bytes(
        ("name", "runs"),
        [
            [text_cell("=SUM(A1)"), blank_cell(3)],
            [text_cell("plain"), blank_cell(None)],
            [text_cell("@x"), "-1"],
        ],
        comment_lines=["# runtime=unavailable"],
    )
    assert body == ("﻿# runtime=unavailable\nname,runs\n'=SUM(A1),3\nplain,\n'@x,-1\n").encode(
        "utf-8"
    )


def test_parse_period_query_rejects_bad_dates() -> None:
    assert parse_period_query("2026-10-01", "2026-10-31") == Period(
        date(2026, 10, 1), date(2026, 10, 31)
    )
    for from_, to in (
        ("not-a-date", "2026-10-31"),
        ("2026-10-31", "2026-10-01"),
        ("1999-12-31", "2026-10-01"),
        ("2026-10-01", "2101-01-01"),
    ):
        with pytest.raises(PeriodInvalid):
            parse_period_query(from_, to)
