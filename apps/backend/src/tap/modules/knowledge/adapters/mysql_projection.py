"""MySQL-backed cross-process generation and deletion-fence authority."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import TypeVar

from sqlalchemy import (
    BigInteger,
    Column,
    String,
    Table,
    delete,
    select,
    text,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from tap.modules.knowledge.ports.errors import IndexUnavailable
from tap.modules.knowledge.ports.projection import ProjectionMutationLease
from tap.operations.milvus.async_call import await_task_terminal
from tap.platform.db.schema import metadata

_DATABASE_DEADLINE_SECONDS = 30.0
_T = TypeVar("_T")

knowledge_projection_state = Table(
    "knowledge_projection_state",
    metadata,
    Column("alias_name", String(255), primary_key=True),
    Column("generation", BigInteger, nullable=False),
    Column("physical_collection", String(255), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
)
knowledge_projection_fence = Table(
    "knowledge_projection_fence",
    metadata,
    Column("alias_name", String(255), primary_key=True),
    Column("revision_id", String(128), primary_key=True),
    Column("document_id", String(64), nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
)
knowledge_projection_cleanup = Table(
    "knowledge_projection_cleanup",
    metadata,
    Column("alias_name", String(255), primary_key=True),
    Column("physical_collection", String(255), primary_key=True),
    Column("generation", BigInteger, nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
)


class MysqlProjectionCoordinator:
    """Own one connection-scoped MySQL advisory lock per mutation lease."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        authority_namespace: str = "default",
        lock_wait_seconds: int = 10,
    ) -> None:
        if not isinstance(engine, AsyncEngine):
            raise TypeError("projection coordinator requires an async SQLAlchemy engine")
        if type(lock_wait_seconds) is not int or not 1 <= lock_wait_seconds <= 30:
            raise ValueError("projection coordinator lock wait must be 1..30 seconds")
        _bounded_text(authority_namespace, "authority namespace", 64)
        self._engine = engine
        self._authority_namespace = authority_namespace
        self._lock_wait_seconds = lock_wait_seconds

    @asynccontextmanager
    async def mutation(self, alias: str) -> AsyncIterator[ProjectionMutationLease]:
        _bounded_text(alias, "alias", 255)
        authority_key = f"{self._authority_namespace}:{alias}"
        _bounded_text(authority_key, "namespaced alias", 255)
        connection = await _settled_database(self._engine.connect(), "connect")
        lock_name = "tap-proj:" + hashlib.sha256(authority_key.encode("utf-8")).hexdigest()[:55]
        acquired = False
        try:
            value = await _settled_database(
                connection.scalar(
                    text("SELECT GET_LOCK(:lock_name, :wait_seconds)"),
                    {"lock_name": lock_name, "wait_seconds": self._lock_wait_seconds},
                ),
                "acquire mutation lease",
            )
            await _settled_database(connection.commit(), "commit mutation lease acquisition")
            if value != 1:
                raise IndexUnavailable("Athena projection mutation lease is unavailable")
            acquired = True
            yield _MysqlProjectionLease(connection, authority_key)
        finally:
            if acquired:
                try:
                    await _settled_database(
                        connection.scalar(
                            text("SELECT RELEASE_LOCK(:lock_name)"),
                            {"lock_name": lock_name},
                        ),
                        "release mutation lease",
                    )
                    await _settled_database(
                        connection.commit(),
                        "commit mutation lease release",
                    )
                finally:
                    await _settled_database(connection.close(), "close mutation connection")
            else:
                await _settled_database(connection.close(), "close mutation connection")

    async def close(self) -> None:
        """The application owns the shared engine lifecycle."""


