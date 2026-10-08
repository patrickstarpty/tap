from __future__ import annotations

from tap.modules.graph.application.communities import (
    OTHER_COMMUNITY_ID,
    community_label,
    propagate_labels,
)


def _clique(prefix: str, size: int) -> tuple[list[str], list[tuple[str, str]]]:
    nodes = [f"{prefix}{index}" for index in range(size)]
    edges = [(a, b) for i, a in enumerate(nodes) for b in nodes[i + 1 :]]
    return nodes, edges


def test_two_cliques_joined_by_one_edge_form_two_communities():
    nodes_a, edges_a = _clique("a", 4)
    nodes_b, edges_b = _clique("b", 4)
    node_ids = nodes_a + nodes_b
    edges = edges_a + edges_b + [("a0", "b0")]

    assignment = propagate_labels(node_ids, edges, min_size=3)

    communities = {assignment[node_id] for node_id in node_ids}
    assert communities == {"community_001", "community_002"}
    assert len({assignment[node_id] for node_id in nodes_a}) == 1
    assert len({assignment[node_id] for node_id in nodes_b}) == 1


def test_small_communities_fold_into_other():
    assignment = propagate_labels(["a", "b", "c", "d", "e"], [("a", "b")], min_size=3)
    assert set(assignment.values()) == {OTHER_COMMUNITY_ID}


def test_label_propagation_is_deterministic_and_bounded():
    nodes_a, edges_a = _clique("a", 4)
    nodes_b, edges_b = _clique("b", 4)
    node_ids = nodes_a + nodes_b
    edges = edges_a + edges_b + [("a0", "b0")]

    first = propagate_labels(node_ids, edges, min_size=3)
    second = propagate_labels(node_ids, edges, min_size=3)
    assert first == second

    bounded = propagate_labels(node_ids, edges, max_rounds=1, min_size=3)
    assert set(bounded) == set(node_ids)


def test_community_count_is_capped_at_fifty():
    node_ids: list[str] = []
    edges: list[tuple[str, str]] = []
    for index in range(60):
        prefix = f"t{index:02d}"
        triple, triple_edges = _clique(prefix, 3)
        node_ids.extend(triple)
        edges.extend(triple_edges)

    assignment = propagate_labels(node_ids, edges, min_size=3)

    assigned_ids = set(assignment.values())
    numbered = {community_id for community_id in assigned_ids if community_id != OTHER_COMMUNITY_ID}
    assert len(numbered) == 50
    assert OTHER_COMMUNITY_ID in assigned_ids


def test_community_label_picks_highest_degree_member_with_smallest_id_tiebreak():
    degree = {"n-b": 2, "n-a": 2, "n-c": 1}
    label = {"n-b": "Beta", "n-a": "Alpha", "n-c": "Gamma"}
    assert community_label(["n-b", "n-a", "n-c"], degree, label) == "Alpha"
