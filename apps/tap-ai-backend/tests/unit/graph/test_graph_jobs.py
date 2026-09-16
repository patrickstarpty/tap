from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.application.jobs import InMemoryGraphJobStore
from tap.modules.graph.domain.jobs import GraphJobLeaseLost, GraphJobRequest, GraphJobStatus
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)

NOW = datetime(2026, 9, 13, 9, 0, 0)


def _request() -> GraphJobRequest:
    return GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="revision-1",
        chunks_locator="art1.chunks",
        extraction_profile_digest="sha256:" + "1" * 64,
        model_alias="tapper-chat",
    )


def _draft(snapshot: GraphSnapshot) -> GraphSnapshotDraft:
    evidence = Evidence(
        "evidence-1",
        snapshot.snapshot_id,
        "revision-1",
        "revision-1",
        "chunk-1",
        {"kind": "text", "start": 0, "end": 5},
        "sha256:" + "a" * 64,
    )
    return GraphSnapshotDraft(
        snapshot,
        (
            GraphNode(
                "node-1",
                snapshot.snapshot_id,
                "Policy",
                "ENTITY",
                "policy",
                ("evidence-1",),
            ),
            GraphNode("node-2", snapshot.snapshot_id, "Claim", "CONCEPT", "claim"),
        ),
        (
            GraphEdge(
                "edge-1",
                snapshot.snapshot_id,
                "node-1",
                "node-2",
                "GOVERNS",
                RelationOrigin.EXTRACTED,
                1.0,
                ("evidence-1",),
            ),
        ),
        (evidence,),
        (),
    )


@pytest.mark.asyncio
async def test_duplicate_request_is_one_durable_job() -> None:
    jobs = InMemoryGraphJobStore()

    first = await jobs.request(VALIDATION_SCOPE, _request(), now=NOW)
    replay = await jobs.request(VALIDATION_SCOPE, _request(), now=NOW + timedelta(seconds=1))

    assert replay == first
    assert len(await jobs.list_jobs(VALIDATION_SCOPE)) == 1


@pytest.mark.asyncio
async def test_expired_owner_is_fenced_after_worker_restart() -> None:
    jobs = InMemoryGraphJobStore()
    requested = await jobs.request(VALIDATION_SCOPE, _request(), now=NOW)
    first = (
        await jobs.claim(
            VALIDATION_SCOPE,
            worker_id="worker-1",
            now=NOW,
            lease_duration=timedelta(seconds=10),
            limit=1,
        )
    )[0]
    assert (
        await jobs.claim(
            VALIDATION_SCOPE,
            worker_id="worker-2",
            now=NOW + timedelta(seconds=5),
            lease_duration=timedelta(seconds=10),
            limit=1,
        )
        == ()
    )

    recovered = (
        await jobs.claim(
            VALIDATION_SCOPE,
            worker_id="worker-2",
            now=NOW + timedelta(seconds=11),
            lease_duration=timedelta(seconds=10),
            limit=1,
        )
    )[0]
    assert recovered.lease_token != first.lease_token
    with pytest.raises(GraphJobLeaseLost):
        await jobs.complete(
            VALIDATION_SCOPE,
            first,
            _draft(requested.snapshot),
            now=NOW + timedelta(seconds=12),
        )

    ready = await jobs.complete(
        VALIDATION_SCOPE,
        recovered,
        _draft(requested.snapshot),
        now=NOW + timedelta(seconds=12),
    )
    assert ready.status is GraphJobStatus.READY
    assert (await jobs.active_snapshot(VALIDATION_SCOPE, ("revision-1",))).status == "READY"


@pytest.mark.asyncio
async def test_graph_failure_is_terminal_without_removing_document_identity() -> None:
    jobs = InMemoryGraphJobStore()
    requested = await jobs.request(VALIDATION_SCOPE, _request(), now=NOW)
    claimed = (
        await jobs.claim(
            VALIDATION_SCOPE,
            worker_id="worker-1",
            now=NOW,
            lease_duration=timedelta(seconds=10),
            limit=1,
        )
    )[0]

    failed = await jobs.fail(
        VALIDATION_SCOPE,
        claimed,
        failure_code="graph-extraction-failed",
        now=NOW + timedelta(seconds=1),
    )

    assert failed.status is GraphJobStatus.FAILED
    assert failed.revision_id == requested.revision_id
    assert failed.snapshot.status == "FAILED"
