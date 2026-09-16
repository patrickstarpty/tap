"""Durable graph extraction job identities and lease state."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.domain.models import GraphSnapshot
from tap.platform.db.project_scope import require_project_scope

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class GraphJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    READY = "READY"
    FAILED = "FAILED"


class GraphJobLeaseLost(Exception):
    """The caller no longer owns the graph job transition."""


@dataclass(frozen=True, slots=True)
class GraphJobRequest:
    job_id: str
    revision_id: str
    snapshot: GraphSnapshot
    chunks_locator: str
    extraction_profile_digest: str
    request_digest: str
    model_alias: str

    @classmethod
    def create(
        cls,
        *,
        scope: ProjectScopeContext,
        revision_id: str,
        chunks_locator: str,
        extraction_profile_digest: str,
        model_alias: str,
    ) -> GraphJobRequest:
        scope = require_project_scope(scope)
        if not revision_id or not chunks_locator or not model_alias:
            raise ValueError("graph job request identities must be nonblank")
        if _DIGEST.fullmatch(extraction_profile_digest) is None:
            raise ValueError("graph extraction profile digest must be canonical")
        material = json.dumps(
            [scope.project_id, revision_id, chunks_locator, extraction_profile_digest, model_alias],
            separators=(",", ":"),
        )
        request_digest = "sha256:" + hashlib.sha256(material.encode()).hexdigest()
        suffix = hashlib.sha256(
            json.dumps([scope.project_id, revision_id], separators=(",", ":")).encode()
        ).hexdigest()[:32]
        snapshot = GraphSnapshot.create(
            snapshot_id=f"grs_{suffix}",
            project_id=scope.project_id,
            source_revision_ids=(revision_id,),
            document_revision_ids=(revision_id,),
        )
        return cls(
            f"grj_{suffix}",
            revision_id,
            snapshot,
            chunks_locator,
            extraction_profile_digest,
            request_digest,
            model_alias,
        )


@dataclass(frozen=True, slots=True)
class GraphJob:
    job_id: str
    revision_id: str
    snapshot: GraphSnapshot
    chunks_locator: str
    extraction_profile_digest: str
    request_digest: str
    model_alias: str
    status: GraphJobStatus
    created_at: datetime
    updated_at: datetime
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimedGraphJob(GraphJob):
    lease_owner: str = ""
    lease_token: str = ""
    lease_expires_at: datetime | None = None
