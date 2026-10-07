"""Bounded project graph merge worker: claim one project's due merge, replay
every currently-ready/partial fragment into a new merged version, and clear
the queue -- or back off on failure, or abandon cleanly on a lost lease."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta, timezone

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.application.merge_jobs import (
    MergeClaim,
    MergeInputsPort,
    ProjectMergeLeaseLost,
    ProjectMergeQueue,
)
from tap.modules.graph.application.merger import ProjectGraphMerger
from tap.modules.graph.domain.project import FragmentRecord
from tap.platform.db.project_scope import require_project_scope
from tap.platform.telemetry import bind_trace, span

# Labels are embedded in bounded chunks -- not all at once -- so the merge
# lease is renewed between chunks: with thousands of distinct labels and a
# one-call-per-text embedding backend, embedding everything before the next
# renew would let the lease expire mid-embedding, losing the claim silently
# every cycle without ever publishing or recording a failure.
_DEFAULT_EMBED_CHUNK_SIZE = 64


class ProjectGraphMergeWorker:
    def __init__(
        self,
        *,
        queue: ProjectMergeQueue,
        inputs: MergeInputsPort,
        merger: ProjectGraphMerger,
        scope: ProjectScopeContext,
        worker_id: str,
        lease_duration: timedelta = timedelta(seconds=300),
        embeddings: Callable[[Sequence[str]], Awaitable[Sequence[tuple[float, ...]]]] | None = None,
        embed_chunk_size: int = _DEFAULT_EMBED_CHUNK_SIZE,
    ) -> None:
        self._queue = queue
        self._inputs = inputs
        self._merger = merger
        self._scope = require_project_scope(scope)
        if not worker_id:
            raise ValueError("project merge worker identity must be nonblank")
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._embeddings = embeddings
        if embed_chunk_size < 1:
            raise ValueError("embed chunk size must be positive")
        self._embed_chunk_size = embed_chunk_size

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    async def run_once(self, limit: int) -> int:
        del limit
        claim = await self._queue.claim(
            self._scope,
            worker_id=self._worker_id,
            now=self._now(),
            lease_duration=self._lease_duration,
        )
        if claim is None:
            return 0
        with (
            bind_trace(scope=self._scope, job_kind="project_merge"),
            span("job.project_merge"),
        ):
            try:
                fragments = await self._inputs.load_fragments(self._scope)
                claim = await self._queue.renew(
                    self._scope, claim, now=self._now(), lease_duration=self._lease_duration
                )
                embeddings: dict[tuple[str, str], tuple[float, ...]] | None = None
                if self._embeddings:
                    claim, embeddings = await self._embed_labels(claim, fragments)
                draft = self._merger.merge(self._scope, fragments, embeddings=embeddings)
                await self._queue.complete(self._scope, claim, draft, now=self._now())
            except ProjectMergeLeaseLost:
                pass
            except Exception as error:
                failure_code = type(error).__name__[:64]
                try:
                    await self._queue.fail(
                        self._scope, claim, failure_code=failure_code, now=self._now()
                    )
                except ProjectMergeLeaseLost:
                    # The lease was already lost (e.g. it expired and another
                    # worker claimed it) by the time this failure was being
                    # recorded -- nothing left to fence; the new owner's own
                    # claim governs retry/backoff from here.
                    pass
        return 1

    async def _embed_labels(
        self, claim: MergeClaim, fragments: Sequence[FragmentRecord]
    ) -> tuple[MergeClaim, dict[tuple[str, str], tuple[float, ...]]]:
        assert self._embeddings is not None
        keys_by_label: dict[str, list[tuple[str, str]]] = {}
        for record in fragments:
            snapshot_id = record.draft.snapshot.snapshot_id
            for node in record.draft.nodes:
                keys_by_label.setdefault(node.label, []).append((snapshot_id, node.node_id))
        unique_labels = list(keys_by_label)
        if not unique_labels:
            return claim, {}
        vector_by_label: dict[str, tuple[float, ...]] = {}
        for start in range(0, len(unique_labels), self._embed_chunk_size):
            chunk = unique_labels[start : start + self._embed_chunk_size]
            vectors = await self._embeddings(chunk)
            if len(vectors) != len(chunk):
                raise ValueError("embedding backend returned a mismatched vector count")
            vector_by_label.update(zip(chunk, vectors))
            claim = await self._queue.renew(
                self._scope, claim, now=self._now(), lease_duration=self._lease_duration
            )
        embeddings: dict[tuple[str, str], tuple[float, ...]] = {}
        for label, keys in keys_by_label.items():
            vector = vector_by_label[label]
            for key in keys:
                embeddings[key] = vector
        return claim, embeddings
