"""The shared MySQL lock-contention retry used by every job-claim path."""

from __future__ import annotations

import asyncio
import logging

import pytest
from asyncmy.errors import OperationalError as DriverOperationalError
from sqlalchemy.exc import OperationalError

from tap.platform.db.lock_contention import (
    CLAIM_ATTEMPTS,
    claim_or_nothing,
    retry_lock_contention,
    transient_lock_error_code,
)


def _mysql_error(code: int) -> OperationalError:
    # The wrapping SQLAlchemy applies to a server error: `.orig` is the
    # driver's own exception carrying the MySQL error code first.
    return OperationalError("UPDATE job", {}, DriverOperationalError(code, "lock"))


class _Transactions:
    """Each attempt opens its own transaction; failed ones must be rolled back."""

    def __init__(self, failures: list[BaseException]) -> None:
        self._failures = failures
        self.opened: list[dict[str, object]] = []

    async def attempt(self) -> str:
        transaction: dict[str, object] = {"id": len(self.opened), "state": "open"}
        self.opened.append(transaction)
        try:
            if self._failures:
                raise self._failures.pop(0)
        except BaseException:
            transaction["state"] = "rolled_back"
            raise
        transaction["state"] = "committed"
        return f"claimed-{transaction['id']}"


class _Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


@pytest.mark.parametrize("code", [1213, 1205])
def test_transient_lock_errors_are_retried_in_a_fresh_transaction(code: int) -> None:
    transactions = _Transactions([_mysql_error(code), _mysql_error(code)])
    sleeps = _Sleeps()

    result = asyncio.run(
        claim_or_nothing(transactions.attempt, kind="test-claim", nothing="none", sleep=sleeps)
    )

    assert result == "claimed-2"
    assert [t["state"] for t in transactions.opened] == ["rolled_back", "rolled_back", "committed"]
    assert len({t["id"] for t in transactions.opened}) == 3
    assert len(sleeps.delays) == 2


def test_backoff_is_small_jittered_and_never_zero() -> None:
    sleeps = _Sleeps()
    for _ in range(20):
        transactions = _Transactions([_mysql_error(1213), _mysql_error(1213)])
        asyncio.run(
            claim_or_nothing(transactions.attempt, kind="test-claim", nothing="none", sleep=sleeps)
        )
    assert all(0.05 <= delay <= 0.2 for delay in sleeps.delays)
    assert len(set(sleeps.delays)) > 1


@pytest.mark.parametrize(
    "error",
    [_mysql_error(1062), _mysql_error(2013), RuntimeError("not a database error")],
    ids=["duplicate-key", "lost-connection", "non-db"],
)
def test_other_errors_are_raised_without_retry(error: BaseException) -> None:
    transactions = _Transactions([error])
    sleeps = _Sleeps()
    with pytest.raises(type(error)):
        asyncio.run(
            claim_or_nothing(transactions.attempt, kind="test-claim", nothing="none", sleep=sleeps)
        )
    assert len(transactions.opened) == 1
    assert sleeps.delays == []


def test_exhausted_attempts_claim_nothing_and_log_one_safe_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transactions = _Transactions([_mysql_error(1205) for _ in range(10)])
    caplog.set_level(logging.WARNING, logger="tap.platform.db.lock_contention")

    result = asyncio.run(
        claim_or_nothing(
            transactions.attempt, kind="test-claim", nothing=("empty",), sleep=_Sleeps()
        )
    )

    assert result == ("empty",)
    assert CLAIM_ATTEMPTS == 3
    assert len(transactions.opened) == 3
    assert all(t["state"] == "rolled_back" for t in transactions.opened)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "test-claim" in message and "1205" in message
    # Only the claim kind and the error code: no SQL, parameters or driver text.
    assert "UPDATE" not in message and "lock" not in message.replace("lock contention", "")


def test_retry_lock_contention_reraises_after_exhaustion() -> None:
    transactions = _Transactions([_mysql_error(1213) for _ in range(10)])

    with pytest.raises(OperationalError):
        asyncio.run(retry_lock_contention(transactions.attempt, sleep=_Sleeps()))
    assert len(transactions.opened) == 3


def test_transient_lock_error_code_reads_only_the_driver_code() -> None:
    assert transient_lock_error_code(_mysql_error(1213)) == 1213
    assert transient_lock_error_code(_mysql_error(1205)) == 1205
    assert transient_lock_error_code(_mysql_error(1062)) is None
    assert transient_lock_error_code(RuntimeError("1213")) is None
    assert transient_lock_error_code(OperationalError("x", {}, Exception("1213 text"))) is None
