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


def test_assemble_fragment_falls_back_to_a_document_node_when_every_batch_is_empty() -> None:
    # Every batch of a relation-less document returns ``None`` (nothing
    # grounded, e.g. the model extractor's empty-batch signal) and the worker
    # never appends it to the drafts list, so assembly sees zero drafts. No
    # per-batch fallback node survives to reuse, so assemble_fragment must
    # synthesize one fresh, rather than publish a draft with zero nodes.
    fragment = assemble_fragment(SNAPSHOT, ())

    assert len(fragment.nodes) == 1
    assert fragment.nodes[0].canonical_key == "document:revision-1"
    assert fragment.edges == ()
    assert fragment.evidence == ()
    assert fragment.provenance == ()


def test_assemble_fragment_merges_duplicate_canonical_keys_across_batches() -> None:
    # Repro #1 from the F1 review finding: the same raw entity ("核保流程") is
    # grounded independently in two batches. The rule-based extractor mints the
    # same *raw* id for it in both batches (deterministic by content hash), but
    # the worker's per-batch namespacing (b{i}-) gives those raw ids different
    # literal node ids across batches -- so the only thing tying them back
    # together is the normalized canonical key, not node_id.
    evidence_1 = _evidence("evidence-1", "chunk-0")
    evidence_2 = _evidence("evidence-2", "chunk-1")
    node_1_batch_1 = GraphNode(
        "b0-grn_a", SNAPSHOT.snapshot_id, "核保流程", "PROCESS", "核保流程", ("evidence-1",)
    )
    node_2_batch_1 = GraphNode(
        "b0-grn_b", SNAPSHOT.snapshot_id, "健康告知", "CONCEPT", "健康告知", ("evidence-1",)
    )
    edge_batch_1 = GraphEdge(
        "b0-ged_a",
        SNAPSHOT.snapshot_id,
        "b0-grn_a",
        "b0-grn_b",
        "REQUIRES",
        RelationOrigin.EXTRACTED,
        1.0,
        ("evidence-1",),
        "需要",
    )
    # Batch 2 re-grounds the same two real-world entities under a different
    # (b1-) namespace prefix, not knowing they were already known.
    node_1_batch_2 = GraphNode(
        "b1-grn_a", SNAPSHOT.snapshot_id, "核保流程", "PROCESS", "核保流程", ("evidence-2",)
    )
    node_2_batch_2 = GraphNode(
        "b1-grn_b", SNAPSHOT.snapshot_id, "健康告知", "CONCEPT", "健康告知", ("evidence-2",)
    )
    edge_batch_2 = GraphEdge(
        "b1-ged_a",
        SNAPSHOT.snapshot_id,
        "b1-grn_a",
        "b1-grn_b",
        "REQUIRES",
        RelationOrigin.EXTRACTED,
        1.0,
        ("evidence-2",),
        "需要",
    )
    draft_1 = GraphSnapshotDraft(
        SNAPSHOT, (node_1_batch_1, node_2_batch_1), (edge_batch_1,), (evidence_1,), ()
    )
    draft_2 = GraphSnapshotDraft(
        SNAPSHOT, (node_1_batch_2, node_2_batch_2), (edge_batch_2,), (evidence_2,), ()
    )

    fragment = assemble_fragment(SNAPSHOT, (draft_1, draft_2))

    # Exactly one surviving node per real-world entity -- no MySQL
    # uq_graph_node_canonical collision -- and exactly one surviving edge between
    # them (the duplicate edge from batch 2 merges rather than violating no
    # expectation of edge uniqueness, since both now share the same endpoints).
    assert sorted(node.canonical_key for node in fragment.nodes) == ["健康告知", "核保流程"]
    assert len(fragment.edges) == 1
    merged_edge = fragment.edges[0]
    assert merged_edge.evidence_ids == ("evidence-1", "evidence-2")
    process_node = next(node for node in fragment.nodes if node.canonical_key == "核保流程")
    disclosure_node = next(node for node in fragment.nodes if node.canonical_key == "健康告知")
    assert merged_edge.source_node_id == process_node.node_id
    assert merged_edge.target_node_id == disclosure_node.node_id


