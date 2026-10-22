"""Every polling job claim claims nothing when MySQL lock contention outlasts its
bounded retry, and still raises every other database error.

The repositories run their real claim code against a session factory whose
statements fail with a real asyncmy server error wrapped as SQLAlchemy wraps
one; the isolated-MySQL regression tests prove the same against InnoDB.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from asyncmy.errors import OperationalError as DriverOperationalError
from sqlalchemy.exc import OperationalError

from tap.entrypoints.tapper_runtime import create_project_audit
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql import OutboxStore
from tap.modules.chat.adapters.mysql_conversations import MysqlConversationRepository
from tap.modules.chat.adapters.mysql_suggestions import MysqlSuggestionStore
from tap.modules.graph.adapters.mysql_jobs import MysqlGraphJobStore
from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.test_management.adapters.mysql import MysqlTestPlanRepository
from tap.platform.db import lock_contention

NOW = datetime(2026, 10, 22, 9, 0, 0)
LEASE = timedelta(seconds=60)


class _Transaction:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def __aenter__(self) -> _Transaction:
        self._log.append("begin")
        return self

    async def __aexit__(self, kind, error, traceback) -> bool:  # type: ignore[no-untyped-def]
        self._log.append("rollback" if error is not None else "commit")
        return False


class _LosingSession:
    """Every statement loses to lock contention (or fails with `code`)."""

    def __init__(self, log: list[str], code: int) -> None:
        self._log = log
        self._code = code

    async def __aenter__(self) -> _LosingSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def begin(self) -> _Transaction:
        return _Transaction(self._log)

    async def _fail(self, *args: object, **kwargs: object) -> Any:
        raise OperationalError("SELECT 1", {}, DriverOperationalError(self._code, "server"))

    execute = _fail
    scalar = _fail
    scalars = _fail


class _Sessions:
    """Stands in for `async_sessionmaker`; `kw` mirrors its bind keywords."""

    kw: dict[str, object] = {"bind": None}

    def __init__(self, log: list[str], code: int) -> None:
        self._log = log
        self._code = code

    def __call__(self) -> _LosingSession:
        return _LosingSession(self._log, self._code)


def _sessions(log: list[str], code: int) -> _Sessions:
    return _Sessions(log, code)


Claim = Callable[[Any], Awaitable[object]]

_CLAIMS: dict[str, tuple[Callable[[Any], object], Claim]] = {
    "ingestion": (
        lambda s: MysqlDocumentRepository(
            s, scope=VALIDATION_SCOPE, audit_factory=create_project_audit
        ),
        lambda r: r.claim_jobs(worker_id="w", now=NOW, lease_duration=LEASE, limit=5),
    ),
    "chat-turn": (
        lambda s: MysqlConversationRepository(s, scope=VALIDATION_SCOPE),
        lambda r: r.claim_queued(limit=5),
    ),
    "prompt-suggestion": (
        lambda s: MysqlSuggestionStore(s, scope=VALIDATION_SCOPE),
        lambda r: r.claim_due(worker_id="w", now=NOW, limit=5, lease_duration=LEASE),
    ),
    "test-design": (
        lambda s: MysqlTestPlanRepository(s, scope=VALIDATION_SCOPE, model_alias="qwen-plus"),
        lambda r: r.claim_generation_jobs(
            VALIDATION_SCOPE, worker_id="w", now=NOW, lease_duration=LEASE, limit=5
        ),
    ),
    "graph-extraction": (
        lambda s: MysqlGraphJobStore(s),
        lambda r: r.claim(VALIDATION_SCOPE, worker_id="w", now=NOW, lease_duration=LEASE, limit=5),
    ),
    "relay-outbox": (
        lambda s: OutboxStore(s, scope=VALIDATION_SCOPE),
        lambda r: r.claim_pending(worker_id="w", now=NOW, lease_duration=LEASE, limit=5),
    ),
    "relay-reconcile": (
        lambda s: OutboxStore(s, scope=VALIDATION_SCOPE),
        lambda r: r.reconcile_expired(NOW, 5),
    ),
}


@pytest.fixture(autouse=True)
def _no_backoff_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_wait(delay: float) -> None:
        assert 0.05 <= delay <= 0.2

    monkeypatch.setattr(lock_contention, "_sleep", no_wait)


@pytest.mark.parametrize("code", [1213, 1205])
@pytest.mark.parametrize("path", sorted(_CLAIMS))
def test_exhausted_lock_contention_claims_nothing(path: str, code: int) -> None:
    build, claim = _CLAIMS[path]
    log: list[str] = []

    result = asyncio.run(claim(build(_sessions(log, code))))

    assert not result  # (), [], an empty ClaimBatch, or 0 reconciled rows
    assert log == ["begin", "rollback"] * lock_contention.CLAIM_ATTEMPTS


@pytest.mark.parametrize("path", sorted(_CLAIMS))
def test_other_database_errors_still_raise_without_retry(path: str) -> None:
    build, claim = _CLAIMS[path]
    log: list[str] = []

    with pytest.raises(OperationalError):
        asyncio.run(claim(build(_sessions(log, 2013))))
    assert log == ["begin", "rollback"]
