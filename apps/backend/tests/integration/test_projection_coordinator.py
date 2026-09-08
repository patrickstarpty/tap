"""Real MySQL durability and cross-connection mutation authority gate."""

from __future__ import annotations

import asyncio
import os

import pytest
from apps.backend.tests.owned_mysql import owned_project_database_url
from scripts.migration_support import IsolatedMysql
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.mysql_projection import MysqlProjectionCoordinator
from tap.modules.knowledge.ports.errors import IndexUnavailable
from tap.platform.db.session import create_engine_and_session_factory

DATABASE_URL = os.getenv("TAP_DATABASE_URL", "")
ALIAS = "kb_doc_tapper_demo_active"
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


@pytest.mark.skipif(not DATABASE_URL, reason="requires isolated TAP_DATABASE_URL")
def test_mysql_projection_authority_is_shared_across_engines_and_reconstruction() -> None:
    async def scenario() -> None:
        first_engine, _ = create_engine_and_session_factory(DATABASE_URL)
        second_engine, _ = create_engine_and_session_factory(DATABASE_URL)
        first = MysqlProjectionCoordinator(
            first_engine,
            scope=VALIDATION_SCOPE,
            authority_namespace=NAMESPACE,
            lock_wait_seconds=2,
        )
        second = MysqlProjectionCoordinator(
            second_engine,
            scope=VALIDATION_SCOPE,
            authority_namespace=NAMESPACE,
            lock_wait_seconds=2,
        )
        await _clean(first_engine)
        await _seed_fence_facts(first_engine)
        entered_second = asyncio.Event()

        async def contender() -> tuple[int, str | None, bool]:
            async with second.mutation(ALIAS) as lease:
                entered_second.set()
                generation, physical = await lease.state()
                return generation, physical, await lease.is_fenced("rev_deleted")

        try:
            async with first.mutation(ALIAS) as lease:
                assert await lease.initialize("kb_doc_v1_tapper_demo") == (
                    1,
                    "kb_doc_v1_tapper_demo",
                )
                await lease.record_fence("rev_deleted", "doc_a")
                task = asyncio.create_task(contender())
                await asyncio.sleep(0.05)
                assert not entered_second.is_set()
                first_build = await lease.reserve_build(
                    "kb_doc_v1_tapper_demo_000000000001",
                    "kb_doc_v1_tapper_demo",
                    "a" * 32,
                )
                assert await lease.activate_build(first_build) == (
                    2,
                    "kb_doc_v1_tapper_demo_000000000001",
                )

            assert await task == (
                2,
                "kb_doc_v1_tapper_demo_000000000001",
                True,
            )
            async with second.mutation(ALIAS) as lease:
                assert await lease.fences(10) == (("rev_deleted", "doc_a"),)
                assert await lease.owned_cleanup(10) == ()
                second_build = await lease.reserve_build(
                    "kb_doc_v1_tapper_demo_000000000002",
                    "kb_doc_v1_tapper_demo_000000000001",
                    "b" * 32,
                )
                assert await lease.activate_build(second_build) == (
                    3,
                    "kb_doc_v1_tapper_demo_000000000002",
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
            await _clean_fence_facts(first_engine)
            await first.close()
            await second.close()
            await first_engine.dispose()
            await second_engine.dispose()

    asyncio.run(scenario())


@pytest.mark.skipif(not DATABASE_URL, reason="requires isolated TAP_DATABASE_URL")
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
            scope=VALIDATION_SCOPE,
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


def test_project_scope_rejects_foreign_physical_alias_before_yield(
    owned_project_mysql: IsolatedMysql,
) -> None:
    database_url = owned_project_database_url(owned_project_mysql)
    from dataclasses import replace

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE

    async def scenario() -> None:
        engine, _ = create_engine_and_session_factory(database_url)
        first = MysqlProjectionCoordinator(
            engine, authority_namespace=NAMESPACE, scope=VALIDATION_SCOPE
        )
        other_scope = replace(VALIDATION_SCOPE, project_id="projection-other")
        second = MysqlProjectionCoordinator(
            engine, authority_namespace=NAMESPACE, scope=other_scope
        )
        await _clean(engine)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO project (project_id, enterprise_id) "
                    "VALUES ('projection-other', 'local')"
                )
            )
        try:
            async with first.mutation(ALIAS) as lease:
                await lease.initialize("owned-physical")
            entered = False
            with pytest.raises(IndexUnavailable, match="ownership"):
                async with second.mutation(ALIAS):
                    entered = True
            assert entered is False
            async with first.mutation(ALIAS) as lease:
                assert await lease.state() == (1, "owned-physical")
        finally:
            await _clean(engine)
            async with engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM project WHERE project_id='projection-other'")
                )
            await engine.dispose()

    asyncio.run(scenario())


