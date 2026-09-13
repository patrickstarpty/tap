from __future__ import annotations

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.adapters.fake_extraction import DeterministicGraphExtraction
from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.models import GraphSnapshot


@pytest.mark.asyncio
async def test_deterministic_demo_extractor_emits_grounded_bounded_snapshot():
    request = GraphExtractionRequest(
        scope=VALIDATION_SCOPE,
        snapshot=GraphSnapshot.create(
            snapshot_id="snapshot-1",
            project_id=VALIDATION_SCOPE.project_id,
            source_revision_ids=("revision-1",),
            document_revision_ids=("revision-1",),
        ),
        chunks=(
            {
                "sourceRevisionId": "revision-1",
                "documentRevisionId": "revision-1",
                "chunkId": "chunk-1",
                "content": "Claims require evidence.",
                "anchor": {
                    "type": "document",
                    "startOffset": 0,
                    "endOffset": 24,
                    "headingPath": [],
                },
                "contentDigest": "sha256:" + "a" * 64,
            },
        ),
        model_alias="tapper-chat",
        idempotency_key="graph:revision-1",
    )
    graph = await DeterministicGraphExtraction().extract(request)
    assert len(graph.nodes) == 2
    assert graph.edges[0].evidence_ids == (graph.evidence[0].evidence_id,)
    assert graph.evidence[0].content_digest == "sha256:" + "a" * 64
