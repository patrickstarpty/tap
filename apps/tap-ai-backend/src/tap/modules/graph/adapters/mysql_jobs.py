"""MySQL graph extraction job ledger with lease fencing and atomic events."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Literal, Mapping, cast
from uuid import uuid4

from sqlalchemy import delete, or_, select, update
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
    graph_edge,
    graph_edge_evidence,
    graph_extraction_job,
    graph_fragment_batch,
    graph_inference_provenance,
    graph_node,
    graph_node_evidence,
    graph_snapshot,
    graph_snapshot_document_revision,
    graph_snapshot_revision,
    publish_graph_snapshot,
)
from tap.modules.graph.adapters.mysql_merge import MysqlProjectMergeQueue
from tap.modules.graph.domain.jobs import (
    ClaimedGraphJob,
    GraphBatchStatus,
    GraphFragmentBatch,
    GraphJob,
    GraphJobBusy,
    GraphJobLeaseLost,
    GraphJobRequest,
    GraphJobStatus,
)
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    InferenceProvenance,
    RelationOrigin,
)
from tap.modules.graph.ports.store import GraphFactNotFound
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.messaging.mysql_outbox import scoped_outbox_id, write_project_event


def serialize_draft(draft: GraphSnapshotDraft) -> dict[str, object]:
    """Serialize a graph snapshot draft to a JSON-safe payload for durable batch storage."""

    return {
        "nodes": [
            {
                "nodeId": node.node_id,
                "label": node.label,
                "nodeType": node.node_type,
                "canonicalKey": node.canonical_key,
                "evidenceIds": list(node.evidence_ids),
                "aliases": list(node.aliases),
            }
            for node in draft.nodes
        ],
        "edges": [
            {
                "edgeId": edge.edge_id,
                "sourceNodeId": edge.source_node_id,
                "targetNodeId": edge.target_node_id,
                "relationType": edge.relation_type,
                "origin": edge.origin.value,
                "confidence": edge.confidence,
                "evidenceIds": list(edge.evidence_ids),
                "relationLabel": edge.relation_label,
            }
            for edge in draft.edges
        ],
        "evidence": [
            {
                "evidenceId": item.evidence_id,
                "sourceRevisionId": item.source_revision_id,
                "documentRevisionId": item.document_revision_id,
                "chunkId": item.chunk_id,
                "anchor": dict(item.anchor),
                "contentDigest": item.content_digest,
            }
            for item in draft.evidence
        ],
        "provenance": [
            {
                "provenanceId": item.provenance_id,
                "edgeId": item.edge_id,
                "inputFactIds": list(item.input_fact_ids),
                "ruleDigest": item.rule_digest,
            }
            for item in draft.provenance
        ],
    }


def deserialize_draft(snapshot: GraphSnapshot, payload: Mapping[str, object]) -> GraphSnapshotDraft:
    """Rebuild a graph snapshot draft bound to ``snapshot`` from a serialized payload."""

    snapshot_id = snapshot.snapshot_id
    nodes = tuple(
        GraphNode(
            cast(str, item["nodeId"]),
            snapshot_id,
            cast(str, item["label"]),
            cast(str, item["nodeType"]),
            cast(str, item["canonicalKey"]),
            tuple(cast("list[str]", item["evidenceIds"])),
            tuple(cast("list[str]", item["aliases"])),
        )
        for item in cast("list[Mapping[str, object]]", payload["nodes"])
    )
    edges = tuple(
        GraphEdge(
            cast(str, item["edgeId"]),
            snapshot_id,
            cast(str, item["sourceNodeId"]),
            cast(str, item["targetNodeId"]),
            cast(str, item["relationType"]),
            RelationOrigin(cast(str, item["origin"])),
            float(cast(float, item["confidence"])),
            tuple(cast("list[str]", item["evidenceIds"])),
            cast(str, item["relationLabel"]),
        )
        for item in cast("list[Mapping[str, object]]", payload["edges"])
    )
    evidence = tuple(
        Evidence(
            cast(str, item["evidenceId"]),
            snapshot_id,
            cast(str, item["sourceRevisionId"]),
            cast(str, item["documentRevisionId"]),
            cast(str, item["chunkId"]),
            cast(Mapping[str, object], item["anchor"]),
            cast(str, item["contentDigest"]),
        )
        for item in cast("list[Mapping[str, object]]", payload["evidence"])
    )
    provenance = tuple(
        InferenceProvenance(
            cast(str, item["provenanceId"]),
            snapshot_id,
            cast(str, item["edgeId"]),
            tuple(cast("list[str]", item["inputFactIds"])),
            cast(str, item["ruleDigest"]),
        )
        for item in cast("list[Mapping[str, object]]", payload["provenance"])
    )
    return GraphSnapshotDraft(snapshot, nodes, edges, evidence, provenance)


class MysqlGraphJobStore:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        merge_queue: MysqlProjectMergeQueue | None = None,
    ) -> None:
        self._sessions = sessions
        self._merge_queue = merge_queue

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

    async def renew(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedGraphJob,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> ClaimedGraphJob:
        scope = require_project_scope(scope)
        expires = now + lease_duration
        async with self._sessions() as session, session.begin():
            await self._owned_row(session, scope, claim, now)
            result = await session.execute(
                update(graph_extraction_job)
                .where(
                    *scope_predicates(graph_extraction_job, scope),
                    graph_extraction_job.c.job_id == claim.job_id,
                    graph_extraction_job.c.status == GraphJobStatus.RUNNING.value,
                    graph_extraction_job.c.lease_token == claim.lease_token,
                    graph_extraction_job.c.lease_expires_at > now,
                )
                .values(lease_expires_at=expires, updated_at=now)
            )
            if result.rowcount != 1:
                raise GraphJobLeaseLost(claim.job_id)
        return replace(claim, lease_expires_at=expires, updated_at=now)

    async def record_batch(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedGraphJob,
        batch: GraphFragmentBatch,
        *,
        now: datetime,
    ) -> None:
        scope = require_project_scope(scope)
        if batch.snapshot_id != claim.snapshot.snapshot_id:
            raise ValueError("graph fragment batch does not match the claimed snapshot")
        async with self._sessions() as session, session.begin():
            await self._owned_row(session, scope, claim, now)
            common = scope_values(scope)
            statement = insert(graph_fragment_batch).values(
                **common,
                snapshot_id=batch.snapshot_id,
                batch_index=batch.batch_index,
                job_id=claim.job_id,
                chunk_ids=list(batch.chunk_ids),
                status=batch.status.value,
                attempt=batch.attempt,
                failure_code=batch.failure_code,
                draft_json=serialize_draft(batch.draft) if batch.draft is not None else None,
                updated_at=now,
            )
            await session.execute(
                statement.on_duplicate_key_update(
                    job_id=statement.inserted.job_id,
                    chunk_ids=statement.inserted.chunk_ids,
                    status=statement.inserted.status,
                    attempt=statement.inserted.attempt,
                    failure_code=statement.inserted.failure_code,
                    draft_json=statement.inserted.draft_json,
                    updated_at=statement.inserted.updated_at,
                )
            )

    async def load_batches(
        self, scope: ProjectScopeContext, claim: ClaimedGraphJob
    ) -> tuple[GraphFragmentBatch, ...]:
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(graph_fragment_batch)
                        .where(
                            *scope_predicates(graph_fragment_batch, scope),
                            graph_fragment_batch.c.snapshot_id == claim.snapshot.snapshot_id,
                        )
                        .order_by(graph_fragment_batch.c.batch_index)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(
            GraphFragmentBatch(
                cast(str, row["snapshot_id"]),
                cast(int, row["batch_index"]),
                tuple(row["chunk_ids"]),
                GraphBatchStatus(cast(str, row["status"])),
                cast(int, row["attempt"]),
                cast(str | None, row["failure_code"]),
                deserialize_draft(claim.snapshot, cast(Mapping[str, object], row["draft_json"]))
                if row["draft_json"] is not None
                else None,
            )
            for row in rows
        )

    async def complete(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedGraphJob,
        draft: GraphSnapshotDraft,
        *,
        now: datetime,
        status: Literal["READY", "PARTIAL"] = "READY",
    ) -> GraphJob:
        scope = require_project_scope(scope)
        async with self._sessions() as session, session.begin():
            row = await self._owned_row(session, scope, claim, now)
            current = await self._job(session, scope, row)
            if draft.snapshot != current.snapshot:
                raise ValueError("graph draft does not match claimed snapshot")
            snapshot = await publish_graph_snapshot(session, scope, draft, now=now, status=status)
            if self._merge_queue is not None:
                await self._merge_queue.request_in_transaction(
                    session, scope, reason="fragment-ready", now=now
                )
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

    async def reset_for_profile(
        self,
        scope: ProjectScopeContext,
        revision_id: str,
        *,
        extraction_profile_digest: str,
        model_alias: str,
        now: datetime,
    ) -> GraphJob:
        """Requeue extraction for one currently-published revision under a
        (possibly changed) extraction profile/model: drop every fact the
        prior snapshot derived, put the snapshot back to CANDIDATE, and
        rewrite the job row as a fresh PENDING request. Used only by the
        `graph rebuild` operator CLI; never deletes a project-graph version
        itself -- the merge worker replays a version once extraction
        finishes."""

        scope = require_project_scope(scope)
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(graph_extraction_job)
                        .where(
                            *scope_predicates(graph_extraction_job, scope),
                            graph_extraction_job.c.revision_id == revision_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ValueError("graph job not found for revision")
            if (
                row["status"] == GraphJobStatus.RUNNING.value
                and row["lease_expires_at"] is not None
                and cast(datetime, row["lease_expires_at"]) > now
            ):
                raise ValueError("graph job is running")
            snapshot_id = cast(str, row["snapshot_id"])
            job_id = cast(str, row["job_id"])
            for table in (
                graph_inference_provenance,
                graph_edge_evidence,
                graph_node_evidence,
                graph_edge,
                graph_node,
                graph_snapshot_revision,
                graph_fragment_batch,
            ):
                await session.execute(
                    delete(table).where(
                        *scope_predicates(table, scope),
                        table.c.snapshot_id == snapshot_id,
                    )
                )
            await session.execute(
                update(graph_snapshot)
                .where(
                    *scope_predicates(graph_snapshot, scope),
                    graph_snapshot.c.snapshot_id == snapshot_id,
                )
                .values(status="CANDIDATE")
            )
            new_digest = (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(
                        [
                            scope.project_id,
                            revision_id,
                            extraction_profile_digest,
                            model_alias,
                            "rebuild",
                            now.isoformat(),
                        ],
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
            )
            await session.execute(
                update(graph_extraction_job)
                .where(
                    *scope_predicates(graph_extraction_job, scope),
                    graph_extraction_job.c.job_id == job_id,
                )
                .values(
                    request_digest=new_digest,
                    extraction_profile_digest=extraction_profile_digest,
                    model_alias=model_alias,
                    status=GraphJobStatus.PENDING.value,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    attempt_count=0,
                    failure_code=None,
                    updated_at=now,
                )
            )
            updated_row = await self._locked_row(session, scope, job_id)
            job = await self._job(session, scope, updated_row)
            await self._append_audit(
                session,
                scope,
                job,
                AuditAction.GRAPH_SNAPSHOT_REQUESTED,
                new_digest,
                key=f"{job_id}:rebuild:{new_digest[7:23]}",
            )
            return job

    async def list_fragment_states(
        self, scope: ProjectScopeContext
    ) -> tuple[tuple[str, str, str], ...]:
        """`(revision_id, job status, snapshot status)` for every graph job in
        this Project, used by `GET /project` to classify currently-extracting
        and partial revisions without loading full job/snapshot rows."""

        scope = require_project_scope(scope)
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            graph_extraction_job.c.revision_id,
                            graph_extraction_job.c.status,
                            graph_snapshot.c.status.label("snapshot_status"),
                        )
                        .select_from(
                            graph_extraction_job.join(
                                graph_snapshot,
                                (graph_extraction_job.c.project_id == graph_snapshot.c.project_id)
                                & (
                                    graph_extraction_job.c.snapshot_id
                                    == graph_snapshot.c.snapshot_id
                                ),
                            )
                        )
                        .where(*scope_predicates(graph_extraction_job, scope))
                        .order_by(graph_extraction_job.c.revision_id)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(
            (
                cast(str, row["revision_id"]),
                cast(str, row["status"]),
                cast(str, row["snapshot_status"]),
            )
            for row in rows
        )

    async def retry_failed_batches(
        self, scope: ProjectScopeContext, revision_id: str, *, now: datetime
    ) -> tuple[int, GraphJobStatus]:
        """Requeue every `FAILED` batch of the revision's current job for
        re-extraction: FAILED -> PENDING with `attempt` reset, and the job
        itself put back to PENDING so the worker claims it again. A job whose
        lease is RUNNING and not yet expired is busy -- raises `GraphJobBusy`
        rather than racing the in-flight attempt. A job with no FAILED
        batches is a no-op, returning `(0, <current status>)`.

        A PARTIAL or FAILED snapshot already carries published facts (or is
        past `CANDIDATE`), and `publish_graph_snapshot` silently no-ops on a
        READY/PARTIAL snapshot and raises on a FAILED one -- so once the
        worker reruns, re-completing the job would never actually update the
        graph. Requeuing a FAILED batch therefore also resets the snapshot
        itself to `CANDIDATE` and deletes the facts it already published,
        *without* touching `graph_fragment_batch` -- the worker reuses every
        still-READY batch's draft and calls the model only for the ones this
        method just reset to PENDING."""

        scope = require_project_scope(scope)
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(graph_extraction_job)
                        .where(
                            *scope_predicates(graph_extraction_job, scope),
                            graph_extraction_job.c.revision_id == revision_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise GraphFactNotFound(f"graph job not found for revision {revision_id!r}")
            job = await self._job(session, scope, cast(Mapping[str, object], row))
            if (
                job.status is GraphJobStatus.RUNNING
                and row["lease_expires_at"] is not None
                and cast(datetime, row["lease_expires_at"]) > now
            ):
                raise GraphJobBusy(job.job_id)
            result = await session.execute(
                update(graph_fragment_batch)
                .where(
                    *scope_predicates(graph_fragment_batch, scope),
                    graph_fragment_batch.c.snapshot_id == job.snapshot.snapshot_id,
                    graph_fragment_batch.c.status == GraphBatchStatus.FAILED.value,
                )
                .values(
                    status=GraphBatchStatus.PENDING.value,
                    attempt=0,
                    failure_code=None,
                    updated_at=now,
                )
            )
            requeued = result.rowcount
            if requeued == 0:
                return 0, job.status
            snapshot_id = job.snapshot.snapshot_id
            for table in (
                graph_inference_provenance,
                graph_edge_evidence,
                graph_node_evidence,
                graph_edge,
                graph_node,
                graph_snapshot_revision,
                graph_snapshot_document_revision,
            ):
                await session.execute(
                    delete(table).where(
                        *scope_predicates(table, scope),
                        table.c.snapshot_id == snapshot_id,
                    )
                )
            await session.execute(
                update(graph_snapshot)
                .where(
                    *scope_predicates(graph_snapshot, scope),
                    graph_snapshot.c.snapshot_id == snapshot_id,
                )
                .values(status="CANDIDATE")
            )
            await session.execute(
                update(graph_extraction_job)
                .where(
                    *scope_predicates(graph_extraction_job, scope),
                    graph_extraction_job.c.job_id == job.job_id,
                )
                .values(
                    status=GraphJobStatus.PENDING.value,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    failure_code=None,
                    updated_at=now,
                )
            )
            return requeued, GraphJobStatus.PENDING

    async def merge_state(
        self, scope: ProjectScopeContext, *, now: datetime
    ) -> Literal["IDLE", "PENDING", "RUNNING", "FAILED"]:
        if self._merge_queue is None:
            return "IDLE"
        return await self._merge_queue.merge_state(scope, now=now)

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
            cast(Literal["CANDIDATE", "READY", "PARTIAL", "FAILED"], snapshot_row["status"]),
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
        # A fragment retry (see `retry_failed_batches`) can legitimately
        # re-complete the same job with a different `graph_digest` once the
        # worker reruns, so the key must vary with content: otherwise the
        # second completion's audit append collides on `job_id:aggregate_
        # version` against the first's *different* content digest and
        # `MysqlProjectAudit.append` raises `AuditIdempotencyConflict`. A
        # digest-stable call (the original, non-retried path) still produces
        # the same key every time, so existing dedup is unaffected.
        key = f"{job.job_id}:{aggregate_version}:{digest.removeprefix('sha256:')[:16]}"
        await self._append_audit(session, scope, job, action, digest, key=key)
        await write_project_event(
            session,
            scope=scope,
            envelope=ProjectEventEnvelope(
                # Identity includes the digest for the same reason the audit
                # `key` above does: a retried completion of the same job and
                # snapshot carries different content, and the outbox's own
                # dedup (keyed by `outbox_id`) would otherwise collide the
                # second write against the first event's distinct payload.
                event_id=scoped_outbox_id(
                    scope, kind=event_type, identity=f"{job.snapshot.snapshot_id}:{digest}"
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
