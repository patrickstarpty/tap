"""Cross-fragment entity alignment (exact/alias/embedding) and edge merging."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal, Mapping, Sequence

import numpy as np
import numpy.typing as npt

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
    position: int
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


# The index's matrix-product scores and `_cosine_similarity` differ only by
# float rounding (on the order of the vector length times 1e-16, orders of
# magnitude below this tolerance for any realistic embedding size); every
# decision that rounding could flip is re-taken with `_cosine_similarity`
# itself once the index has narrowed the candidates.
_SCORE_TOLERANCE = 1e-9
# Squared norms outside this range (or non-finite) can lose precision in
# `_cosine_similarity`'s own sums, so a node type holding any such vector is
# aligned by the plain scan instead of the index.
_MIN_INDEXED_NORM_SQUARED = 1e-150
_MAX_INDEXED_NORM_SQUARED = 1e150


def _indexable_norm_squared(vector: tuple[float, ...]) -> float | None:
    norm_squared = sum(x * x for x in vector)
    if norm_squared == 0.0:
        return 0.0
    if not math.isfinite(norm_squared):
        return None
    if not _MIN_INDEXED_NORM_SQUARED <= norm_squared <= _MAX_INDEXED_NORM_SQUARED:
        return None
    return norm_squared


class _TypeEmbeddingIndex:
    """Unit-normalized vectors of one node type's placed members, one row per
    member in placement order, with the cluster each row was placed in.

    Every placed node is a member of exactly one cluster, so "best member of
    any same-type cluster" is "best row", and the earliest cluster among the
    rows tied at the best score is the cluster the plain scan picks."""

    def __init__(self, dimension: int) -> None:
        self._rows: npt.NDArray[np.float64] = np.empty((64, dimension), dtype=np.float64)
        self._count = 0
        self.keys: list[tuple[str, str]] = []
        self.clusters: list[_Cluster] = []

    def append(
        self, key: tuple[str, str], unit: npt.NDArray[np.float64], cluster: _Cluster
    ) -> None:
        if self._count == len(self._rows):
            grown = np.empty((2 * len(self._rows), self._rows.shape[1]), dtype=np.float64)
            grown[: self._count] = self._rows[: self._count]
            self._rows = grown
        self._rows[self._count] = unit
        self._count += 1
        self.keys.append(key)
        self.clusters.append(cluster)

    def scores(self, unit: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        return self._rows[: self._count] @ unit


class _EmbeddingAligner:
    """EMBEDDING-level matching: the cluster holding the most similar
    same-type member at or above `threshold`, ties to the earliest-created
    cluster -- the same answer as scoring every member of every cluster.

    Node types whose vectors share one length and a well-scaled norm go
    through a per-type matrix index that scores all placed members with one
    matrix-vector product and re-scores only the near-best rows exactly; any
    other node type falls back to the plain scan."""

    def __init__(
        self,
        ordered_fragments: Sequence[GraphSnapshotDraft],
        embeddings: Mapping[tuple[str, str], tuple[float, ...]],
        threshold: float,
    ) -> None:
        self._embeddings = embeddings
        self._threshold = threshold
        self._units: dict[tuple[str, str], npt.NDArray[np.float64]] = {}
        dimensions: dict[str, int | None] = {}
        for fragment in ordered_fragments:
            snapshot_id = fragment.snapshot.snapshot_id
            for node in fragment.nodes:
                key = (snapshot_id, node.node_id)
                vector = embeddings.get(key)
                if vector is None:
                    continue
                norm_squared = _indexable_norm_squared(vector)
                known = dimensions.get(node.node_type, len(vector))
                if norm_squared is None or known != len(vector):
                    dimensions[node.node_type] = None
                    continue
                dimensions.setdefault(node.node_type, len(vector))
                if norm_squared == 0.0:
                    self._units[key] = np.zeros(len(vector), dtype=np.float64)
                else:
                    self._units[key] = np.asarray(vector, dtype=np.float64) / math.sqrt(
                        norm_squared
                    )
        self._indexes: dict[str, _TypeEmbeddingIndex] = {
            node_type: _TypeEmbeddingIndex(dimension)
            for node_type, dimension in dimensions.items()
            if dimension is not None
        }

    def place(self, node_type: str, key: tuple[str, str], cluster: _Cluster) -> None:
        index = self._indexes.get(node_type)
        unit = self._units.get(key)
        if index is not None and unit is not None:
            index.append(key, unit, cluster)

    def best_cluster(
        self, node_type: str, key: tuple[str, str], clusters: Sequence[_Cluster]
    ) -> _Cluster | None:
        vector = self._embeddings.get(key)
        if vector is None:
            return None
        index = self._indexes.get(node_type)
        if index is None:
            return self._scan(node_type, vector, clusters)
        unit = self._units[key]
        scores = index.scores(unit)
        if not len(scores):
            return None
        floor = self._threshold - _SCORE_TOLERANCE
        top = float(scores.max())
        if not top >= floor:
            return None
        # Rows whose exact score could still equal the exact maximum.
        rows = np.flatnonzero(scores >= max(top - 2 * _SCORE_TOLERANCE, floor))
        winners = {index.clusters[int(row)].position for row in rows}
        if (
            len(winners) == 1
            and top - _SCORE_TOLERANCE >= self._threshold
            and top - _SCORE_TOLERANCE > -1.0
        ):
            return index.clusters[int(rows[0])]
        best_cluster: _Cluster | None = None
        best_score = -1.0
        for row in sorted(
            (int(row) for row in rows), key=lambda item: (index.clusters[item].position, item)
        ):
            score = _cosine_similarity(vector, self._embeddings[index.keys[row]])
            if score >= self._threshold and score > best_score:
                best_score, best_cluster = score, index.clusters[row]
        return best_cluster

    def _scan(
        self, node_type: str, vector: tuple[float, ...], clusters: Sequence[_Cluster]
    ) -> _Cluster | None:
        best_cluster: _Cluster | None = None
        best_score = -1.0
        for cluster in clusters:
            if cluster.node_type != node_type:
                continue
            for member in cluster.members:
                member_vector = self._embeddings.get(member)
                if member_vector is None:
                    continue
                score = _cosine_similarity(vector, member_vector)
                if score >= self._threshold and score > best_score:
                    best_score, best_cluster = score, cluster
        return best_cluster


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
    # A level-1 key joins a cluster only when no same-type cluster already holds it
    # (otherwise the EXACT match below would have picked that cluster), so each
    # ``(node_type, key)`` names at most one cluster and a dict lookup returns the
    # same cluster a scan in creation order would.
    exact_index: dict[tuple[str, str], _Cluster] = {}
    # Level-2 keys can be shared by several clusters, and the ALIAS match is the
    # earliest-created one, so the index keeps the smallest creation position per
    # ``(node_type, key)``; the minimum over a node's keys is that same cluster.
    alias_index: dict[tuple[str, str], int] = {}
    # EMBEDDING matches the best-scoring same-type member of any cluster; the
    # aligner indexes placed members' vectors per node type so each node is
    # scored against all of them in one matrix product (see `_EmbeddingAligner`).
    aligner = _EmbeddingAligner(ordered_fragments, embeddings, threshold) if embeddings else None

    for fragment in ordered_fragments:
        snapshot_id = fragment.snapshot.snapshot_id
        for node in fragment.nodes:
            key1 = alignment_key(node.canonical_key)
            keys2 = {alignment_key(node.label)} | {alignment_key(alias) for alias in node.aliases}

            target: _Cluster | None = None
            rule_used: Literal["EXACT", "ALIAS", "EMBEDDING"] | None = None

            exact_match = exact_index.get((node.node_type, key1))
            if exact_match is not None:
                target, rule_used = exact_match, "EXACT"

            if target is None:
                alias_positions = [
                    position
                    for key in keys2
                    if (position := alias_index.get((node.node_type, key))) is not None
                ]
                if alias_positions:
                    target, rule_used = clusters[min(alias_positions)], "ALIAS"

            node_key = (snapshot_id, node.node_id)
            if target is None and aligner is not None:
                best_cluster = aligner.best_cluster(node.node_type, node_key, clusters)
                if best_cluster is not None:
                    target, rule_used = best_cluster, "EMBEDDING"

            if target is None:
                target = _Cluster(
                    position=len(clusters),
                    node_type=node.node_type,
                    canonical_key=node.canonical_key,
                    level1_keys=set(),
                    level2_keys=set(),
                )
                clusters.append(target)
                rule_used = "EXACT"

            target.members.append(node_key)
            if aligner is not None:
                aligner.place(node.node_type, node_key, target)
            target.level1_keys.add(key1)
            exact_index.setdefault((node.node_type, key1), target)
            target.level2_keys |= keys2
            for key in keys2:
                known_position = alias_index.get((node.node_type, key))
                if known_position is None or target.position < known_position:
                    alias_index[(node.node_type, key)] = target.position
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
