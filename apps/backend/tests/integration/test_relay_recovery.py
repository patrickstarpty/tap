from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tap.modules.chat.adapters.mysql import MysqlTurnRepository, OutboxStore
from tap.modules.chat.application.ports import CreateTurnCommand, DispatchMessage, LeaseLost
from tap.modules.chat.domain.models import ChatId, CommandId, TurnId
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.messaging.redis_dispatch import RedisDispatchPublisher, Relay

DATABASE_URL = os.getenv(
    "TAP_DATABASE_URL",
    "mysql+asyncmy://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4",
)
OWNED_TABLES = ("outbox", "turn_snapshot", "chat_event", "chat_turn")


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class ProcessStopped(BaseException):
    pass


class AtomicMemoryPublisher:
    def __init__(
        self,
        delivered: dict[str, DispatchMessage] | None = None,
        *,
        stop_after_first_publish: bool = False,
        fail_with: Exception | None = None,
    ) -> None:
        self.delivered = delivered if delivered is not None else {}
        self.stop_after_first_publish = stop_after_first_publish
        self.fail_with = fail_with
        self.attempted_command_ids: list[str] = []

    async def publish_once(self, message: DispatchMessage) -> bool:
        self.attempted_command_ids.append(str(message.command_id))
        if self.fail_with is not None:
            raise self.fail_with
        inserted = str(message.command_id) not in self.delivered
        if inserted:
            self.delivered[str(message.command_id)] = message
        if self.stop_after_first_publish:
            self.stop_after_first_publish = False
            raise ProcessStopped
        return inserted


class RecordingRedis:
    def __init__(self, result: int) -> None:
        self.result = result
        self.calls: list[tuple[str, int, tuple[object, ...]]] = []

    async def eval(self, script: str, number_of_keys: int, *values: object) -> int:
        self.calls.append((script, number_of_keys, values))
        return self.result


class RedisLuaEmulator:
    """Execute the adapter's small Redis command subset with Redis error semantics."""

    def __init__(
        self,
        *,
        stream: list[str] | None = None,
        fail_next_xadd: bool = False,
    ) -> None:
        self.stream = stream if stream is not None else []
        self.fail_next_xadd = fail_next_xadd
        self.dedup_keys: set[str] = set()

    async def eval(self, script: str, number_of_keys: int, *values: object) -> int:
        assert number_of_keys == 2
        dedup_key = str(values[0])
        capacity = int(values[-2]) if len(values) == 5 else 100_000
        payload = str(values[-1])
        commands = re.findall(r"redis\.call\('([A-Z]+)'", script)
        for command in commands:
            if command == "EXISTS" and dedup_key in self.dedup_keys:
                return 0
            if command == "XLEN" and len(self.stream) >= capacity:
                return -1
            if command == "SET":
                if dedup_key in self.dedup_keys:
                    return 0
                self.dedup_keys.add(dedup_key)
            if command == "XADD":
                if self.fail_next_xadd:
                    self.fail_next_xadd = False
                    raise RuntimeError("injected XADD failure")
                self.stream.append(payload)
        return 1


async def _clean_owned_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        for table in OWNED_TABLES:
            await connection.execute(text(f"DELETE FROM {table}"))


def _run_with_clean_database(
    scenario: Callable[[AsyncEngine, MysqlTurnRepository, OutboxStore], Awaitable[None]],
) -> None:
    async def run() -> None:
        engine, sessions = create_engine_and_session_factory(DATABASE_URL)
        repository = MysqlTurnRepository(sessions)
        outbox = OutboxStore(sessions)
        await _clean_owned_tables(engine)
        try:
            await scenario(engine, repository, outbox)
        finally:
            await _clean_owned_tables(engine)
            await engine.dispose()

    asyncio.run(run())


def _command(number: int, now: datetime) -> CreateTurnCommand:
    return CreateTurnCommand(
        command_id=CommandId(f"relay-command-{number}"),
        turn_id=TurnId(f"relay-turn-{number}"),
        chat_id=ChatId(f"relay-chat-{number}"),
        client_request_id=f"relay-request-{number}",
        message="Dispatch this turn",
        occurred_at=now,
    )


