"""MySQL graph extraction job ledger with lease fencing and atomic events."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Literal, Mapping, cast
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.contracts.events import ProjectEventEnvelope
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.governance.adapters.mysql_audit import MysqlProjectAudit
from tap.modules.governance.domain.audit import (
    AuditAction,
    AuditOutcome,
    AuditResource,
    SafeAuditMetadata,
)
from tap.modules.graph.adapters.model_gateway_extraction import GRAPH_EXTRACTION_PROFILE_DIGEST
from tap.modules.graph.adapters.mysql import (
    _draft_digest,
    graph_extraction_job,
    graph_snapshot,
    publish_graph_snapshot,
)
from tap.modules.graph.domain.jobs import (
    ClaimedGraphJob,
    GraphJob,
    GraphJobLeaseLost,
    GraphJobRequest,
    GraphJobStatus,
)
from tap.modules.graph.domain.models import GraphSnapshot, GraphSnapshotDraft
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.messaging.mysql_outbox import scoped_outbox_id, write_project_event


class MysqlGraphJobStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def request(
        self, scope: ProjectScopeContext, request: GraphJobRequest, *, now: datetime
    ) -> GraphJob:
        async with self._sessions() as session, session.begin():
            return await self.request_in_transaction(session, scope, request, now=now)

    async def request_in_transaction(
        self,
        session: AsyncSession,
        scope: ProjectScopeContext,
        request: GraphJobRequest,
        *,
        now: datetime,
    ) -> GraphJob:
        scope = require_project_scope(scope)
        if not session.in_transaction():
            raise ValueError("graph job request requires an active transaction")
        if request.snapshot.project_id != scope.project_id:
            raise ValueError("graph job request is outside Project scope")
        common = scope_values(scope)
        snapshot_insert = insert(graph_snapshot).values(
            **common,
            snapshot_id=request.snapshot.snapshot_id,
            source_set_digest=request.snapshot.source_set_digest,
            source_revision_ids=list(request.snapshot.source_revision_ids),
            document_revision_ids=list(request.snapshot.document_revision_ids),
            status="CANDIDATE",
            created_at=now,
        )
        await session.execute(
            snapshot_insert.on_duplicate_key_update(snapshot_id=graph_snapshot.c.snapshot_id)
        )
        statement = insert(graph_extraction_job).values(
            **common,
            job_id=request.job_id,
            snapshot_id=request.snapshot.snapshot_id,
            revision_id=request.revision_id,
            chunks_locator=request.chunks_locator,
            extraction_profile_digest=request.extraction_profile_digest,
            model_alias=request.model_alias,
            request_digest=request.request_digest,
            status=GraphJobStatus.PENDING.value,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            attempt_count=0,
            failure_code=None,
            created_at=now,
            updated_at=now,
        )
        await session.execute(
            statement.on_duplicate_key_update(job_id=graph_extraction_job.c.job_id)
        )
        row = await self._locked_row(session, scope, request.job_id)
        job = await self._job(session, scope, row)
        if (
            job.request_digest != request.request_digest
            or job.snapshot != request.snapshot
            or job.chunks_locator != request.chunks_locator
            or job.model_alias != request.model_alias
        ):
            raise ValueError("immutable graph job request conflict")
        await self._audit_event(
            session,
            scope,
            job,
            action=AuditAction.GRAPH_SNAPSHOT_REQUESTED,
            event_type="knowledge.graph-snapshot.requested",
            aggregate_version=1,
            payload={
                "snapshotId": job.snapshot.snapshot_id,
                "sourceRevisionIds": job.snapshot.source_revision_ids,
                "extractionProfileDigest": job.extraction_profile_digest,
            },
            digest=job.request_digest,
            now=job.created_at,
        )
        return job

    async def claim(
        self,
        scope: ProjectScopeContext,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[ClaimedGraphJob, ...]:
        scope = require_project_scope(scope)
        if not worker_id or not timedelta(0) < lease_duration <= timedelta(minutes=15):
            raise ValueError("graph worker lease is invalid")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("graph job claim limit must be between 1 and 50")
        claims: list[ClaimedGraphJob] = []
        async with self._sessions() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        select(graph_extraction_job)
                        .where(
                            *scope_predicates(graph_extraction_job, scope),
                            or_(
                                graph_extraction_job.c.status == GraphJobStatus.PENDING.value,
                                (graph_extraction_job.c.status == GraphJobStatus.RUNNING.value)
                                & (graph_extraction_job.c.lease_expires_at <= now),
                            ),
                        )
                        .order_by(graph_extraction_job.c.created_at)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                token = uuid4().hex
                expires = now + lease_duration
                await session.execute(
                    update(graph_extraction_job)
                    .where(
                        *scope_predicates(graph_extraction_job, scope),
                        graph_extraction_job.c.job_id == row["job_id"],
                    )
                    .values(
                        status=GraphJobStatus.RUNNING.value,
                        lease_owner=worker_id,
                        lease_token=token,
                        lease_expires_at=expires,
                        attempt_count=graph_extraction_job.c.attempt_count + 1,
                        updated_at=now,
                    )
                )
                claimed_row = dict(row)
                claimed_row.update(
                    status=GraphJobStatus.RUNNING.value,
                    lease_owner=worker_id,
                    lease_token=token,
                    lease_expires_at=expires,
                    updated_at=now,
                )
                job = await self._job(session, scope, claimed_row)
                claims.append(
                    ClaimedGraphJob(
                        job_id=job.job_id,
                        revision_id=job.revision_id,
                        snapshot=job.snapshot,
                        chunks_locator=job.chunks_locator,
                        extraction_profile_digest=job.extraction_profile_digest,
                        request_digest=job.request_digest,
                        model_alias=job.model_alias,
                        status=job.status,
                        created_at=job.created_at,
                        updated_at=job.updated_at,
                        failure_code=job.failure_code,
                        lease_owner=worker_id,
                        lease_token=token,
                        lease_expires_at=expires,
                    )
                )
        return tuple(claims)

    async def complete(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedGraphJob,
        draft: GraphSnapshotDraft,
        *,
        now: datetime,
    ) -> GraphJob:
        scope = require_project_scope(scope)
        async with self._sessions() as session, session.begin():
            row = await self._owned_row(session, scope, claim, now)
            current = await self._job(session, scope, row)
            if draft.snapshot != current.snapshot:
                raise ValueError("graph draft does not match claimed snapshot")
            snapshot = await publish_graph_snapshot(session, scope, draft, now=now)
            result = await session.execute(
                update(graph_extraction_job)
                .where(
                    *scope_predicates(graph_extraction_job, scope),
                    graph_extraction_job.c.job_id == claim.job_id,
                    graph_extraction_job.c.status == GraphJobStatus.RUNNING.value,
                    graph_extraction_job.c.lease_token == claim.lease_token,
                    graph_extraction_job.c.lease_expires_at > now,
                )
                .values(
                    status=GraphJobStatus.READY.value,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    failure_code=None,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise GraphJobLeaseLost(claim.job_id)
            ready = replace(
                current,
                snapshot=snapshot,
                status=GraphJobStatus.READY,
                updated_at=now,
                failure_code=None,
            )
            graph_digest = _draft_digest(draft)
            evidence_digest = (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(
                        [item.evidence_id for item in draft.evidence], separators=(",", ":")
                    ).encode()
                ).hexdigest()
            )
            await self._audit_event(
                session,
                scope,
                ready,
                action=AuditAction.GRAPH_SNAPSHOT_READY,
                event_type="knowledge.graph-snapshot.ready",
                aggregate_version=2,
                payload={
                    "snapshotId": snapshot.snapshot_id,
                    "graphDigest": graph_digest,
                    "evidenceDigest": evidence_digest,
                },
                digest=graph_digest,
                now=now,
            )
            return ready

    async def fail(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedGraphJob,
        *,
        failure_code: str,
        now: datetime,
    ) -> GraphJob:
        scope = require_project_scope(scope)
        if not failure_code or len(failure_code) > 64:
            raise ValueError("graph failure code must be bounded")
        async with self._sessions() as session, session.begin():
            row = await self._owned_row(session, scope, claim, now)
            current = await self._job(session, scope, row)
            await session.execute(
                update(graph_snapshot)
                .where(
                    *scope_predicates(graph_snapshot, scope),
                    graph_snapshot.c.snapshot_id == claim.snapshot.snapshot_id,
                    graph_snapshot.c.status == "CANDIDATE",
                )
                .values(status="FAILED")
            )
            result = await session.execute(
                update(graph_extraction_job)
                .where(
                    *scope_predicates(graph_extraction_job, scope),
                    graph_extraction_job.c.job_id == claim.job_id,
                    graph_extraction_job.c.lease_token == claim.lease_token,
                )
                .values(
                    status=GraphJobStatus.FAILED.value,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    failure_code=failure_code,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise GraphJobLeaseLost(claim.job_id)
            failed = replace(
                current,
                snapshot=replace(current.snapshot, status="FAILED"),
                status=GraphJobStatus.FAILED,
                updated_at=now,
                failure_code=failure_code,
            )
            await self._append_audit(
                session,
                scope,
                failed,
                AuditAction.GRAPH_SNAPSHOT_FAILED,
                failed.request_digest,
                key=f"{failed.job_id}:failed",
            )
            return failed

    async def _owned_row(
        self,
        session: AsyncSession,
        scope: ProjectScopeContext,
        claim: ClaimedGraphJob,
        now: datetime,
    ) -> Mapping[str, object]:
        row = await self._locked_row(session, scope, claim.job_id)
        if (
            row["status"] != GraphJobStatus.RUNNING.value
            or row["lease_token"] != claim.lease_token
            or row["lease_expires_at"] is None
            or cast(datetime, row["lease_expires_at"]) <= now
        ):
            raise GraphJobLeaseLost(claim.job_id)
        return row

    @staticmethod
    async def _locked_row(
        session: AsyncSession, scope: ProjectScopeContext, job_id: str
    ) -> Mapping[str, object]:
        row = (
            (
                await session.execute(
                    select(graph_extraction_job)
                    .where(
                        *scope_predicates(graph_extraction_job, scope),
                        graph_extraction_job.c.job_id == job_id,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GraphJobLeaseLost(job_id)
        return cast(Mapping[str, object], row)

    @staticmethod
    async def _job(
        session: AsyncSession, scope: ProjectScopeContext, row: Mapping[str, object]
    ) -> GraphJob:
        snapshot_row = (
            (
                await session.execute(
                    select(graph_snapshot).where(
                        *scope_predicates(graph_snapshot, scope),
                        graph_snapshot.c.snapshot_id == row["snapshot_id"],
                    )
                )
            )
            .mappings()
            .one()
        )
        snapshot = GraphSnapshot(
            cast(str, snapshot_row["snapshot_id"]),
            cast(str, snapshot_row["project_id"]),
            tuple(snapshot_row["source_revision_ids"]),
            tuple(snapshot_row["document_revision_ids"]),
            cast(str, snapshot_row["source_set_digest"]),
            cast(Literal["CANDIDATE", "READY", "FAILED"], snapshot_row["status"]),
        )
        return GraphJob(
            cast(str, row["job_id"]),
            cast(str, row["revision_id"]),
            snapshot,
            cast(str, row["chunks_locator"]),
            cast(str, row["extraction_profile_digest"]),
            cast(str, row["request_digest"]),
            cast(str, row["model_alias"]),
            GraphJobStatus(cast(str, row["status"])),
            cast(datetime, row["created_at"]),
            cast(datetime, row["updated_at"]),
            cast(str | None, row["failure_code"]),
        )

    async def _audit_event(
        self,
        session: AsyncSession,
        scope: ProjectScopeContext,
        job: GraphJob,
        *,
        action: AuditAction,
        event_type: str,
        aggregate_version: int,
        payload: Mapping[str, object],
        digest: str,
        now: datetime,
    ) -> None:
        key = f"{job.job_id}:{aggregate_version}"
        await self._append_audit(session, scope, job, action, digest, key=key)
        await write_project_event(
            session,
            scope=scope,
            envelope=ProjectEventEnvelope(
                event_id=scoped_outbox_id(
                    scope, kind=event_type, identity=job.snapshot.snapshot_id
                ),
                event_type=event_type,
                schema_version=1,
                occurred_at=now.replace(tzinfo=timezone.utc),
                scope_kind="PROJECT",
                enterprise_id=scope.enterprise_id,
                project_id=scope.project_id,
                actor_id=scope.actor_id,
                identity_mode=scope.identity_mode.value,
                aggregate_type="GraphSnapshot",
                aggregate_id=job.snapshot.snapshot_id,
                aggregate_version=aggregate_version,
                correlation_id=job.job_id,
                causation_id=job.revision_id,
                idempotency_key=key,
                payload=payload,
            ),
        )

    @staticmethod
    async def _append_audit(
        session: AsyncSession,
        scope: ProjectScopeContext,
        job: GraphJob,
        action: AuditAction,
        digest: str,
        *,
        key: str,
    ) -> None:
        await MysqlProjectAudit(await session.connection(), scope=scope).append(
            scope,
            action,
            AuditResource.GRAPH_SNAPSHOT,
            AuditOutcome.FAILED
            if action is AuditAction.GRAPH_SNAPSHOT_FAILED
            else AuditOutcome.COMPLETED,
            SafeAuditMetadata({"content_digest": digest.removeprefix("sha256:")}),
            correlation_id=job.job_id,
            idempotency_key=key,
            resource_id=job.snapshot.snapshot_id,
        )


class MysqlGraphReadyProjection:
    """Create the durable graph job inside the document READY transaction."""

    def __init__(self, jobs: MysqlGraphJobStore, *, model_alias: str) -> None:
        if not model_alias:
            raise ValueError("graph model alias must be nonblank")
        self._jobs = jobs
        self._model_alias = model_alias

    async def after_ready(
        self,
        session: AsyncSession,
        scope: ProjectScopeContext,
        revision: RowMapping,
        *,
        now: datetime,
        ingestion_job_id: str,
    ) -> None:
        del ingestion_job_id
        revision_id = cast(str, revision["revision_id"])
        chunks_locator = revision["chunks_blob_locator"]
        if not isinstance(chunks_locator, str) or not chunks_locator:
            raise ValueError("ready revision is missing its chunks artifact")
        request = GraphJobRequest.create(
            scope=scope,
            revision_id=revision_id,
            chunks_locator=chunks_locator,
            extraction_profile_digest=GRAPH_EXTRACTION_PROFILE_DIGEST,
            model_alias=self._model_alias,
        )
        await self._jobs.request_in_transaction(session, scope, request, now=now)
