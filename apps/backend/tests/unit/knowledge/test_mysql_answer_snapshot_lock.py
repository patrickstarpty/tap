from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from tap.modules.knowledge.adapters import mysql_documents
from tap.modules.knowledge.adapters.mysql_documents import MysqlDocumentRepository
from tap.modules.knowledge.ports.answers import (
    AnswerSnapshot,
    AnswerSnapshotUnavailable,
    ReadyDocumentRevision,
)


class _SyncConnection:
    def __init__(self, owner: _ControlledConnection) -> None:
        self._owner = owner

    def invalidate(self, error: BaseException | None = None) -> None:
        self._owner.invalidation_error = error
        self._owner.invalidated = True
        self._owner.allow_result.set()

    def rollback(self) -> None:
        self._owner.rollback_count += 1


class _ControlledConnection:
    def __init__(self, *, result: object = 1, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.started = asyncio.Event()
        self.allow_result = asyncio.Event()
        self.invalidated = False
        self.invalidation_error: BaseException | None = None
        self.rollback_count = 0
        self.sync_connection = _SyncConnection(self)

    async def scalar(self, _statement: object, _parameters: object) -> object:
        self.started.set()
        await self.allow_result.wait()
        if self.error is not None:
            raise self.error
        return self.result

    async def commit(self) -> None:
        return


class _AsyncContext:
    def __init__(self, value: object = None) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Engine:
    def __init__(self, connection: _ControlledConnection) -> None:
        self._connection = connection

    def connect(self) -> _AsyncContext:
        return _AsyncContext(self._connection)


class _Session(_AsyncContext):
    def __init__(self) -> None:
        super().__init__(self)

    def begin(self) -> _AsyncContext:
        return _AsyncContext()


class _Sessions:
    def __call__(self, **_kwargs: object) -> _Session:
        return _Session()


class _CancelInSnapshotRepository(MysqlDocumentRepository):
    snapshot_started = asyncio.Event()

    async def _acquire_answer_snapshot_lock(self, connection: object) -> object:
        del connection
        return 1

    async def _save_answer_snapshot(self, session: object, snapshot: object) -> None:
        del session, snapshot
        type(self).snapshot_started.set()
        await asyncio.Event().wait()


async def _finish_for_red(task: asyncio.Task[object], connection: _ControlledConnection) -> None:
    if task.done():
        with suppress(BaseException):
            await task
        return
    connection.allow_result.set()
    task.cancel()
    with suppress(BaseException):
        await task


def test_release_deadline_terminates_a_connection_with_uncertain_lock_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(
            mysql_documents,
            "ANSWER_SNAPSHOT_LOCK_RELEASE_DEADLINE_SECONDS",
            0.02,
            raising=False,
        )
        connection = _ControlledConnection()
        releasing = asyncio.create_task(
            mysql_documents._release_named_lock(  # pyright: ignore[reportPrivateUsage]
                connection,  # type: ignore[arg-type]
                "answer-lock",
                ownership_confirmed=True,
            )
        )
        await connection.started.wait()
        releasing.cancel("caller cancelled while release hung")
        await asyncio.sleep(0)
        done, _pending = await asyncio.wait({releasing}, timeout=0.1)
        completed_within_deadline = releasing in done
        if completed_within_deadline:
            with pytest.raises(asyncio.CancelledError) as caught:
                await releasing
            assert caught.value.args == ("caller cancelled while release hung",)
        else:
            await _finish_for_red(releasing, connection)

        assert completed_within_deadline
        assert connection.invalidated is True

    asyncio.run(scenario())


def test_acquire_deadline_preserves_caller_cancellation_and_terminates_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(
            mysql_documents,
            "ANSWER_SNAPSHOT_LOCK_ACQUIRE_DEADLINE_SECONDS",
            0.02,
        )
        repository = object.__new__(MysqlDocumentRepository)
        connection = _ControlledConnection()
        acquiring = asyncio.create_task(
            repository._acquire_answer_snapshot_lock(connection)  # type: ignore[arg-type]
        )
        await connection.started.wait()
        acquiring.cancel("caller cancelled while acquire hung")

        with pytest.raises(asyncio.CancelledError) as caught:
            await acquiring

        assert caught.value.args == ("caller cancelled while acquire hung",)
        assert connection.invalidated is True

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_release_preserves_first_concurrent_caller_cancellation(
    outcome: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(
            mysql_documents,
            "ANSWER_SNAPSHOT_LOCK_RELEASE_DEADLINE_SECONDS",
            0.1,
            raising=False,
        )
        connection = _ControlledConnection(
            error=RuntimeError("driver secret") if outcome == "failure" else None
        )
        releasing = asyncio.create_task(
            mysql_documents._release_named_lock(  # pyright: ignore[reportPrivateUsage]
                connection,  # type: ignore[arg-type]
                "answer-lock",
                ownership_confirmed=True,
            )
        )
        await connection.started.wait()
        releasing.cancel("first cancellation")
        await asyncio.sleep(0)
        releasing.cancel("second cancellation")
        connection.allow_result.set()

        with pytest.raises(asyncio.CancelledError) as caught:
            await releasing
        assert caught.value.args == ("first cancellation",)
        assert connection.invalidated is (outcome == "failure")

    asyncio.run(scenario())


def test_release_malformed_result_terminates_the_owning_connection() -> None:
    async def scenario() -> None:
        connection = _ControlledConnection(result="one")
        connection.allow_result.set()

        with pytest.raises(AnswerSnapshotUnavailable):
            await mysql_documents._release_named_lock(  # pyright: ignore[reportPrivateUsage]
                connection,  # type: ignore[arg-type]
                "answer-lock",
                ownership_confirmed=True,
            )
        assert connection.invalidated is True

    asyncio.run(scenario())


def test_acquire_has_a_client_deadline_and_terminates_uncertain_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(
            mysql_documents,
            "ANSWER_SNAPSHOT_LOCK_ACQUIRE_DEADLINE_SECONDS",
            0.02,
            raising=False,
        )
        repository = object.__new__(MysqlDocumentRepository)
        connection = _ControlledConnection()
        acquiring = asyncio.create_task(
            repository._acquire_answer_snapshot_lock(connection)  # type: ignore[arg-type]
        )
        await connection.started.wait()
        done, _pending = await asyncio.wait({acquiring}, timeout=0.1)
        completed_within_deadline = acquiring in done
        if completed_within_deadline:
            with pytest.raises(AnswerSnapshotUnavailable):
                await acquiring
        else:
            await _finish_for_red(acquiring, connection)

        assert completed_within_deadline
        assert connection.invalidated is True

    asyncio.run(scenario())


def test_snapshot_body_cancellation_wins_over_a_release_failure() -> None:
    async def scenario() -> None:
        connection = _ControlledConnection(error=RuntimeError("release password=secret"))
        repository = object.__new__(_CancelInSnapshotRepository)
        repository._engine = _Engine(connection)  # type: ignore[assignment]
        repository._sessions = _Sessions()  # type: ignore[assignment]
        _CancelInSnapshotRepository.snapshot_started = asyncio.Event()
        snapshot = AnswerSnapshot(
            trace_id="trace-cancel",
            query_hash="sha256:" + "d" * 64,
            selected_revisions=(ReadyDocumentRevision("doc-a", "rev-a", "sha256:" + "a" * 64),),
            citations=(),
        )
        saving = asyncio.create_task(repository.save_answer_with_citations(snapshot))
        await _CancelInSnapshotRepository.snapshot_started.wait()
        saving.cancel("caller disconnected during snapshot")
        await connection.started.wait()
        connection.allow_result.set()

        with pytest.raises(asyncio.CancelledError) as caught:
            await saving

        assert caught.value.args == ("caller disconnected during snapshot",)
        assert connection.invalidated is True

    asyncio.run(scenario())
