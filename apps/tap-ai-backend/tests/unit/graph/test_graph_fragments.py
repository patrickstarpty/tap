from __future__ import annotations

from tap.modules.graph.application.fragments import (
    assemble_fragment,
    known_entities_from,
    split_batches,
)
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)
from tap.modules.knowledge.domain.documents import ChunkDraft

SNAPSHOT = GraphSnapshot.create(
    snapshot_id="grs_" + "a" * 32,
    project_id="tapper-demo",
    source_revision_ids=("revision-1",),
    document_revision_ids=("revision-1",),
)


def _chunk(index: int) -> ChunkDraft:
    return ChunkDraft(
        chunk_id=f"chunk-{index}",
        logical_chunk_id=f"logical-{index}",
        root_id="document-1",
        parent_id=None,
        content=f"content {index}",
        anchor_json='{"endOffset":1,"headingPath":[],"startOffset":0,"type":"document"}',
        source_content_hash="sha256:" + "b" * 64,
        chunk_content_hash="sha256:" + "a" * 64,
    )


def test_split_batches_respects_size_and_order() -> None:
    chunks = tuple(_chunk(index) for index in range(25))

    batches = split_batches(chunks, batch_size=10)

    assert [len(batch) for batch in batches] == [10, 10, 5]
    assert batches[0][0].chunk_id == "chunk-0"
    assert batches[2][-1].chunk_id == "chunk-24"


def _evidence(evidence_id: str, chunk_id: str) -> Evidence:
    return Evidence(
        evidence_id,
        SNAPSHOT.snapshot_id,
        "revision-1",
        "revision-1",
        chunk_id,
        {"kind": "text", "start": 0, "end": 5},
        "sha256:" + "a" * 64,
    )


def test_assemble_fragment_merges_nodes_edges_and_evidence() -> None:
    evidence_1 = _evidence("evidence-1", "chunk-0")
    evidence_2 = _evidence("evidence-2", "chunk-1")
    node_batch_1 = GraphNode(
        "node-1", SNAPSHOT.snapshot_id, "Policy", "ENTITY", "policy", ("evidence-1",), ("P1",)
    )
    node_batch_2 = GraphNode(
        "node-1",
        SNAPSHOT.snapshot_id,
        "Policy (renamed)",
        "CONCEPT",
        "policy-other",
        ("evidence-2",),
        ("P2", "P3", "P4", "P5", "P6"),
    )
    other_node = GraphNode("node-2", SNAPSHOT.snapshot_id, "Claim", "CONCEPT", "claim")
    edge_batch_1 = GraphEdge(
        "edge-1",
        SNAPSHOT.snapshot_id,
        "node-1",
        "node-2",
        "GOVERNS",
        RelationOrigin.EXTRACTED,
        0.4,
        ("evidence-1",),
    )
    edge_batch_2 = GraphEdge(
        "edge-1",
        SNAPSHOT.snapshot_id,
        "node-1",
        "node-2",
        "GOVERNS",
        RelationOrigin.EXTRACTED,
        0.9,
        ("evidence-2",),
    )
    draft_1 = GraphSnapshotDraft(
        SNAPSHOT, (node_batch_1, other_node), (edge_batch_1,), (evidence_1,), ()
    )
    draft_2 = GraphSnapshotDraft(
        SNAPSHOT, (node_batch_2, other_node), (edge_batch_2,), (evidence_2,), ()
    )

    fragment = assemble_fragment(SNAPSHOT, (draft_1, draft_2))

    merged_node = next(node for node in fragment.nodes if node.node_id == "node-1")
    assert merged_node.label == "Policy"
    assert merged_node.node_type == "ENTITY"
    assert merged_node.canonical_key == "policy"
    assert merged_node.evidence_ids == ("evidence-1", "evidence-2")
    assert merged_node.aliases == ("P1", "P2", "P3", "P4", "P5")
    merged_edge = next(edge for edge in fragment.edges if edge.edge_id == "edge-1")
    assert merged_edge.evidence_ids == ("evidence-1", "evidence-2")
    assert merged_edge.confidence == 0.9
    assert {item.evidence_id for item in fragment.evidence} == {"evidence-1", "evidence-2"}


def _node_draft(index: int) -> GraphSnapshotDraft:
    node = GraphNode(
        f"node-{index}", SNAPSHOT.snapshot_id, f"Entity {index}", "CONCEPT", f"entity-{index}"
    )
    return GraphSnapshotDraft(SNAPSHOT, (node,), (), (), ())


def test_known_entities_keep_the_newest_two_hundred() -> None:
    drafts = tuple(_node_draft(index) for index in range(250))

    known = known_entities_from(drafts, limit=200)

    assert len(known) == 200
    assert known[0]["id"] == "node-50"
    assert known[-1]["id"] == "node-249"
