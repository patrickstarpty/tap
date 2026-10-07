"""Bounded durable graph extraction worker."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.application.fragments import (
    assemble_fragment,
    known_entities_from,
    split_batches,
)
from tap.modules.graph.application.jobs import GraphJobStore
from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.jobs import (
    ClaimedGraphJob,
    GraphBatchStatus,
    GraphFragmentBatch,
    GraphJobLeaseLost,
)
from tap.modules.graph.domain.models import GraphSnapshotDraft
from tap.modules.graph.ports.extraction import GraphExtractionPort
from tap.modules.knowledge.domain.documents import ChunkDraft
from tap.modules.knowledge.ports.documents import ArtifactLocator, ArtifactStore
from tap.platform.db.project_scope import require_project_scope
from tap.platform.telemetry import bind_trace, flush_traces, span

_CHUNK_READ_LIMIT = 500


@dataclass(frozen=True, slots=True)
class GraphWorkerRun:
    claimed: int
    ready: int
    failed: int
    lease_lost: int
    partial: int = 0


def _chunk_request_mapping(chunk: ChunkDraft, *, revision_id: str) -> Mapping[str, object]:
    return {
        "sourceRevisionId": revision_id,
        "documentRevisionId": revision_id,
        "chunkId": str(chunk.chunk_id),
        "content": chunk.content,
        "anchor": json.loads(chunk.anchor_json),
        "contentDigest": chunk.chunk_content_hash,
    }


def _namespace_draft(
    draft: GraphSnapshotDraft, *, batch_index: int, known_entity_ids: frozenset[str]
) -> GraphSnapshotDraft:
    """Namespace every id this batch minted so restart-at-``n1``/``e1``/``ev1`` model ids
    cannot collide with another batch's ids for the same document.

    Evidence, edge and provenance ids are always rewritten under the ``b{batch_index}-``
    prefix. Node ids are rewritten the same way, except a node id already present in
    ``known_entity_ids`` (the known entities handed to this batch's request) is an
    intentional reuse of an entity minted by an earlier batch and keeps its id as-is.
    Every reference to a rewritten id (edge endpoints, evidence ids, provenance edge id
    and input-fact ids) is rewritten consistently.
    """

    prefix = f"b{batch_index}-"
    node_id_map = {
        node.node_id: node.node_id if node.node_id in known_entity_ids else prefix + node.node_id
        for node in draft.nodes
    }
    evidence_id_map = {item.evidence_id: prefix + item.evidence_id for item in draft.evidence}
    edge_id_map = {edge.edge_id: prefix + edge.edge_id for edge in draft.edges}
    provenance_id_map = {
        item.provenance_id: prefix + item.provenance_id for item in draft.provenance
    }
    fact_id_map = {**node_id_map, **edge_id_map}

    nodes = tuple(
        replace(
            node,
            node_id=node_id_map[node.node_id],
            evidence_ids=tuple(evidence_id_map[eid] for eid in node.evidence_ids),
        )
        for node in draft.nodes
    )
    evidence = tuple(
        replace(item, evidence_id=evidence_id_map[item.evidence_id]) for item in draft.evidence
    )
    edges = tuple(
        replace(
            edge,
            edge_id=edge_id_map[edge.edge_id],
            source_node_id=node_id_map[edge.source_node_id],
            target_node_id=node_id_map[edge.target_node_id],
            evidence_ids=tuple(evidence_id_map[eid] for eid in edge.evidence_ids),
        )
        for edge in draft.edges
    )
    provenance = tuple(
        replace(
            item,
            provenance_id=provenance_id_map[item.provenance_id],
            edge_id=edge_id_map[item.edge_id],
            input_fact_ids=tuple(fact_id_map[fact_id] for fact_id in item.input_fact_ids),
        )
        for item in draft.provenance
    )
    return replace(draft, nodes=nodes, edges=edges, evidence=evidence, provenance=provenance)


class GraphWorker:
    def __init__(
        self,
        *,
        jobs: GraphJobStore,
        artifacts: ArtifactStore,
        extractor: GraphExtractionPort,
        scope: ProjectScopeContext,
        worker_id: str,
        batch_size: int = 10,
        batch_retries: int = 3,
        backoff_seconds: float = 1.0,
        lease_duration: timedelta = timedelta(seconds=60),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._jobs = jobs
        self._artifacts = artifacts
        self._extractor = extractor
        self._scope = require_project_scope(scope)
        if not worker_id:
            raise ValueError("graph worker identity must be nonblank")
        self._worker_id = worker_id
        if batch_size < 1:
            raise ValueError("graph worker batch size must be positive")
        if batch_retries < 1:
            raise ValueError("graph worker batch retries must be positive")
        self._batch_size = batch_size
        self._batch_retries = batch_retries
        self._backoff_seconds = backoff_seconds
        self._lease_duration = lease_duration
        self._sleep = sleep

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    async def run_once(self, limit: int) -> GraphWorkerRun:
        claims = await self._jobs.claim(
            self._scope,
            worker_id=self._worker_id,
            now=self._now(),
            lease_duration=self._lease_duration,
            limit=limit,
        )
        ready = partial = failed = lease_lost = 0
        for claim in claims:
            with (
                bind_trace(scope=self._scope, job_id=claim.job_id, job_kind="graph_extraction"),
                span("job.graph_extraction", {"tap.job_id": claim.job_id}),
            ):
                try:
                    outcome = await self._run_claim(claim)
                except GraphJobLeaseLost:
                    lease_lost += 1
                    continue
                if outcome == "READY":
                    ready += 1
                elif outcome == "PARTIAL":
                    partial += 1
                else:
                    failed += 1
        if claims:
            await flush_traces()
        return GraphWorkerRun(len(claims), ready, failed, lease_lost, partial)

    async def _run_claim(self, claim: ClaimedGraphJob) -> str:
        """Process one claimed job end to end, never letting a non-lease error escape.

        Any exception other than ``GraphJobLeaseLost`` (a crash reading chunks, a
        database error from the job store, an unexpected error assembling the merged
        fragment, ...) is caught here and turned into a terminal job failure instead
        of propagating out of ``run_once`` -- which has no caller-side try/except and
        would otherwise kill the worker process (and, for a condition that recurs on
        every reclaim, crash-loop it forever, starving every job behind it).
        """
        try:
            return await self._run_claim_body(claim)
        except GraphJobLeaseLost:
            raise
        except Exception as error:
            failure_code = type(error).__name__[:64]
            await self._jobs.fail(self._scope, claim, failure_code=failure_code, now=self._now())
            return "FAILED"

    async def _run_claim_body(self, claim: ClaimedGraphJob) -> str:
        chunks = (await self._artifacts.read_chunks(ArtifactLocator(claim.chunks_locator)))[
            :_CHUNK_READ_LIMIT
        ]
        batches = split_batches(chunks, batch_size=self._batch_size)
        existing = {
            batch.batch_index: batch for batch in await self._jobs.load_batches(self._scope, claim)
        }
        drafts: list[GraphSnapshotDraft] = []
        batch_ready = 0
        batch_failed = 0

        for index, batch_chunks in enumerate(batches):
            existing_batch = existing.get(index)
            chunk_ids = tuple(str(chunk.chunk_id) for chunk in batch_chunks)
            if existing_batch is not None and existing_batch.status is GraphBatchStatus.READY:
                assert existing_batch.draft is not None
                drafts.append(existing_batch.draft)
                batch_ready += 1
            elif existing_batch is not None and existing_batch.status is GraphBatchStatus.FAILED:
                batch_failed += 1
            else:
                draft, batch_failed_here, claim = await self._run_batch(
                    claim,
                    index,
                    batch_chunks,
                    chunk_ids,
                    attempt=existing_batch.attempt if existing_batch is not None else 0,
                    drafts_so_far=drafts,
                )
                if draft is not None:
                    drafts.append(draft)
                    batch_ready += 1
                else:
                    batch_failed += batch_failed_here
            claim = await self._jobs.renew(
                self._scope, claim, now=self._now(), lease_duration=self._lease_duration
            )

        if batch_ready == 0:
            await self._jobs.fail(
                self._scope, claim, failure_code="graph-extraction-failed", now=self._now()
            )
            return "FAILED"
        fragment = assemble_fragment(claim.snapshot, drafts)
        if batch_failed > 0:
            await self._jobs.complete(
                self._scope, claim, fragment, now=self._now(), status="PARTIAL"
            )
            return "PARTIAL"
        await self._jobs.complete(self._scope, claim, fragment, now=self._now(), status="READY")
        return "READY"

    async def _run_batch(
        self,
        claim: ClaimedGraphJob,
        index: int,
        batch_chunks: Sequence[ChunkDraft],
        chunk_ids: tuple[str, ...],
        *,
        attempt: int,
        drafts_so_far: list[GraphSnapshotDraft],
    ) -> tuple[GraphSnapshotDraft | None, int, ClaimedGraphJob]:
        request_chunks = tuple(
            _chunk_request_mapping(chunk, revision_id=claim.revision_id) for chunk in batch_chunks
        )
        while attempt < self._batch_retries:
            attempt += 1
            try:
                request = GraphExtractionRequest(
                    scope=self._scope,
                    snapshot=claim.snapshot,
                    chunks=request_chunks,
                    model_alias=claim.model_alias,
                    idempotency_key=f"{claim.request_digest}:batch:{index}",
                    batch_index=index,
                    document_title=claim.revision_id,
                    known_entities=known_entities_from(drafts_so_far),
                )
                raw_draft = await self._extractor.extract(request)
                known_entity_ids = frozenset(str(entity["id"]) for entity in request.known_entities)
                draft = _namespace_draft(
                    raw_draft, batch_index=index, known_entity_ids=known_entity_ids
                )
            except Exception as error:
                failure_code = type(error).__name__[:64]
                if attempt >= self._batch_retries:
                    await self._jobs.record_batch(
                        self._scope,
                        claim,
                        GraphFragmentBatch(
                            claim.snapshot.snapshot_id,
                            index,
                            chunk_ids,
                            GraphBatchStatus.FAILED,
                            attempt=attempt,
                            failure_code=failure_code,
                        ),
                        now=self._now(),
                    )
                    return None, 1, claim
                await self._jobs.record_batch(
                    self._scope,
                    claim,
                    GraphFragmentBatch(
                        claim.snapshot.snapshot_id,
                        index,
                        chunk_ids,
                        GraphBatchStatus.PENDING,
                        attempt=attempt,
                        failure_code=failure_code,
                    ),
                    now=self._now(),
                )
                # Renew now, not only after the whole batch finishes: a long model
                # timeout plus this attempt's own elapsed time could otherwise outlive
                # the lease before the next renew at the bottom of the batch loop.
                claim = await self._jobs.renew(
                    self._scope, claim, now=self._now(), lease_duration=self._lease_duration
                )
                await self._sleep(self._backoff_seconds * 2 ** (attempt - 1))
            else:
                await self._jobs.record_batch(
                    self._scope,
                    claim,
                    GraphFragmentBatch(
                        claim.snapshot.snapshot_id,
                        index,
                        chunk_ids,
                        GraphBatchStatus.READY,
                        attempt=attempt,
                        draft=draft,
                    ),
                    now=self._now(),
                )
                return draft, 0, claim
        # The retry budget was already exhausted before this call started (e.g.
        # batch_retries was lowered after a previous PENDING attempt was recorded for
        # this batch). Record it as terminally failed instead of looping forever or
        # crashing the worker with an assertion.
        await self._jobs.record_batch(
            self._scope,
            claim,
            GraphFragmentBatch(
                claim.snapshot.snapshot_id,
                index,
                chunk_ids,
                GraphBatchStatus.FAILED,
                attempt=attempt,
                failure_code="graph-batch-retry-limit-exceeded",
            ),
            now=self._now(),
        )
        return None, 1, claim