# The relation-less-multi-batch-document repro (two batches each minting their
# own "document:revision-1" node) is covered end to end, with the real
# DeterministicGraphExtraction extractor and the real worker batching loop, by
# tests/unit/graph/test_graph_worker.py::
#   test_batches_a_relation_less_document_into_one_document_node


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
    # An unrelated EXTRACTED edge between two other kept early nodes, so the
    # document still has a real relation overall once edge-inferred cascades
    # away -- otherwise the zero-kept-edges document-node fallback (see R2)
    # would replace everything, which is not what this test is about.
    evidence = _evidence("evidence-unrelated", "chunk-0")
    unrelated_edge = GraphEdge(
        "edge-unrelated",
        SNAPSHOT.snapshot_id,
        "node-2",
        "node-3",
        "GOVERNS",
        RelationOrigin.EXTRACTED,
        1.0,
        ("evidence-unrelated",),
    )
    draft_1 = GraphSnapshotDraft(SNAPSHOT, early_nodes, (unrelated_edge,), (evidence,), ())
    draft_2 = GraphSnapshotDraft(
        SNAPSHOT,
        (late_node, early_nodes[0], early_nodes[1]),
        (inferred_edge,),
        (),
        (provenance,),
    )

    fragment = assemble_fragment(SNAPSHOT, (draft_1, draft_2))

    assert len(fragment.nodes) == 500
    assert {edge.edge_id for edge in fragment.edges} == {"edge-unrelated"}
    assert fragment.provenance == ()


def test_assemble_fragment_keeps_one_provenance_when_two_batches_infer_the_same_edge() -> None:
    # R1(a): two batches each independently infer the same real-world relation
    # (e.g. the same rule fired once per batch) with their own provenance. Once
    # the duplicate edge merges (shape dedupe), both provenances would cite the
    # same surviving edge_id -- InferenceProvenance uniqueness forbids that.
    # Only the first (draft-order) provenance may survive.
    node_a1 = GraphNode("b0-node-a", SNAPSHOT.snapshot_id, "Entity A", "CONCEPT", "entity-a")
    node_b1 = GraphNode("b0-node-b", SNAPSHOT.snapshot_id, "Entity B", "CONCEPT", "entity-b")
    node_a2 = GraphNode("b1-node-a", SNAPSHOT.snapshot_id, "Entity A", "CONCEPT", "entity-a")
    node_b2 = GraphNode("b1-node-b", SNAPSHOT.snapshot_id, "Entity B", "CONCEPT", "entity-b")
    edge_1 = GraphEdge(
        "b0-edge",
        SNAPSHOT.snapshot_id,
        "b0-node-a",
        "b0-node-b",
        "TRIGGERS",
        RelationOrigin.INFERRED,
        0.7,
        (),
    )
    edge_2 = GraphEdge(
        "b1-edge",
        SNAPSHOT.snapshot_id,
        "b1-node-a",
        "b1-node-b",
        "TRIGGERS",
        RelationOrigin.INFERRED,
        0.9,
        (),
    )
    provenance_1 = InferenceProvenance(
        "b0-prov", SNAPSHOT.snapshot_id, "b0-edge", ("b0-node-a",), "sha256:" + "c" * 64
    )
    provenance_2 = InferenceProvenance(
        "b1-prov", SNAPSHOT.snapshot_id, "b1-edge", ("b1-node-a",), "sha256:" + "c" * 64
    )
    draft_1 = GraphSnapshotDraft(SNAPSHOT, (node_a1, node_b1), (edge_1,), (), (provenance_1,))
    draft_2 = GraphSnapshotDraft(SNAPSHOT, (node_a2, node_b2), (edge_2,), (), (provenance_2,))

    fragment = assemble_fragment(SNAPSHOT, (draft_1, draft_2))

    assert len(fragment.edges) == 1
    assert len(fragment.provenance) == 1
    assert fragment.provenance[0].provenance_id == "b0-prov"
    assert fragment.edges[0].confidence == 0.9


def test_assemble_fragment_dedupes_provenance_inputs_that_collapse_to_one_node() -> None:
    # R1(b): a provenance originally cites two *distinct* input facts
    # ("b0-node-a" and "b1-node-a") that each name the same real-world entity
    # mentioned in a different batch -- so the canonical-key merge collapses
    # them onto the same surviving node. After remapping, both input facts
    # become the same id; deduping must keep the provenance valid (one
    # surviving input, still citing a real fact) instead of letting
    # InferenceProvenance's uniqueness check raise on two identical ids.
    node_a1 = GraphNode("b0-node-a", SNAPSHOT.snapshot_id, "Entity A", "CONCEPT", "entity-a")
    node_a2 = GraphNode("b1-node-a", SNAPSHOT.snapshot_id, "Entity A", "CONCEPT", "entity-a")
    node_b = GraphNode("b0-node-b", SNAPSHOT.snapshot_id, "Entity B", "CONCEPT", "entity-b")
    node_c = GraphNode("b0-node-c", SNAPSHOT.snapshot_id, "Entity C", "CONCEPT", "entity-c")
    edge = GraphEdge(
        "b0-edge",
        SNAPSHOT.snapshot_id,
        "b0-node-b",
        "b0-node-c",
        "TRIGGERS",
        RelationOrigin.INFERRED,
        0.7,
        (),
    )
    # Two distinct input facts naming the same real-world entity under its
    # two different batch-namespaced ids. Both ids must be present in the
    # same draft for GraphSnapshotDraft's own dangling-input-fact check to
    # accept it as constructed (a real per-batch draft would only ever cite
    # facts grounded in its own batch); the cross-batch merge this test
    # exercises is in assemble_fragment's node dedup, not in draft
    # construction.
    provenance = InferenceProvenance(
        "b0-prov",
        SNAPSHOT.snapshot_id,
        "b0-edge",
        ("b0-node-a", "b1-node-a"),
        "sha256:" + "c" * 64,
    )
    draft = GraphSnapshotDraft(
        SNAPSHOT, (node_a1, node_a2, node_b, node_c), (edge,), (), (provenance,)
    )

    fragment = assemble_fragment(SNAPSHOT, (draft,))

    assert "b0-edge" in {edge.edge_id for edge in fragment.edges}
    assert len(fragment.provenance) == 1
    assert fragment.provenance[0].input_fact_ids == ("b0-node-a",)


