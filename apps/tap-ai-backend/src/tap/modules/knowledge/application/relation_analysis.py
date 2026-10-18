"""Deterministic relation analysis pipeline over the merged project graph.

Every function here is a pure or deterministic-async step -- no model calls --
that turns retrieval evidence and a standalone question into R-labeled edge
citations (`RelationContext`). The pipeline is: seed (from evidence and from
the question's aliases) -> expand (bounded neighborhood) -> find paths
(bounded hops between seeds) -> rank edges (path edges first, then confidence
x seed adjacency) -> assemble (resolve each ranked edge's support against
already-cited chunks (S) or fresh snippets, numbering R1..Rn).

`assemble` and `snippet_chunk_refs` take `ranked` as
`tuple[tuple[ProjectEdge, bool, tuple[EdgeEvidence, ...]], ...]`: the
`(edge, on_path)` pairs `rank_edges` returns, joined by the caller with each
edge's candidate `EdgeEvidence` rows (e.g. from the `expand()` subgraph's
`evidence`, grouped by `edge_id`) so support resolution has something to
resolve against.
"""

from __future__ import annotations

import dataclasses
import re
from collections import deque
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.domain.project import (
    STRUCTURAL_NODE_KEY_PREFIXES,
    EdgeEvidence,
    NodeSource,
    ProjectEdge,
    ProjectNode,
    ProjectSubgraph,
)
from tap.modules.graph.domain.vocabulary import normalize_key
from tap.modules.graph.ports.project_store import ProjectGraphStorePort
from tap.modules.knowledge.domain.models import RelationContextStatus

__all__ = [
    "EvidenceRef",
    "PathExpansion",
    "RelationAnalysisInput",
    "RelationContext",
    "RelationContextStatus",
    "RelationEvidence",
    "RelationPath",
    "RelationSupport",
    "SeedNode",
    "assemble",
    "expand",
    "expansion_depth",
    "find_paths",
    "path_from_subgraph",
    "rank_edges",
    "seed_from_evidence",
    "seed_from_query",
    "snippet_chunk_refs",
]

_RELATION_LABEL_PATTERN = re.compile(r"^R(?:[1-9]|1[0-9]|20)$")
_SNIPPET_MAX = 300
_MAX_RELATIONS = 20
_MAX_SNIPPET_SUPPORTS_PER_RELATION = 2


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    label: str
    chunk_id: str
    source_revision_id: str
    document_revision_id: str


@dataclass(frozen=True, slots=True)
class RelationAnalysisInput:
    """Per-invocation payload of the relation analysis subgraph. The
    authorized source revision selection and the pinned project graph version
    travel only on `AgentContext`, never duplicated here."""

    query: str
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True)
class SeedNode:
    node_id: str
    label: str
    origin: Literal["evidence", "query"]


@dataclass(frozen=True, slots=True)
class RelationSupport:
    chunk_id: str
    source_revision_id: str
    document_revision_id: str
    content_digest: str
    anchor: Mapping[str, object]
    evidence_label: str | None = None
    snippet: str | None = None
    # The support's document root identity (distinct from `document_revision_id`,
    # which is the *revision*) -- carried alongside a snippet-only support so a
    # citation can later be reconstructed via `chunk_id_for`/`logical_chunk_id_for`
    # without the answer pipeline needing its own document lookup. Populated only
    # for snippet-only supports (`evidence_label is None`); an S-labelled support's
    # citation is always copied from the already-resolved evidence instead.
    document_id: str | None = None

    def __post_init__(self) -> None:
        if self.evidence_label is not None and self.snippet is not None:
            raise ValueError("relation support cannot carry both an evidence_label and a snippet")
        if self.snippet is not None and len(self.snippet) > _SNIPPET_MAX:
            raise ValueError(f"relation support snippet must be at most {_SNIPPET_MAX} characters")
        object.__setattr__(self, "anchor", MappingProxyType(dict(self.anchor)))


