"""The relay loop outlives a publish pass that lost to MySQL lock contention
(e.g. a `mark_published`/`mark_failed`/`mark_terminal` settlement write) and
still ends on every other error."""

from __future__ import annotations

import asyncio
import logging

import pytest
from asyncmy.errors import OperationalError as DriverOperationalError
from sqlalchemy.exc import OperationalError

from tap.entrypoints import relay_reconciler

SETTINGS = relay_reconciler.RelaySettings(
    database_url="mysql+asyncmy://unused",
    redis_url="redis://unused",
    batch_size=10,
    poll_seconds=0.01,
    stream_name="stream",
    worker_id="relay",
)


def _mysql_error(code: int) -> OperationalError:
    return OperationalError(
        "UPDATE outbox SET status=%s", {"claim_token": "secret"}, DriverOperationalError(code, "x")
    )


class _Closeable:
    async def dispose(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


class _Relay:
    def __init__(self, failures: list[BaseException]) -> None:
        self._failures = failures
        self.passes = 0

    async def publish_pending(self, batch_size: int) -> int:
        del batch_size
        self.passes += 1
        if self._failures:
            raise self._failures.pop(0)
        return 0


def _run(monkeypatch: pytest.MonkeyPatch, relay: _Relay, iterations: int) -> None:
    monkeypatch.setattr(relay_reconciler, "_open_database", lambda _s: (_Closeable(), object()))
    monkeypatch.setattr(relay_reconciler, "create_redis_client", lambda _url: _Closeable())
    monkeypatch.setattr(relay_reconciler, "_build_relay", lambda *_a, **_k: relay)
    asyncio.run(
        relay_reconciler.run(
            settings=SETTINGS, signal_installer=lambda _stop: None, max_iterations=iterations
        )
    )


@pytest.mark.parametrize("code", [1213, 1205])
def test_a_publish_pass_lost_to_lock_contention_does_not_end_the_relay(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, code: int
) -> None:
    relay = _Relay([_mysql_error(code)])
    caplog.set_level(logging.WARNING)

    _run(monkeypatch, relay, iterations=2)

    assert relay.passes == 2
    (warning,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert str(code) in warning.getMessage()
    assert "secret" not in warning.getMessage() and "UPDATE" not in warning.getMessage()


@pytest.mark.parametrize(
    "error", [_mysql_error(2013), RuntimeError("boom")], ids=["lost-connection", "non-db"]
)
def test_every_other_error_still_ends_the_relay(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    relay = _Relay([error])
    with pytest.raises(type(error)):
        _run(monkeypatch, relay, iterations=3)
    assert relay.passes == 1
