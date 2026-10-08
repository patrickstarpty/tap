"""Durable graph extraction job identities and lease state."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.domain.models import GraphSnapshot, GraphSnapshotDraft
from tap.platform.db.project_scope import require_project_scope

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class GraphJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    READY = "READY"
    FAILED = "FAILED"


class GraphBatchStatus(StrEnum):
    PENDING = "PENDING"
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
        source_revision_ids: tuple[str, ...] | None = None,
        document_revision_ids: tuple[str, ...] | None = None,
    ) -> GraphJobRequest:
        scope = require_project_scope(scope)
        if not revision_id or not chunks_locator or not model_alias:
            raise ValueError("graph job request identities must be nonblank")
        if _DIGEST.fullmatch(extraction_profile_digest) is None:
            raise ValueError("graph extraction profile digest must be canonical")
        sources = tuple(sorted(source_revision_ids or (revision_id,)))
        documents = tuple(sorted(document_revision_ids or sources))
        if revision_id not in sources or not sources or not documents:
            raise ValueError("graph job must bind its complete revision selection")
        material = json.dumps(
            [
                scope.project_id,
                revision_id,
                sources,
                documents,
                chunks_locator,
                extraction_profile_digest,
                model_alias,
            ],
            separators=(",", ":"),
        )
        request_digest = "sha256:" + hashlib.sha256(material.encode()).hexdigest()
        suffix = hashlib.sha256(
            json.dumps([scope.project_id, revision_id, sources], separators=(",", ":")).encode()
        ).hexdigest()[:32]
        snapshot = GraphSnapshot.create(
            snapshot_id=f"grs_{suffix}",
            project_id=scope.project_id,
            source_revision_ids=sources,
            document_revision_ids=documents,
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


@dataclass(frozen=True, slots=True)
class GraphFragmentBatch:
    snapshot_id: str
    batch_index: int
    chunk_ids: tuple[str, ...]
    status: GraphBatchStatus
    attempt: int = 0
    failure_code: str | None = None
    draft: GraphSnapshotDraft | None = None

    def __post_init__(self) -> None:
        if self.batch_index < 0:
            raise ValueError("graph fragment batch index must be nonnegative")
        if not self.chunk_ids:
            raise ValueError("graph fragment batch requires chunk ids")
        # A READY batch's draft may be ``None``: the extractor grounded nothing in
        # this batch (boilerplate chunks, or a relation-less span of a document) and
        # the batch still succeeded, contributing nothing to the merged fragment.
        if self.status is not GraphBatchStatus.READY and self.draft is not None:
            raise ValueError("only a ready graph fragment batch may carry a draft")