@dataclass(frozen=True, slots=True)
class RelationEvidence:
    """`subject_aliases`/`object_aliases` come from the project merge's `ProjectNode.aliases`,
    which carry no per-source provenance (aliases are merged across every project
    source, not just the ones selected for this answer). They are used here only
    for endpoint matching (citation validation's "claim text must contain an
    endpoint label or alias" rule) and must never be surfaced in prompts or events,
    since an alias could originate from a source outside the current selection."""

    label: str
    edge_id: str
    subject_node_id: str
    subject_label: str
    subject_aliases: tuple[str, ...]
    object_node_id: str
    object_label: str
    object_aliases: tuple[str, ...]
    relation_type: str
    relation_label: str
    origin: str
    confidence: float
    support: tuple[RelationSupport, ...]
    on_path: bool

    def __post_init__(self) -> None:
        if not _RELATION_LABEL_PATTERN.fullmatch(self.label):
            raise ValueError(f"relation evidence label must be R1..R20, got {self.label!r}")
        if not self.support:
            raise ValueError("relation evidence requires at least one support")


@dataclass(frozen=True, slots=True)
class PathExpansion:
    """`find_paths`'s full result: the ordered paths themselves (unchanged
    shape, still what callers primarily consume) plus every edge and edge
    evidence row `store.path(...)` returned along the way -- already
    selection-filtered and version-pinned by the same call -- so a caller can
    merge path-only edges (ones outside the node-limited `expand()` subgraph)
    into its own ranking pool instead of silently losing them."""

    paths: tuple["RelationPath", ...]
    edges: tuple[ProjectEdge, ...] = ()
    evidence: tuple[EdgeEvidence, ...] = ()
    nodes: tuple[ProjectNode, ...] = ()


@dataclass(frozen=True, slots=True)
class RelationPath:
    node_ids: tuple[str, ...]
    node_labels: tuple[str, ...]
    edge_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.node_ids) != len(self.edge_ids) + 1:
            raise ValueError("relation path must have exactly one more node than edges")
        if len(self.node_ids) != len(self.node_labels):
            raise ValueError("relation path node_ids and node_labels must align")


@dataclass(frozen=True, slots=True)
class RelationContext:
    status: RelationContextStatus
    graph_version: str | None
    seeds: tuple[SeedNode, ...] = ()
    relations: tuple[RelationEvidence, ...] = ()
    paths: tuple[RelationPath, ...] = ()
    augment_chunks: tuple[RelationSupport, ...] = ()
    diagnostics: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.status is not RelationContextStatus.APPLIED and self.relations:
            raise ValueError("non-applied relation context cannot carry relations")
        if self.status is RelationContextStatus.APPLIED:
            if not self.relations:
                raise ValueError("applied relation context requires at least one relation")
            if not self.graph_version:
                raise ValueError("applied relation context requires a graph_version")
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))

    @property
    def query_seed_count(self) -> int:
        """Seeds the question itself named (alias matches), as opposed to
        seeds reached only through retrieval evidence."""
        return sum(1 for seed in self.seeds if seed.origin == "query")

    def by_label(self) -> Mapping[str, RelationEvidence]:
        return MappingProxyType({relation.label: relation for relation in self.relations})

    def rebind(self, evidence: tuple[EvidenceRef, ...]) -> "RelationContext":
        """Move any support whose `chunk_id` now appears in `evidence` onto
        that S label (and drop its snippet); drop matching chunks from
        `augment_chunks` so they are not double-cited."""
        chunk_labels = {ref.chunk_id: ref.label for ref in evidence}
        if not chunk_labels:
            return self

        def rebind_support(support: RelationSupport) -> RelationSupport:
            label = chunk_labels.get(support.chunk_id)
            if label is None:
                return support
            return dataclasses.replace(support, evidence_label=label, snippet=None)

        new_relations = tuple(
            dataclasses.replace(
                relation, support=tuple(rebind_support(item) for item in relation.support)
            )
            for relation in self.relations
        )
        new_augment_chunks = tuple(
            chunk for chunk in self.augment_chunks if chunk.chunk_id not in chunk_labels
        )
        return dataclasses.replace(self, relations=new_relations, augment_chunks=new_augment_chunks)

    def to_prompt_records(self) -> tuple[dict[str, object], ...]:
        records: list[dict[str, object]] = []
        for relation in self.relations:
            evidence_items: list[dict[str, object]] = []
            for support in relation.support:
                if support.evidence_label:
                    evidence_items.append({"label": support.evidence_label})
                else:
                    evidence_items.append(
                        {
                            "chunkId": support.chunk_id,
                            "snippet": support.snippet,
                            "anchor": dict(support.anchor),
                        }
                    )
            records.append(
                {
                    "label": relation.label,
                    "subject": relation.subject_label,
                    "relationType": relation.relation_type,
                    "relationLabel": relation.relation_label,
                    "object": relation.object_label,
                    "origin": relation.origin,
                    "confidence": relation.confidence,
                    "evidence": evidence_items,
                }
            )
        return tuple(records)

    def path_summaries(self, limit: int = 3) -> tuple[tuple[str, ...], ...]:
        return tuple(path.node_labels for path in self.paths[:limit])


