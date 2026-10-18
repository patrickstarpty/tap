"""`MysqlTestPlanRepository._transaction`'s commit-gated wake-up notify."""

from __future__ import annotations

import pytest

from tap.modules.test_management.adapters.mysql import MysqlTestPlanRepository


class _FakeTransaction:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def __aenter__(self) -> "_FakeTransaction":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._log.append("rollback" if exc_type is not None else "commit")
        return False


class _FakeSession:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction(self._log)

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeSessionFactory:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    def __call__(self) -> _FakeSession:
        return _FakeSession(self._log)


class _FakeWakeups:
    def __init__(self) -> None:
        self.notified: list[tuple[str, int]] = []

    async def notify(self, conversation_id: str, sequence: int) -> None:
        self.notified.append((conversation_id, sequence))


def _repository(wakeups, log: list[str]) -> MysqlTestPlanRepository:
    repository = object.__new__(MysqlTestPlanRepository)
    repository._sessions = _FakeSessionFactory(log)
    repository._wakeups = wakeups
    return repository


@pytest.mark.asyncio
async def test_transaction_notifies_only_after_a_clean_commit() -> None:
    log: list[str] = []
    wakeups = _FakeWakeups()
    repository = _repository(wakeups, log)

    async with repository._transaction() as (session, pending):
        assert session is not None
        pending.append(("conversation-1", 5))
        # The notify must not have fired yet: the transaction is still open.
        assert wakeups.notified == []

    assert log == ["commit"]
    assert wakeups.notified == [("conversation-1", 5)]


@pytest.mark.asyncio
async def test_transaction_fires_nothing_on_rollback() -> None:
    log: list[str] = []
    wakeups = _FakeWakeups()
    repository = _repository(wakeups, log)

    with pytest.raises(RuntimeError, match="boom"):
        async with repository._transaction() as (session, pending):
            pending.append(("conversation-1", 5))
            raise RuntimeError("boom")

    assert log == ["rollback"]
    assert wakeups.notified == []


@pytest.mark.asyncio
async def test_transaction_notifies_on_early_return_from_the_caller() -> None:
    """A `return` from inside `async with self._transaction() as (...):` is a
    normal exit for the context manager (not an exception), so the pending
    notifies still fire -- this is what the four generation-worker-facing
    methods (`cancel_generation`/`complete_generation`/`fail_generation`/
    `wait_generation`) rely on for their early-return branches."""
    log: list[str] = []
    wakeups = _FakeWakeups()
    repository = _repository(wakeups, log)

    async def scenario() -> str:
        async with repository._transaction() as (_session, pending):
            pending.append(("conversation-1", 9))
            return "done"

    result = await scenario()

    assert result == "done"
    assert log == ["commit"]
    assert wakeups.notified == [("conversation-1", 9)]


class _FailingCommitTransaction:
    """Simulates the commit itself raising (e.g. a deadlock or constraint
    violation detected by the database at `COMMIT`), as opposed to the
    caller's body raising before `__aexit__` is even reached."""

    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def __aenter__(self) -> "_FailingCommitTransaction":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self._log.append("rollback")
            return False
        self._log.append("commit-failed")
        raise RuntimeError("commit failed")


class _FailingCommitSession:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    def begin(self) -> _FailingCommitTransaction:
        return _FailingCommitTransaction(self._log)

    async def __aenter__(self) -> "_FailingCommitSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FailingCommitSessionFactory:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    def __call__(self) -> _FailingCommitSession:
        return _FailingCommitSession(self._log)


@pytest.mark.asyncio
async def test_transaction_fires_no_notify_when_the_commit_itself_fails() -> None:
    """Item 8: a database-detected commit failure (the caller's body ran
    clean, but `COMMIT` itself raised) must not fire any pending notify --
    the write never durably happened, so there is nothing to hint at."""
    log: list[str] = []
    wakeups = _FakeWakeups()
    repository = object.__new__(MysqlTestPlanRepository)
    repository._sessions = _FailingCommitSessionFactory(log)
    repository._wakeups = wakeups

    with pytest.raises(RuntimeError, match="commit failed"):
        async with repository._transaction() as (session, pending):
            assert session is not None
            pending.append(("conversation-1", 5))

    assert log == ["commit-failed"]
    assert wakeups.notified == []


@pytest.mark.asyncio
async def test_transaction_fires_multiple_pending_notifies_concurrently() -> None:
    import asyncio

    log: list[str] = []
    started: list[str] = []

    class _SlowWakeups:
        def __init__(self) -> None:
            self.notified: list[tuple[str, int]] = []

        async def notify(self, conversation_id: str, sequence: int) -> None:
            started.append(conversation_id)
            await asyncio.sleep(0)
            self.notified.append((conversation_id, sequence))

    wakeups = _SlowWakeups()
    repository = _repository(wakeups, log)

    async with repository._transaction() as (_session, pending):
        pending.append(("conversation-1", 1))
        pending.append(("conversation-2", 2))

    # Both notify() calls were in flight together (gather), not one awaited
    # to completion before the other started.
    assert started == ["conversation-1", "conversation-2"]
    assert set(wakeups.notified) == {("conversation-1", 1), ("conversation-2", 2)}
