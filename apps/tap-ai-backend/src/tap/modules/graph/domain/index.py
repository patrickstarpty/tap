"""Immutable per-snapshot indexes for bounded graph queries."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphFactNotFound,
    GraphNode,
    GraphSearchQuery,
    GraphSnapshot,
    GraphSnapshotDraft,
    GraphSubgraph,
    NeighborQuery,
    PathQuery,
)

_WORD = 1
_CJK = 2


def _kind(character: str) -> int:
    if character.isascii():
        return _WORD if character.isalnum() else 0
    code = ord(character)
    if (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x20000 <= code <= 0x2EBEF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    ):
        return _CJK
    return 0


def _runs(text: str) -> list[tuple[int, int, int]]:
    """Return ``(kind, start, end)`` runs of ASCII alphanumerics or CJK characters."""

    runs: list[tuple[int, int, int]] = []
    start = 0
    current = 0
    for position, character in enumerate(text):
        kind = _kind(character)
        if kind != current:
            if current:
                runs.append((current, start, position))
            current, start = kind, position
    if current:
        runs.append((current, start, len(text)))
    return runs


def _cjk_tokens(run: str) -> Iterable[str]:
    yield from run
    for position in range(len(run) - 1):
        yield run[position : position + 2]


def tokenize(text: str) -> frozenset[str]:
    """Lowercase tokens: ASCII words split on non-alphanumerics, CJK unigrams and bigrams."""

    folded = text.casefold()
    tokens: set[str] = set()
    for kind, start, end in _runs(folded):
        if kind == _WORD:
            tokens.add(folded[start:end])
        else:
            tokens.update(_cjk_tokens(folded[start:end]))
    return frozenset(tokens)


def _required_tokens(needle: str) -> set[str]:
    """Tokens every text containing ``needle`` must also produce.

    ASCII words touching either end of the needle may be partial words in the
    matching text, so only interior words are required; CJK grams always survive.
    """

    tokens: set[str] = set()
    for kind, start, end in _runs(needle):
        if kind == _CJK:
            tokens.update(_cjk_tokens(needle[start:end]))
        elif start > 0 and end < len(needle):
            tokens.add(needle[start:end])
    return tokens


@dataclass(frozen=True, slots=True)
class SnapshotIndex:
    snapshot: GraphSnapshot
    nodes: Mapping[str, GraphNode]
    edges: Mapping[str, GraphEdge]
    evidence: Mapping[str, Evidence]
    adjacency: Mapping[str, tuple[str, ...]]
    label_tokens: Mapping[str, frozenset[str]]
    chunk_facts: Mapping[str, tuple[str, ...]]
    degree: Mapping[str, int]
    _node_order: Mapping[str, int] = field(repr=False, compare=False)
    _edge_order: Mapping[str, int] = field(repr=False, compare=False)
    _evidence_order: Mapping[str, int] = field(repr=False, compare=False)
    _search_text: tuple[tuple[str, str, str], ...] = field(repr=False, compare=False)

    @classmethod
    def build(cls, draft: GraphSnapshotDraft) -> SnapshotIndex:
        nodes = {node.node_id: node for node in draft.nodes}
        edges = {edge.edge_id: edge for edge in draft.edges}
        evidence = {item.evidence_id: item for item in draft.evidence}
        incident: dict[str, list[str]] = {identity: [] for identity in nodes}
        for edge in draft.edges:
            incident[edge.source_node_id].append(edge.edge_id)
            incident[edge.target_node_id].append(edge.edge_id)
        tokens: dict[str, set[str]] = {}
        search_text: list[tuple[str, str, str]] = []
        for node in draft.nodes:
            label, key = node.label.casefold(), node.canonical_key.casefold()
            search_text.append((node.node_id, label, key))
            for token in tokenize(label) | tokenize(key):
                tokens.setdefault(token, set()).add(node.node_id)
        facts: dict[str, set[str]] = {}
        for fact_id, evidence_ids in (
            *((node.node_id, node.evidence_ids) for node in draft.nodes),
            *((edge.edge_id, edge.evidence_ids) for edge in draft.edges),
        ):
            for evidence_id in evidence_ids:
                facts.setdefault(evidence[evidence_id].chunk_id, set()).add(fact_id)
        return cls(
            snapshot=draft.snapshot,
            nodes=nodes,
            edges=edges,
            evidence=evidence,
            adjacency={identity: tuple(items) for identity, items in incident.items()},
            label_tokens={token: frozenset(ids) for token, ids in tokens.items()},
            chunk_facts={chunk: tuple(sorted(ids)) for chunk, ids in facts.items()},
            degree={identity: len(items) for identity, items in incident.items()},
            _node_order={identity: position for position, identity in enumerate(nodes)},
            _edge_order={identity: position for position, identity in enumerate(edges)},
            _evidence_order={identity: position for position, identity in enumerate(evidence)},
            _search_text=tuple(search_text),
        )

    @property
    def size(self) -> int:
        return len(self.nodes) + len(self.edges) + len(self.evidence)

    def _subgraph(self, node_ids: set[str], edge_ids: set[str] | None = None) -> GraphSubgraph:
        present = sorted(
            (identity for identity in node_ids if identity in self.nodes),
            key=self._node_order.__getitem__,
        )
        selected: set[str] = set()
        candidates: Iterable[str] = (
            (edge_id for identity in present for edge_id in self.adjacency[identity])
            if edge_ids is None
            else edge_ids
        )
        for edge_id in candidates:
            edge = self.edges.get(edge_id)
            if (
                edge is not None
                and edge.source_node_id in node_ids
                and edge.target_node_id in node_ids
            ):
                selected.add(edge_id)
        ordered_edges = sorted(selected, key=self._edge_order.__getitem__)
        nodes = tuple(self.nodes[identity] for identity in present)
        edges = tuple(self.edges[identity] for identity in ordered_edges)
        evidence_ids = {item for node in nodes for item in node.evidence_ids} | {
            item for edge in edges for item in edge.evidence_ids
        }
        evidence = tuple(
            self.evidence[identity]
            for identity in sorted(evidence_ids, key=self._evidence_order.__getitem__)
        )
        return GraphSubgraph(self.snapshot.snapshot_id, nodes, edges, evidence)

    def node_detail(self, node_id: str) -> GraphSubgraph:
        if node_id not in self.nodes:
            raise GraphFactNotFound("graph node not found")
        return self._subgraph({node_id}, set())

    def evidence_detail(self, evidence_id: str) -> Evidence:
        try:
            return self.evidence[evidence_id]
        except KeyError as error:
            raise GraphFactNotFound("graph evidence not found") from error

    def search(self, query: GraphSearchQuery) -> GraphSubgraph:
        limit = query.node_limit
        if query.text == "*":
            return self._subgraph(set(list(self.nodes)[:limit]))
        needle = query.text.casefold()
        required = _required_tokens(needle)
        if required:
            groups = sorted(
                (self.label_tokens.get(token, frozenset()) for token in required), key=len
            )
            candidates = set(groups[0]).intersection(*groups[1:])
            texts: Iterable[tuple[str, str, str]] = (
                (identity, *self._search_text[self._node_order[identity]][1:])
                for identity in sorted(candidates, key=self._node_order.__getitem__)
            )
        else:
            texts = self._search_text
        matches: set[str] = set()
        for identity, label, key in texts:
            if needle in label or needle in key:
                matches.add(identity)
                if len(matches) >= limit:
                    break
        return self._subgraph(matches)

    def _adjacent(self, node_id: str) -> Iterable[str]:
        edges = self.edges
        for edge_id in self.adjacency.get(node_id, ()):
            edge = edges[edge_id]
            yield edge.target_node_id if edge.source_node_id == node_id else edge.source_node_id

    def neighbors(self, query: NeighborQuery) -> GraphSubgraph:
        seen = {query.node_id}
        frontier = {query.node_id}
        for _ in range(query.depth):
            if len(seen) >= query.node_limit:
                break
            adjacent: set[str] = set()
            for identity in frontier:
                adjacent.update(self._adjacent(identity))
            frontier = adjacent - seen
            seen.update(sorted(frontier)[: query.node_limit - len(seen)])
        return self._subgraph(seen)

    def bounded_path(self, query: PathQuery) -> GraphSubgraph:
        source, target, limit = query.source_node_id, query.target_node_id, query.node_limit
        previous: dict[str, tuple[str, str] | None] = {source: None}
        queue = [source]
        head = 0
        while head < len(queue) and target not in previous and len(previous) < limit:
            current = queue[head]
            head += 1
            for edge_id in self.adjacency.get(current, ()):
                edge = self.edges[edge_id]
                neighbor = (
                    edge.target_node_id if edge.source_node_id == current else edge.source_node_id
                )
                if neighbor not in previous:
                    previous[neighbor] = (current, edge_id)
                    queue.append(neighbor)
                    if len(previous) >= limit or neighbor == target:
                        break
        if target not in previous:
            return GraphSubgraph(query.snapshot_id, (), ())
        node_ids = {target}
        edge_ids: set[str] = set()
        current = target
        while (parent := previous[current]) is not None:
            current, edge_id = parent
            node_ids.add(current)
            edge_ids.add(edge_id)
        return self._subgraph(node_ids, edge_ids)