def test_relay_claims_no_more_than_the_requested_batch() -> None:
    async def scenario(
        engine: AsyncEngine,
        repository: MysqlTurnRepository,
        outbox: OutboxStore,
    ) -> None:
        now = datetime(2026, 8, 23, 11, 0, 0)
        for number in range(3):
            await repository.create_with_outbox(_command(number, now))
        publisher = AtomicMemoryPublisher()
        relay = Relay(
            outbox=outbox,
            publisher=publisher,
            clock=MutableClock(now),
            worker_id="relay-bounded",
            lease_duration=timedelta(seconds=30),
            retry_delay=timedelta(seconds=5),
        )

        published = await relay.publish_pending(batch_size=2)

        async with engine.connect() as connection:
            published_rows = await connection.scalar(
                text("SELECT COUNT(*) FROM outbox WHERE published_at IS NOT NULL")
            )
            pending_rows = await connection.scalar(
                text("SELECT COUNT(*) FROM outbox WHERE published_at IS NULL")
            )
        assert published == 2
        assert published_rows == 2
        assert pending_rows == 1

    _run_with_clean_database(scenario)


def test_publish_failure_records_attempt_and_waits_until_injected_retry_time() -> None:
    async def scenario(
        engine: AsyncEngine,
        repository: MysqlTurnRepository,
        outbox: OutboxStore,
    ) -> None:
        now = datetime(2026, 8, 23, 11, 30, 0)
        await repository.create_with_outbox(_command(10, now))
        clock = MutableClock(now)
        failing = AtomicMemoryPublisher(fail_with=RuntimeError("redis unavailable"))
        relay = Relay(
            outbox=outbox,
            publisher=failing,
            clock=clock,
            worker_id="relay-retry",
            lease_duration=timedelta(seconds=30),
            retry_delay=timedelta(seconds=5),
        )

        assert await relay.publish_pending(batch_size=1) == 0
        assert await relay.publish_pending(batch_size=1) == 0

        async with engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT attempt_count, next_attempt_at, last_error FROM outbox "
                            "WHERE command_id = 'relay-command-10'"
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert row["attempt_count"] == 1
        assert row["next_attempt_at"] == datetime(2026, 8, 23, 11, 30, 5)
        assert row["last_error"] == "redis unavailable"

        clock.advance(timedelta(seconds=5))
        succeeding = AtomicMemoryPublisher()
        restarted = Relay(
            outbox=outbox,
            publisher=succeeding,
            clock=clock,
            worker_id="relay-retry-restarted",
            lease_duration=timedelta(seconds=30),
            retry_delay=timedelta(seconds=5),
        )
        assert await restarted.publish_pending(batch_size=1) == 1

    _run_with_clean_database(scenario)


def test_expired_claim_replays_the_same_command_without_duplicate_delivery() -> None:
    async def scenario(
        engine: AsyncEngine,
        repository: MysqlTurnRepository,
        outbox: OutboxStore,
    ) -> None:
        now = datetime(2026, 8, 23, 12, 0, 0)
        await repository.create_with_outbox(_command(20, now))
        clock = MutableClock(now)
        downstream: dict[str, DispatchMessage] = {}
        interrupted_publisher = AtomicMemoryPublisher(
            downstream,
            stop_after_first_publish=True,
        )
        interrupted = Relay(
            outbox=outbox,
            publisher=interrupted_publisher,
            clock=clock,
            worker_id="relay-before-crash",
            lease_duration=timedelta(seconds=30),
            retry_delay=timedelta(seconds=5),
        )

        try:
            await interrupted.publish_pending(batch_size=1)
        except ProcessStopped:
            pass
        else:
            raise AssertionError("the simulated worker process should stop after publishing")

        clock.advance(timedelta(seconds=30))
        restarted_publisher = AtomicMemoryPublisher(downstream)
        restarted = Relay(
            outbox=outbox,
            publisher=restarted_publisher,
            clock=clock,
            worker_id="relay-after-crash",
            lease_duration=timedelta(seconds=30),
            retry_delay=timedelta(seconds=5),
        )

        assert await restarted.publish_pending(batch_size=1) == 1

        async with engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT command_id, attempt_count, published_at FROM outbox "
                            "WHERE command_id = 'relay-command-20'"
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert list(downstream) == ["relay-command-20"]
        assert interrupted_publisher.attempted_command_ids == ["relay-command-20"]
        assert restarted_publisher.attempted_command_ids == ["relay-command-20"]
        assert row["command_id"] == "relay-command-20"
        assert row["attempt_count"] == 2
        assert row["published_at"] == datetime(2026, 8, 23, 12, 0, 30)

    _run_with_clean_database(scenario)


