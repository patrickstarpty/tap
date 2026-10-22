"""Shared fixtures for the claim-loop MySQL lock-contention regression tests.

`inject_lock_errors` raises a real asyncmy server error (1213 deadlock victim
or 1205 lock-wait timeout) at the driver boundary, wrapped in SQLAlchemy's
`OperationalError` exactly as `DBAPIError.instance` wraps a server error, so
the real repository, transaction rollback, worker and loop all run against
the owned isolated MySQL.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable

from asyncmy.errors import OperationalError as DriverOperationalError
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from tap.entrypoints.tapper_ingestion_worker import WorkerSettings
from tap.platform.messaging.redis_dispatch import DispatchWakeup

_MESSAGES = {
    1213: "Deadlock found when trying to get lock; try restarting transaction",
    1205: "Lock wait timeout exceeded; try restarting transaction",
}


def inject_lock_errors(
    engine: AsyncEngine | Engine,
    *,
    matches: Callable[[str], bool],
    remaining: list[int],
    code: int = 1213,
) -> None:
    """Fail the next `remaining[0]` statements that `matches` with MySQL `code`."""

    target = engine.sync_engine if isinstance(engine, AsyncEngine) else engine

    @event.listens_for(target, "before_cursor_execute")
    def lose_lock(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        del conn, cursor, context, executemany
        if remaining[0] > 0 and matches(statement):
            remaining[0] -= 1
            raise OperationalError(
                statement, parameters, DriverOperationalError(code, _MESSAGES[code])
            )


def locking_read_of(table: str, *, skip_locked: bool | None = None) -> Callable[[str], bool]:
    """Match a locking read (`SELECT ... FROM <table> ... FOR UPDATE`), optionally
    only the claim's `SKIP LOCKED` scan (True) or only blocking reads (False)."""

    source = re.compile(rf"\nFROM {re.escape(table)}\b")

    def matches(statement: str) -> bool:
        return (
            statement.startswith("SELECT")
            and source.search(statement) is not None
            and "FOR UPDATE" in statement
            and (skip_locked is None or ("SKIP LOCKED" in statement) is skip_locked)
        )

    return matches


class IdleWakeups:
    """No Redis: every wait times out quickly and the loop polls on its timer."""

    async def wait(self, *, max_wait_seconds: float) -> DispatchWakeup | None:
        await asyncio.sleep(min(max_wait_seconds, 0.01))
        return None

    async def ack(self, wakeup: DispatchWakeup) -> None:
        raise AssertionError(f"idle wakeups cannot produce {wakeup}")


def worker_settings(database_url: str, *, worker_id: str, batch_size: int = 10) -> WorkerSettings:
    return WorkerSettings(
        database_url=database_url,
        redis_url="redis://127.0.0.1:1/0",
        job_batch_size=batch_size,
        poll_seconds=0.01,
        wakeup_seconds=0.01,
        stream_name="tap-lock-contention:commands",
        group_name="tap-lock-contention",
        worker_id=worker_id,
    )
