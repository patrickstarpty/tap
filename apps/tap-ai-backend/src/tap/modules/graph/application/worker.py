"""Bounded durable graph extraction worker."""

from __future__ import annotations

import asyncio
import json
import logging
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
from tap.modules.graph.domain.extraction import GraphExtractionRequest, GraphExtractionTruncated
from tap.modules.graph.domain.jobs import (
    ClaimedGraphJob,
    GraphBatchStatus,
    GraphFragmentBatch,
    GraphJobLeaseLost,
)
from tap.modules.graph.domain.models import GraphNode, GraphSnapshotDraft
from tap.modules.graph.domain.vocabulary import NODE_ALIAS_MAX
from tap.modules.graph.ports.extraction import GraphExtractionPort
from tap.modules.knowledge.domain.documents import ChunkDraft
from tap.modules.knowledge.ports.documents import ArtifactLocator, ArtifactStore
from tap.platform.db.project_scope import require_project_scope
from tap.platform.telemetry import bind_trace, flush_traces, span

logger = logging.getLogger(__name__)

# A READY batch whose output was truncated, split in two, and one half truncated
# again: the other half's facts are kept and the job counts the batch as partial.
HALF_TRUNCATED = "graph-batch-half-truncated"


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
    draft: GraphSnapshotDraft, *, prefix: str, known_entity_ids: frozenset[str]
) -> GraphSnapshotDraft:
    """Namespace every id this batch minted so restart-at-``n1``/``e1``/``ev1`` model ids
    cannot collide with another batch's ids for the same document.

    Evidence, edge and provenance ids are always rewritten under ``prefix``
    (``b{batch_index}-``, or ``b{batch_index}h{half}-`` for the halves of a split
    batch). Node ids are rewritten the same way, except a node id already present in
    ``known_entity_ids`` (the known entities handed to this batch's request) is an
    intentional reuse of an entity minted by an earlier batch and keeps its id as-is.
    Every reference to a rewritten id (edge endpoints, evidence ids, provenance edge id
    and input-fact ids) is rewritten consistently.
    """

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


def _combine_halves(drafts: Sequence[GraphSnapshotDraft]) -> GraphSnapshotDraft | None:
    """One batch draft from the namespaced drafts of its two halves.

    Their minted ids differ by prefix; a node id both halves carry is a known
    entity they both referenced and is kept once with its evidence and aliases
    unioned. ``None`` when neither half grounded anything.
    """

    if not drafts:
        return None
    nodes: dict[str, GraphNode] = {}
    for draft in drafts:
        for node in draft.nodes:
            existing = nodes.get(node.node_id)
            if existing is None:
                nodes[node.node_id] = node
                continue
            nodes[node.node_id] = replace(
                existing,
                evidence_ids=tuple(dict.fromkeys((*existing.evidence_ids, *node.evidence_ids))),
                aliases=tuple(dict.fromkeys((*existing.aliases, *node.aliases)))[:NODE_ALIAS_MAX],
            )
    return GraphSnapshotDraft(
        drafts[0].snapshot,
        tuple(nodes.values()),
        tuple(edge for draft in drafts for edge in draft.edges),
        tuple(item for draft in drafts for item in draft.evidence),
        tuple(item for draft in drafts for item in draft.provenance),
    )