def test_xadd_failure_cannot_leave_a_marker_that_suppresses_retry() -> None:
    async def scenario() -> None:
        redis = RedisLuaEmulator(fail_next_xadd=True)
        publisher = RedisDispatchPublisher(
            redis=redis,
            stream_name="tap:commands",
            dedup_ttl=timedelta(hours=1),
        )
        message = DispatchMessage(
            command_id=CommandId("retryable-command"),
            outbox_id="retryable-outbox",
            aggregate_type="chat_turn",
            aggregate_id="retryable-turn",
            sequence=4,
        )

        with pytest.raises(RuntimeError, match="injected XADD failure"):
            await publisher.publish_once(message)
        assert await publisher.publish_once(message) is True

        assert len(redis.stream) == 1
        assert json.loads(redis.stream[0])["commandId"] == "retryable-command"

    asyncio.run(scenario())


def test_full_redis_stream_keeps_outbox_pending_for_retry() -> None:
    async def scenario(
        engine: AsyncEngine,
        repository: MysqlTurnRepository,
        outbox: OutboxStore,
    ) -> None:
        now = datetime(2026, 8, 23, 12, 30, 0)
        await repository.create_with_outbox(_command(30, now))
        redis = RedisLuaEmulator(stream=['{"commandId":"unseen-command"}'])
        publisher = RedisDispatchPublisher(
            redis=redis,
            stream_name="tap:commands",
            dedup_ttl=timedelta(hours=1),
            max_stream_length=1,
        )
        relay = Relay(
            outbox=outbox,
            publisher=publisher,
            clock=MutableClock(now),
            worker_id="relay-capacity",
            lease_duration=timedelta(seconds=30),
            retry_delay=timedelta(seconds=5),
        )

        assert await relay.publish_pending(batch_size=1) == 0

        async with engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT status, attempt_count, next_attempt_at, last_error FROM outbox "
                            "WHERE command_id = 'relay-command-30'"
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert len(redis.stream) == 1
        assert row["status"] == "pending"
        assert row["attempt_count"] == 1
        assert row["next_attempt_at"] == datetime(2026, 8, 23, 12, 30, 5)
        assert row["last_error"] == "Redis command stream reached capacity 1"

    _run_with_clean_database(scenario)


def test_maximum_attempt_failure_becomes_a_durable_terminal_fact() -> None:
    async def scenario(
        engine: AsyncEngine,
        repository: MysqlTurnRepository,
        outbox: OutboxStore,
    ) -> None:
        now = datetime(2026, 8, 23, 13, 0, 0)
        await repository.create_with_outbox(_command(40, now))
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE outbox SET attempt_count = 9 WHERE command_id = 'relay-command-40'")
            )
        relay = Relay(
            outbox=outbox,
            publisher=AtomicMemoryPublisher(fail_with=RuntimeError("permanent outage")),
            clock=MutableClock(now),
            worker_id="relay-terminal",
            lease_duration=timedelta(seconds=30),
            retry_delay=timedelta(seconds=5),
        )

        assert await relay.publish_pending(batch_size=1) == 0

        async with engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT status, attempt_count, last_error FROM outbox "
                            "WHERE command_id = 'relay-command-40'"
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert dict(row) == {
            "status": "delivery_failed",
            "attempt_count": 10,
            "last_error": "permanent outage",
        }

    _run_with_clean_database(scenario)


