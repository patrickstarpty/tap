from dataclasses import replace

import pytest

from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    InferenceProvenance,
    RelationOrigin,
    source_set_digest,
)


def test_snapshot_identity_is_content_bound_and_immutable():
    snapshot = GraphSnapshot.create(
        snapshot_id="snapshot-1",
        project_id="project-1",
        source_revision_ids=("source-revision-2", "source-revision-1"),
        document_revision_ids=("document-revision-1",),
    )
    assert snapshot.source_set_digest == source_set_digest(
        ("source-revision-1", "source-revision-2")
    )
    with pytest.raises(AttributeError):
        snapshot.snapshot_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="source set digest"):
        replace(snapshot, source_set_digest="sha256:" + "0" * 64)


def test_edges_require_snapshot_local_nodes_and_resolvable_extracted_evidence():
    node = GraphNode("node-1", "snapshot-1", "Policy", "ENTITY", "policy")
    evidence = Evidence(
        evidence_id="evidence-1",
        snapshot_id="snapshot-1",
        source_revision_id="source-revision-1",
        document_revision_id="document-revision-1",
        chunk_id="chunk-1",
        anchor={"kind": "text", "start": 0, "end": 6},
        content_digest="sha256:" + "a" * 64,
    )
    edge = GraphEdge(
        edge_id="edge-1",
        snapshot_id="snapshot-1",
        source_node_id=node.node_id,
        target_node_id="node-2",
        relation_type="GOVERNS",
        origin=RelationOrigin.EXTRACTED,
        confidence=0.9,
        evidence_ids=(evidence.evidence_id,),
    )
    assert edge.evidence_ids == ("evidence-1",)
    with pytest.raises(ValueError, match="evidence"):
        replace(edge, evidence_ids=())
    with pytest.raises(ValueError, match="snapshot"):
        GraphSnapshotDraft(
            GraphSnapshot.create(
                snapshot_id="snapshot-2",
                project_id="project-1",
                source_revision_ids=("source-revision-1",),
                document_revision_ids=("document-revision-1",),
            ),
            (node,),
            (edge,),
            (evidence,),
            (),
        )


def test_inferred_edges_require_complete_input_fact_lineage():
    with pytest.raises(ValueError, match="provenance"):
        InferenceProvenance(
            provenance_id="provenance-1",
            snapshot_id="snapshot-1",
            edge_id="edge-1",
            input_fact_ids=(),
            rule_digest="sha256:" + "b" * 64,
        )
    with pytest.raises(ValueError, match="evidence"):
        GraphEdge(
            edge_id="edge-1",
            snapshot_id="snapshot-1",
            source_node_id="node-1",
            target_node_id="node-2",
            relation_type="IMPLIES",
            origin=RelationOrigin.INFERRED,
            confidence=0.8,
            evidence_ids=("evidence-1",),
        )
