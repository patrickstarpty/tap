"""Split document chunks into batches and merge per-batch extraction drafts."""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from tap.modules.graph.domain.models import (
    MAX_GRAPH_NODES,
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    InferenceProvenance,
)
from tap.modules.graph.domain.vocabulary import NODE_ALIAS_MAX
from tap.modules.knowledge.domain.documents import ChunkDraft

# Mirrors GraphSnapshotDraft's own internal edge bound (domain/models.py), which a merge
# across batches -- each individually within bounds -- can otherwise exceed.
_MAX_GRAPH_EDGES = 2_000


def split_batches(
    chunks: Sequence[ChunkDraft], *, batch_size: int
) -> tuple[tuple[ChunkDraft, ...], ...]:
    """Split chunks into fixed-size, order-preserving batches."""

    if batch_size < 1:
        raise ValueError("graph extraction batch size must be positive")
    return tuple(
        tuple(chunks[index : index + batch_size]) for index in range(0, len(chunks), batch_size)
    )


def assemble_fragment(
    snapshot: GraphSnapshot, drafts: Sequence[GraphSnapshotDraft]
) -> GraphSnapshotDraft:
    """Merge every batch draft into one snapshot draft bound to ``snapshot``.

    Nodes merge by ``node_id``: evidence ids union (first-seen order), aliases union
    capped to ``NODE_ALIAS_MAX`` (first-seen order), label/type/canonical_key kept from
    the first occurrence. Edges merge by ``edge_id``: evidence ids union, confidence
    takes the maximum across batches. Evidence and inference provenance dedupe by id.

    Each batch draft is individually within ``GraphSnapshotDraft``'s node/edge bounds,
    but the merge can still exceed them. Rather than let that raise and crash the
    worker, the merge truncates deterministically: nodes are kept in first-seen order
    up to ``MAX_GRAPH_NODES``; edges whose endpoint was dropped are dropped too; the
    remaining edges are kept in first-seen order up to the same 2000-edge bound
    ``GraphSnapshotDraft`` enforces; provenance for a dropped edge is dropped with it
    (an inference's own provenance always shares its edge's fate, so no further
    cascade is possible). Evidence has no such cap and is kept in full.
    """

    nodes_by_id: dict[str, GraphNode] = {}
    node_order: list[str] = []
    edges_by_id: dict[str, GraphEdge] = {}
    edge_order: list[str] = []
    evidence_by_id: dict[str, Evidence] = {}
    evidence_order: list[str] = []
    provenance_by_id: dict[str, InferenceProvenance] = {}
    provenance_order: list[str] = []

    for draft in drafts:
        for node in draft.nodes:
            existing_node = nodes_by_id.get(node.node_id)
            if existing_node is None:
                nodes_by_id[node.node_id] = node
                node_order.append(node.node_id)
                continue
            evidence_ids = existing_node.evidence_ids
            for evidence_id in node.evidence_ids:
                if evidence_id not in evidence_ids:
                    evidence_ids += (evidence_id,)
            aliases = existing_node.aliases
            for alias in node.aliases:
                if alias not in aliases and len(aliases) < NODE_ALIAS_MAX:
                    aliases += (alias,)
            nodes_by_id[node.node_id] = replace(
                existing_node, evidence_ids=evidence_ids, aliases=aliases
            )
        for edge in draft.edges:
            existing_edge = edges_by_id.get(edge.edge_id)
            if existing_edge is None:
                edges_by_id[edge.edge_id] = edge
                edge_order.append(edge.edge_id)
                continue
            evidence_ids = existing_edge.evidence_ids
            for evidence_id in edge.evidence_ids:
                if evidence_id not in evidence_ids:
                    evidence_ids += (evidence_id,)
            edges_by_id[edge.edge_id] = replace(
                existing_edge,
                evidence_ids=evidence_ids,
                confidence=max(existing_edge.confidence, edge.confidence),
            )
        for evidence_item in draft.evidence:
            if evidence_item.evidence_id not in evidence_by_id:
                evidence_by_id[evidence_item.evidence_id] = evidence_item
                evidence_order.append(evidence_item.evidence_id)
        for provenance_item in draft.provenance:
            if provenance_item.provenance_id not in provenance_by_id:
                provenance_by_id[provenance_item.provenance_id] = provenance_item
                provenance_order.append(provenance_item.provenance_id)

    kept_node_order = node_order[:MAX_GRAPH_NODES]
    kept_node_id_set = set(kept_node_order)

    endpoint_filtered_edge_order = [
        edge_id
        for edge_id in edge_order
        if edges_by_id[edge_id].source_node_id in kept_node_id_set
        and edges_by_id[edge_id].target_node_id in kept_node_id_set
    ]
    kept_edge_order = endpoint_filtered_edge_order[:_MAX_GRAPH_EDGES]
    kept_edge_id_set = set(kept_edge_order)

    kept_provenance_order = [
        provenance_id
        for provenance_id in provenance_order
        if provenance_by_id[provenance_id].edge_id in kept_edge_id_set
    ]

    return GraphSnapshotDraft(
        snapshot,
        tuple(nodes_by_id[node_id] for node_id in kept_node_order),
        tuple(edges_by_id[edge_id] for edge_id in kept_edge_order),
        tuple(evidence_by_id[evidence_id] for evidence_id in evidence_order),
        tuple(provenance_by_id[provenance_id] for provenance_id in kept_provenance_order),
    )


def known_entities_from(
    drafts: Sequence[GraphSnapshotDraft], *, limit: int = 200
) -> tuple[dict[str, object], ...]:
    """Summarize nodes seen so far for reuse by later batches of the same document.

    Nodes are kept in first-seen order; when more than ``limit`` distinct nodes have
    been seen, only the newest ``limit`` (the most recently first-seen) are kept.
    """

    nodes_by_id: dict[str, GraphNode] = {}
    order: list[str] = []
    for draft in drafts:
        for node in draft.nodes:
            if node.node_id not in nodes_by_id:
                nodes_by_id[node.node_id] = node
                order.append(node.node_id)
    kept = order[-limit:] if len(order) > limit else order
    return tuple(
        {
            "id": nodes_by_id[node_id].node_id,
            "label": nodes_by_id[node_id].label,
            "type": nodes_by_id[node_id].node_type,
            "canonicalKey": nodes_by_id[node_id].canonical_key,
        }
        for node_id in kept
    )
