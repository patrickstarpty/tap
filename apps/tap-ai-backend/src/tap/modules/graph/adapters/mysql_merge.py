"""MySQL-backed `ProjectMergeQueue` (one lease-fenced row per project, keyed
by `graph_project_merge_job`) and `MergeInputsPort` reading currently-ready or
-partial fragments for the merge worker to replay.

`GraphMergeOnSourceChange` is the shared trigger projection: Knowledge calls
`after_source_deleted` when a source is tombstoned and `after_publication_changed`
when a review publishes/withdraws, both inside the caller's own transaction, so a
merge is requested exactly once a durable cause for one actually occurred.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Literal, cast
from uuid import uuid4

from sqlalchemy import select, update
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
    graph_extraction_job,
    graph_snapshot,
    graph_snapshot_revision,
)
from tap.modules.graph.adapters.mysql_project import (
    graph_project_merge_job,
    graph_project_version,
    load_fragment_draft,
    prune_project_versions,
    publish_project_version,
)
from tap.modules.graph.application.merge_jobs import (
    MergeClaim,
    ProjectMergeLeaseLost,
    merge_failure_backoff_seconds,
)
from tap.modules.graph.application.project_queries import ProjectGraphCache
from tap.modules.graph.domain.project import FragmentRecord, ProjectGraphDraft, ProjectGraphVersion
from tap.modules.graph.ports.project_store import CurrentRevisionsPort
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.messaging.mysql_outbox import scoped_outbox_id, write_project_event

__all__ = ["GraphMergeOnSourceChange", "MysqlMergeInputs", "MysqlProjectMergeQueue"]


class MysqlProjectMergeQueue:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        cache: ProjectGraphCache | None = None,
    ) -> None:
        self._sessions = sessions
        self._cache = cache

    async def request(self, scope: ProjectScopeContext, *, reason: str, now: datetime) -> None:
        async with self._sessions() as session, session.begin():
            await self.request_in_transaction(session, scope, reason=reason, now=now)

    async def request_in_transaction(
        self,
        session: AsyncSession,
        scope: ProjectScopeContext,
        *,
        reason: str,
        now: datetime,
    ) -> None:
        scope = require_project_scope(scope)
        if not session.in_transaction():
            raise ValueError("project merge request requires an active transaction")
        common = scope_values(scope)
        statement = insert(graph_project_merge_job).values(
            **common,
            due_at=now,
            last_reason=reason,
            claimed_due_at=None,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            attempt_count=0,
            failure_code=None,
            created_at=now,
            updated_at=now,
        )
        await session.execute(
            statement.on_duplicate_key_update(
                due_at=statement.inserted.due_at,
                last_reason=statement.inserted.last_reason,
                updated_at=statement.inserted.updated_at,
            )
        )

    async def _locked_row(
        self, session: AsyncSession, scope: ProjectScopeContext
    ) -> RowMapping | None:
        return (
            (
                await session.execute(
                    select(graph_project_merge_job)
                    .where(*scope_predicates(graph_project_merge_job, scope))
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )

    async def _owned_row(
        self,
        session: AsyncSession,
        scope: ProjectScopeContext,
        claim: MergeClaim,
        now: datetime,
    ) -> RowMapping:
        row = await self._locked_row(session, scope)
        if (
            row is None
            or row["lease_token"] != claim.lease_token
            or row["lease_expires_at"] is None
            or cast(datetime, row["lease_expires_at"]) <= now
        ):
            raise ProjectMergeLeaseLost(scope.project_id)
        return row

    async def claim(
        self,
        scope: ProjectScopeContext,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> MergeClaim | None:
        scope = require_project_scope(scope)
        if not worker_id:
            raise ValueError("project merge worker identity must be nonblank")
        async with self._sessions() as session, session.begin():
            row = await self._locked_row(session, scope)
            if row is None or row["due_at"] is None or cast(datetime, row["due_at"]) > now:
                return None
            if (
                row["lease_owner"] is not None
                and row["lease_expires_at"] is not None
                and cast(datetime, row["lease_expires_at"]) > now
            ):
                return None
            token = uuid4().hex
            expires = now + lease_duration
            due_at = cast(datetime, row["due_at"])
            await session.execute(
                update(graph_project_merge_job)
                .where(*scope_predicates(graph_project_merge_job, scope))
                .values(
                    lease_owner=worker_id,
                    lease_token=token,
                    lease_expires_at=expires,
                    claimed_due_at=due_at,
                    updated_at=now,
                )
            )
            attempt = cast(int, row["attempt_count"])
            reason = cast("str | None", row["last_reason"]) or ""
        return MergeClaim(
            project_id=scope.project_id,
            lease_owner=worker_id,
            lease_token=token,
            lease_expires_at=expires,
            claimed_due_at=due_at,
            reason=reason,
            attempt=attempt,
        )

    async def renew(
        self,
        scope: ProjectScopeContext,
        claim: MergeClaim,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> MergeClaim:
        scope = require_project_scope(scope)
        expires = now + lease_duration
        async with self._sessions() as session, session.begin():
            await self._owned_row(session, scope, claim, now)
            result = await session.execute(
                update(graph_project_merge_job)
                .where(
                    *scope_predicates(graph_project_merge_job, scope),
                    graph_project_merge_job.c.lease_token == claim.lease_token,
                    graph_project_merge_job.c.lease_expires_at > now,
                )
                .values(lease_expires_at=expires, updated_at=now)
            )
            if result.rowcount != 1:
                raise ProjectMergeLeaseLost(scope.project_id)
        return replace(claim, lease_expires_at=expires)

    async def complete(
        self,
        scope: ProjectScopeContext,
        claim: MergeClaim,
        draft: ProjectGraphDraft,
        *,
        now: datetime,
    ) -> ProjectGraphVersion | None:
        scope = require_project_scope(scope)
        version: ProjectGraphVersion | None = None
        async with self._sessions() as session, session.begin():
            row = await self._owned_row(session, scope, claim, now)
            current_row = (
                (
                    await session.execute(
                        select(
                            graph_project_version.c.version,
                            graph_project_version.c.fragment_digest,
                        )
                        .where(
                            *scope_predicates(graph_project_version, scope),
                            graph_project_version.c.status == "READY",
                        )
                        .order_by(graph_project_version.c.version.desc())
                        .limit(1)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current_row is None or current_row["fragment_digest"] != draft.fragment_digest:
                next_version = 1 if current_row is None else current_row["version"] + 1
                version = await publish_project_version(
                    session, scope, draft, version=next_version, now=now
                )
                await prune_project_versions(session, scope, keep_latest=2)
                await self._audit_ready(session, scope, version, now=now)
            due_at = cast("datetime | None", row["due_at"])
            values: dict[str, object] = {
                "lease_owner": None,
                "lease_token": None,
                "lease_expires_at": None,
                "attempt_count": 0,
                "failure_code": None,
                "claimed_due_at": None,
                "updated_at": now,
            }
            if claim.claimed_due_at == due_at:
                values["due_at"] = None
                values["last_reason"] = None
            await session.execute(
                update(graph_project_merge_job)
                .where(*scope_predicates(graph_project_merge_job, scope))
                .values(**values)
            )
        if version is not None and self._cache is not None:
            self._cache.invalidate(scope.project_id)
        return version

    async def _audit_ready(
        self,
        session: AsyncSession,
        scope: ProjectScopeContext,
        version: ProjectGraphVersion,
        *,
        now: datetime,
    ) -> None:
        key = f"graph-project:{version.version}"
        await MysqlProjectAudit(await session.connection(), scope=scope).append(
            scope,
            AuditAction.GRAPH_SNAPSHOT_READY,
            AuditResource.GRAPH_SNAPSHOT,
            AuditOutcome.COMPLETED,
            SafeAuditMetadata({"content_digest": version.fragment_digest.removeprefix("sha256:")}),
            correlation_id=key,
            idempotency_key=key,
            resource_id=version.version_id,
        )
        await write_project_event(
            session,
            scope=scope,
            envelope=ProjectEventEnvelope(
                event_id=scoped_outbox_id(
                    scope, kind="knowledge.graph-project.ready", identity=version.version_id
                ),
                event_type="knowledge.graph-project.ready",
                schema_version=1,
                occurred_at=now.replace(tzinfo=timezone.utc),
                scope_kind="PROJECT",
                enterprise_id=scope.enterprise_id,
                project_id=scope.project_id,
                actor_id=scope.actor_id,
                identity_mode=scope.identity_mode.value,
                aggregate_type="ProjectGraphVersion",
                aggregate_id=version.version_id,
                aggregate_version=version.version,
                correlation_id=key,
                causation_id=None,
                idempotency_key=key,
                payload={
                    "versionId": version.version_id,
                    "graphVersion": str(version.version),
                    "fragmentDigest": version.fragment_digest,
                    "nodeCount": str(version.node_count),
                    "edgeCount": str(version.edge_count),
                },
            ),
        )

    async def fail(
        self,
        scope: ProjectScopeContext,
        claim: MergeClaim,
        *,
        failure_code: str,
        now: datetime,
    ) -> None:
        scope = require_project_scope(scope)
        if not failure_code or len(failure_code) > 64:
            raise ValueError("project merge failure code must be bounded")
        async with self._sessions() as session, session.begin():
            await self._owned_row(session, scope, claim, now)
            backoff = merge_failure_backoff_seconds(claim.attempt)
            await session.execute(
                update(graph_project_merge_job)
                .where(*scope_predicates(graph_project_merge_job, scope))
                .values(
                    failure_code=failure_code,
                    attempt_count=claim.attempt + 1,
                    due_at=now + timedelta(seconds=backoff),
                    last_reason=claim.reason,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    claimed_due_at=None,
                    updated_at=now,
                )
            )

    async def merge_state(
        self, scope: ProjectScopeContext, *, now: datetime
    ) -> Literal["IDLE", "PENDING", "RUNNING", "FAILED"]:
        scope = require_project_scope(scope)
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(graph_project_merge_job).where(
                            *scope_predicates(graph_project_merge_job, scope)
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return "IDLE"
        lease_expires_at = cast("datetime | None", row["lease_expires_at"])
        lease_active = lease_expires_at is not None and lease_expires_at > now
        if row["lease_owner"] is not None and lease_active:
            return "RUNNING"
        if row["failure_code"] is not None:
            return "FAILED"
        if row["due_at"] is not None:
            return "PENDING"
        return "IDLE"


class MysqlMergeInputs:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        current_revisions: CurrentRevisionsPort,
    ) -> None:
        self._sessions = sessions
        self._current_revisions = current_revisions

    async def load_fragments(self, scope: ProjectScopeContext) -> tuple[FragmentRecord, ...]:
        scope = require_project_scope(scope)
        revision_ids = await self._current_revisions.current_revision_ids(scope)
        if not revision_ids:
            return ()
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            graph_extraction_job.c.snapshot_id,
                            graph_extraction_job.c.revision_id,
                            graph_snapshot.c.status,
                            graph_snapshot_revision.c.content_digest,
                        )
                        .select_from(
                            graph_extraction_job.join(
                                graph_snapshot,
                                (graph_extraction_job.c.project_id == graph_snapshot.c.project_id)
                                & (
                                    graph_extraction_job.c.snapshot_id
                                    == graph_snapshot.c.snapshot_id
                                ),
                            ).join(
                                graph_snapshot_revision,
                                (
                                    graph_snapshot.c.project_id
                                    == graph_snapshot_revision.c.project_id
                                )
                                & (
                                    graph_snapshot.c.snapshot_id
                                    == graph_snapshot_revision.c.snapshot_id
                                ),
                            )
                        )
                        .where(
                            *scope_predicates(graph_extraction_job, scope),
                            graph_snapshot.c.status.in_(("READY", "PARTIAL")),
                            graph_extraction_job.c.extraction_profile_digest
                            == GRAPH_EXTRACTION_PROFILE_DIGEST,
                            graph_extraction_job.c.revision_id.in_(revision_ids),
                        )
                        .order_by(graph_extraction_job.c.snapshot_id)
                    )
                )
                .mappings()
                .all()
            )
            fragments: list[FragmentRecord] = []
            for row in rows:
                draft = await load_fragment_draft(session, scope, cast(str, row["snapshot_id"]))
                fragments.append(
                    FragmentRecord(
                        snapshot_id=cast(str, row["snapshot_id"]),
                        revision_id=cast(str, row["revision_id"]),
                        status=cast('Literal["READY", "PARTIAL"]', row["status"]),
                        content_digest=cast(str, row["content_digest"]),
                        draft=draft,
                    )
                )
        return tuple(fragments)


class GraphMergeOnSourceChange:
    """Shared projection requesting a re-merge whenever a durable Knowledge
    fact that bears on the merged Project graph changes: a whole source or a
    single document is deleted (fragments they fed may now be orphaned,
    `after_source_deleted`), or a publication is published or withdrawn (the
    set of currently-ready revisions the merge replays may have shifted,
    `after_publication_changed`)."""

    def __init__(self, queue: MysqlProjectMergeQueue) -> None:
        self._queue = queue

    async def after_source_deleted(
        self,
        session: AsyncSession,
        scope: ProjectScopeContext,
        source_id: str,
        *,
        now: datetime,
    ) -> None:
        del source_id
        await self._queue.request_in_transaction(session, scope, reason="source-deleted", now=now)

    async def after_publication_changed(
        self, session: AsyncSession, scope: ProjectScopeContext, *, now: datetime
    ) -> None:
        await self._queue.request_in_transaction(
            session, scope, reason="publication-changed", now=now
        )