def test_assemble_fragment_drops_provenance_that_cites_only_its_own_edge() -> None:
    # A provenance whose only input fact is its own edge's id is
    # self-referential and invalid; it (and its now-orphaned INFERRED edge)
    # must be dropped rather than raise.
    node_a = GraphNode("b0-node-a", SNAPSHOT.snapshot_id, "Entity A", "CONCEPT", "entity-a")
    node_b = GraphNode("b0-node-b", SNAPSHOT.snapshot_id, "Entity B", "CONCEPT", "entity-b")
    edge = GraphEdge(
        "b0-edge",
        SNAPSHOT.snapshot_id,
        "b0-node-a",
        "b0-node-b",
        "TRIGGERS",
        RelationOrigin.INFERRED,
        0.7,
        (),
    )
    provenance = InferenceProvenance(
        "b0-prov", SNAPSHOT.snapshot_id, "b0-edge", ("b0-edge",), "sha256:" + "c" * 64
    )
    draft = GraphSnapshotDraft(SNAPSHOT, (node_a, node_b), (edge,), (), (provenance,))

    fragment = assemble_fragment(SNAPSHOT, (draft,))

    assert "b0-edge" not in {edge.edge_id for edge in fragment.edges}
    assert fragment.provenance == ()


def test_assemble_fragment_prefers_extracted_edge_over_inferred_of_the_same_shape() -> None:
    # R1(c): one batch infers a relation (no evidence, carries provenance);
    # another batch directly extracts the same relation (real evidence). Once
    # the duplicate shape merges, naively unioning "evidence" onto the INFERRED
    # edge would violate GraphEdge's "inferred relation cannot claim direct
    # evidence" rule. The EXTRACTED edge must win outright, and the INFERRED
    # edge's now-orphaned provenance must be dropped.
    node_a1 = GraphNode("b0-node-a", SNAPSHOT.snapshot_id, "Entity A", "CONCEPT", "entity-a")
    node_b1 = GraphNode("b0-node-b", SNAPSHOT.snapshot_id, "Entity B", "CONCEPT", "entity-b")
    node_a2 = GraphNode("b1-node-a", SNAPSHOT.snapshot_id, "Entity A", "CONCEPT", "entity-a")
    node_b2 = GraphNode("b1-node-b", SNAPSHOT.snapshot_id, "Entity B", "CONCEPT", "entity-b")
    inferred_edge = GraphEdge(
        "b0-edge",
        SNAPSHOT.snapshot_id,
        "b0-node-a",
        "b0-node-b",
        "TRIGGERS",
        RelationOrigin.INFERRED,
        0.7,
        (),
    )
    provenance = InferenceProvenance(
        "b0-prov", SNAPSHOT.snapshot_id, "b0-edge", ("b0-node-a",), "sha256:" + "c" * 64
    )
    evidence = _evidence("evidence-extracted", "chunk-0")
    extracted_edge = GraphEdge(
        "b1-edge",
        SNAPSHOT.snapshot_id,
        "b1-node-a",
        "b1-node-b",
        "TRIGGERS",
        RelationOrigin.EXTRACTED,
        0.95,
        ("evidence-extracted",),
    )
    draft_1 = GraphSnapshotDraft(SNAPSHOT, (node_a1, node_b1), (inferred_edge,), (), (provenance,))
    draft_2 = GraphSnapshotDraft(SNAPSHOT, (node_a2, node_b2), (extracted_edge,), (evidence,), ())

    fragment = assemble_fragment(SNAPSHOT, (draft_1, draft_2))

    assert len(fragment.edges) == 1
    winner = fragment.edges[0]
    assert winner.origin is RelationOrigin.EXTRACTED
    assert winner.evidence_ids == ("evidence-extracted",)
    assert fragment.provenance == ()