class GraphWorker:
    def __init__(
        self,
        *,
        jobs: GraphJobStore,
        artifacts: ArtifactStore,
        extractor: GraphExtractionPort,
        scope: ProjectScopeContext,
        worker_id: str,
        batch_size: int = 100,
        batch_chars: int | None = 3000,
        batch_retries: int = 3,
        backoff_seconds: float = 1.0,
        lease_duration: timedelta = timedelta(seconds=60),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        concurrency: int = 1,
        claim_interval_seconds: float = 1.0,
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
        if batch_chars is not None and batch_chars < 1:
            raise ValueError("graph worker batch character budget must be positive")
        if batch_retries < 1:
            raise ValueError("graph worker batch retries must be positive")
        if type(concurrency) is not int or concurrency < 1:
            raise ValueError("graph worker concurrency must be positive")
        if claim_interval_seconds <= 0:
            raise ValueError("graph worker claim interval must be positive")
        self._claim_interval_seconds = claim_interval_seconds
        self._draining = False
        self._batch_size = batch_size
        self._batch_chars = batch_chars
        self._concurrency = concurrency
        self._batch_retries = batch_retries
        self._backoff_seconds = backoff_seconds
        self._lease_duration = lease_duration
        self._sleep = sleep

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def stop_claiming(self) -> None:
        """Stop claiming new jobs (process shutdown); running jobs keep going
        until they finish or the caller cancels the pass, which releases them."""

        self._draining = True

    async def run_once(self, limit: int) -> GraphWorkerRun:
        """Claim and process up to ``limit`` jobs, at most ``concurrency`` at a time.

        A job is claimed only when a slot is free, so no claimed job waits idle
        while its lease runs down. Claiming goes through the job store's lease
        (``FOR UPDATE SKIP LOCKED`` in MySQL), so concurrent slots and concurrent
        worker processes never hold the same job, and a job's batches run
        sequentially inside its one slot (later batches reuse the entities earlier
        batches minted) -- no batch is ever extracted twice at once.

        While a slot stays free the loop keeps claiming every
        ``claim_interval_seconds``, not only when a running job finishes: a claim
        that found fewer jobs than free slots must not leave those slots idle for
        the rest of the pass while jobs arrive or a crashed worker's lease expires.
        """

        outcomes: dict[str, int] = {}
        claimed_total = 0
        running: set[asyncio.Task[str]] = set()
        claim_error: BaseException | None = None
        try:
            while True:
                free = min(self._concurrency - len(running), limit - claimed_total)
                if free > 0 and claim_error is None and not self._draining:
                    try:
                        claims = await self._jobs.claim(
                            self._scope,
                            worker_id=self._worker_id,
                            now=self._now(),
                            lease_duration=self._lease_duration,
                            limit=free,
                        )
                    except Exception as error:
                        # Let the jobs already running finish (their leases are
                        # live and their batches are being recorded), then surface
                        # the claim failure to the caller as before.
                        claim_error = error
                        claims = ()
                    claimed_total += len(claims)
                    running.update(
                        asyncio.create_task(self._process_claim(claim)) for claim in claims
                    )
                if not running:
                    break
                slots_free = (
                    claim_error is None
                    and not self._draining
                    and min(self._concurrency - len(running), limit - claimed_total) > 0
                )
                done, running = await asyncio.wait(
                    running,
                    timeout=self._claim_interval_seconds if slots_free else None,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    outcome = task.result()
                    outcomes[outcome] = outcomes.get(outcome, 0) + 1
        finally:
            for task in running:
                task.cancel()
            if running:
                await asyncio.gather(*running, return_exceptions=True)
            if claimed_total:
                await flush_traces()
        if claim_error is not None:
            raise claim_error
        return GraphWorkerRun(
            claimed_total,
            outcomes.get("READY", 0),
            outcomes.get("FAILED", 0),
            outcomes.get("LEASE_LOST", 0),
            outcomes.get("PARTIAL", 0),
        )

    async def _process_claim(self, claim: ClaimedGraphJob) -> str:
        with (
            bind_trace(scope=self._scope, job_id=claim.job_id, job_kind="graph_extraction"),
            span("job.graph_extraction", {"tap.job_id": claim.job_id}),
        ):
            try:
                return await self._run_claim(claim)
            except GraphJobLeaseLost:
                return "LEASE_LOST"
            except asyncio.CancelledError:
                await self._release(claim)
                raise

    async def _release(self, claim: ClaimedGraphJob) -> None:
        """Give a cancelled job back to PENDING; on failure its lease just expires."""
        try:
            released = await self._jobs.release(self._scope, claim, now=self._now())
        except Exception:
            logger.warning(
                "graph job release failed: job_kind=graph_extraction job_id=%s",
                claim.job_id,
                exc_info=True,
            )
            return
        logger.info("graph job released on shutdown: job_id=%s released=%s", claim.job_id, released)

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
            try:
                await self._jobs.fail(
                    self._scope, claim, failure_code=failure_code, now=self._now()
                )
            except GraphJobLeaseLost:
                raise
            except Exception as fail_error:
                # ``fail`` itself failing (a database error, a transport blip, ...)
                # must not escape and crash the worker process either. Treat it the
                # same way a genuinely lost lease is treated: stop working this claim
                # and let a later reclaim retry it, instead of propagating out of
                # ``run_once`` (which has no caller-side try/except for this).
                with span(
                    "job.graph_extraction.fail_error",
                    {
                        "tap.job_id": claim.job_id,
                        "tap.error.type": type(fail_error).__name__,
                    },
                ):
                    pass
                raise GraphJobLeaseLost(claim.job_id) from fail_error
            return "FAILED"

    async def _run_claim_body(self, claim: ClaimedGraphJob) -> str:
        # Every chunk is extracted: each model call carries at most ``batch_chars``
        # characters (or one oversized chunk) and at most ``batch_size`` chunks, and
        # ingestion already bounds a document's chunk count
        # (``MAX_CHUNKS_PER_DOCUMENT``), so no per-document read cap is needed.
        chunks = await self._artifacts.read_chunks(ArtifactLocator(claim.chunks_locator))
        batches = split_batches(chunks, batch_size=self._batch_size, max_chars=self._batch_chars)
        # Jobs claimed without a resolvable document record keep the revision id as
        # their title, which is what every job carried before filenames were resolved.
        document_title = claim.document_title.strip() or claim.revision_id
        existing = {
            batch.batch_index: batch for batch in await self._jobs.load_batches(self._scope, claim)
        }
        drafts: list[GraphSnapshotDraft] = []
        batch_ready = 0
        batch_failed = 0

        for index, batch_chunks in enumerate(batches):
            existing_batch = existing.get(index)
            chunk_ids = tuple(str(chunk.chunk_id) for chunk in batch_chunks)
            # A stored batch only covers *this* batch if its chunk ids still match --
            # otherwise the document's chunks were re-split differently since that
            # batch was recorded (e.g. batch_size changed), and reusing it by index
            # alone would silently publish facts for the wrong chunks. Recompute.
            if existing_batch is not None and existing_batch.chunk_ids != chunk_ids:
                existing_batch = None
            if existing_batch is not None and existing_batch.status is GraphBatchStatus.READY:
                if existing_batch.draft is not None:
                    drafts.append(existing_batch.draft)
                batch_ready += 1
                if existing_batch.failure_code == HALF_TRUNCATED:
                    batch_failed += 1
            elif existing_batch is not None and existing_batch.status is GraphBatchStatus.FAILED:
                batch_failed += 1
            else:
                draft, succeeded, batch_failed_here, claim = await self._run_batch(
                    claim,
                    index,
                    batch_chunks,
                    chunk_ids,
                    attempt=existing_batch.attempt if existing_batch is not None else 0,
                    drafts_so_far=drafts,
                    document_title=document_title,
                )
                if succeeded:
                    if draft is not None:
                        drafts.append(draft)
                    batch_ready += 1
                batch_failed += batch_failed_here
            claim = await self._jobs.renew(
                self._scope, claim, now=self._now(), lease_duration=self._lease_duration
            )

        if batch_ready == 0:
            await self._jobs.fail(
                self._scope, claim, failure_code="graph-extraction-failed", now=self._now()
            )
            return "FAILED"
        fragment = assemble_fragment(claim.snapshot, drafts, document_title=document_title)
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
        document_title: str,
    ) -> tuple[GraphSnapshotDraft | None, bool, int, ClaimedGraphJob]:
        request_chunks = tuple(
            _chunk_request_mapping(chunk, revision_id=claim.revision_id) for chunk in batch_chunks
        )
        batch_text = "\n".join(chunk.content for chunk in batch_chunks)
        # Once the whole batch has truncated, a retry (after a transient error in a
        # half) goes straight to the halves: the whole batch would truncate again.
        split = False
        while attempt < self._batch_retries:
            attempt += 1
            lost_halves = 0
            try:
                try:
                    if split:
                        raise GraphExtractionTruncated()
                    draft = await self._extract(
                        claim,
                        index,
                        request_chunks,
                        batch_text,
                        drafts_so_far=drafts_so_far,
                        document_title=document_title,
                        idempotency_key=f"{claim.request_digest}:batch:{index}",
                        prefix=f"b{index}-",
                    )
                except GraphExtractionTruncated:
                    if len(batch_chunks) < 2:
                        raise
                    split = True
                    draft, lost_halves, claim = await self._extract_halves(
                        claim,
                        index,
                        batch_chunks,
                        request_chunks,
                        drafts_so_far=drafts_so_far,
                        document_title=document_title,
                    )
            except Exception as error:
                failure_code = type(error).__name__[:64]
                # A truncated output repeats on the same input, so a batch still
                # truncated after its one split fails now instead of retrying.
                if attempt >= self._batch_retries or isinstance(error, GraphExtractionTruncated):
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
                    return None, False, 1, claim
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
                backoff = self._backoff_seconds * 2 ** (attempt - 1)
                # Never sleep for longer than the lease we just renewed: the
                # extra margin keeps a renew (and any bookkeeping around it)
                # inside the lease window instead of racing its expiry.
                capped_backoff = min(backoff, max(self._lease_duration.total_seconds() - 1.0, 0.0))
                await self._sleep(capped_backoff)
                # Renew again after waking up, not only before sleeping: the cap
                # above only bounds the sleep itself, but the next attempt's own
                # extract() call can still take long enough that sleep + call
                # together outlive the lease renewed before the sleep. Fence on
                # lease loss here too, same as every other renew in this method.
                claim = await self._jobs.renew(
                    self._scope, claim, now=self._now(), lease_duration=self._lease_duration
                )
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
                        failure_code=HALF_TRUNCATED if lost_halves else None,
                        draft=draft,
                    ),
                    now=self._now(),
                )
                return draft, True, 1 if lost_halves else 0, claim
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
        return None, False, 1, claim

    async def _extract(
        self,
        claim: ClaimedGraphJob,
        index: int,
        request_chunks: tuple[Mapping[str, object], ...],
        text: str,
        *,
        drafts_so_far: Sequence[GraphSnapshotDraft],
        document_title: str,
        idempotency_key: str,
        prefix: str,
    ) -> GraphSnapshotDraft | None:
        request = GraphExtractionRequest(
            scope=self._scope,
            snapshot=claim.snapshot,
            chunks=request_chunks,
            model_alias=claim.model_alias,
            idempotency_key=idempotency_key,
            batch_index=index,
            document_title=document_title,
            known_entities=known_entities_from(drafts_so_far, text=text),
        )
        raw_draft = await self._extractor.extract(request)
        # ``None`` means the extractor grounded nothing in this batch (all
        # boilerplate, or no relation sentences): a successful batch that simply
        # contributes nothing, not a failure -- do not namespace or retry it.
        if raw_draft is None:
            return None
        known_entity_ids = frozenset(str(entity["id"]) for entity in request.known_entities)
        return _namespace_draft(raw_draft, prefix=prefix, known_entity_ids=known_entity_ids)

    async def _extract_halves(
        self,
        claim: ClaimedGraphJob,
        index: int,
        batch_chunks: Sequence[ChunkDraft],
        request_chunks: tuple[Mapping[str, object], ...],
        *,
        drafts_so_far: Sequence[GraphSnapshotDraft],
        document_title: str,
    ) -> tuple[GraphSnapshotDraft | None, int, ClaimedGraphJob]:
        """Extract a truncated batch as two halves, once (no further splitting).

        The second half also reuses entities the first half minted. A half that
        truncates again is lost and counted; the other half's facts are kept. Any
        other error, or both halves truncating, propagates to the batch's retry
        handling. The lease is renewed before each half, since each is a full
        extraction call of its own.
        """

        middle = len(batch_chunks) // 2
        drafts: list[GraphSnapshotDraft] = []
        lost = 0
        for half, (start, end) in enumerate(((0, middle), (middle, len(batch_chunks)))):
            claim = await self._jobs.renew(
                self._scope, claim, now=self._now(), lease_duration=self._lease_duration
            )
            try:
                draft = await self._extract(
                    claim,
                    index,
                    request_chunks[start:end],
                    "\n".join(chunk.content for chunk in batch_chunks[start:end]),
                    drafts_so_far=(*drafts_so_far, *drafts),
                    document_title=document_title,
                    idempotency_key=f"{claim.request_digest}:batch:{index}:half:{half}",
                    prefix=f"b{index}h{half}-",
                )
            except GraphExtractionTruncated:
                lost += 1
                if lost == 2:
                    raise
                continue
            if draft is not None:
                drafts.append(draft)
        return _combine_halves(drafts), lost, claim
