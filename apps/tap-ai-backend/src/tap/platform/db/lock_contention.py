"""Bounded retry of MySQL transactions that lost to transient InnoDB lock contention.

Job-claim transactions take locking reads (often across tables) while API
writes hold and insert into the same rows and gaps. InnoDB resolves such a
cycle by rolling one transaction back with 1213 (deadlock victim) or ending a
wait with 1205 (lock-wait timeout). A rolled-back attempt leased nothing, so it
may simply be retried in a fresh transaction; after a bounded number of
attempts a polling claim claims nothing for this poll, and the worker's next
poll scans again. Only those two error codes are treated as transient.

A 1205 only arrives after `innodb_lock_wait_timeout` (server default 50 s), so
every claim attempt runs in `claim_transaction`, which bounds that wait to a
short claim-scoped value (default 5 s) for its own transaction only:

* MySQL has no transaction-scoped form of this variable (and `SET_VAR` would
  have to be repeated on every locking statement), so the attempt sets the
  *session* value as its first statement and restores the connection's prior
  value as its last statement, inside the same transaction and before commit
  or rollback. The variable is not transactional: a rollback (including the
  full rollback of a 1213 victim) does not undo either assignment, and a 1205
  rolls back only the timed-out statement, so the restore still runs.
* The connection is therefore back at its previous value before SQLAlchemy can
  return it to the pool, so non-claim work never inherits the bound. If the
  restore itself fails (for example a lost connection), the connection is
  invalidated so the pool discards it instead of reusing it. A restore failure
  is re-raised in place of any original lock error, which stays chained as its
  `__context__`.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

_logger = logging.getLogger(__name__)

TRANSIENT_LOCK_ERRORS = frozenset({1213, 1205})
CLAIM_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.05, 0.2)

# Engine execution option carrying the configured bound (seconds); engines
# built without it use the default.
CLAIM_LOCK_WAIT_OPTION = "tap_claim_lock_wait_seconds"
DEFAULT_CLAIM_LOCK_WAIT_SECONDS = 5
MIN_CLAIM_LOCK_WAIT_SECONDS = 1
MAX_CLAIM_LOCK_WAIT_SECONDS = 50
_BOUND_LOCK_WAIT = (
    "SET @tap_claim_prior_lock_wait = @@SESSION.innodb_lock_wait_timeout, "
    "SESSION innodb_lock_wait_timeout = {seconds:d}"
)
_RESTORE_LOCK_WAIT = "SET SESSION innodb_lock_wait_timeout = @tap_claim_prior_lock_wait"

Sleep = Callable[[float], Awaitable[None]]
_sleep: Sleep = asyncio.sleep


def validated_claim_lock_wait_seconds(seconds: int) -> int:
    """`seconds` when it is an integer InnoDB lock-wait bound in 1..50."""

    if (
        type(seconds) is not int
        or not MIN_CLAIM_LOCK_WAIT_SECONDS <= seconds <= MAX_CLAIM_LOCK_WAIT_SECONDS
    ):
        raise ValueError("claim lock-wait timeout must be an integer between 1 and 50 seconds")
    return seconds


def claim_lock_wait_seconds(sessions: Any) -> int:
    """The claim lock-wait bound configured on the session factory's engine."""

    bind = getattr(sessions, "kw", {}).get("bind")
    options = bind.get_execution_options() if bind is not None else {}
    return validated_claim_lock_wait_seconds(
        options.get(CLAIM_LOCK_WAIT_OPTION, DEFAULT_CLAIM_LOCK_WAIT_SECONDS)
    )


@asynccontextmanager
async def claim_transaction(sessions: Any) -> AsyncIterator[Any]:
    """One claim attempt's session and transaction, with its InnoDB lock waits
    bounded to the claim lock-wait timeout and the connection's previous value
    restored before the transaction ends (see the module docstring)."""

    seconds = claim_lock_wait_seconds(sessions)
    async with sessions() as session, session.begin():
        await session.execute(text(_BOUND_LOCK_WAIT.format(seconds=seconds)))
        try:
            yield session
        finally:
            try:
                await session.execute(text(_RESTORE_LOCK_WAIT))
            except BaseException:
                with suppress(Exception):
                    await (await session.connection()).invalidate()
                raise


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
