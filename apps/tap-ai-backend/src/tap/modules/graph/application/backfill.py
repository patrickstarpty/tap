"""Enqueue graph extraction for published revisions the graph never covered.

Graph jobs are normally created inside the document READY transaction, so a
revision published before graph extraction existed, or whose fragment was
extracted under an older extraction profile (prompt or schema changed), would
otherwise never reach the Project graph until an operator runs `graph rebuild`.
The graph worker runs this once at startup: it requests a job for every
published revision that has none, requeues every fragment whose profile is
stale, and -- when nothing needs extracting but current fragments exist --
requests one Project merge so an upgraded database is not left with an empty
graph. Every step is idempotent, so concurrent worker starts are harmless.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.domain.jobs import GraphJobRequest, GraphJobStatus
from tap.platform.db.project_scope import require_project_scope

BACKFILL_MERGE_REASON = "graph-backfill"


@dataclass(frozen=True, slots=True)
class GraphRevisionJobState:
    revision_id: str
    status: GraphJobStatus
    extraction_profile_digest: str
    lease_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class GraphBackfillResult:
    requested: int
    requeued: int
    skipped: int
    merge_requested: bool


class GraphBackfillJobs(Protocol):
    async def revision_job_states(
        self, scope: ProjectScopeContext
    ) -> tuple[GraphRevisionJobState, ...]: ...

    async def request(
        self, scope: ProjectScopeContext, request: GraphJobRequest, *, now: datetime
    ) -> object: ...

    async def reset_for_profile(
        self,
        scope: ProjectScopeContext,
        revision_id: str,
        *,
        extraction_profile_digest: str,
        model_alias: str,
        now: datetime,
        only_if_stale: bool,
    ) -> object: ...


class GraphBackfillMergeQueue(Protocol):
    async def request(self, scope: ProjectScopeContext, *, reason: str, now: datetime) -> None: ...


async def backfill_graph_jobs(
    *,
    scope: ProjectScopeContext,
    published: Mapping[str, str],
    jobs: GraphBackfillJobs,
    merge_queue: GraphBackfillMergeQueue,
    extraction_profile_digest: str,
    model_alias: str,
    now: datetime,
) -> GraphBackfillResult:
    """Bring every published revision (``revision id -> chunks locator``) under
    the current extraction profile; see the module docstring."""

    scope = require_project_scope(scope)
    states = {state.revision_id: state for state in await jobs.revision_job_states(scope)}
    requested = requeued = skipped = 0
    extracting = False
    current_fragment = False
    for revision_id, locator in sorted(published.items()):
        state = states.get(revision_id)
        if state is None:
            await jobs.request(
                scope,
                GraphJobRequest.create(
                    scope=scope,
                    revision_id=revision_id,
                    chunks_locator=locator,
                    extraction_profile_digest=extraction_profile_digest,
                    model_alias=model_alias,
                ),
                now=now,
            )
            requested += 1
            continue
        if state.extraction_profile_digest == extraction_profile_digest:
            if state.status in (GraphJobStatus.PENDING, GraphJobStatus.RUNNING):
                extracting = True
            elif state.status is GraphJobStatus.READY:
                current_fragment = True
            # A FAILED job under the current profile stays failed: retrying it on
            # every restart would loop; `graph rebuild` or a batch retry is explicit.
            continue
        if (
            state.status is GraphJobStatus.RUNNING
            and state.lease_expires_at is not None
            and state.lease_expires_at > now
        ):
            # Another worker holds it right now; its next restart requeues it.
            skipped += 1
            continue
        try:
            await jobs.reset_for_profile(
                scope,
                revision_id,
                extraction_profile_digest=extraction_profile_digest,
                model_alias=model_alias,
                now=now,
                only_if_stale=True,
            )
        except ValueError:
            # Claimed between the read above and the reset, or gone: skip it.
            skipped += 1
            continue
        requeued += 1
    merge_requested = False
    if not (requested or requeued or extracting) and current_fragment:
        # Fragments publishing later request their own merges; here nothing will,
        # so request one. An unchanged fragment digest makes the merge a no-op.
        await merge_queue.request(scope, reason=BACKFILL_MERGE_REASON, now=now)
        merge_requested = True
    return GraphBackfillResult(requested, requeued, skipped, merge_requested)