def _source_key(item: NodeSource) -> tuple[object, ...]:
    return (
        item.node_id,
        item.source_revision_id,
        item.document_revision_id,
        item.chunk_id,
        item.fragment_snapshot_id,
        item.fragment_node_id,
    )


def _is_structural(node: ProjectNode) -> bool:
    return node.canonical_key.startswith(STRUCTURAL_NODE_KEY_PREFIXES)


def _evidence_key(item: EdgeEvidence) -> tuple[object, ...]:
    return (
        item.edge_id,
        item.source_revision_id,
        item.document_revision_id,
        item.chunk_id,
        item.fragment_snapshot_id,
        item.fragment_edge_id,
    )


async def seed_from_evidence(
    store: ProjectGraphStorePort,
    scope: ProjectScopeContext,
    version: int,
    evidence: tuple[EvidenceRef, ...],
) -> tuple[ProjectNode, ...]:
    chunk_ids = tuple(dict.fromkeys(ref.chunk_id for ref in evidence))
    if not chunk_ids:
        return ()
    node_ids = tuple(dict.fromkeys(await store.nodes_for_chunks(scope, chunk_ids, version=version)))
    if not node_ids:
        return ()
    nodes_by_id = {
        node.node_id: node for node in await store.nodes(scope, node_ids, version=version)
    }
    return tuple(
        nodes_by_id[node_id]
        for node_id in node_ids
        if node_id in nodes_by_id and not _is_structural(nodes_by_id[node_id])
    )


async def seed_from_query(
    store: ProjectGraphStorePort,
    scope: ProjectScopeContext,
    version: int,
    query: str,
    *,
    allowed_source_revision_ids: frozenset[str],
) -> tuple[ProjectNode, ...]:
    """Alias-match the question, then keep only matches that have at least one
    visible source inside `allowed_source_revision_ids` -- the project merge's
    aliases carry no per-source provenance, so an alias hit alone cannot tell
    whether the node is actually backed by a selected source. Visibility is
    checked with a one-hop, one-node `store.neighbors` call pinned to
    `version` and filtered to the selection: `_node_visible` (the store's own
    visibility rule) always keeps the queried node itself when it is visible,
    regardless of `node_limit`. An empty selection returns `()` without
    calling the store."""
    if not allowed_source_revision_ids:
        return ()
    source_revision_ids = tuple(sorted(allowed_source_revision_ids))
    matches = await store.match_aliases(scope, query, version=version)
    node_ids = tuple(dict.fromkeys(match.node_id for match in matches))
    if not node_ids:
        return ()
    nodes_by_id = {
        node.node_id: node for node in await store.nodes(scope, node_ids, version=version)
    }
    visible_node_ids: list[str] = []
    for node_id in node_ids:
        if node_id not in nodes_by_id or _is_structural(nodes_by_id[node_id]):
            continue
        visible = await store.neighbors(
            scope,
            node_id,
            depth=1,
            node_limit=1,
            source_revision_ids=source_revision_ids,
            version=version,
        )
        if any(node.node_id == node_id for node in visible.nodes):
            visible_node_ids.append(node_id)
    return tuple(nodes_by_id[node_id] for node_id in visible_node_ids)


