"""Project-scoped asynchronous LangGraph checkpointer backed by MySQL."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import (
    Column,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    select,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON, LONGBLOB, insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.contracts.events import ProjectEventEnvelope
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.graph_runs import GraphCheckpointUnavailable
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import metadata
from tap.platform.messaging.mysql_outbox import scoped_outbox_id, write_project_event


def _scope_constraints(name: str):
    return (
        ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name=f"fk_{name}_scope_project",
        ),
        ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name=f"fk_{name}_scope_actor",
        ),
    )


def _scoped(name: str, *columns, uniques=(), parents=()):
    return Table(
        name,
        metadata,
        *columns,
        Column("enterprise_id", String(128), nullable=False),
        Column("project_id", String(128), primary_key=True),
        Column("actor_id", String(128), nullable=False),
        Column("identity_mode", String(16), nullable=False),
        Column("identity_origin", String(16), nullable=False),
        *(
            UniqueConstraint("project_id", *fields, name=constraint)
            for fields, constraint in uniques
        ),
        *(
            ForeignKeyConstraint(
                ["project_id", *source],
                [f"{target}.project_id", *[f"{target}.{item}" for item in destination]],
                name=constraint,
            )
            for source, target, destination, constraint in parents
        ),
        *_scope_constraints(name),
    )


graph_run = _scoped(
    "ai_graph_run",
    Column("run_id", String(128), primary_key=True),
    Column("graph_version", String(128), nullable=False),
    Column("state_schema_version", Integer, nullable=False),
    Column("execution_mode", String(16), nullable=False),
    Column("reasoning_mode", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("waiting_reason", Text),
    Column("current_checkpoint_id", String(64)),
    Column("lease_owner", String(128)),
    Column("lease_token", String(64)),
    Column("lease_until", DATETIME(fsp=6)),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("budget", JSON, nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
    uniques=((("run_id",), "uq_ai_graph_run_project_pk"),),
)
graph_checkpoint = _scoped(
    "ai_graph_checkpoint",
    Column("run_id", String(128), primary_key=True),
    Column("checkpoint_ns", String(255), primary_key=True),
    Column("checkpoint_id", String(64), primary_key=True),
    Column("parent_checkpoint_id", String(64)),
    Column("checkpoint_type", String(64), nullable=False),
    Column("checkpoint_blob", LONGBLOB, nullable=False),
    Column("metadata_type", String(64), nullable=False),
    Column("metadata_blob", LONGBLOB, nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    uniques=((("run_id", "checkpoint_ns", "checkpoint_id"), "uq_ai_graph_checkpoint_pk"),),
    parents=((("run_id",), "ai_graph_run", ("run_id",), "fk_ai_graph_checkpoint_run"),),
)
graph_checkpoint_write = _scoped(
    "ai_graph_checkpoint_write",
    Column("run_id", String(128), primary_key=True),
    Column("checkpoint_ns", String(255), primary_key=True),
    Column("checkpoint_id", String(64), primary_key=True),
    Column("task_id", String(128), primary_key=True),
    Column("write_index", Integer, primary_key=True),
    Column("channel", String(255), nullable=False),
    Column("value_type", String(64), nullable=False),
    Column("value_blob", LONGBLOB, nullable=False),
    Column("task_path", String(512), nullable=False),
    uniques=(
        (
            ("run_id", "checkpoint_ns", "checkpoint_id", "task_id", "write_index"),
            "uq_ai_graph_checkpoint_write_pk",
        ),
    ),
    parents=(
        (
            ("run_id", "checkpoint_ns", "checkpoint_id"),
            "ai_graph_checkpoint",
            ("run_id", "checkpoint_ns", "checkpoint_id"),
            "fk_ai_graph_checkpoint_write_checkpoint",
        ),
    ),
)
graph_settlement = _scoped(
    "ai_graph_settlement",
    Column("run_id", String(128), primary_key=True),
    Column("checkpoint_id", String(64), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    uniques=((("run_id",), "uq_ai_graph_settlement_project_pk"),),
    parents=((("run_id",), "ai_graph_run", ("run_id",), "fk_ai_graph_settlement_run"),),
)

AI_GRAPH_TABLES = (graph_run, graph_checkpoint, graph_checkpoint_write, graph_settlement)


@asynccontextmanager
async def _checkpoint_storage():
    try:
        yield
    except SQLAlchemyError as error:
        raise GraphCheckpointUnavailable("graph checkpoint storage is unavailable") from error


@dataclass(frozen=True, slots=True)
class GraphFence:
    table: Any
    identity_column: Any
    identity: str
    token_column: Any
    token: str
    lease_until_column: Any
    status_column: Any
    running_status: str
    lease_owner: str
    attempt_count_column: Any | None = None


class MysqlGraphCheckpointer(BaseCheckpointSaver[str]):
    """Async-only saver retaining checkpoints as immutable serialized snapshots."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        scope: ProjectScopeContext,
        fence: GraphFence | None = None,
        defer_completion: bool = False,
        budget: dict[str, int] | None = None,
    ) -> None:
        super().__init__(serde=JsonPlusSerializer(allowed_msgpack_modules=None))
        self._sessions = sessions
        self._scope = require_project_scope(scope)
        self._fence = fence
        self._defer_completion = defer_completion
        self._budget = dict(budget or {})

    async def _assert_fence(
        self, session: AsyncSession, *, lock: bool = False
    ) -> tuple[datetime, int] | None:
        if self._fence is None:
            return None
        fence = self._fence
        selected = [fence.lease_until_column]
        if fence.attempt_count_column is not None:
            selected.append(fence.attempt_count_column)
        query = select(*selected).where(
            *scope_predicates(fence.table, self._scope),
            fence.identity_column == fence.identity,
            fence.token_column == fence.token,
            fence.status_column == fence.running_status,
            fence.lease_until_column >= datetime.now(timezone.utc).replace(tzinfo=None),
        )
        if lock:
            query = query.with_for_update()
        valid = (await session.execute(query)).one_or_none()
        if valid is None:
            raise PermissionError("graph run fencing token is stale")
        return valid[0], int(valid[1]) if len(valid) > 1 else 1

    @staticmethod
    def _coordinates(config: RunnableConfig) -> tuple[str, str, str | None]:
        configurable = config["configurable"]
        return (
            str(configurable["thread_id"]),
            str(configurable.get("checkpoint_ns", "")),
            get_checkpoint_id(config),
        )

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        run_id, namespace, checkpoint_id = self._coordinates(config)
        async with _checkpoint_storage(), self._sessions() as session:
            await self._assert_fence(session)
            query = select(graph_checkpoint).where(
                *scope_predicates(graph_checkpoint, self._scope),
                graph_checkpoint.c.run_id == run_id,
                graph_checkpoint.c.checkpoint_ns == namespace,
            )
            if checkpoint_id is not None:
                query = query.where(graph_checkpoint.c.checkpoint_id == checkpoint_id)
            else:
                query = query.order_by(graph_checkpoint.c.checkpoint_id.desc()).limit(1)
            row = (await session.execute(query)).mappings().one_or_none()
            if row is None:
                return None
            writes = (
                (
                    await session.execute(
                        select(graph_checkpoint_write)
                        .where(
                            *scope_predicates(graph_checkpoint_write, self._scope),
                            graph_checkpoint_write.c.run_id == run_id,
                            graph_checkpoint_write.c.checkpoint_ns == namespace,
                            graph_checkpoint_write.c.checkpoint_id == row["checkpoint_id"],
                        )
                        .order_by(
                            graph_checkpoint_write.c.task_id, graph_checkpoint_write.c.write_index
                        )
                    )
                )
                .mappings()
                .all()
            )
        saved_config: RunnableConfig = {
            "configurable": {
                "thread_id": run_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": row["checkpoint_id"],
            }
        }
        parent_config: RunnableConfig | None = None
        if row["parent_checkpoint_id"]:
            parent_config = RunnableConfig(
                configurable={
                    "thread_id": run_id,
                    "checkpoint_ns": namespace,
                    "checkpoint_id": row["parent_checkpoint_id"],
                }
            )
        return CheckpointTuple(
            config=saved_config,
            checkpoint=self.serde.loads_typed((row["checkpoint_type"], row["checkpoint_blob"])),
            metadata=self.serde.loads_typed((row["metadata_type"], row["metadata_blob"])),
            parent_config=parent_config,
            pending_writes=[
                (
                    item["task_id"],
                    item["channel"],
                    self.serde.loads_typed((item["value_type"], item["value_blob"])),
                )
                for item in writes
            ],
        )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if filter:
            raise ValueError("metadata filtering is not supported")
        if config is None:
            raise ValueError("project graph checkpoint listing requires a run")
        run_id, namespace, _ = self._coordinates(config)
        before_id = get_checkpoint_id(before) if before else None
        async with _checkpoint_storage(), self._sessions() as session:
            await self._assert_fence(session)
            query = (
                select(graph_checkpoint.c.checkpoint_id)
                .where(
                    *scope_predicates(graph_checkpoint, self._scope),
                    graph_checkpoint.c.run_id == run_id,
                    graph_checkpoint.c.checkpoint_ns == namespace,
                )
                .order_by(graph_checkpoint.c.checkpoint_id.desc())
            )
            if before_id:
                query = query.where(graph_checkpoint.c.checkpoint_id < before_id)
            if limit is not None:
                query = query.limit(limit)
            identities = tuple((await session.scalars(query)).all())
        for identity in identities:
            item = await self.aget_tuple(
                {
                    "configurable": {
                        "thread_id": run_id,
                        "checkpoint_ns": namespace,
                        "checkpoint_id": identity,
                    }
                }
            )
            assert item is not None
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata_value: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        del new_versions
        run_id, namespace, parent_id = self._coordinates(config)
        configurable = config["configurable"]
        values = checkpoint.get("channel_values", {})
        graph_version = str(values.get("graph_version", configurable.get("graph_version", "")))
        schema_version = int(
            values.get("state_schema_version", configurable.get("state_schema_version", 0))
        )
        if not graph_version or schema_version < 1:
            raise ValueError("durable graph checkpoint lacks version identity")
        execution_mode = str(
            values.get("execution_mode", configurable.get("execution_mode", "durable"))
        )
        reasoning_mode = str(values.get("reasoning_mode", "direct"))
        waiting_reason = values.get("waiting_reason")
        status = (
            "RUNNING"
            if values.get("result") and self._defer_completion
            else "SUCCEEDED"
            if values.get("result")
            else "WAITING"
            if waiting_reason
            else "RUNNING"
        )
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint)
        persisted_metadata = get_checkpoint_metadata(config, metadata_value)
        metadata_type, metadata_blob = self.serde.dumps_typed(persisted_metadata)
        created_at = datetime.fromisoformat(checkpoint["ts"]).astimezone(timezone.utc)
        instant = created_at.replace(tzinfo=None)
        identity = scope_values(self._scope)
        async with _checkpoint_storage(), self._sessions() as session, session.begin():
            fence_state = await self._assert_fence(session, lock=True)
            lease_values = (
                {}
                if self._fence is None or fence_state is None
                else {
                    "lease_owner": None if status == "WAITING" else self._fence.lease_owner,
                    "lease_token": None if status == "WAITING" else self._fence.token,
                    "lease_until": None if status == "WAITING" else fence_state[0],
                }
            )
            attempt_count = 0 if fence_state is None else fence_state[1]
            await session.execute(
                insert(graph_run)
                .values(
                    **identity,
                    run_id=run_id,
                    graph_version=graph_version,
                    state_schema_version=schema_version,
                    execution_mode=execution_mode,
                    reasoning_mode=reasoning_mode,
                    status=status,
                    waiting_reason=waiting_reason,
                    current_checkpoint_id=checkpoint["id"],
                    attempt_count=attempt_count,
                    budget=self._budget,
                    created_at=instant,
                    updated_at=instant,
                    **lease_values,
                )
                .on_duplicate_key_update(
                    current_checkpoint_id=checkpoint["id"],
                    reasoning_mode=reasoning_mode,
                    status=status,
                    waiting_reason=waiting_reason,
                    attempt_count=attempt_count,
                    updated_at=instant,
                    **lease_values,
                )
            )
            await session.execute(
                insert(graph_checkpoint)
                .values(
                    **identity,
                    run_id=run_id,
                    checkpoint_ns=namespace,
                    checkpoint_id=checkpoint["id"],
                    parent_checkpoint_id=parent_id,
                    checkpoint_type=checkpoint_type,
                    checkpoint_blob=checkpoint_blob,
                    metadata_type=metadata_type,
                    metadata_blob=metadata_blob,
                    created_at=instant,
                )
                .on_duplicate_key_update(checkpoint_id=graph_checkpoint.c.checkpoint_id)
            )
            if not (self._defer_completion and values.get("result")):
                await write_project_event(
                    session,
                    scope=self._scope,
                    envelope=ProjectEventEnvelope(
                        event_id=scoped_outbox_id(
                            self._scope,
                            kind="ai-graph-checkpoint",
                            identity=f"{run_id}:{checkpoint['id']}",
                        ),
                        event_type="ai.graph-run.checkpointed",
                        schema_version=1,
                        occurred_at=created_at,
                        scope_kind="PROJECT",
                        enterprise_id=self._scope.enterprise_id,
                        project_id=self._scope.project_id,
                        actor_id=self._scope.actor_id,
                        identity_mode=self._scope.identity_mode.value,
                        aggregate_type="GraphRun",
                        aggregate_id=run_id,
                        aggregate_version=max(1, int(persisted_metadata.get("step", -1)) + 2),
                        correlation_id=run_id,
                        causation_id=parent_id,
                        idempotency_key=f"graph-checkpoint:{checkpoint['id']}",
                        payload={
                            "runId": run_id,
                            "checkpointId": checkpoint["id"],
                            "graphVersion": graph_version,
                            "stateSchemaVersion": str(schema_version),
                        },
                    ),
                )
        return {
            "configurable": {
                **configurable,
                "thread_id": run_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        run_id, namespace, checkpoint_id = self._coordinates(config)
        if checkpoint_id is None:
            raise ValueError("checkpoint writes require a checkpoint identity")
        identity = scope_values(self._scope)
        async with _checkpoint_storage(), self._sessions() as session, session.begin():
            await self._assert_fence(session, lock=True)
            for index, (channel, value) in enumerate(writes):
                write_index = WRITES_IDX_MAP.get(channel, index)
                value_type, value_blob = self.serde.dumps_typed(value)
                statement = insert(graph_checkpoint_write).values(
                    **identity,
                    run_id=run_id,
                    checkpoint_ns=namespace,
                    checkpoint_id=checkpoint_id,
                    task_id=task_id,
                    write_index=write_index,
                    channel=channel,
                    value_type=value_type,
                    value_blob=value_blob,
                    task_path=task_path,
                )
                if write_index >= 0:
                    statement = statement.on_duplicate_key_update(
                        write_index=graph_checkpoint_write.c.write_index
                    )
                else:
                    statement = statement.on_duplicate_key_update(
                        channel=channel,
                        value_type=value_type,
                        value_blob=value_blob,
                        task_path=task_path,
                    )
                await session.execute(statement)

    async def adelete_thread(self, thread_id: str) -> None:
        async with _checkpoint_storage(), self._sessions() as session, session.begin():
            await self._assert_fence(session, lock=True)
            run = await session.scalar(
                select(graph_run.c.run_id).where(
                    *scope_predicates(graph_run, self._scope), graph_run.c.run_id == thread_id
                )
            )
            if run is None:
                return
            await session.execute(
                delete(graph_checkpoint_write).where(
                    *scope_predicates(graph_checkpoint_write, self._scope),
                    graph_checkpoint_write.c.run_id == thread_id,
                )
            )
            await session.execute(
                delete(graph_checkpoint).where(
                    *scope_predicates(graph_checkpoint, self._scope),
                    graph_checkpoint.c.run_id == thread_id,
                )
            )
            await session.execute(
                update(graph_run)
                .where(*scope_predicates(graph_run, self._scope), graph_run.c.run_id == thread_id)
                .values(status="CANCELLED", current_checkpoint_id=None)
            )
