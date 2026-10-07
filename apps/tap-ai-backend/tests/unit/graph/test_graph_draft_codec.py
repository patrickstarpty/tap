"""serialize_draft / deserialize_draft must round-trip every fact kind bit-for-bit."""

from __future__ import annotations

import json

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.adapters.mysql_jobs import deserialize_draft, serialize_draft
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    InferenceProvenance,
    RelationOrigin,
)


def _full_draft() -> GraphSnapshotDraft:
    snapshot = GraphSnapshot.create(
        snapshot_id="snapshot-codec-1",
        project_id=VALIDATION_SCOPE.project_id,
        source_revision_ids=("source-revision-1", "source-revision-2"),
        document_revision_ids=("document-revision-1", "document-revision-2"),
    )
    text_evidence = Evidence(
        "evidence-text-1",
        snapshot.snapshot_id,
        "source-revision-1",
        "document-revision-1",
        "chunk-1",
        {"kind": "text", "start": 0, "end": 5},
        "sha256:" + "a" * 64,
    )
    page_evidence = Evidence(
        "evidence-page-1",
        snapshot.snapshot_id,
        "source-revision-2",
        "document-revision-2",
        "chunk-2",
        {"kind": "page", "page": 7},
        "sha256:" + "b" * 64,
    )
    nodes = (
        GraphNode(
            "node-1",
            snapshot.snapshot_id,
            "Policy",
            "ENTITY",
            "policy",
            (text_evidence.evidence_id,),
            ("核保", "underwriting"),
        ),
        GraphNode(
            "node-2",
            snapshot.snapshot_id,
            "Claim",
            "CONCEPT",
            "claim",
            (page_evidence.evidence_id,),
        ),
        GraphNode("node-3", snapshot.snapshot_id, "Rule", "ENTITY", "rule"),
    )
    edges = (
        GraphEdge(
            "edge-extracted-1",
            snapshot.snapshot_id,
            "node-1",
            "node-2",
            "GOVERNS",
            RelationOrigin.EXTRACTED,
            0.87,
            (text_evidence.evidence_id,),
            "需要",
        ),
        GraphEdge(
            "edge-inferred-1",
            snapshot.snapshot_id,
            "node-2",
            "node-3",
            "SUPPORTS",
            RelationOrigin.INFERRED,
            0.65,
            (),
            "支持",
        ),
    )
    provenance = (
        InferenceProvenance(
            "provenance-1",
            snapshot.snapshot_id,
            "edge-inferred-1",
            ("node-1", "edge-extracted-1"),
            "sha256:" + "e" * 64,
        ),
    )
    return GraphSnapshotDraft(snapshot, nodes, edges, (text_evidence, page_evidence), provenance)


def test_draft_codec_round_trips_aliases_labels_anchors_and_provenance() -> None:
    draft = _full_draft()

    payload = json.loads(json.dumps(serialize_draft(draft)))
    restored = deserialize_draft(draft.snapshot, payload)

    assert restored == draft
    assert restored.nodes[0].aliases == ("核保", "underwriting")
    assert restored.edges[0].relation_label == "需要"
    assert restored.edges[0].origin is RelationOrigin.EXTRACTED
    assert restored.edges[0].confidence == 0.87
    assert restored.edges[1].origin is RelationOrigin.INFERRED
    assert restored.edges[1].relation_label == "支持"
    assert restored.evidence[1].anchor == {"kind": "page", "page": 7}
    assert restored.provenance[0].input_fact_ids == ("node-1", "edge-extracted-1")