def expansion_depth(seed_count: int) -> int:
    return 2 if seed_count < 3 else 1


async def expand(
    store: ProjectGraphStorePort,
    scope: ProjectScopeContext,
    version: int,
    seeds: tuple[ProjectNode, ...],
    *,
    allowed_source_revision_ids: frozenset[str],
    node_limit: int = 60,
    edge_limit: int = 200,
) -> ProjectSubgraph:
    if not allowed_source_revision_ids:
        return ProjectSubgraph(version=version, nodes=(), edges=(), sources=(), evidence=())

    depth = expansion_depth(len(seeds))
    source_revision_ids = tuple(sorted(allowed_source_revision_ids))

    nodes_by_id: dict[str, ProjectNode] = {}
    edges_by_id: dict[str, ProjectEdge] = {}
    sources_seen: set[tuple[object, ...]] = set()
    sources: list[NodeSource] = []
    evidence_seen: set[tuple[object, ...]] = set()
    evidence: list[EdgeEvidence] = []

    for seed in seeds:
        subgraph = await store.neighbors(
            scope,
            seed.node_id,
            depth=depth,
            node_limit=node_limit,
            source_revision_ids=source_revision_ids,
            version=version,
        )
        for node in subgraph.nodes:
            nodes_by_id.setdefault(node.node_id, node)
        for edge in subgraph.edges:
            edges_by_id.setdefault(edge.edge_id, edge)
        for source in subgraph.sources:
            key = _source_key(source)
            if key not in sources_seen:
                sources_seen.add(key)
                sources.append(source)
        for item in subgraph.evidence:
            key = _evidence_key(item)
            if key not in evidence_seen:
                evidence_seen.add(key)
                evidence.append(item)

    # De-duplicated, given-order seed ids: a `set` iteration order is not
    # stable across processes/hash seeds, so both the discovery order below
    # and the `node_limit` cut must be driven by this ordered sequence, not
    # by iterating `seed_id_set` directly.
    seed_ids_ordered = tuple(dict.fromkeys(seed.node_id for seed in seeds))
    seed_id_set = set(seed_ids_ordered)
    ordered_node_ids = [node_id for node_id in seed_ids_ordered if node_id in nodes_by_id] + [
        node_id for node_id in nodes_by_id if node_id not in seed_id_set
    ]
    selected_node_ids = tuple(ordered_node_ids[:node_limit])
    selected_node_id_set = set(selected_node_ids)

    candidate_edges = [
        edge
        for edge in edges_by_id.values()
        if edge.source_node_id in selected_node_id_set
        and edge.target_node_id in selected_node_id_set
    ]
    seed_adjacent_edges = [
        edge
        for edge in candidate_edges
        if edge.source_node_id in seed_id_set or edge.target_node_id in seed_id_set
    ]
    seed_adjacent_edge_ids = {edge.edge_id for edge in seed_adjacent_edges}
    other_edges = [edge for edge in candidate_edges if edge.edge_id not in seed_adjacent_edge_ids]
    selected_edges = tuple((seed_adjacent_edges + other_edges)[:edge_limit])
    selected_edge_ids = {edge.edge_id for edge in selected_edges}

    selected_nodes = tuple(nodes_by_id[node_id] for node_id in selected_node_ids)
    selected_sources = tuple(item for item in sources if item.node_id in selected_node_id_set)
    selected_evidence = tuple(item for item in evidence if item.edge_id in selected_edge_ids)

    return ProjectSubgraph(
        version=version,
        nodes=selected_nodes,
        edges=selected_edges,
        sources=selected_sources,
        evidence=selected_evidence,
    )