def test_assemble_fragment_merges_normalize_key_through_accent_folding() -> None:
    # R4: MySQL's server default collation for canonical_key
    # (utf8mb4_0900_ai_ci) is accent-insensitive, not just case-insensitive --
    # "café" and "cafe" collide there. normalize_key (used for the merge above)
    # must fold accents too, or such a pair would survive assembly as two nodes
    # and then collide on uq_graph_node_canonical at publish.
    node_accented = GraphNode(
        "b0-node", SNAPSHOT.snapshot_id, "Café", "CONCEPT", "café", ("evidence-1",)
    )
    node_plain = GraphNode(
        "b1-node", SNAPSHOT.snapshot_id, "Cafe", "CONCEPT", "cafe", ("evidence-2",)
    )
    # An unrelated other node and edge, purely so the merged document has a
    # real relation overall and does not instead trigger the zero-kept-edges
    # document-node fallback (see R2), which is not what this test is about.
    other_node = GraphNode("node-other", SNAPSHOT.snapshot_id, "Other", "CONCEPT", "other")
    evidence_1 = _evidence("evidence-1", "chunk-0")
    evidence_2 = _evidence("evidence-2", "chunk-1")
    edge = GraphEdge(
        "edge-1",
        SNAPSHOT.snapshot_id,
        "b0-node",
        "node-other",
        "USES",
        RelationOrigin.EXTRACTED,
        1.0,
        ("evidence-1",),
    )
    draft_1 = GraphSnapshotDraft(SNAPSHOT, (node_accented, other_node), (edge,), (evidence_1,), ())
    draft_2 = GraphSnapshotDraft(SNAPSHOT, (node_plain,), (), (evidence_2,), ())

    fragment = assemble_fragment(SNAPSHOT, (draft_1, draft_2))

    merged = next(node for node in fragment.nodes if node.node_id == "b0-node")
    assert set(merged.evidence_ids) == {"evidence-1", "evidence-2"}
    assert "b1-node" not in {node.node_id for node in fragment.nodes}


def test_assemble_fragment_keeps_a_real_node_whose_key_merely_starts_with_document() -> None:
    # N1: a model is free to mint a real, edge-bearing node whose own
    # canonicalKey happens to start with "document:" for an unrelated
    # real-world thing (e.g. "document:handbook", when SNAPSHOT's own
    # revision is "revision-1" -- this snapshot's actual reserved fallback key
    # is "document:revision-1", not this one). Prefix-matching would
    # misidentify it as this snapshot's own fallback node and strip it while
    # leaving its edge behind, which then fails GraphSnapshotDraft's dangling
    # edge endpoint check. Only an exact match against this snapshot's own
    # revision-derived fallback key may identify a fallback node.
    handbook_node = GraphNode(
        "node-handbook", SNAPSHOT.snapshot_id, "Handbook", "ENTITY", "document:handbook"
    )
    other_node = GraphNode("node-other", SNAPSHOT.snapshot_id, "Other", "CONCEPT", "other")
    evidence = _evidence("evidence-1", "chunk-0")
    handbook_edge = GraphEdge(
        "edge-handbook",
        SNAPSHOT.snapshot_id,
        "node-handbook",
        "node-other",
        "USES",
        RelationOrigin.EXTRACTED,
        1.0,
        ("evidence-1",),
    )
    # The document also has other, unrelated edges elsewhere.
    node_a = GraphNode("node-a", SNAPSHOT.snapshot_id, "Entity A", "CONCEPT", "entity-a")
    node_b = GraphNode("node-b", SNAPSHOT.snapshot_id, "Entity B", "CONCEPT", "entity-b")
    evidence_2 = _evidence("evidence-2", "chunk-1")
    other_edge = GraphEdge(
        "edge-other",
        SNAPSHOT.snapshot_id,
        "node-a",
        "node-b",
        "GOVERNS",
        RelationOrigin.EXTRACTED,
        1.0,
        ("evidence-2",),
    )
    draft = GraphSnapshotDraft(
        SNAPSHOT,
        (handbook_node, other_node, node_a, node_b),
        (handbook_edge, other_edge),
        (evidence, evidence_2),
        (),
    )

    fragment = assemble_fragment(SNAPSHOT, (draft,))

    assert "node-handbook" in {node.node_id for node in fragment.nodes}
    assert "edge-handbook" in {edge.edge_id for edge in fragment.edges}
    assert "edge-other" in {edge.edge_id for edge in fragment.edges}
