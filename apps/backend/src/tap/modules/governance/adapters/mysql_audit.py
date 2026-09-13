"""Append Project Audit in the caller's active connection, without committing."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext, require_identifier
from tap.modules.governance.adapters.schema import project_audit
from tap.modules.governance.domain.audit import (
    AuditAction,
    AuditIdempotencyConflict,
    AuditOutcome,
    AuditResource,
    ProjectAuditFact,
    SafeAuditMetadata,
    audit_content_digest,
    require_audit_scope,
)
from tap.platform.db.project_scope import scope_predicates, scope_values


class MysqlProjectAudit:
    def __init__(self, connection: AsyncConnection, *, scope: ProjectScopeContext) -> None:
        self._scope = require_audit_scope(scope)
        self._connection = connection
        self._require_transaction()

    def _require_transaction(self) -> None:
        if not self._connection.in_transaction():
            raise ValueError("audit requires an active transaction")

    async def append(
        self,
        scope: ProjectScopeContext,
        action: AuditAction,
        resource: AuditResource,
        outcome: AuditOutcome,
        safe_metadata: SafeAuditMetadata,
        *,
        correlation_id: str,
        idempotency_key: str,
        resource_id: str | None = None,
    ) -> ProjectAuditFact:
        scope = require_audit_scope(scope)
        if scope != self._scope:
            raise ValueError("audit scope differs from bound scope")
        digest = audit_content_digest(
            scope, action, resource, outcome, safe_metadata, resource_id=resource_id
        )
        require_identifier("correlation_id", correlation_id)
        require_identifier("idempotency_key", idempotency_key)
        self._require_transaction()
        values = {
            **scope_values(scope),
            "audit_id": str(uuid4()),
            "occurred_at": datetime.now(timezone.utc).replace(tzinfo=None),
            "action": action.value,
            "resource": resource.value,
            "resource_id": resource_id,
            "outcome": outcome.value,
            "correlation_id": correlation_id,
            "idempotency_key": idempotency_key,
            "content_digest": digest,
            "safe_metadata": dict(safe_metadata.values),
        }
        # The unique key serializes concurrent writers. Its no-op duplicate
        # branch preserves the first fact, including its correlation and time.
        await self._connection.execute(
            insert(project_audit)
            .values(**values)
            .on_duplicate_key_update(audit_id=project_audit.c.audit_id)
        )
        row = (
            (
                await self._connection.execute(
                    select(project_audit)
                    .where(
                        *scope_predicates(project_audit, scope),
                        project_audit.c.idempotency_key == idempotency_key,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["content_digest"] != digest:
            raise AuditIdempotencyConflict("idempotency-conflict")
        persisted_scope = ProjectScopeContext(
            enterprise_id=row["enterprise_id"],
            project_id=row["project_id"],
            actor_id=row["actor_id"],
            identity_mode=IdentityMode(row["identity_mode"]),
        )
        if persisted_scope != scope or row["identity_origin"] != scope.identity_mode.value.upper():
            raise AuditIdempotencyConflict("idempotency-conflict")
        return ProjectAuditFact(
            audit_id=row["audit_id"],
            occurred_at=row["occurred_at"].replace(tzinfo=timezone.utc),
            scope=persisted_scope,
            action=AuditAction(row["action"]),
            resource=AuditResource(row["resource"]),
            resource_id=row["resource_id"],
            outcome=AuditOutcome(row["outcome"]),
            correlation_id=row["correlation_id"],
            idempotency_key=row["idempotency_key"],
            content_digest=row["content_digest"],
            safe_metadata=SafeAuditMetadata(row["safe_metadata"]),
        )
