"""MySQL-backed cross-process generation and deletion-fence authority."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Sequence
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
from tap.modules.knowledge.ports.projection import (
    ProjectionMutationLease,
    ProjectionOwnershipReceipt,
)
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
knowledge_projection_lineage = Table(
    "knowledge_projection_lineage",
    metadata,
    Column("alias_name", String(255), primary_key=True),
    Column("physical_collection", String(255), primary_key=True),
    Column("operation_id", String(64), nullable=False),
    Column("predecessor_collection", String(255), nullable=False),
    Column("predecessor_generation", BigInteger, nullable=False),
    Column("generation", BigInteger, nullable=True),
    Column("status", String(16), nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
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
        connection = await _acquire_database_connection(self._engine)
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
                    released = await _settled_database(
                        connection.scalar(
                            text("SELECT RELEASE_LOCK(:lock_name)"),
                            {"lock_name": lock_name},
                        ),
                        "release mutation lease",
                    )
                    if released != 1:
                        raise IndexUnavailable("Athena projection mutation lease release failed")
                    await _settled_database(
                        connection.commit(),
                        "commit mutation lease release",
                    )
                except BaseException:
                    await _discard_connection_terminal(connection)
                    raise
                await _close_connection_terminal(connection)
            else:
                await _close_connection_terminal(connection)

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

    async def reserve_build(
        self,
        physical: str,
        predecessor: str,
        operation_id: str,
    ) -> ProjectionOwnershipReceipt:
        _bounded_text(physical, "physical collection", 255)
        _bounded_text(predecessor, "predecessor collection", 255)
        requested = ProjectionOwnershipReceipt(
            physical_collection=physical,
            operation_id=operation_id,
            predecessor_collection=predecessor,
            status="building",
        )
        _validate_receipt(requested, expected_status="building")
        generation, recorded = await self.state()
        if recorded != predecessor:
            raise IndexUnavailable("Athena projection build predecessor changed")

        async def operation() -> None:
            await self._connection.execute(
                mysql_insert(knowledge_projection_lineage).values(
                    alias_name=self._alias,
                    physical_collection=physical,
                    operation_id=operation_id,
                    predecessor_collection=predecessor,
                    predecessor_generation=generation,
                    generation=None,
                    status="building",
                )
            )
            await self._connection.commit()

        await _settled_database(operation(), "reserve projection build ownership")
        receipt = await self.ownership(physical)
        if receipt is None:
            raise IndexUnavailable("Athena projection build ownership was lost")
        return receipt

    async def ownership(self, physical: str) -> ProjectionOwnershipReceipt | None:
        _bounded_text(physical, "physical collection", 255)

        async def operation() -> ProjectionOwnershipReceipt | None:
            row = (
                await self._connection.execute(
                    select(
                        knowledge_projection_lineage.c.physical_collection,
                        knowledge_projection_lineage.c.operation_id,
                        knowledge_projection_lineage.c.predecessor_collection,
                        knowledge_projection_lineage.c.status,
                    ).where(
                        knowledge_projection_lineage.c.alias_name == self._alias,
                        knowledge_projection_lineage.c.physical_collection == physical,
                    )
                )
            ).one_or_none()
            return None if row is None else _ownership_receipt(row)

        return await _settled_database(operation(), "read projection ownership")

    async def activate_build(
        self,
        receipt: ProjectionOwnershipReceipt,
    ) -> tuple[int, str]:
        _validate_receipt(receipt, expected_status="building")
        generation, recorded = await self.state()
        if recorded != receipt.predecessor_collection:
            raise IndexUnavailable("Athena projection build predecessor changed")

        async def operation() -> None:
            lineage = (
                await self._connection.execute(
                    select(
                        knowledge_projection_lineage.c.operation_id,
                        knowledge_projection_lineage.c.predecessor_collection,
                        knowledge_projection_lineage.c.predecessor_generation,
                        knowledge_projection_lineage.c.status,
                    ).where(
                        knowledge_projection_lineage.c.alias_name == self._alias,
                        knowledge_projection_lineage.c.physical_collection
                        == receipt.physical_collection,
                    )
                )
            ).one_or_none()
            if lineage != (
                receipt.operation_id,
                receipt.predecessor_collection,
                generation,
                "building",
            ):
                raise IndexUnavailable("Athena projection build lineage conflicts")
            state_update = await self._connection.execute(
                update(knowledge_projection_state)
                .where(
                    knowledge_projection_state.c.alias_name == self._alias,
                    knowledge_projection_state.c.generation == generation,
                    knowledge_projection_state.c.physical_collection == recorded,
                )
                .values(
                    generation=generation + 1,
                    physical_collection=receipt.physical_collection,
                    updated_at=text("CURRENT_TIMESTAMP(6)"),
                )
            )
            if state_update.rowcount != 1:
                raise IndexUnavailable("Athena projection generation changed unexpectedly")
            await self._connection.execute(
                update(knowledge_projection_lineage)
                .where(
                    knowledge_projection_lineage.c.alias_name == self._alias,
                    knowledge_projection_lineage.c.physical_collection == recorded,
                    knowledge_projection_lineage.c.generation == generation,
                    knowledge_projection_lineage.c.status == "active",
                )
                .values(status="cleanup", updated_at=text("CURRENT_TIMESTAMP(6)"))
            )
            owned_update = await self._connection.execute(
                update(knowledge_projection_lineage)
                .where(
                    knowledge_projection_lineage.c.alias_name == self._alias,
                    knowledge_projection_lineage.c.physical_collection
                    == receipt.physical_collection,
                    knowledge_projection_lineage.c.operation_id == receipt.operation_id,
                    knowledge_projection_lineage.c.status == "building",
                )
                .values(
                    generation=generation + 1,
                    status="active",
                    updated_at=text("CURRENT_TIMESTAMP(6)"),
                )
            )
            if owned_update.rowcount != 1:
                raise IndexUnavailable("Athena projection activation ownership changed")
            await self._connection.commit()

        await _settled_database(operation(), "activate owned projection generation")
        return generation + 1, receipt.physical_collection

    async def abandon_build(self, receipt: ProjectionOwnershipReceipt) -> None:
        _validate_receipt(receipt, expected_status="building")

        async def operation() -> None:
            result = await self._connection.execute(
                update(knowledge_projection_lineage)
                .where(
                    knowledge_projection_lineage.c.alias_name == self._alias,
                    knowledge_projection_lineage.c.physical_collection
                    == receipt.physical_collection,
                    knowledge_projection_lineage.c.operation_id == receipt.operation_id,
                    knowledge_projection_lineage.c.predecessor_collection
                    == receipt.predecessor_collection,
                    knowledge_projection_lineage.c.status == "building",
                )
                .values(status="cleanup", updated_at=text("CURRENT_TIMESTAMP(6)"))
            )
            if result.rowcount != 1:
                raise IndexUnavailable("Athena projection build ownership changed")
            await self._connection.commit()

        await _settled_database(operation(), "abandon projection build")

    async def owned_cleanup(self, limit: int) -> tuple[ProjectionOwnershipReceipt, ...]:
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError("projection cleanup inventory limit is outside 1..64")

        async def operation() -> tuple[ProjectionOwnershipReceipt, ...]:
            result = await self._connection.execute(
                select(
                    knowledge_projection_lineage.c.physical_collection,
                    knowledge_projection_lineage.c.operation_id,
                    knowledge_projection_lineage.c.predecessor_collection,
                    knowledge_projection_lineage.c.status,
                )
                .where(
                    knowledge_projection_lineage.c.alias_name == self._alias,
                    knowledge_projection_lineage.c.status.in_(("building", "cleanup")),
                )
                .order_by(
                    knowledge_projection_lineage.c.created_at,
                    knowledge_projection_lineage.c.physical_collection,
                )
                .limit(limit)
            )
            return tuple(_ownership_receipt(row) for row in result)

        return await _settled_database(operation(), "list owned projection cleanup")

    async def verify_cleanup(self, receipt: ProjectionOwnershipReceipt) -> bool:
        _validate_receipt(receipt, expected_status=receipt.status)
        if receipt.status not in {"building", "cleanup"}:
            return False
        return await self.ownership(receipt.physical_collection) == receipt

    async def complete_owned_cleanup(self, receipt: ProjectionOwnershipReceipt) -> None:
        if not await self.verify_cleanup(receipt):
            raise IndexUnavailable("Athena projection cleanup ownership changed")

        async def operation() -> None:
            result = await self._connection.execute(
                delete(knowledge_projection_lineage).where(
                    knowledge_projection_lineage.c.alias_name == self._alias,
                    knowledge_projection_lineage.c.physical_collection
                    == receipt.physical_collection,
                    knowledge_projection_lineage.c.operation_id == receipt.operation_id,
                    knowledge_projection_lineage.c.predecessor_collection
                    == receipt.predecessor_collection,
                    knowledge_projection_lineage.c.status == receipt.status,
                )
            )
            if result.rowcount != 1:
                raise IndexUnavailable("Athena projection cleanup ownership changed")
            await self._connection.commit()

        await _settled_database(operation(), "complete owned projection cleanup")


def _ownership_receipt(row: Sequence[object]) -> ProjectionOwnershipReceipt:
    if len(row) != 4:
        raise IndexUnavailable("Athena projection ownership row is malformed")
    physical, operation_id, predecessor, status = row
    receipt = ProjectionOwnershipReceipt(
        physical_collection=_bounded_text(physical, "physical collection", 255),
        operation_id=_bounded_text(operation_id, "operation id", 64),
        predecessor_collection=_bounded_text(predecessor, "predecessor collection", 255),
        status=status,  # type: ignore[arg-type]
    )
    _validate_receipt(receipt, expected_status=receipt.status)
    return receipt


def _validate_receipt(
    receipt: ProjectionOwnershipReceipt,
    *,
    expected_status: str,
) -> None:
    if not isinstance(receipt, ProjectionOwnershipReceipt):
        raise ValueError("projection ownership receipt is malformed")
    _bounded_text(receipt.physical_collection, "physical collection", 255)
    _bounded_text(receipt.predecessor_collection, "predecessor collection", 255)
    operation_id = _bounded_text(receipt.operation_id, "operation id", 64)
    if len(operation_id) != 32 or any(
        character not in "0123456789abcdef" for character in operation_id
    ):
        raise ValueError("projection operation id must be 32 lowercase hex characters")
    if receipt.status not in {"building", "active", "cleanup"}:
        raise IndexUnavailable("Athena projection ownership status is malformed")
    if receipt.status != expected_status:
        raise IndexUnavailable("Athena projection ownership status changed")


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


async def _acquire_database_connection(engine: AsyncEngine) -> AsyncConnection:
    task = asyncio.ensure_future(engine.connect())
    try:
        async with asyncio.timeout(_DATABASE_DEADLINE_SECONDS):
            return await asyncio.shield(task)
    except asyncio.CancelledError as cancellation:
        outcome = await await_task_terminal(task, initial_cancellations=(cancellation,))
        if isinstance(outcome.value, AsyncConnection):
            await _close_connection_terminal(outcome.value)
        raise outcome.cancellations[0]
    except TimeoutError as error:
        task.cancel()
        outcome = await await_task_terminal(task)
        if isinstance(outcome.value, AsyncConnection):
            await _close_connection_terminal(outcome.value)
        raise IndexUnavailable("Athena projection database connect timed out") from error
    except DBAPIError as error:
        raise IndexUnavailable("Athena projection database connect failed") from error


async def _close_connection_terminal(connection: AsyncConnection) -> None:
    close = asyncio.ensure_future(connection.close())
    outcome = await await_task_terminal(close)
    if outcome.error is None:
        return
    await _discard_connection_terminal(connection)
    if isinstance(outcome.error, asyncio.CancelledError):
        raise outcome.error
    if isinstance(outcome.error, DBAPIError):
        raise IndexUnavailable(
            "Athena projection database connection close failed"
        ) from outcome.error
    raise outcome.error


async def _discard_connection_terminal(connection: AsyncConnection) -> None:
    invalidate = asyncio.ensure_future(connection.invalidate())
    await await_task_terminal(invalidate)
    close = asyncio.ensure_future(connection.close())
    await await_task_terminal(close)


def _bounded_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError(f"projection {field} must be a bounded non-empty string")
    return value
