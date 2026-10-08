"""Cross-fragment entity alignment (exact/alias/embedding) and edge merging."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal, Mapping, Sequence

from tap.modules.graph.domain.models import GraphEdge, GraphSnapshotDraft, RelationOrigin
from tap.modules.graph.domain.project import ProjectEdge, project_edge_id, project_node_id
from tap.modules.graph.domain.vocabulary import normalize_key

ALIGNMENT_SUFFIXES: tuple[str, ...] = (
    "规则",
    "条款",
    "流程",
    "政策",
    "要求",
    "policy",
    "rule",
    "rules",
    "process",
    "clause",
)


def alignment_key(text: str) -> str:
    """Normalize text to a comparison key, stripping one trailing modifier word."""
    key = normalize_key(text)
    for suffix in ALIGNMENT_SUFFIXES:
        if len(key) > len(suffix) and key.endswith(suffix):
            return key[: -len(suffix)]
    return key


@dataclass(frozen=True, slots=True)
class ResolvedEntity:
    node_id: str
    label: str
    node_type: str
    canonical_key: str
    aliases: tuple[str, ...]
    members: tuple[tuple[str, str], ...]
    rule: Literal["EXACT", "ALIAS", "EMBEDDING"]


@dataclass(slots=True)
class _Cluster:
    node_type: str
    canonical_key: str
    level1_keys: set[str]
    level2_keys: set[str]
    members: list[tuple[str, str]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    model_aliases: list[str] = field(default_factory=list)
    rule: Literal["EXACT", "ALIAS", "EMBEDDING"] = "EXACT"


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    dot = sum(x * y for x, y in zip(left, right))
    norm_left = math.sqrt(sum(x * x for x in left))
    norm_right = math.sqrt(sum(y * y for y in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return dot / (norm_left * norm_right)


def _majority(values: Sequence[str]) -> str:
    """Return the modal value, breaking ties by first occurrence."""
    counts = Counter(values)
    best_count = max(counts.values())
    for value in values:
        if counts[value] == best_count:
            return value
    raise AssertionError("unreachable: values is nonempty")


def resolve_entities(
    fragments: Sequence[GraphSnapshotDraft],
    *,
    embeddings: Mapping[tuple[str, str], tuple[float, ...]] | None = None,
    threshold: float = 0.92,
) -> tuple[ResolvedEntity, ...]:
    """Merge Fragment nodes into Project entities across EXACT/ALIAS/EMBEDDING levels."""
    ordered_fragments = sorted(fragments, key=lambda fragment: fragment.snapshot.snapshot_id)
    clusters: list[_Cluster] = []

    for fragment in ordered_fragments:
        snapshot_id = fragment.snapshot.snapshot_id
        for node in fragment.nodes:
            key1 = alignment_key(node.canonical_key)
            keys2 = {alignment_key(node.label)} | {alignment_key(alias) for alias in node.aliases}

            target: _Cluster | None = None
            rule_used: Literal["EXACT", "ALIAS", "EMBEDDING"] | None = None

            for cluster in clusters:
                if cluster.node_type == node.node_type and key1 in cluster.level1_keys:
                    target, rule_used = cluster, "EXACT"
                    break

            if target is None:
                for cluster in clusters:
                    if cluster.node_type == node.node_type and cluster.level2_keys & keys2:
                        target, rule_used = cluster, "ALIAS"
                        break

            if target is None and embeddings:
                vector = embeddings.get((snapshot_id, node.node_id))
                if vector is not None:
                    best_cluster: _Cluster | None = None
                    best_score = -1.0
                    for cluster in clusters:
                        if cluster.node_type != node.node_type:
                            continue
                        for member_snapshot_id, member_node_id in cluster.members:
                            member_vector = embeddings.get((member_snapshot_id, member_node_id))
                            if member_vector is None:
                                continue
                            score = _cosine_similarity(vector, member_vector)
                            if score >= threshold and score > best_score:
                                best_score, best_cluster = score, cluster
                    if best_cluster is not None:
                        target, rule_used = best_cluster, "EMBEDDING"

            if target is None:
                target = _Cluster(
                    node_type=node.node_type,
                    canonical_key=node.canonical_key,
                    level1_keys=set(),
                    level2_keys=set(),
                )
                clusters.append(target)
                rule_used = "EXACT"

            target.members.append((snapshot_id, node.node_id))
            target.level1_keys.add(key1)
            target.level2_keys |= keys2
            target.labels.append(node.label)
            target.model_aliases.extend(node.aliases)
            target.rule = rule_used if rule_used is not None else target.rule

    resolved: list[ResolvedEntity] = []
    for cluster in clusters:
        label = _majority(cluster.labels)
        seen: set[str] = set()
        aliases: list[str] = []
        for text in (*cluster.labels, *cluster.model_aliases):
            if text == label or text in seen:
                continue
            seen.add(text)
            aliases.append(text)
        node_id = project_node_id(cluster.node_type, alignment_key(cluster.canonical_key))
        resolved.append(
            ResolvedEntity(
                node_id=node_id,
                label=label,
                node_type=cluster.node_type,
                canonical_key=cluster.canonical_key,
                aliases=tuple(aliases),
                members=tuple(cluster.members),
                rule=cluster.rule,
            )
        )
    return tuple(resolved)


def merge_edges(
    fragments: Sequence[GraphSnapshotDraft],
    entity_of: Mapping[tuple[str, str], str],
) -> tuple[tuple[ProjectEdge, tuple[tuple[str, str], ...]], ...]:
    """Union Fragment edges whose aligned endpoints coincide; drop endpoint self-loops."""
    ordered_fragments = sorted(fragments, key=lambda fragment: fragment.snapshot.snapshot_id)
    groups: dict[tuple[str, str, str], list[tuple[str, GraphEdge]]] = {}

    for fragment in ordered_fragments:
        snapshot_id = fragment.snapshot.snapshot_id
        for edge in fragment.edges:
            source_id = entity_of[(snapshot_id, edge.source_node_id)]
            target_id = entity_of[(snapshot_id, edge.target_node_id)]
            if source_id == target_id:
                continue
            key = (source_id, edge.relation_type, target_id)
            groups.setdefault(key, []).append((snapshot_id, edge))

    results: list[tuple[ProjectEdge, tuple[tuple[str, str], ...]]] = []
    for (source_id, relation_type, target_id), items in groups.items():
        confidence = max(edge.confidence for _, edge in items)
        non_blank_labels = [edge.relation_label for _, edge in items if edge.relation_label.strip()]
        relation_label = _majority(non_blank_labels) if non_blank_labels else relation_type
        origin = (
            RelationOrigin.EXTRACTED
            if any(edge.origin is RelationOrigin.EXTRACTED for _, edge in items)
            else RelationOrigin.INFERRED
        )
        edge_id = project_edge_id(source_id, relation_type, target_id)
        evidence = tuple((snapshot_id, edge.edge_id) for snapshot_id, edge in items)
        results.append(
            (
                ProjectEdge(
                    edge_id=edge_id,
                    source_node_id=source_id,
                    target_node_id=target_id,
                    relation_type=relation_type,
                    relation_label=relation_label,
                    origin=origin,
                    confidence=confidence,
                ),
                evidence,
            )
        )
    return tuple(results)