def path_from_subgraph(
    subgraph: ProjectSubgraph, source_node_id: str, target_node_id: str
) -> RelationPath | None:
    """Rebuild an ordered `source_node_id` -> `target_node_id` path from a
    bounded-hop subgraph (e.g. `store.path(...)`'s result) by breadth-first
    search over its edges, treated as traversable in either direction."""
    nodes_by_id = {node.node_id: node for node in subgraph.nodes}
    if source_node_id not in nodes_by_id or target_node_id not in nodes_by_id:
        return None
    if source_node_id == target_node_id:
        return RelationPath((source_node_id,), (nodes_by_id[source_node_id].label,), ())

    adjacency: dict[str, list[tuple[str, str]]] = {}
    for edge in subgraph.edges:
        adjacency.setdefault(edge.source_node_id, []).append((edge.target_node_id, edge.edge_id))
        adjacency.setdefault(edge.target_node_id, []).append((edge.source_node_id, edge.edge_id))

    visited = {source_node_id}
    queue: deque[tuple[str, tuple[str, ...], tuple[str, ...]]] = deque(
        [(source_node_id, (source_node_id,), ())]
    )
    while queue:
        current, node_path, edge_path = queue.popleft()
        for neighbor, edge_id in adjacency.get(current, ()):
            if neighbor in visited:
                continue
            new_node_path = node_path + (neighbor,)
            new_edge_path = edge_path + (edge_id,)
            if neighbor == target_node_id:
                labels = tuple(nodes_by_id[node_id].label for node_id in new_node_path)
                return RelationPath(new_node_path, labels, new_edge_path)
            visited.add(neighbor)
            queue.append((neighbor, new_node_path, new_edge_path))
    return None


async def find_paths(
    store: ProjectGraphStorePort,
    scope: ProjectScopeContext,
    version: int,
    query_seeds: tuple[ProjectNode, ...],
    evidence_seeds: tuple[ProjectNode, ...],
    *,
    allowed_source_revision_ids: frozenset[str],
    max_hops: int = 3,
    path_limit: int = 10,
    max_attempts: int = 20,
) -> PathExpansion:
    if not allowed_source_revision_ids:
        return PathExpansion(paths=())
    source_revision_ids = tuple(sorted(allowed_source_revision_ids))

    pairs: list[tuple[ProjectNode, ProjectNode]] = []
    seen_pairs: set[frozenset[str]] = set()

    def add_pair(left: ProjectNode, right: ProjectNode) -> None:
        if left.node_id == right.node_id:
            return
        key = frozenset({left.node_id, right.node_id})
        if key in seen_pairs:
            return
        seen_pairs.add(key)
        pairs.append((left, right))

    # Query x evidence pairs first: a question that names one entity and has
    # retrieval evidence pointing at another is the common case this pipeline
    # exists for, so those attempts must not be starved by query x query pairs
    # when there are many query seeds (C(n, 2) grows quadratically -- 7 query
    # seeds alone already produce 21 pairs, more than `max_attempts`'s
    # default of 20).
    for left in query_seeds:
        for right in evidence_seeds:
            add_pair(left, right)
    for index, left in enumerate(query_seeds):
        for right in query_seeds[index + 1 :]:
            add_pair(left, right)

    # Bound the number of `store.path` round-trips attempted, independent of
    # how many of them actually find a path: a poorly-connected graph could
    # otherwise cost one store call per candidate pair before `path_limit`
    # successes are ever reached (or ever concluded unreachable).
    pairs = pairs[:max_attempts]

    paths: list[RelationPath] = []
    edges_by_id: dict[str, ProjectEdge] = {}
    nodes_by_id: dict[str, ProjectNode] = {}
    evidence_seen: set[tuple[object, ...]] = set()
    evidence: list[EdgeEvidence] = []
    for left, right in pairs:
        if len(paths) >= path_limit:
            break
        subgraph = await store.path(
            scope,
            left.node_id,
            right.node_id,
            max_hops=max_hops,
            source_revision_ids=source_revision_ids,
            version=version,
        )
        if not subgraph.nodes:
            continue
        path = path_from_subgraph(subgraph, left.node_id, right.node_id)
        if path is None:
            continue
        paths.append(path)
        for node in subgraph.nodes:
            nodes_by_id.setdefault(node.node_id, node)
        for edge in subgraph.edges:
            edges_by_id.setdefault(edge.edge_id, edge)
        for item in subgraph.evidence:
            key = _evidence_key(item)
            if key not in evidence_seen:
                evidence_seen.add(key)
                evidence.append(item)

    # Restrict to exactly what the found paths reference: `store.path`'s own
    # subgraph could in principle carry nodes/edges beyond the specific path
    # it reported (its own traversal bookkeeping), and nothing outside a
    # path's own node_ids/edge_ids should be exposed as "a path edge/node"
    # the caller can merge into its ranking pool.
    path_node_ids = {node_id for path in paths for node_id in path.node_ids}
    path_edge_ids = {edge_id for path in paths for edge_id in path.edge_ids}
    return PathExpansion(
        paths=tuple(paths[:path_limit]),
        edges=tuple(edge for edge in edges_by_id.values() if edge.edge_id in path_edge_ids),
        evidence=tuple(evidence),
        nodes=tuple(node for node in nodes_by_id.values() if node.node_id in path_node_ids),
    )