class _MysqlProjectionLease:
    def __init__(self, connection: AsyncConnection, alias: str) -> None:
        self._connection = connection
        self._alias = alias

    async def state(self) -> tuple[int, str | None]:
        async def operation() -> tuple[int, str | None]:
            row = (
                await self._connection.execute(
                    select(
                        knowledge_projection_state.c.generation,
                        knowledge_projection_state.c.physical_collection,
                    ).where(knowledge_projection_state.c.alias_name == self._alias)
                )
            ).one_or_none()
            if row is None:
                return 0, None
            generation, physical = row
            if type(generation) is not int or not isinstance(physical, str):
                raise IndexUnavailable("Athena projection generation row is malformed")
            return generation, physical

        return await _settled_database(operation(), "read projection generation")

    async def initialize(self, physical: str) -> tuple[int, str]:
        _bounded_text(physical, "physical collection", 255)

        async def operation() -> None:
            statement = mysql_insert(knowledge_projection_state).values(
                alias_name=self._alias,
                generation=1,
                physical_collection=physical,
            )
            await self._connection.execute(statement.prefix_with("IGNORE"))
            await self._connection.commit()

        await _settled_database(operation(), "initialize projection generation")
        generation, recorded = await self.state()
        if recorded is None:
            raise IndexUnavailable("Athena projection generation initialization was lost")
        return generation, recorded

    async def is_fenced(self, revision_id: str) -> bool:
        _bounded_text(revision_id, "revision", 128)

        async def operation() -> bool:
            value = await self._connection.scalar(
                select(knowledge_projection_fence.c.revision_id).where(
                    knowledge_projection_fence.c.alias_name == self._alias,
                    knowledge_projection_fence.c.revision_id == revision_id,
                )
            )
            return value is not None

        return await _settled_database(operation(), "read projection fence")

    async def record_fence(self, revision_id: str, document_id: str) -> None:
        _bounded_text(revision_id, "revision", 128)
        _bounded_text(document_id, "document", 64)

        async def operation() -> str | None:
            statement = mysql_insert(knowledge_projection_fence).values(
                alias_name=self._alias,
                revision_id=revision_id,
                document_id=document_id,
            )
            await self._connection.execute(statement.prefix_with("IGNORE"))
            recorded = await self._connection.scalar(
                select(knowledge_projection_fence.c.document_id).where(
                    knowledge_projection_fence.c.alias_name == self._alias,
                    knowledge_projection_fence.c.revision_id == revision_id,
                )
            )
            await self._connection.commit()
            return recorded if isinstance(recorded, str) else None

        recorded = await _settled_database(operation(), "record projection fence")
        if recorded != document_id:
            raise IndexUnavailable("Athena projection fence identity conflicts")

    async def fences(self, limit: int) -> tuple[tuple[str, str], ...]:
        if type(limit) is not int or not 1 <= limit <= 10_001:
            raise ValueError("projection fence inventory limit is outside 1..10001")

        async def operation() -> tuple[tuple[str, str], ...]:
            result = await self._connection.execute(
                select(
                    knowledge_projection_fence.c.revision_id,
                    knowledge_projection_fence.c.document_id,
                )
                .where(knowledge_projection_fence.c.alias_name == self._alias)
                .order_by(knowledge_projection_fence.c.revision_id)
                .limit(limit)
            )
            rows = tuple((row[0], row[1]) for row in result)
            if any(not isinstance(a, str) or not isinstance(b, str) for a, b in rows):
                raise IndexUnavailable("Athena projection fence inventory is malformed")
            return rows

        return await _settled_database(operation(), "list projection fences")

    async def activate(self, physical: str) -> tuple[int, str]:
        _bounded_text(physical, "physical collection", 255)
        generation, recorded = await self.state()
        if recorded is None:
            return await self.initialize(physical)
        if recorded == physical:
            return generation, physical

        async def operation() -> None:
            result = await self._connection.execute(
                update(knowledge_projection_state)
                .where(
                    knowledge_projection_state.c.alias_name == self._alias,
                    knowledge_projection_state.c.generation == generation,
                    knowledge_projection_state.c.physical_collection == recorded,
                )
                .values(
                    generation=generation + 1,
                    physical_collection=physical,
                    updated_at=text("CURRENT_TIMESTAMP(6)"),
                )
            )
            if result.rowcount != 1:
                raise IndexUnavailable("Athena projection generation changed unexpectedly")
            await self._connection.commit()

        await _settled_database(operation(), "activate projection generation")
        return generation + 1, physical

    async def enqueue_cleanup(self, physical: str) -> None:
        _bounded_text(physical, "physical collection", 255)
        generation, _ = await self.state()

        async def operation() -> None:
            statement = mysql_insert(knowledge_projection_cleanup).values(
                alias_name=self._alias,
                physical_collection=physical,
                generation=generation,
            )
            await self._connection.execute(statement.prefix_with("IGNORE"))
            await self._connection.commit()

        await _settled_database(operation(), "enqueue projection cleanup")

    async def pending_cleanup(self, limit: int) -> tuple[str, ...]:
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError("projection cleanup inventory limit is outside 1..64")

        async def operation() -> tuple[str, ...]:
            result = await self._connection.execute(
                select(knowledge_projection_cleanup.c.physical_collection)
                .where(knowledge_projection_cleanup.c.alias_name == self._alias)
                .order_by(
                    knowledge_projection_cleanup.c.generation,
                    knowledge_projection_cleanup.c.physical_collection,
                )
                .limit(limit)
            )
            values = tuple(row[0] for row in result)
            if any(not isinstance(value, str) for value in values):
                raise IndexUnavailable("Athena projection cleanup inventory is malformed")
            return values

        return await _settled_database(operation(), "list projection cleanup")

    async def complete_cleanup(self, physical: str) -> None:
        _bounded_text(physical, "physical collection", 255)

        async def operation() -> None:
            await self._connection.execute(
                delete(knowledge_projection_cleanup).where(
                    knowledge_projection_cleanup.c.alias_name == self._alias,
                    knowledge_projection_cleanup.c.physical_collection == physical,
                )
            )
            await self._connection.commit()

        await _settled_database(operation(), "complete projection cleanup")


async def _settled_database(
    coroutine: Awaitable[_T],
    operation: str,
) -> _T:
    task = asyncio.ensure_future(coroutine)
    try:
        async with asyncio.timeout(_DATABASE_DEADLINE_SECONDS):
            return await asyncio.shield(task)
    except asyncio.CancelledError as cancellation:
        outcome = await await_task_terminal(task, initial_cancellations=(cancellation,))
        raise outcome.cancellations[0]
    except TimeoutError as error:
        task.cancel()
        await await_task_terminal(task)
        raise IndexUnavailable(f"Athena projection database {operation} timed out") from error
    except IndexUnavailable:
        raise
    except DBAPIError as error:
        raise IndexUnavailable(f"Athena projection database {operation} failed") from error


def _bounded_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError(f"projection {field} must be a bounded non-empty string")
    return value