def test_stale_same_worker_claim_cannot_settle_a_newer_claim() -> None:
    async def scenario(
        engine: AsyncEngine,
        repository: MysqlTurnRepository,
        outbox: OutboxStore,
    ) -> None:
        now = datetime(2026, 8, 23, 13, 30, 0)
        await repository.create_with_outbox(_command(50, now))
        first = (
            await outbox.claim_pending(
                worker_id="shared-worker",
                now=now,
                lease_duration=timedelta(seconds=30),
                limit=1,
            )
        )[0]
        reclaimed_at = now + timedelta(seconds=30)
        assert await outbox.reconcile_expired(reclaimed_at, 1) == 1
        second = (
            await outbox.claim_pending(
                worker_id="shared-worker",
                now=reclaimed_at,
                lease_duration=timedelta(seconds=30),
                limit=1,
            )
        )[0]

        assert first.claim_token != second.claim_token
        with pytest.raises(LeaseLost):
            await outbox.mark_published(
                outbox_id=first.message.outbox_id,
                claim_token=first.claim_token,
                published_at=reclaimed_at,
            )

        async with engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT status, claimed_by FROM outbox "
                            "WHERE command_id = 'relay-command-50'"
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert dict(row) == {"status": "publishing", "claimed_by": "shared-worker"}

        await outbox.mark_published(
            outbox_id=second.message.outbox_id,
            claim_token=second.claim_token,
            published_at=reclaimed_at,
        )

    _run_with_clean_database(scenario)


@pytest.mark.parametrize("batch_size", [0, -1, 501])
def test_relay_rejects_batch_sizes_outside_its_hard_bound(batch_size: int) -> None:
    async def scenario(
        engine: AsyncEngine,
        repository: MysqlTurnRepository,
        outbox: OutboxStore,
    ) -> None:
        relay = Relay(
            outbox=outbox,
            publisher=AtomicMemoryPublisher(),
            clock=MutableClock(datetime(2026, 8, 23, 14, 0, 0)),
            worker_id="relay-invalid-batch",
            lease_duration=timedelta(seconds=30),
            retry_delay=timedelta(seconds=5),
        )

        with pytest.raises(ValueError, match="batch_size must be between 1 and 500"):
            await relay.publish_pending(batch_size=batch_size)

    _run_with_clean_database(scenario)


def test_relay_rejects_attempt_limit_above_its_hard_bound() -> None:
    async def scenario(
        engine: AsyncEngine,
        repository: MysqlTurnRepository,
        outbox: OutboxStore,
    ) -> None:
        with pytest.raises(ValueError, match="maximum_attempts must be between 1 and 100"):
            Relay(
                outbox=outbox,
                publisher=AtomicMemoryPublisher(),
                clock=MutableClock(datetime(2026, 8, 23, 14, 30, 0)),
                worker_id="relay-invalid-attempt-limit",
                lease_duration=timedelta(seconds=30),
                retry_delay=timedelta(seconds=5),
                maximum_attempts=101,
            )

    _run_with_clean_database(scenario)


def test_redis_adapter_atomically_deduplicates_a_lookup_only_message() -> None:
    async def scenario() -> None:
        redis = RecordingRedis(result=1)
        publisher = RedisDispatchPublisher(
            redis=redis,
            stream_name="tap:commands",
            dedup_ttl=timedelta(hours=1),
            max_stream_length=10_000,
        )
        message = DispatchMessage(
            command_id=CommandId("safe-command"),
            outbox_id="safe-outbox",
            aggregate_type="chat_turn",
            aggregate_id="safe-turn",
            sequence=None,
        )

        assert await publisher.publish_once(message) is True

        script, number_of_keys, values = redis.calls[0]
        payload = json.loads(str(values[4]))
        assert number_of_keys == 2
        assert values[:3] == (
            "tap:dispatch-dedup:safe-command",
            "tap:commands",
            3600,
        )
        assert values[3] == 10_000
        assert payload == {
            "aggregateId": "safe-turn",
            "aggregateType": "chat_turn",
            "commandId": "safe-command",
            "outboxId": "safe-outbox",
            "sequence": None,
        }
        assert "state" not in payload
        assert "token" not in payload

    asyncio.run(scenario())