def _bigrams(text: str) -> frozenset[str]:
    """Character bigrams of `text` after `normalize_key` folding, used only
    as a cheap lexical-overlap signal between a question and an edge's own
    text (no Chinese tokenizer is available here). A normalized text shorter
    than two characters yields its own single character as its one "gram"
    instead of the empty set, so a short but exact match (e.g. a one-word
    query against a one-word label) still registers."""
    normalized = normalize_key(text)
    if len(normalized) < 2:
        return frozenset({normalized}) if normalized else frozenset()
    return frozenset(normalized[index : index + 2] for index in range(len(normalized) - 1))


# Bigrams of these high-frequency domain terms are ignored by `_query_relevance`:
# almost every question and almost every edge in this corpus mentions "保险"/
# "合同"/"投保人"/"被保险人" etc., so overlap on *only* these generic bigrams
# says nothing about whether an edge is actually on-topic -- it would let an
# edge that merely shares this common vocabulary outrank one that doesn't,
# with no signal either way about the specific thing asked. A small fixed
# list (not a general stopword model) keeps this deterministic and auditable.
_GENERIC_DOMAIN_TERMS: tuple[str, ...] = (
    "保险",
    "合同",
    "投保人",
    "被保险人",
    "保险人",
    "保险金",
    "保险费",
    "本合同",
    "本公司",
    "责任",
    "条款",
    "公司",
    # Common adjacent-word junctions (e.g. "...责任条款..." -> "任条"): a
    # junction bigram straddling two already-generic words is itself still
    # generic, but is not covered by either word's own bigram set alone.
    "责任条款",
    "合同责任",
    "保险责任",
    "保险合同",
)
_GENERIC_BIGRAMS: frozenset[str] = frozenset(
    bigram for term in _GENERIC_DOMAIN_TERMS for bigram in _bigrams(term)
)


def _query_relevance(
    edge: ProjectEdge, query_bigrams: frozenset[str], nodes_by_id: Mapping[str, ProjectNode]
) -> int:
    """How many of the query's own bigrams also appear in this edge's own
    text (its `relation_label` plus its endpoints' labels), excluding
    `_GENERIC_BIGRAMS` -- a tie-breaker so an edge whose text actually
    echoes the question's *specific* wording surfaces ahead of an edge that
    is merely confidence/seed-adjacency-favored but textually unrelated,
    without letting shared generic domain vocabulary alone count as
    relevance. Zero when `query_bigrams` is empty (no query text was
    supplied), leaving prior ordering untouched."""
    if not query_bigrams:
        return 0
    subject = nodes_by_id.get(edge.source_node_id)
    obj = nodes_by_id.get(edge.target_node_id)
    subject_label = subject.label if subject else ""
    object_label = obj.label if obj else ""
    text = " ".join(part for part in (edge.relation_label, subject_label, object_label) if part)
    if not text:
        return 0
    overlap = (query_bigrams & _bigrams(text)) - _GENERIC_BIGRAMS
    return len(overlap)


