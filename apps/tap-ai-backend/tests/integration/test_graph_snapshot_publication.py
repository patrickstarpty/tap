import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.application.extraction import GraphExtractionService
from tap.modules.graph.application.queries import InMemoryGraphStore
from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.jobs import GraphJobRequest
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)


class Extractor:
    def __init__(self, draft):
        self.draft = draft
        self.calls = 0

    async def extract(self, request):
        self.calls += 1
        return self.draft


@pytest.mark.asyncio
async def test_duplicate_worker_delivery_reuses_the_same_published_snapshot():
    store = InMemoryGraphStore()
    snapshot = GraphSnapshot.create(
        snapshot_id="snapshot-1",
        project_id=VALIDATION_SCOPE.project_id,
        source_revision_ids=("source-revision-1",),
        document_revision_ids=("document-revision-1",),
    )
    evidence = Evidence(
        "evidence-1",
        "snapshot-1",
        "source-revision-1",
        "document-revision-1",
        "chunk-1",
        {"kind": "text", "start": 0, "end": 5},
        "sha256:" + "a" * 64,
    )
    draft = GraphSnapshotDraft(
        snapshot,
        (
            GraphNode("node-1", "snapshot-1", "Policy", "ENTITY", "policy"),
            GraphNode("node-2", "snapshot-1", "Claim", "ENTITY", "claim"),
        ),
        (
            GraphEdge(
                "edge-1",
                "snapshot-1",
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
    extractor = Extractor(draft)
    service = GraphExtractionService(store=store, extractor=extractor)
    request = GraphExtractionRequest(
        scope=VALIDATION_SCOPE,
        snapshot=GraphSnapshot.create(
            snapshot_id="snapshot-1",
            project_id=VALIDATION_SCOPE.project_id,
            source_revision_ids=("source-revision-1",),
            document_revision_ids=("document-revision-1",),
        ),
        chunks=({},),
        model_alias="tapper-graph",
        idempotency_key="graph:snapshot-1",
    )
    first = await service.execute(request)
    replay = await service.execute(request)
    assert first == replay
    assert extractor.calls == 1


@pytest.mark.asyncio
async def test_active_snapshot_binds_the_complete_revision_selection_digest():
    store = InMemoryGraphStore()
    snapshot = GraphSnapshot.create(
        snapshot_id="snapshot-selection",
        project_id=VALIDATION_SCOPE.project_id,
        source_revision_ids=("source-revision-2", "source-revision-1"),
        document_revision_ids=("document-revision-2", "document-revision-1"),
    )
    await store.publish(
        VALIDATION_SCOPE,
        GraphSnapshotDraft(
            snapshot,
            (
                GraphNode(
                    "node-selection", "snapshot-selection", "Selection", "ENTITY", "selection"
                ),
            ),
            (),
            (),
            (),
        ),
    )

    active = await store.active_snapshot(
        VALIDATION_SCOPE,
        ("source-revision-1", "source-revision-2"),
    )

    assert active is not None
    assert active.source_revision_ids == ("source-revision-1", "source-revision-2")
    assert active.document_revision_ids == ("document-revision-1", "document-revision-2")
    assert await store.active_snapshot(VALIDATION_SCOPE, ("source-revision-1",)) is None


def test_graph_job_snapshot_is_created_for_the_complete_normalized_selection():
    request = GraphJobRequest.create(
        scope=VALIDATION_SCOPE,
        revision_id="source-revision-2",
        source_revision_ids=("source-revision-2", "source-revision-1"),
        document_revision_ids=("document-revision-2", "document-revision-1"),
        chunks_locator="blob://chunks/revision-2.json",
        extraction_profile_digest="sha256:" + "b" * 64,
        model_alias="tapper-graph",
    )

    assert request.snapshot.source_revision_ids == (
        "source-revision-1",
        "source-revision-2",
    )
    assert request.snapshot.document_revision_ids == (
        "document-revision-1",
        "document-revision-2",
    )