async def _seed_fence_facts(engine) -> None:
    from datetime import datetime

    from sqlalchemy import insert

    from tap.modules.knowledge.adapters.mysql_documents import (
        knowledge_document,
        knowledge_document_revision,
    )
    from tap.platform.db.project_scope import scope_values

    now = datetime(2026, 9, 5)
    async with engine.begin() as connection:
        from tap.modules.knowledge.adapters.mysql_documents import knowledge_source

        for document_id in ("doc_a", "doc_b"):
            await connection.execute(
                insert(knowledge_source).values(
                    **scope_values(VALIDATION_SCOPE),
                    source_id="src_" + document_id,
                    name="fixture",
                    created_at=now,
                    updated_at=now,
                )
            )
            await connection.execute(
                insert(knowledge_document).values(
                    **scope_values(VALIDATION_SCOPE),
                    document_id=document_id,
                    source_id="src_" + document_id,
                    filename="fence.txt",
                    media_type="text/plain",
                    source_content_hash="sha256:" + "a" * 64,
                    reservation_parser_version="test",
                    reservation_chunker_version="test",
                    reservation_pipeline_version="test",
                    status="deleted",
                    stage="pending",
                    created_at=now,
                    updated_at=now,
                )
            )
        await connection.execute(
            insert(knowledge_document_revision).values(
                **scope_values(VALIDATION_SCOPE),
                revision_id="rev_deleted",
                document_id="doc_a",
                source_id="src_doc_a",
                source_content_hash="sha256:" + "a" * 64,
                original_blob_locator="local/fence.txt",
                parser_version="test",
                chunker_version="test",
                pipeline_version="test",
                created_at=now,
            )
        )


async def _clean_fence_facts(engine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM knowledge_document_revision WHERE revision_id='rev_deleted'")
        )
        await connection.execute(
            text("DELETE FROM knowledge_document WHERE document_id IN ('doc_a', 'doc_b')")
        )
        await connection.execute(
            text("DELETE FROM knowledge_source WHERE source_id IN ('src_doc_a', 'src_doc_b')")
        )


def test_projection_fence_rejects_mismatched_document_on_first_insert(
    owned_project_mysql: IsolatedMysql,
) -> None:
    database_url = owned_project_database_url(owned_project_mysql)

    async def scenario() -> None:
        engine, _ = create_engine_and_session_factory(database_url)
        coordinator = MysqlProjectionCoordinator(
            engine, scope=VALIDATION_SCOPE, authority_namespace=NAMESPACE
        )
        await _clean(engine)
        await _seed_fence_facts(engine)
        try:
            async with coordinator.mutation(ALIAS) as lease:
                await lease.initialize("fence-parent-physical")
                with pytest.raises(IndexUnavailable, match="identity"):
                    await lease.record_fence("rev_deleted", "doc_b")
                assert await lease.is_fenced("rev_deleted") is False
                await lease.record_fence("rev_deleted", "doc_a")
                assert await lease.fences(10) == (("rev_deleted", "doc_a"),)
        finally:
            await _clean(engine)
            await _clean_fence_facts(engine)
            await coordinator.close()
            await engine.dispose()

    asyncio.run(scenario())
