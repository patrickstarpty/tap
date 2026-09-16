"""Bounded durable graph extraction worker."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.application.jobs import GraphJobStore
from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.ports.extraction import GraphExtractionPort
from tap.modules.knowledge.ports.documents import ArtifactLocator, ArtifactStore
from tap.platform.db.project_scope import require_project_scope


@dataclass(frozen=True, slots=True)
class GraphWorkerRun:
    claimed: int
    ready: int
    failed: int
    lease_lost: int


class GraphWorker:
    def __init__(
        self,
        *,
        jobs: GraphJobStore,
        artifacts: ArtifactStore,
        extractor: GraphExtractionPort,
        scope: ProjectScopeContext,
        worker_id: str,
    ) -> None:
        self._jobs = jobs
        self._artifacts = artifacts
        self._extractor = extractor
        self._scope = require_project_scope(scope)
        if not worker_id:
            raise ValueError("graph worker identity must be nonblank")
        self._worker_id = worker_id

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    async def run_once(self, limit: int) -> GraphWorkerRun:
        claims = await self._jobs.claim(
            self._scope,
            worker_id=self._worker_id,
            now=self._now(),
            lease_duration=timedelta(seconds=60),
            limit=limit,
        )
        ready = failed = lease_lost = 0
        for claim in claims:
            try:
                chunks = await self._artifacts.read_chunks(ArtifactLocator(claim.chunks_locator))
                request = GraphExtractionRequest(
                    scope=self._scope,
                    snapshot=claim.snapshot,
                    chunks=tuple(
                        {
                            "sourceRevisionId": claim.revision_id,
                            "documentRevisionId": claim.revision_id,
                            "chunkId": str(chunk.chunk_id),
                            "content": chunk.content,
                            "anchor": json.loads(chunk.anchor_json),
                            "contentDigest": chunk.chunk_content_hash,
                        }
                        for chunk in chunks[:500]
                    ),
                    model_alias=claim.model_alias,
                    idempotency_key=claim.request_digest,
                )
                draft = await self._extractor.extract(request)
                await self._jobs.complete(self._scope, claim, draft, now=self._now())
                ready += 1
            except Exception:
                try:
                    await self._jobs.fail(
                        self._scope,
                        claim,
                        failure_code="graph-extraction-failed",
                        now=self._now(),
                    )
                    failed += 1
                except Exception:
                    lease_lost += 1
        return GraphWorkerRun(len(claims), ready, failed, lease_lost)
