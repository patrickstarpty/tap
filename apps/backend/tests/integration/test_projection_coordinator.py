"""Real MySQL durability and cross-connection mutation authority gate."""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tap.modules.knowledge.adapters.mysql_projection import MysqlProjectionCoordinator
from tap.modules.knowledge.ports.errors import IndexUnavailable
from tap.platform.db.session import create_engine_and_session_factory

DATABASE_URL = os.getenv(
    "TAP_DATABASE_URL",
    "mysql+asyncmy://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4",
)
ALIAS = "kb_doc_athena_demo_active"
NAMESPACE = "task5-coordinator-contract"
AUTHORITY_KEY = f"{NAMESPACE}:{ALIAS}"


async def _clean(engine) -> None:  # type: ignore[no-untyped-def]
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM knowledge_projection_lineage WHERE alias_name=:alias"),
            {"alias": AUTHORITY_KEY},
        )
        await connection.execute(
            text("DELETE FROM knowledge_projection_cleanup WHERE alias_name=:alias"),
            {"alias": AUTHORITY_KEY},
        )
        await connection.execute(
            text("DELETE FROM knowledge_projection_fence WHERE alias_name=:alias"),
            {"alias": AUTHORITY_KEY},
        )
        await connection.execute(
            text("DELETE FROM knowledge_projection_state WHERE alias_name=:alias"),
            {"alias": AUTHORITY_KEY},
        )


def test_mysql_projection_authority_is_shared_across_engines_and_reconstruction() -> None:
    async def scenario() -> None:
        first_engine, _ = create_engine_and_session_factory(DATABASE_URL)
        second_engine, _ = create_engine_and_session_factory(DATABASE_URL)
        first = MysqlProjectionCoordinator(
            first_engine,
            authority_namespace=NAMESPACE,
            lock_wait_seconds=2,
        )
        second = MysqlProjectionCoordinator(
            second_engine,
            authority_namespace=NAMESPACE,
            lock_wait_seconds=2,
        )
        await _clean(first_engine)
        entered_second = asyncio.Event()

        async def contender() -> tuple[int, str | None, bool]:
            async with second.mutation(ALIAS) as lease:
                entered_second.set()
                generation, physical = await lease.state()
                return generation, physical, await lease.is_fenced("rev_deleted")

        try:
            async with first.mutation(ALIAS) as lease:
                assert await lease.initialize("kb_doc_v1_athena_demo") == (
                    1,
                    "kb_doc_v1_athena_demo",
                )
                await lease.record_fence("rev_deleted", "doc_a")
                task = asyncio.create_task(contender())
                await asyncio.sleep(0.05)
                assert not entered_second.is_set()
                first_build = await lease.reserve_build(
                    "kb_doc_v1_athena_demo_000000000001",
                    "kb_doc_v1_athena_demo",
                    "a" * 32,
                )
                assert await lease.activate_build(first_build) == (
                    2,
                    "kb_doc_v1_athena_demo_000000000001",
                )

            assert await task == (
                2,
                "kb_doc_v1_athena_demo_000000000001",
                True,
            )
            async with second.mutation(ALIAS) as lease:
                assert await lease.fences(10) == (("rev_deleted", "doc_a"),)
                assert await lease.owned_cleanup(10) == ()
                second_build = await lease.reserve_build(
                    "kb_doc_v1_athena_demo_000000000002",
                    "kb_doc_v1_athena_demo_000000000001",
                    "b" * 32,
                )
                assert await lease.activate_build(second_build) == (
                    3,
                    "kb_doc_v1_athena_demo_000000000002",
                )
                cleanup = await lease.owned_cleanup(10)
                assert cleanup == (
                    first_build.__class__(
                        physical_collection=first_build.physical_collection,
                        operation_id=first_build.operation_id,
                        predecessor_collection=first_build.predecessor_collection,
                        status="cleanup",
                    ),
                )
                assert await lease.verify_cleanup(cleanup[0]) is True
                await lease.complete_owned_cleanup(cleanup[0])
                assert await lease.owned_cleanup(10) == ()
                with pytest.raises(IndexUnavailable):
                    await lease.record_fence("rev_deleted", "doc_b")
        finally:
            await _clean(first_engine)
            await first.close()
            await second.close()
            await first_engine.dispose()
            await second_engine.dispose()

    asyncio.run(scenario())


def test_cancelled_connection_acquisition_returns_late_checkout_before_rethrow() -> None:
    """A shielded connect that succeeds after cancellation must not exhaust a size-one pool."""

    async def scenario() -> None:
        engine = create_async_engine(
            DATABASE_URL,
            pool_size=1,
            max_overflow=0,
            pool_timeout=1,
        )
        coordinator = MysqlProjectionCoordinator(
            engine,
            authority_namespace="task5-connect-cancellation",
            lock_wait_seconds=1,
        )
        holder = await engine.connect()

        async def acquire() -> None:
            async with coordinator.mutation(ALIAS):
                raise AssertionError("cancelled waiter entered mutation body")

        task = asyncio.create_task(acquire())
        await asyncio.sleep(0.05)
        task.cancel("cancel-connect-waiter")
        await holder.close()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert engine.sync_engine.pool.checkedout() == 0
        async with asyncio.timeout(1):
            async with coordinator.mutation(ALIAS) as lease:
                assert await lease.state() == (0, None)
        assert engine.sync_engine.pool.checkedout() == 0
        await coordinator.close()
        await engine.dispose()

    asyncio.run(scenario())