def rank_edges(
    subgraph: ProjectSubgraph,
    paths: tuple[RelationPath, ...],
    seed_ids: frozenset[str],
    *,
    extra_edges: tuple[ProjectEdge, ...] = (),
    limit: int = 20,
    query: str = "",
) -> tuple[tuple[ProjectEdge, bool], ...]:
    """Rank `subgraph.edges` plus `extra_edges` (e.g. `PathExpansion.edges` --
    path edges that `expand()`'s node/edge limits left outside the subgraph,
    but which `find_paths` already fetched selection-filtered and
    version-pinned), deduped by edge ID so a path edge is never ranked twice
    just because both sources happened to return it.

    `query` (optional, defaults to `""`) adds a lexical-overlap tie-breaker
    (`_query_relevance`) strictly *within* the same on-path-status and
    seed-adjacency-vs-zero group, ranked ahead of the existing confidence x
    seed-adjacency score: among edges that are equally "adjacent to a seed"
    (both > 0, or both == 0), one whose own label text overlaps the
    question's wording (beyond shared generic domain vocabulary, see
    `_GENERIC_BIGRAMS`) ranks ahead of one that does not. Text overlap never
    promotes an edge with zero seed adjacency above one with positive seed
    adjacency -- that grouping is checked before relevance, not after --
    so a textually-coincidental but structurally unconnected edge cannot
    outrank a genuinely seed-adjacent one. Omitting `query` reproduces the
    prior ordering exactly."""
    edges_by_id: dict[str, ProjectEdge] = {edge.edge_id: edge for edge in subgraph.edges}
    for edge in extra_edges:
        edges_by_id.setdefault(edge.edge_id, edge)
    nodes_by_id: dict[str, ProjectNode] = {node.node_id: node for node in subgraph.nodes}

    path_positions: dict[str, int] = {}
    for path in paths:
        for position, edge_id in enumerate(path.edge_ids):
            path_positions.setdefault(edge_id, position)

    query_bigrams = _bigrams(query)

    def sort_key(edge: ProjectEdge) -> tuple[int, int, int, int, float, str]:
        on_path = edge.edge_id in path_positions
        adjacency = len({edge.source_node_id, edge.target_node_id} & seed_ids)
        relevance = _query_relevance(edge, query_bigrams, nodes_by_id)
        return (
            0 if on_path else 1,
            path_positions.get(edge.edge_id, 0) if on_path else 0,
            0 if adjacency > 0 else 1,
            -relevance,
            -edge.confidence * adjacency,
            edge.edge_id,
        )

    ranked = sorted(edges_by_id.values(), key=sort_key)
    return tuple((edge, edge.edge_id in path_positions) for edge in ranked[:limit])


def _edge_origin(edge: ProjectEdge) -> str:
    origin = edge.origin
    value = getattr(origin, "value", origin)
    return str(value)


def _resolve_support(
    items: tuple[EdgeEvidence, ...],
    evidence_by_chunk: Mapping[str, str],
    snippets: Mapping[str, str],
    allowed_source_revision_ids: frozenset[str],
    document_ids: Mapping[str, str],
) -> tuple[RelationSupport, ...]:
    """S-labelled supports (already-cited chunks) are never bounded -- they
    cost nothing new, the citation already exists. Snippet-only supports
    (fresh, never-cited chunks) are capped at
    `_MAX_SNIPPET_SUPPORTS_PER_RELATION` per relation so a single highly
    evidenced edge cannot alone blow up the per-answer snippet-reading and
    prompt-size budget; S-labelled supports are returned first either way."""
    s_supports: list[RelationSupport] = []
    snippet_supports: list[RelationSupport] = []
    seen_chunks: set[str] = set()
    for item in items:
        if item.source_revision_id not in allowed_source_revision_ids:
            continue
        if item.chunk_id in seen_chunks:
            continue
        label = evidence_by_chunk.get(item.chunk_id)
        if label is not None:
            s_supports.append(
                RelationSupport(
                    chunk_id=item.chunk_id,
                    source_revision_id=item.source_revision_id,
                    document_revision_id=item.document_revision_id,
                    content_digest=item.content_digest,
                    anchor=item.anchor,
                    evidence_label=label,
                    snippet=None,
                )
            )
            seen_chunks.add(item.chunk_id)
            continue
        if len(snippet_supports) >= _MAX_SNIPPET_SUPPORTS_PER_RELATION:
            continue
        snippet = snippets.get(item.chunk_id)
        if snippet is not None:
            snippet_supports.append(
                RelationSupport(
                    chunk_id=item.chunk_id,
                    source_revision_id=item.source_revision_id,
                    document_revision_id=item.document_revision_id,
                    content_digest=item.content_digest,
                    anchor=item.anchor,
                    evidence_label=None,
                    snippet=snippet[:_SNIPPET_MAX],
                    document_id=document_ids.get(item.document_revision_id),
                )
            )
            seen_chunks.add(item.chunk_id)
    return tuple(s_supports + snippet_supports)


