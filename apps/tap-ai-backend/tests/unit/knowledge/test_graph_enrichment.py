from __future__ import annotations

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.application.queries import InMemoryGraphStore
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)
from tap.modules.knowledge.application.graph_enrichment import (
    GraphAnswerEnricher,
    GraphContextStatus,
)


def _draft() -> GraphSnapshotDraft:
    evidence = Evidence(
        "evidence-1",
        "snapshot-1",
        "source-revision-1",
        "document-revision-1",
        "chunk-1",
        {"kind": "text", "start": 0, "end": 6},
        "sha256:" + "a" * 64,
    )
    return GraphSnapshotDraft(
        GraphSnapshot.create(
            snapshot_id="snapshot-1",
            project_id=VALIDATION_SCOPE.project_id,
            source_revision_ids=("source-revision-1",),
            document_revision_ids=("document-revision-1",),
        ),
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


@pytest.mark.asyncio
async def test_graph_enrichment_applies_only_selected_revision_evidence():
    store = InMemoryGraphStore()
    await store.publish(VALIDATION_SCOPE, _draft())
    result = await GraphAnswerEnricher(store).enrich(VALIDATION_SCOPE, ("source-revision-1",), "*")
    assert result.status is GraphContextStatus.APPLIED
    assert result.snapshot_id == "snapshot-1"
    assert result.facts[2]["evidence"][0]["sourceRevisionId"] == "source-revision-1"


@pytest.mark.asyncio
async def test_graph_enrichment_falls_back_without_changing_vector_answer_path():
    result = await GraphAnswerEnricher(InMemoryGraphStore()).enrich(
        VALIDATION_SCOPE, ("source-revision-1",), "Policy"
    )
    assert result.status is GraphContextStatus.NOT_READY
    assert result.snapshot_id is None
    assert result.facts == ()
