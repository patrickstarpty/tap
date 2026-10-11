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


def test_merge_edges_ignores_blank_label_when_a_real_label_exists():
    a = _edge_fragment(
        "frag-a",
        ["n-src", "n-dst"],
        [("e1", "n-src", "n-dst", "REQUIRES", 0.6, "", "ev-a1")],
    )
    b = _edge_fragment(
        "frag-b",
        ["m-src", "m-dst"],
        [("e2", "m-src", "m-dst", "REQUIRES", 0.9, "需要", "ev-b1")],
    )
    entity_of = {
        ("frag-a", "n-src"): "pid-src",
        ("frag-a", "n-dst"): "pid-dst",
        ("frag-b", "m-src"): "pid-src",
        ("frag-b", "m-dst"): "pid-dst",
    }

    merged = merge_edges([a, b], entity_of)

    assert len(merged) == 1
    edge, _ = merged[0]
    assert edge.relation_label == "需要"


def test_merge_edges_falls_back_to_relation_type_when_all_labels_blank():
    a = _edge_fragment(
        "frag-a",
        ["n-src", "n-dst"],
        [("e1", "n-src", "n-dst", "REQUIRES", 0.6, "", "ev-a1")],
    )
    entity_of = {
        ("frag-a", "n-src"): "pid-src",
        ("frag-a", "n-dst"): "pid-dst",
    }

    merged = merge_edges([a], entity_of)

    assert len(merged) == 1
    edge, _ = merged[0]
    assert edge.relation_label == "REQUIRES"
    assert edge.relation_label != ""


def _linear_scan_clusters(
    fragments: Sequence[GraphSnapshotDraft],
    embeddings: dict[tuple[str, str], tuple[float, ...]] | None,
    threshold: float,
) -> list[tuple[str, tuple[tuple[str, str], ...], str]]:
    """Reference resolver that scans every cluster for every node, in creation order.

    It states the alignment semantics directly (first cluster of the same type whose
    level-1 keys contain the node's key, else first whose level-2 keys intersect,
    else the best embedding match) so the indexed resolver can be checked against it.
    """
    clusters: list[dict[str, object]] = []
    for fragment in sorted(fragments, key=lambda item: item.snapshot.snapshot_id):
        snapshot_id = fragment.snapshot.snapshot_id
        for node in fragment.nodes:
            key1 = alignment_key(node.canonical_key)
            keys2 = {alignment_key(node.label)} | {alignment_key(a) for a in node.aliases}
            target = None
            rule = None
            for cluster in clusters:
                if cluster["type"] == node.node_type and key1 in cluster["l1"]:
                    target, rule = cluster, "EXACT"
                    break
            if target is None:
                for cluster in clusters:
                    if cluster["type"] == node.node_type and cluster["l2"] & keys2:
                        target, rule = cluster, "ALIAS"
                        break
            if target is None and embeddings:
                vector = embeddings.get((snapshot_id, node.node_id))
                if vector is not None:
                    best, best_score = None, -1.0
                    for cluster in clusters:
                        if cluster["type"] != node.node_type:
                            continue
                        for member in cluster["members"]:
                            other = embeddings.get(member)
                            if other is None:
                                continue
                            dot = sum(x * y for x, y in zip(vector, other))
                            norm = (
                                sum(x * x for x in vector) ** 0.5 * sum(y * y for y in other) ** 0.5
                            )
                            score = 0.0 if norm == 0.0 else dot / norm
                            if score >= threshold and score > best_score:
                                best, best_score = cluster, score
                    if best is not None:
                        target, rule = best, "EMBEDDING"
            if target is None:
                target = {
                    "type": node.node_type,
                    "l1": set(),
                    "l2": set(),
                    "members": [],
                    "rule": "EXACT",
                }
                clusters.append(target)
                rule = "EXACT"
            target["members"].append((snapshot_id, node.node_id))
            target["l1"].add(key1)
            target["l2"] |= keys2
            target["rule"] = rule
    return [
        (str(cluster["type"]), tuple(cluster["members"]), str(cluster["rule"]))
        for cluster in clusters
    ]


def test_indexed_alignment_matches_a_full_cluster_scan_on_overlapping_fragments():
    import random

    words = ["核保", "理赔", "保单", "退保", "refund", "claim", "policy holder", "agent"]
    suffixes = ["", "", "规则", "流程", " policy", " rule"]
    node_types = ["CONCEPT", "ENTITY", "PROCESS"]
    directions = [(1.0, 0.0), (0.0, 1.0), (0.96, 0.28), (0.7, 0.7)]

    for seed in range(40):
        rng = random.Random(seed)
        fragments = []
        embeddings: dict[tuple[str, str], tuple[float, ...]] = {}
        for fragment_index in range(rng.randint(2, 6)):
            snapshot_id = f"frag-{rng.randint(0, 99):02d}-{fragment_index}"
            specs = []
            for node_index in range(rng.randint(1, 12)):
                key = rng.choice(words) + rng.choice(suffixes)
                label = rng.choice(words) + rng.choice(suffixes)
                aliases = tuple(
                    dict.fromkeys(
                        rng.choice(words) + rng.choice(suffixes) for _ in range(rng.randint(0, 2))
                    )
                )
                node_id = f"n{node_index}"
                specs.append((node_id, label, rng.choice(node_types), key, aliases))
                if rng.random() < 0.5:
                    embeddings[(snapshot_id, node_id)] = rng.choice(directions)
            fragments.append(_fragment(snapshot_id, specs))

        for supplied in (None, embeddings):
            expected = _linear_scan_clusters(fragments, supplied, 0.92)
            actual = [
                (entity.node_type, entity.members, entity.rule)
                for entity in resolve_entities(fragments, embeddings=supplied, threshold=0.92)
            ]
            assert actual == expected, f"seed {seed} embeddings={supplied is not None}"
