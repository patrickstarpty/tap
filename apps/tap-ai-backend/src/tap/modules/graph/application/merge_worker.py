"""Bounded project graph merge worker: claim one project's due merge, replay
every currently-ready/partial fragment into a new merged version, and clear
the queue -- or back off on failure, or abandon cleanly on a lost lease."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta, timezone

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.application.merge_jobs import (
    MergeInputsPort,
    ProjectMergeLeaseLost,
    ProjectMergeQueue,
)
from tap.modules.graph.application.merger import ProjectGraphMerger
from tap.modules.graph.domain.project import FragmentRecord
from tap.platform.db.project_scope import require_project_scope
from tap.platform.telemetry import bind_trace, span


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
        embeddings: Callable[[Sequence[str]], Awaitable[Sequence[tuple[float, ...]]]]
        | None = None,
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
                embeddings = await self._embed_labels(fragments) if self._embeddings else None
                draft = self._merger.merge(self._scope, fragments, embeddings=embeddings)
                await self._queue.complete(self._scope, claim, draft, now=self._now())
            except ProjectMergeLeaseLost:
                pass
            except Exception as error:
                failure_code = type(error).__name__[:64]
                await self._queue.fail(
                    self._scope, claim, failure_code=failure_code, now=self._now()
                )
        return 1

    async def _embed_labels(
        self, fragments: Sequence[FragmentRecord]
    ) -> dict[tuple[str, str], tuple[float, ...]]:
        assert self._embeddings is not None
        keys: list[tuple[str, str]] = []
        texts: list[str] = []
        for record in fragments:
            snapshot_id = record.draft.snapshot.snapshot_id
            for node in record.draft.nodes:
                keys.append((snapshot_id, node.node_id))
                texts.append(node.label)
        if not texts:
            return {}
        vectors = await self._embeddings(texts)
        return dict(zip(keys, vectors))
