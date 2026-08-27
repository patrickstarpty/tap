"""Real MySQL durability and cross-connection mutation authority gate."""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text

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
                await lease.enqueue_cleanup("kb_doc_v1_athena_demo_old000000")
                task = asyncio.create_task(contender())
                await asyncio.sleep(0.05)
                assert not entered_second.is_set()
                assert await lease.activate("kb_doc_v1_athena_demo_000000000001") == (
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
                assert await lease.pending_cleanup(10) == ("kb_doc_v1_athena_demo_old000000",)
                with pytest.raises(IndexUnavailable):
                    await lease.record_fence("rev_deleted", "doc_b")
        finally:
            await _clean(first_engine)
            await first.close()
            await second.close()
            await first_engine.dispose()
            await second_engine.dispose()

    asyncio.run(scenario())
