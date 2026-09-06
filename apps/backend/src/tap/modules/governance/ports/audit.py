"""Project Audit port; the application owns resource checks and its transaction."""

from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.governance.domain.audit import (
    AuditAction,
    AuditOutcome,
    AuditResource,
    ProjectAuditFact,
    SafeAuditMetadata,
    require_audit_scope,
)

__all__ = [
    "AuditAction",
    "AuditOutcome",
    "AuditResource",
    "ProjectAuditFact",
    "SafeAuditMetadata",
    "ProjectAuditPort",
    "require_audit_scope",
]


class ProjectAuditPort(Protocol):
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
    ) -> ProjectAuditFact: ...
