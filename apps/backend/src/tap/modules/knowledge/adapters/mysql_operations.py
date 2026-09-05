"""MySQL fenced operation receipts and one atomic completion/Audit/Outbox fact."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    String,
    Table,
    UniqueConstraint,
    select,
    text,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON, insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from tap.contracts.events import ProjectEventEnvelope
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.governance.adapters.mysql_audit import MysqlProjectAudit
from tap.modules.governance.domain.audit import (
    AuditAction,
    AuditOutcome,
    AuditResource,
    SafeAuditMetadata,
)
from tap.modules.knowledge.domain.operations import (
    OperationBusy,
    OperationClaim,
    OperationLeaseLost,
    OperationRequest,
    OperationResult,
)
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import metadata
from tap.platform.messaging.mysql_outbox import write_project_event

knowledge_operator_operation = Table(
    "knowledge_operator_operation",
    metadata,
    Column("operation_id", String(64, collation="utf8mb4_bin"), primary_key=True),
    Column("enterprise_id", String(128), nullable=False),
    Column("project_id", String(128), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("identity_mode", String(16), nullable=False),
    Column("identity_origin", String(16), nullable=False),
    Column("idempotency_key", String(128, collation="utf8mb4_bin"), nullable=False),
    Column("command", String(32), nullable=False),
    Column("parameters", JSON, nullable=False),
    Column("parameters_digest", String(64), nullable=False),
    Column("correlation_id", String(128), nullable=False),
    Column("fence", BigInteger, nullable=False),
    Column("claim_token", String(64), nullable=False),
    Column("lease_until", DATETIME(fsp=6), nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("completed_at", DATETIME(fsp=6)),
    Column("result", JSON),
    UniqueConstraint(
        "enterprise_id", "project_id", "idempotency_key", name="uq_knowledge_operation_replay"
    ),
    UniqueConstraint("project_id", "operation_id", name="uq_knowledge_operation_project_pk"),
    ForeignKeyConstraint(
        ["enterprise_id", "project_id"],
        ["project.enterprise_id", "project.project_id"],
        name="fk_knowledge_operation_scope_project",
    ),
    ForeignKeyConstraint(
        ["enterprise_id", "actor_id"],
        ["actor_principal.enterprise_id", "actor_principal.actor_id"],
        name="fk_knowledge_operation_scope_actor",
    ),
    CheckConstraint("fence > 0", name="ck_knowledge_operation_fence"),
    CheckConstraint("json_type(parameters) = 'OBJECT'", name="ck_knowledge_operation_parameters"),
    CheckConstraint(
        "((completed_at is null) and (result is null)) or "
        "((completed_at is not null) and (result is not null) and (json_type(result) = 'OBJECT'))",
        name="ck_knowledge_operation_completion",
    ),
)
Index(
    "ix_knowledge_operation_lease",
    knowledge_operator_operation.c.enterprise_id,
    knowledge_operator_operation.c.project_id,
    knowledge_operator_operation.c.lease_until,
)


async def _now(connection: AsyncConnection) -> datetime:
    return (await connection.execute(text("SELECT UTC_TIMESTAMP(6)"))).scalar_one()


def _duration(value: timedelta) -> None:
    if not isinstance(value, timedelta) or not 3 <= value.total_seconds() <= 300:
        raise ValueError("operator lease must be between 3 and 300 seconds")


class MysqlOperationRepository:
    def __init__(self, engine: AsyncEngine, *, scope: ProjectScopeContext) -> None:
        self._engine = engine
        self._scope = require_project_scope(scope)

    @property
    def scope(self) -> ProjectScopeContext:
        return self._scope

    def _claim(self, row: Any) -> OperationClaim:
        result = row["result"]
        return OperationClaim(
            scope=self._scope,
            operation_id=row["operation_id"],
            command=row["command"],
            limit=row["parameters"]["limit"],
            correlation_id=row["correlation_id"],
            fence=row["fence"],
            claim_token=row["claim_token"],
            result=OperationResult(**result) if result is not None else None,
        )

    async def claim(
        self, request: OperationRequest, *, lease_duration: timedelta
    ) -> OperationClaim:
        if type(request) is not OperationRequest:
            raise TypeError("operator requires a validated request")
        _duration(lease_duration)
        operation_id, token = str(uuid4()), str(uuid4())
        digest = request.digest(self._scope)
        async with self._engine.begin() as connection:
            now = await _now(connection)
            await connection.execute(
                insert(knowledge_operator_operation)
                .values(
                    **scope_values(self._scope),
                    operation_id=operation_id,
                    idempotency_key=request.idempotency_key,
                    command=request.command,
                    parameters={"limit": request.limit},
                    parameters_digest=digest,
                    correlation_id=request.correlation_id,
                    fence=1,
                    claim_token=token,
                    lease_until=now + lease_duration,
                    created_at=now,
                )
                .on_duplicate_key_update(operation_id=knowledge_operator_operation.c.operation_id)
            )
            row = (
                (
                    await connection.execute(
                        select(knowledge_operator_operation)
                        .where(
                            *scope_predicates(knowledge_operator_operation, self._scope),
                            knowledge_operator_operation.c.idempotency_key
                            == request.idempotency_key,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if row["parameters_digest"] != digest or any(
                row[key] != value for key, value in scope_values(self._scope).items()
            ):
                raise ValueError("idempotency-conflict")
            if row["operation_id"] == operation_id or row["result"] is not None:
                return self._claim(row)
            now = await _now(connection)
            if row["lease_until"] > now:
                raise OperationBusy("operation-in-progress")
            await connection.execute(
                update(knowledge_operator_operation)
                .where(
                    *scope_predicates(knowledge_operator_operation, self._scope),
                    knowledge_operator_operation.c.operation_id == row["operation_id"],
                )
                .values(claim_token=token, fence=row["fence"] + 1, lease_until=now + lease_duration)
            )
            return replace(self._claim(row), claim_token=token, fence=row["fence"] + 1)

    async def _locked_claim(self, connection: AsyncConnection, claim: OperationClaim) -> Any:
        if not connection.in_transaction():
            raise ValueError("operation requires an active transaction")
        if (
            type(claim) is not OperationClaim
            or claim.scope != self._scope
            or claim.result is not None
        ):
            raise OperationLeaseLost("operation-lease-lost")
        row = (
            (
                await connection.execute(
                    select(knowledge_operator_operation)
                    .where(
                        *scope_predicates(knowledge_operator_operation, self._scope),
                        knowledge_operator_operation.c.operation_id == claim.operation_id,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or self._claim(row) != claim or row["lease_until"] <= await _now(connection):
            raise OperationLeaseLost("operation-lease-lost")
        return row

    async def renew(self, claim: OperationClaim, *, lease_duration: timedelta) -> None:
        _duration(lease_duration)
        async with self._engine.begin() as connection:
            await self._locked_claim(connection, claim)
            await connection.execute(
                update(knowledge_operator_operation)
                .where(
                    *scope_predicates(knowledge_operator_operation, self._scope),
                    knowledge_operator_operation.c.operation_id == claim.operation_id,
                )
                .values(lease_until=await _now(connection) + lease_duration)
            )

    async def complete(self, claim: OperationClaim, result: OperationResult) -> OperationClaim:
        async with self._engine.begin() as connection:
            return await self.complete_in_transaction(connection, claim, result)

    async def complete_in_transaction(
        self, connection: AsyncConnection, claim: OperationClaim, result: OperationResult
    ) -> OperationClaim:
        if type(result) is not OperationResult:
            raise TypeError("operator requires a validated result")
        await self._locked_claim(connection, claim)
        now = await _now(connection)
        await connection.execute(
            update(knowledge_operator_operation)
            .where(
                *scope_predicates(knowledge_operator_operation, self._scope),
                knowledge_operator_operation.c.operation_id == claim.operation_id,
            )
            .values(result=result.to_dict(), completed_at=now)
        )
        key = "operator:" + claim.operation_id
        await MysqlProjectAudit(connection, scope=self._scope).append(
            self._scope,
            AuditAction(claim.command),
            AuditResource.PROJECT_MAINTENANCE,
            AuditOutcome(result.outcome),
            SafeAuditMetadata(
                {**result.counts, "mode": "apply", "content_digest": result.digest[7:]}
            ),
            correlation_id=claim.correlation_id,
            idempotency_key=key,
        )
        await write_project_event(
            connection,
            scope=self._scope,
            envelope=ProjectEventEnvelope(
                event_id=str(uuid4()),
                event_type="knowledge.operator.completed",
                schema_version=1,
                occurred_at=now.replace(tzinfo=timezone.utc),
                scope_kind="PROJECT",
                enterprise_id=self._scope.enterprise_id,
                project_id=self._scope.project_id,
                actor_id=self._scope.actor_id,
                identity_mode=self._scope.identity_mode.value,
                aggregate_type="KnowledgeOperation",
                aggregate_id=claim.operation_id,
                aggregate_version=1,
                correlation_id=claim.correlation_id,
                causation_id=None,
                idempotency_key=key,
                payload={
                    "operationId": claim.operation_id,
                    "command": claim.command,
                    "outcome": result.outcome,
                    "resultDigest": result.digest,
                },
            ),
        )
        return replace(claim, result=result)

    async def staging_pins(self) -> frozenset[str]:
        from tap.modules.knowledge.adapters.mysql_documents import knowledge_document

        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        select(knowledge_document.c.staging_blob_locator)
                        .where(
                            *scope_predicates(knowledge_document, self._scope),
                            knowledge_document.c.staging_blob_locator.is_not(None),
                        )
                        .limit(1001)
                    )
                )
                .scalars()
                .all()
            )
            if len(rows) > 1000:
                raise ValueError("staging pin snapshot exceeds bound")
            return frozenset(rows)

    async def ready_work(self, limit: int) -> tuple[Any, ...]:
        """Open a fresh snapshot only inside the caller's physical alias lock.

        An indexed-but-not-ready publishing transition makes rebuilding unsafe;
        a truncated ready corpus must never replace the active alias either.
        """
        from tap.modules.knowledge.adapters.mysql_documents import (
            _manifest_from_rows,
            knowledge_chunk_manifest,
            knowledge_document,
            knowledge_document_revision,
            knowledge_ingestion_job,
        )
        from tap.modules.knowledge.ports.documents import (
            ArtifactLocator,
            IngestionWork,
            JobKind,
            JobStage,
        )

        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("operator limit must be between 1 and 500")
        async with self._engine.begin() as connection:
            publishing = (
                await connection.execute(
                    select(knowledge_ingestion_job.c.job_id)
                    .where(
                        *scope_predicates(knowledge_ingestion_job, self._scope),
                        knowledge_ingestion_job.c.stage == "publishing",
                        knowledge_ingestion_job.c.status != "completed",
                        knowledge_ingestion_job.c.kind == "ingestion",
                    )
                    .limit(1)
                )
            ).first()
            if publishing is not None:
                raise OperationBusy("publication-in-progress")
            rows = (
                (
                    await connection.execute(
                        select(
                            knowledge_document.c.filename,
                            knowledge_document.c.media_type,
                            knowledge_document_revision,
                        )
                        .select_from(
                            knowledge_document.join(
                                knowledge_document_revision,
                                knowledge_document.c.current_revision_id
                                == knowledge_document_revision.c.revision_id,
                            )
                        )
                        .where(
                            *scope_predicates(knowledge_document, self._scope),
                            *scope_predicates(knowledge_document_revision, self._scope),
                            knowledge_document.c.status == "ready",
                            knowledge_document.c.deleted_at.is_(None),
                            knowledge_document.c.activated_at.is_not(None),
                        )
                        .order_by(knowledge_document.c.document_id)
                        .limit(limit + 1)
                    )
                )
                .mappings()
                .all()
            )
            if len(rows) > limit:
                raise ValueError("ready corpus exceeds rebuild bound")
            result = []
            for row in rows:
                manifests = (
                    (
                        await connection.execute(
                            select(knowledge_chunk_manifest)
                            .where(
                                *scope_predicates(knowledge_chunk_manifest, self._scope),
                                knowledge_chunk_manifest.c.revision_id == row["revision_id"],
                            )
                            .order_by(knowledge_chunk_manifest.c.ordinal)
                            .limit(1001)
                        )
                    )
                    .mappings()
                    .all()
                )
                if (
                    not manifests
                    or len(manifests) > 1000
                    or any(
                        row[key] is None
                        for key in ("chunks_blob_locator", "embeddings_blob_locator")
                    )
                ):
                    raise ValueError("ready artifact snapshot is incomplete or exceeds bound")
                result.append(
                    IngestionWork(
                        job_id="operator-rebuild",
                        lease_token="snapshot",
                        kind=JobKind.INGESTION,
                        stage=JobStage.READY,
                        document_id=row["document_id"],
                        revision_id=row["revision_id"],
                        filename=row["filename"],
                        media_type=row["media_type"],
                        source_content_hash=row["source_content_hash"],
                        original_locator=ArtifactLocator(row["original_blob_locator"]),
                        normalized_locator=ArtifactLocator(row["normalized_blob_locator"])
                        if row["normalized_blob_locator"]
                        else None,
                        chunks_locator=ArtifactLocator(row["chunks_blob_locator"]),
                        embeddings_locator=ArtifactLocator(row["embeddings_blob_locator"]),
                        parser_version=row["parser_version"],
                        chunker_version=row["chunker_version"],
                        pipeline_version=row["pipeline_version"],
                        manifest=_manifest_from_rows(list(manifests)),
                    )
                )
            return tuple(result)
