from __future__ import annotations

import hashlib
from typing import Sequence

from tap.modules.graph.application.alignment import alignment_key, merge_edges, resolve_entities
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)


def _digest(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def _snapshot(snapshot_id: str) -> GraphSnapshot:
    return GraphSnapshot.create(
        snapshot_id=snapshot_id,
        project_id="project-1",
        source_revision_ids=(f"{snapshot_id}-source",),
        document_revision_ids=(f"{snapshot_id}-document",),
        status="READY",
    )


def _fragment(
    snapshot_id: str,
    node_specs: Sequence[tuple[str, str, str, str] | tuple[str, str, str, str, tuple[str, ...]]],
) -> GraphSnapshotDraft:
    nodes = []
    for spec in node_specs:
        node_id, label, node_type, canonical_key, *rest = spec
        aliases = rest[0] if rest else ()
        nodes.append(GraphNode(node_id, snapshot_id, label, node_type, canonical_key, (), aliases))
    return GraphSnapshotDraft(_snapshot(snapshot_id), tuple(nodes), (), (), ())


def test_alignment_key_strips_one_trailing_modifier():
    assert alignment_key("核保流程") == "核保" and alignment_key("Refund Policy") == "refund"
    assert alignment_key("流程") == "流程"


def test_exact_key_merges_across_fragments_and_keeps_majority_label():
    a = _fragment(
        "frag-a",
        [
            ("a1", "健康告知", "CONCEPT", "健康告知"),
            ("a2", "健康告知", "CONCEPT", "健康告知"),
        ],
    )
    b = _fragment("frag-b", [("b1", "健康告知书", "CONCEPT", "健康告知")])

    entities = resolve_entities([a, b])

    assert len(entities) == 1
    entity = entities[0]
    assert entity.label == "健康告知"
    assert "健康告知书" in entity.aliases
    assert entity.rule == "EXACT"
    assert len(entity.members) == 3


def test_same_key_different_type_stays_separate():
    a = _fragment("frag-a", [("n1", "保单", "ENTITY", "保单")])
    b = _fragment("frag-b", [("n1", "保单", "REQUIREMENT", "保单")])
    entities = resolve_entities([a, b])
    assert len(entities) == 2 and {e.node_type for e in entities} == {"ENTITY", "REQUIREMENT"}


def test_alias_level_requires_same_type_and_records_rule():
    a = _fragment(
        "frag-a",
        [("a1", "Underwriting", "CONCEPT", "underwriting", ("核保",))],
    )
    b_same_type = _fragment("frag-b", [("b1", "核保", "CONCEPT", "核保")])

    entities = resolve_entities([a, b_same_type])
    assert len(entities) == 1
    assert entities[0].rule == "ALIAS"

    b_other_type = _fragment("frag-b", [("b1", "核保", "PROCESS", "核保")])
    separate_entities = resolve_entities([a, b_other_type])
    assert len(separate_entities) == 2


def test_embedding_level_only_when_vectors_supplied():
    a = _fragment("frag-a", [("a1", "X", "ENTITY", "keyx")])
    b = _fragment("frag-b", [("b1", "Y", "ENTITY", "keyy")])

    assert len(resolve_entities([a, b])) == 2

    embeddings = {
        ("frag-a", "a1"): (1.0, 0.0),
        ("frag-b", "b1"): (1.0, 0.0),
    }
    merged = resolve_entities([a, b], embeddings=embeddings, threshold=0.92)
    assert len(merged) == 1
    assert merged[0].rule == "EMBEDDING"


def _edge_fragment(
    snapshot_id: str,
    nodes: Sequence[str],
    edges: Sequence[tuple[str, str, str, str, float, str, str]],
) -> GraphSnapshotDraft:
    snapshot = _snapshot(snapshot_id)
    graph_nodes = tuple(
        GraphNode(node_id, snapshot_id, node_id, "ENTITY", node_id) for node_id in nodes
    )
    evidence = []
    graph_edges = []
    for edge_id, source, target, relation_type, confidence, label, evidence_id in edges:
        evidence.append(
            Evidence(
                evidence_id=evidence_id,
                snapshot_id=snapshot_id,
                source_revision_id=f"{snapshot_id}-source",
                document_revision_id=f"{snapshot_id}-document",
                chunk_id=f"chunk-{evidence_id}",
                anchor={"kind": "text", "start": 0, "end": 4},
                content_digest=_digest(evidence_id),
            )
        )
        graph_edges.append(
            GraphEdge(
                edge_id=edge_id,
                snapshot_id=snapshot_id,
                source_node_id=source,
                target_node_id=target,
                relation_type=relation_type,
                origin=RelationOrigin.EXTRACTED,
                confidence=confidence,
                evidence_ids=(evidence_id,),
                relation_label=label,
            )
        )
    return GraphSnapshotDraft(snapshot, graph_nodes, tuple(graph_edges), tuple(evidence), ())


def test_merge_edges_unions_evidence_and_takes_max_confidence():
    a = _edge_fragment(
        "frag-a",
        ["n-src", "n-dst"],
        [("e1", "n-src", "n-dst", "REQUIRES", 0.6, "需要", "ev-a1")],
    )
    b = _edge_fragment(
        "frag-b",
        ["m-src", "m-dst", "m-other"],
        [
            ("e2", "m-src", "m-dst", "REQUIRES", 0.9, "需要", "ev-b1"),
            ("e3", "m-src", "m-other", "REQUIRES", 0.5, "须提供", "ev-b2"),
        ],
    )
    entity_of = {
        ("frag-a", "n-src"): "pid-src",
        ("frag-a", "n-dst"): "pid-dst",
        ("frag-b", "m-src"): "pid-src",
        ("frag-b", "m-dst"): "pid-dst",
        ("frag-b", "m-other"): "pid-src",
    }

    merged = merge_edges([a, b], entity_of)

    assert len(merged) == 1
    edge, evidence_pairs = merged[0]
    assert edge.source_node_id == "pid-src"
    assert edge.target_node_id == "pid-dst"
    assert edge.confidence == 0.9
    assert edge.relation_label == "需要"
    assert len(evidence_pairs) == 2
