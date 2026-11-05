"""The shared bounded worker loop outlives a pass that lost to MySQL lock
contention outside the claim (a stage or settlement commit), and still ends on
every other error."""

from __future__ import annotations

import asyncio
import logging

import pytest
from asyncmy.errors import OperationalError as DriverOperationalError
from sqlalchemy.exc import OperationalError

from tap.entrypoints.tapper_ingestion_worker import WorkerSettings, run_worker_loop

SETTINGS = WorkerSettings("mysql", "redis", 2, 0.01, 0.01, "stream", "group", "worker")


def _mysql_error(code: int) -> OperationalError:
    return OperationalError(
        "UPDATE knowledge_ingestion_job",
        {"lease_token": "secret"},
        DriverOperationalError(code, "x"),
    )


class _Wakeups:
    async def wait(self, *, max_wait_seconds: float) -> None:
        del max_wait_seconds

    async def ack(self, wakeup: object) -> None:
        raise AssertionError(wakeup)


class _Worker:
    def __init__(self, failures: list[BaseException]) -> None:
        self._failures = failures
        self.passes = 0

    async def run_once(self, limit: int) -> int:
        del limit
        self.passes += 1
        if self._failures:
            raise self._failures.pop(0)
        return 0


@pytest.mark.parametrize("code", [1213, 1205])
def test_a_pass_lost_to_lock_contention_does_not_end_the_loop(
    code: int, caplog: pytest.LogCaptureFixture
) -> None:
    worker = _Worker([_mysql_error(code)])
    caplog.set_level(logging.WARNING)

    asyncio.run(
        run_worker_loop(
            worker=worker,
            wakeups=_Wakeups(),
            settings=SETTINGS,
            stop=asyncio.Event(),
            max_iterations=2,
        )
    )

    assert worker.passes == 2
    (warning,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert str(code) in warning.getMessage()
    assert "secret" not in warning.getMessage() and "UPDATE" not in warning.getMessage()


def test_pending_work_lost_to_lock_contention_does_not_end_the_loop() -> None:
    worker = _Worker([])
    failures = [_mysql_error(1213)]
    calls: list[int] = []

    async def pending_work(*, limit: int) -> int:
        calls.append(limit)
        if failures:
            raise failures.pop(0)
        return 0

    asyncio.run(
        run_worker_loop(
            worker=worker,
            wakeups=_Wakeups(),
            settings=SETTINGS,
            stop=asyncio.Event(),
            max_iterations=2,
            pending_work=pending_work,
        )
    )
    assert calls == [2, 2]


@pytest.mark.parametrize(
    "error", [_mysql_error(2013), RuntimeError("boom")], ids=["lost-connection", "non-db"]
)
def test_every_other_error_still_ends_the_loop(error: BaseException) -> None:
    worker = _Worker([error])
    with pytest.raises(type(error)):
        asyncio.run(
            run_worker_loop(
                worker=worker,
                wakeups=_Wakeups(),
                settings=SETTINGS,
                stop=asyncio.Event(),
                max_iterations=3,
            )
        )
    assert worker.passes == 1


def test_suggestion_daily_refresh_lost_to_lock_contention_does_not_end_the_loop() -> None:
    """`request_daily_refresh` takes a blocking range `FOR UPDATE` before the
    claim and is not inside the claim retry; the loop guard must cover it."""

    from datetime import datetime, timezone

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.chat.application.suggestions import SuggestionRefreshWorker

    class Store:
        def __init__(self) -> None:
            self.daily = 0
            self.claims = 0

        async def request_daily_refresh(self, *, now: datetime) -> int:
            del now
            self.daily += 1
            if self.daily == 1:
                raise _mysql_error(1213)
            return 0

        async def claim_due(self, **kwargs: object) -> tuple[()]:
            del kwargs
            self.claims += 1
            return ()

    store = Store()
    worker = SuggestionRefreshWorker(
        store=store,  # type: ignore[arg-type]
        service=object(),  # type: ignore[arg-type]
        worker_id="suggestions",
        clock=lambda: datetime(2026, 10, 22, tzinfo=timezone.utc),
        scope=VALIDATION_SCOPE,
    )

    asyncio.run(
        run_worker_loop(
            worker=worker,
            wakeups=_Wakeups(),
            settings=SETTINGS,
            stop=asyncio.Event(),
            max_iterations=2,
        )
    )

    assert store.daily == 2 and store.claims == 1
