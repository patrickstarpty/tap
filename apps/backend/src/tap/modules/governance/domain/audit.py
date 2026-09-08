"""Closed, immutable Project Audit facts; no transport or persistence dependencies."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType

from tap.modules.access.domain.context import ProjectScopeContext, require_identifier


class AuditAction(StrEnum):
    SOURCE_CREATED = "source-created"
    SOURCE_DELETED = "source-deleted"
    REVISION_ACCEPTED = "document-revision-accepted"
    REVISION_READY = "document-revision-ready"
    INGESTION_RETRIED = "document-ingestion-retried"
    DELETION_REQUESTED = "document-deletion-requested"
    RECOVER_UPLOADS = "recover-uploads"
    SCAVENGE_STAGING = "scavenge-staging"
    REBUILD_MILVUS = "rebuild-milvus"
    RECONCILE_ALL = "reconcile-all"


class AuditResource(StrEnum):
    # The resource identity is the explicit Enterprise/Project scope.
    KNOWLEDGE_SOURCE = "knowledge-source"
    DOCUMENT_REVISION = "document-revision"
    PROJECT_MAINTENANCE = "project-maintenance"


class AuditOutcome(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


_COUNT_KEYS = frozenset(
    {
        "processed_count",
        "recovered_count",
        "removed_count",
        "rebuilt_count",
        "failed_count",
        "skipped_count",
    }
)
_METADATA_KEYS = _COUNT_KEYS | {"mode", "content_digest"}


@dataclass(frozen=True, slots=True)
class SafeAuditMetadata:
    """Registered counts, mode, and SHA-256 digest only; never free text."""

    values: Mapping[str, int | str]

    def __post_init__(self) -> None:
        if not isinstance(self.values, Mapping):
            raise TypeError("audit metadata requires a mapping")
        if len(self.values) > len(_METADATA_KEYS):
            raise ValueError("audit metadata must be bounded")
        values = dict(self.values)
        for key, value in values.items():
            if key not in _METADATA_KEYS:
                raise ValueError("unregistered audit metadata key")
            if key in _COUNT_KEYS:
                if type(value) is not int or not 0 <= value <= 1_000_000:
                    raise ValueError("audit counts must be bounded integers")
            elif key == "mode":
                if type(value) is not str or value not in {"apply", "dry-run"}:
                    raise ValueError("audit mode must be registered")
            elif type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError("audit digest must be a lowercase SHA-256 digest")
        if len(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()) > 512:
            raise ValueError("audit metadata must be bounded")
        object.__setattr__(self, "values", MappingProxyType(values))


def require_audit_scope(scope: object) -> ProjectScopeContext:
    if type(scope) is not ProjectScopeContext:
        raise TypeError("audit requires a trusted ProjectScopeContext")
    # Validate fields again and snapshot provenance at the boundary.
    return replace(scope)


def audit_content_digest(
    scope: ProjectScopeContext,
    action: AuditAction,
    resource: AuditResource,
    outcome: AuditOutcome,
    safe_metadata: SafeAuditMetadata,
    *,
    resource_id: str | None = None,
) -> str:
    scope = require_audit_scope(scope)
    for value, kind in ((action, AuditAction), (resource, AuditResource), (outcome, AuditOutcome)):
        if type(value) is not kind:
            raise TypeError("audit vocabulary must use registered values")
    if type(safe_metadata) is not SafeAuditMetadata:
        raise TypeError("audit requires SafeAuditMetadata")
    expected_resource = (
        AuditResource.KNOWLEDGE_SOURCE
        if action in {AuditAction.SOURCE_CREATED, AuditAction.SOURCE_DELETED}
        else AuditResource.DOCUMENT_REVISION
        if action
        in {
            AuditAction.REVISION_ACCEPTED,
            AuditAction.REVISION_READY,
            AuditAction.INGESTION_RETRIED,
            AuditAction.DELETION_REQUESTED,
        }
        else AuditResource.PROJECT_MAINTENANCE
    )
    if resource is not expected_resource:
        raise ValueError("audit action/resource mismatch")
    if resource is AuditResource.PROJECT_MAINTENANCE:
        if resource_id is not None:
            raise ValueError("maintenance resource identity must be null")
    else:
        if resource_id is None:
            raise ValueError("knowledge audit requires resource identity")
        require_identifier("resource_id", resource_id)
    metadata = SafeAuditMetadata(safe_metadata.values)
    material = [
        scope.enterprise_id,
        scope.project_id,
        scope.actor_id,
        scope.identity_mode.value,
        scope.identity_mode.value.upper(),
        action.value,
        resource.value,
        outcome.value,
        dict(metadata.values),
    ]
    if resource_id is not None:
        material.append(resource_id)
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class AuditIdempotencyConflict(ValueError):
    """An existing scoped command identifies different business facts."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectAuditFact:
    audit_id: str
    occurred_at: datetime
    scope: ProjectScopeContext
    action: AuditAction
    resource: AuditResource
    outcome: AuditOutcome
    correlation_id: str
    idempotency_key: str
    content_digest: str
    safe_metadata: SafeAuditMetadata
    resource_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("audit_id", "correlation_id", "idempotency_key"):
            require_identifier(name, getattr(self, name))
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("audit timestamp must be UTC-aware")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(timezone.utc))
        object.__setattr__(self, "scope", require_audit_scope(self.scope))
        if self.content_digest != audit_content_digest(
            self.scope,
            self.action,
            self.resource,
            self.outcome,
            self.safe_metadata,
            resource_id=self.resource_id,
        ):
            raise ValueError("audit content digest does not match its facts")
        object.__setattr__(self, "safe_metadata", SafeAuditMetadata(self.safe_metadata.values))

    @property
    def identity_origin(self) -> str:
        return self.scope.identity_mode.value.upper()
