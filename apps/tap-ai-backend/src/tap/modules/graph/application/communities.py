"""Deterministic label propagation and community labeling for the Project graph."""

from __future__ import annotations

from collections import Counter
from typing import Mapping, Sequence

OTHER_COMMUNITY_ID = "community_other"
OTHER_COMMUNITY_LABEL = "其他"


def _smallest_mode(values: Sequence[str]) -> str:
    """Return the modal value, breaking ties by lexicographically smallest value."""
    counts = Counter(values)
    best_count = max(counts.values())
    return min(value for value, count in counts.items() if count == best_count)


def propagate_labels(
    node_ids: Sequence[str],
    edges: Sequence[tuple[str, str]],
    *,
    max_rounds: int = 20,
    max_communities: int = 50,
    min_size: int = 3,
) -> dict[str, str]:
    """Assign every node to a numbered or overflow (`community_other`) community."""
    nodes = list(node_ids)
    label: dict[str, str] = {node_id: node_id for node_id in nodes}
    neighbors: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for source, target in edges:
        neighbors[source].append(target)
        neighbors[target].append(source)

    for _ in range(max_rounds):
        previous = dict(label)
        changed = False
        for node_id in sorted(nodes):
            neighbor_labels = [previous[neighbor] for neighbor in neighbors[node_id]]
            if not neighbor_labels:
                continue
            candidate = _smallest_mode(neighbor_labels)
            if candidate != label[node_id]:
                label[node_id] = candidate
                changed = True
        if not changed:
            break

    groups: dict[str, list[str]] = {}
    for node_id in nodes:
        groups.setdefault(label[node_id], []).append(node_id)
    ordered_communities = sorted(groups.values(), key=lambda members: (-len(members), min(members)))

    assignment: dict[str, str] = {}
    for index, members in enumerate(ordered_communities):
        if index < max_communities and len(members) >= min_size:
            community_id = f"community_{index + 1:03d}"
        else:
            community_id = OTHER_COMMUNITY_ID
        for member in members:
            assignment[member] = community_id
    return assignment


def community_label(
    member_ids: Sequence[str],
    degree: Mapping[str, int],
    label: Mapping[str, str],
) -> str:
    """Return the label of the highest-degree member, tie-broken by smallest id."""
    best_id = min(member_ids, key=lambda node_id: (-degree.get(node_id, 0), node_id))
    return label[best_id]
