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
    InferenceProvenance,
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
    # assemble_fragment only ever sees two different batches' drafts share a node id
    # when the worker has deliberately kept it that way: a model-chosen id namespaced
    # per batch (see worker._namespace_draft), except for a known-entity reuse, which
    # keeps its id across batches on purpose. "node-1" here stands in for such a
    # reuse -- the same real-world entity mentioned again in a later batch, not a
    # coincidental id collision.
    evidence_1 = _evidence("evidence-1", "chunk-0")
    evidence_2 = _evidence("evidence-2", "chunk-1")
    known_entity_batch_1 = GraphNode(
        "node-1", SNAPSHOT.snapshot_id, "Policy", "ENTITY", "policy", ("evidence-1",), ("P1",)
    )
    known_entity_batch_2 = GraphNode(
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
        SNAPSHOT, (known_entity_batch_1, other_node), (edge_batch_1,), (evidence_1,), ()
    )
    draft_2 = GraphSnapshotDraft(
        SNAPSHOT, (known_entity_batch_2, other_node), (edge_batch_2,), (evidence_2,), ()
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


def test_assemble_fragment_truncates_oversized_merges_to_the_draft_caps() -> None:
    # 520 distinct nodes across two drafts, each individually within the 500-node
    # cap but exceeding it once merged. One edge's target falls in the dropped tail
    # and must be dropped with it; one edge stays fully within the kept nodes.
    first_nodes = tuple(
        GraphNode(f"node-{i}", SNAPSHOT.snapshot_id, f"Entity {i}", "CONCEPT", f"entity-{i}")
        for i in range(500)
    )
    extra_nodes = tuple(
        GraphNode(f"node-{i}", SNAPSHOT.snapshot_id, f"Entity {i}", "CONCEPT", f"entity-{i}")
        for i in range(500, 520)
    )
    evidence_1 = _evidence("evidence-1", "chunk-0")
    evidence_2 = _evidence("evidence-2", "chunk-1")
    edge_within_cap = GraphEdge(
        "edge-within",
        SNAPSHOT.snapshot_id,
        "node-0",
        "node-1",
        "GOVERNS",
        RelationOrigin.EXTRACTED,
        1.0,
        ("evidence-1",),
    )
    edge_touches_dropped = GraphEdge(
        "edge-dropped",
        SNAPSHOT.snapshot_id,
        "node-500",
        "node-510",
        "GOVERNS",
        RelationOrigin.EXTRACTED,
        1.0,
        ("evidence-2",),
    )
    draft_1 = GraphSnapshotDraft(SNAPSHOT, first_nodes, (edge_within_cap,), (evidence_1,), ())
    draft_2 = GraphSnapshotDraft(SNAPSHOT, extra_nodes, (edge_touches_dropped,), (evidence_2,), ())

    fragment = assemble_fragment(SNAPSHOT, (draft_1, draft_2))

    assert len(fragment.nodes) == 500
    assert {node.node_id for node in fragment.nodes} == {f"node-{i}" for i in range(500)}
    assert [edge.edge_id for edge in fragment.edges] == ["edge-within"]


def test_assemble_fragment_caps_merged_edges_at_two_thousand() -> None:
    # Two drafts, 1001 distinct edges each (2002 total merged) among 50 shared nodes
    # that stay well within the node cap, so only the 2000-edge cap is exercised.
    nodes = tuple(
        GraphNode(f"node-{i}", SNAPSHOT.snapshot_id, f"Entity {i}", "CONCEPT", f"entity-{i}")
        for i in range(50)
    )

    def _edges(prefix: str, count: int, *, evidence_id: str) -> tuple[GraphEdge, ...]:
        return tuple(
            GraphEdge(
                f"{prefix}-edge-{i}",
                SNAPSHOT.snapshot_id,
                f"node-{i % 50}",
                f"node-{(i + 1) % 50}",
                "GOVERNS",
                RelationOrigin.EXTRACTED,
                1.0,
                (evidence_id,),
            )
            for i in range(count)
        )

    evidence_1 = _evidence("evidence-1", "chunk-0")
    evidence_2 = _evidence("evidence-2", "chunk-1")
    draft_1 = GraphSnapshotDraft(
        SNAPSHOT, nodes, _edges("a", 1001, evidence_id="evidence-1"), (evidence_1,), ()
    )
    draft_2 = GraphSnapshotDraft(
        SNAPSHOT, nodes, _edges("b", 1001, evidence_id="evidence-2"), (evidence_2,), ()
    )

    fragment = assemble_fragment(SNAPSHOT, (draft_1, draft_2))

    assert len(fragment.edges) == 2000
    kept_ids = [edge.edge_id for edge in fragment.edges]
    assert kept_ids[:1001] == [f"a-edge-{i}" for i in range(1001)]
    assert kept_ids[1001:] == [f"b-edge-{i}" for i in range(999)]


def test_assemble_fragment_cascades_provenance_whose_inputs_fall_past_the_cap() -> None:
    # 500 "early" nodes fill the node cap and are kept. A later, dropped node
    # ("node-510") is cited as an inference's input fact. The inference's own edge
    # sits between two *kept* early nodes, so it survives the endpoint filter and
    # the edge cap on its own -- but its provenance names a dropped node, so the
    # edge and its provenance must cascade-drop together, or GraphSnapshotDraft
    # raises on a dangling input fact.
    early_nodes = tuple(
        GraphNode(f"node-{i}", SNAPSHOT.snapshot_id, f"Entity {i}", "CONCEPT", f"entity-{i}")
        for i in range(500)
    )
    late_node = GraphNode("node-510", SNAPSHOT.snapshot_id, "Entity 510", "CONCEPT", "entity-510")
    inferred_edge = GraphEdge(
        "edge-inferred",
        SNAPSHOT.snapshot_id,
        "node-0",
        "node-1",
        "TRIGGERS",
        RelationOrigin.INFERRED,
        0.8,
        (),  # an INFERRED edge carries no direct evidence
    )
    provenance = InferenceProvenance(
        "prov-1",
        SNAPSHOT.snapshot_id,
        "edge-inferred",
        ("node-510",),
        "sha256:" + "c" * 64,
    )
    draft_1 = GraphSnapshotDraft(SNAPSHOT, early_nodes, (), (), ())
    draft_2 = GraphSnapshotDraft(
        SNAPSHOT,
        (late_node, early_nodes[0], early_nodes[1]),
        (inferred_edge,),
        (),
        (provenance,),
    )

    fragment = assemble_fragment(SNAPSHOT, (draft_1, draft_2))

    assert len(fragment.nodes) == 500
    assert "edge-inferred" not in {edge.edge_id for edge in fragment.edges}
    assert fragment.provenance == ()