def assemble(
    ranked: tuple[tuple[ProjectEdge, bool, tuple[EdgeEvidence, ...]], ...],
    nodes_by_id: Mapping[str, ProjectNode],
    evidence: tuple[EvidenceRef, ...],
    snippets: Mapping[str, str],
    *,
    allowed_source_revision_ids: frozenset[str],
    document_ids: Mapping[str, str] = MappingProxyType({}),
) -> tuple[RelationEvidence, ...]:
    evidence_by_chunk = {ref.chunk_id: ref.label for ref in evidence}
    relations: list[RelationEvidence] = []
    for edge, on_path, edge_evidence_items in ranked:
        if len(relations) >= _MAX_RELATIONS:
            break
        subject = nodes_by_id.get(edge.source_node_id)
        obj = nodes_by_id.get(edge.target_node_id)
        if subject is None or obj is None:
            continue
        support = _resolve_support(
            edge_evidence_items,
            evidence_by_chunk,
            snippets,
            allowed_source_revision_ids,
            document_ids,
        )
        if not support:
            continue
        relations.append(
            RelationEvidence(
                label=f"R{len(relations) + 1}",
                edge_id=edge.edge_id,
                subject_node_id=subject.node_id,
                subject_label=subject.label,
                subject_aliases=subject.aliases,
                object_node_id=obj.node_id,
                object_label=obj.label,
                object_aliases=obj.aliases,
                relation_type=edge.relation_type,
                relation_label=edge.relation_label,
                origin=_edge_origin(edge),
                confidence=edge.confidence,
                support=support,
                on_path=on_path,
            )
        )
    return tuple(relations)


def snippet_chunk_refs(
    ranked: tuple[tuple[ProjectEdge, bool, tuple[EdgeEvidence, ...]], ...],
    evidence: tuple[EvidenceRef, ...],
    *,
    allowed_source_revision_ids: frozenset[str],
) -> tuple[tuple[str, str], ...]:
    """Collect at most `_MAX_SNIPPET_SUPPORTS_PER_RELATION` non-S chunk refs
    per edge -- the same cap `_resolve_support` applies to the supports it
    builds -- so a single highly evidenced edge cannot inflate the number of
    chunks actually read for snippet text, not just the number ultimately
    kept as support. Collecting every candidate here first and capping only
    in `_resolve_support` would still pay the read cost for every discarded
    one."""
    if not allowed_source_revision_ids:
        return ()
    evidence_chunk_ids = {ref.chunk_id for ref in evidence}
    seen: set[tuple[str, str]] = set()
    refs: list[tuple[str, str]] = []
    for _edge, _on_path, edge_evidence_items in ranked:
        per_edge_count = 0
        for item in edge_evidence_items:
            if per_edge_count >= _MAX_SNIPPET_SUPPORTS_PER_RELATION:
                break
            if item.source_revision_id not in allowed_source_revision_ids:
                continue
            if item.chunk_id in evidence_chunk_ids:
                continue
            key = (item.document_revision_id, item.chunk_id)
            if key in seen:
                continue
            seen.add(key)
            refs.append(key)
            per_edge_count += 1
    return tuple(refs)
