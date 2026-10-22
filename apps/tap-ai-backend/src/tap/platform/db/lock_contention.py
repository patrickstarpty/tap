"""Bounded retry of MySQL transactions that lost to transient InnoDB lock contention.

Job-claim transactions take locking reads (often across tables) while API
writes hold and insert into the same rows and gaps. InnoDB resolves such a
cycle by rolling one transaction back with 1213 (deadlock victim) or ending a
wait with 1205 (lock-wait timeout). A rolled-back attempt leased nothing, so it
may simply be retried in a fresh transaction; after a bounded number of
attempts a polling claim claims nothing for this poll, and the worker's next
poll scans again. Only those two error codes are treated as transient.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

from sqlalchemy.exc import DBAPIError

_logger = logging.getLogger(__name__)

TRANSIENT_LOCK_ERRORS = frozenset({1213, 1205})
CLAIM_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.05, 0.2)

Sleep = Callable[[float], Awaitable[None]]
_sleep: Sleep = asyncio.sleep


def mysql_error_code(error: DBAPIError) -> int | None:
    """The server error code the driver put first in its exception args."""

    args = getattr(error.orig, "args", ())
    return args[0] if args and isinstance(args[0], int) else None


def transient_lock_error_code(error: BaseException) -> int | None:
    """1213 or 1205 when `error` is a MySQL lock-contention error, else None."""

    if not isinstance(error, DBAPIError):
        return None
    code = mysql_error_code(error)
    return code if code in TRANSIENT_LOCK_ERRORS else None


async def retry_lock_contention[T](
    attempt: Callable[[], Awaitable[T]],
    *,
    sleep: Sleep | None = None,
) -> T:
    """Run `attempt` (which must open its own transaction) up to `CLAIM_ATTEMPTS`
    times while it loses to lock contention; re-raise the last such error."""

    for number in range(1, CLAIM_ATTEMPTS + 1):
        try:
            return await attempt()
        except DBAPIError as error:
            if transient_lock_error_code(error) is None or number == CLAIM_ATTEMPTS:
                raise
        await (sleep or _sleep)(random.uniform(*_BACKOFF_SECONDS))
    raise AssertionError("unreachable")


async def claim_or_nothing[T](
    attempt: Callable[[], Awaitable[T]],
    *,
    kind: str,
    nothing: T,
    sleep: Sleep | None = None,
) -> T:
    """`retry_lock_contention` for a polling claim: when every attempt loses,
    log one warning (claim kind and error code only) and return `nothing`."""

    try:
        return await retry_lock_contention(attempt, sleep=sleep)
    except DBAPIError as error:
        code = transient_lock_error_code(error)
        if code is None:
            raise
        _logger.warning(
            "%s claim lost to MySQL lock contention (%s); claiming nothing this poll",
            kind,
            code,
        )
        return nothing
