"""Split document chunks into batches and merge per-batch extraction drafts."""

from __future__ import annotations

import hashlib
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
    RelationOrigin,
)
from tap.modules.graph.domain.vocabulary import NODE_ALIAS_MAX, normalize_key
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

    Nodes merge by ``node_id`` first (the same literal id recurring across batches,
    e.g. an intentional known-entity reuse): evidence ids union (first-seen order),
    aliases union capped to ``NODE_ALIAS_MAX`` (first-seen order), label/type/
    canonical_key kept from the first occurrence. A node whose id has not been seen
    before but whose ``normalize_key(canonical_key)`` matches an already-kept node's
    is the *same* real-world entity under a different (batch-namespaced) id --
    typically because the extractor re-grounded it in a later batch without being
    told it was already known -- and merges into that node the same way, remapping
    every later reference to the duplicate id (edge endpoints, and any provenance
    input fact id) onto the surviving id for the rest of this merge.

    Edges merge by ``edge_id`` first (same rationale as nodes). Once node-merge
    remapping is applied to an edge's endpoints, an edge whose (source, relation
    type, target) shape -- after remapping -- matches an already-kept edge's is the
    same relation duplicated under a different id (e.g. the same sentence re-
    extracted once per batch, or a cross-batch node merge making two previously
    distinct edges land on the same endpoints) and merges into it the same way
    (evidence ids union, confidence takes the maximum), remapping its id too. An
    edge whose endpoints become equal after remapping is a self-edge created by a
    node merge and is dropped outright (``GraphEdge`` forbids self-edges).

    Evidence and inference provenance dedupe by id; a provenance's ``edge_id`` and
    ``input_fact_ids`` are remapped the same way as edge/node ids above.

    Each batch draft is individually within ``GraphSnapshotDraft``'s node/edge bounds,
    but the merge can still exceed them. Rather than let that raise and crash the
    worker, the merge truncates deterministically: nodes are kept in first-seen order
    up to ``MAX_GRAPH_NODES``; edges whose endpoint was dropped are dropped too; the
    remaining edges are kept in first-seen order up to the same 2000-edge bound
    ``GraphSnapshotDraft`` enforces. Provenance for a dropped edge is dropped with it,
    and -- because an ``INFERRED`` edge requires its own provenance, and a
    provenance's ``input_fact_ids`` can themselves name other (node or edge) facts --
    dropping one inferred edge can orphan another provenance that cites it as an
    input fact. This cascades: any ``INFERRED`` edge whose provenance's input facts
    are no longer all kept is dropped together with that provenance, repeating until
    nothing more changes. Evidence has no such cap and is kept in full.

    If, after all of the above, the merged document has zero edges (every batch was
    either empty or only ever touched a lone, relation-less entity), the whole
    result is replaced by exactly one ``document:``-prefixed fallback node carrying
    every evidence id collected so far and zero edges -- the per-batch extractors
    never emit this fallback themselves, precisely so a relation-less batch of an
    otherwise-related document cannot leave a stray extra document node behind.
    """

    nodes_by_id: dict[str, GraphNode] = {}
    node_order: list[str] = []
    canonical_key_to_node_id: dict[str, str] = {}
    node_id_remap: dict[str, str] = {}

    edges_by_id: dict[str, GraphEdge] = {}
    edge_order: list[str] = []
    edge_shape_to_id: dict[tuple[str, str, str], str] = {}
    edge_id_remap: dict[str, str] = {}

    evidence_by_id: dict[str, Evidence] = {}
    evidence_order: list[str] = []
    provenance_by_id: dict[str, InferenceProvenance] = {}
    provenance_order: list[str] = []

    def _remap_fact_id(fact_id: str) -> str:
        if fact_id in node_id_remap:
            return node_id_remap[fact_id]
        return edge_id_remap.get(fact_id, fact_id)

    for draft in drafts:
        for node in draft.nodes:
            existing_node = nodes_by_id.get(node.node_id)
            if existing_node is None:
                key = normalize_key(node.canonical_key)
                surviving_id = canonical_key_to_node_id.get(key)
                if surviving_id is None:
                    nodes_by_id[node.node_id] = node
                    node_order.append(node.node_id)
                    canonical_key_to_node_id[key] = node.node_id
                    continue
                node_id_remap[node.node_id] = surviving_id
                existing_node = nodes_by_id[surviving_id]
                target_id = surviving_id
            else:
                target_id = node.node_id
            evidence_ids = existing_node.evidence_ids
            for evidence_id in node.evidence_ids:
                if evidence_id not in evidence_ids:
                    evidence_ids += (evidence_id,)
            aliases = existing_node.aliases
            for alias in node.aliases:
                if alias not in aliases and len(aliases) < NODE_ALIAS_MAX:
                    aliases += (alias,)
            nodes_by_id[target_id] = replace(
                existing_node, evidence_ids=evidence_ids, aliases=aliases
            )
        for edge in draft.edges:
            source = node_id_remap.get(edge.source_node_id, edge.source_node_id)
            target = node_id_remap.get(edge.target_node_id, edge.target_node_id)
            was_remapped = (source, target) != (edge.source_node_id, edge.target_node_id)
            if source == target:
                # A node merge collapsed this edge's two endpoints into one entity;
                # keeping it would violate GraphEdge's no-self-edge rule.
                continue
            shape = (source, edge.relation_type, target)
            existing_edge = edges_by_id.get(edge.edge_id)
            if existing_edge is None and was_remapped:
                # Only an edge whose endpoint was itself remapped (a batch-
                # namespaced duplicate of an already-kept node) is considered for
                # shape-based dedupe -- two coincidentally same-shaped edges that
                # were never touched by a node merge are left as distinct edges,
                # exactly as before this merge-by-canonical-key behavior existed.
                surviving_edge_id = edge_shape_to_id.get(shape)
                if surviving_edge_id is not None:
                    edge_id_remap[edge.edge_id] = surviving_edge_id
                    existing_edge = edges_by_id[surviving_edge_id]
            if existing_edge is None:
                new_edge = (
                    replace(edge, source_node_id=source, target_node_id=target)
                    if (was_remapped)
                    else edge
                )
                edges_by_id[edge.edge_id] = new_edge
                edge_order.append(edge.edge_id)
                edge_shape_to_id[shape] = edge.edge_id
                continue
            evidence_ids = existing_edge.evidence_ids
            for evidence_id in edge.evidence_ids:
                if evidence_id not in evidence_ids:
                    evidence_ids += (evidence_id,)
            edges_by_id[existing_edge.edge_id] = replace(
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
                provenance_by_id[provenance_item.provenance_id] = replace(
                    provenance_item,
                    edge_id=_remap_fact_id(provenance_item.edge_id),
                    input_fact_ids=tuple(
                        _remap_fact_id(fact_id) for fact_id in provenance_item.input_fact_ids
                    ),
                )
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

    # An INFERRED edge requires its own provenance, and a provenance's input_fact_ids
    # can themselves name other node or edge facts. Dropping one inferred edge (for
    # falling past the node/edge cut above, or in an earlier pass of this loop) can
    # therefore orphan another provenance that cites it as an input fact. Iterate to
    # a fixpoint: drop any INFERRED edge whose provenance is missing or whose input
    # facts are not all still kept, together with that provenance; repeat until a
    # pass drops nothing.
    provenance_by_edge_id = {
        provenance_by_id[provenance_id].edge_id: provenance_by_id[provenance_id]
        for provenance_id in provenance_order
    }
    changed = True
    while changed:
        changed = False
        kept_fact_id_set = kept_node_id_set | set(kept_edge_order)
        next_kept_edge_order = []
        for edge_id in kept_edge_order:
            edge = edges_by_id[edge_id]
            if edge.origin is RelationOrigin.INFERRED:
                provenance_for_edge = provenance_by_edge_id.get(edge_id)
                if (
                    provenance_for_edge is None
                    or set(provenance_for_edge.input_fact_ids) - kept_fact_id_set
                ):
                    changed = True
                    continue
            next_kept_edge_order.append(edge_id)
        kept_edge_order = next_kept_edge_order
    kept_edge_id_set = set(kept_edge_order)

    kept_provenance_order = [
        provenance_id
        for provenance_id in provenance_order
        if provenance_by_id[provenance_id].edge_id in kept_edge_id_set
    ]

    if not node_order:
        # Every batch was empty (nothing grounded anywhere in the document): the
        # degenerate case the per-batch extractors deliberately leave to assembly
        # (see the docstring above) instead of each minting its own document-node
        # fallback. A document that *did* ground real nodes but ended up with zero
        # kept edges (e.g. every edge fell past the node cap) keeps those nodes --
        # it is not this fallback's concern.
        fallback_node_id = "grn_" + hashlib.sha256(snapshot.snapshot_id.encode()).hexdigest()[:32]
        fallback_node = GraphNode(
            fallback_node_id,
            snapshot.snapshot_id,
            snapshot.document_revision_ids[0],
            "ENTITY",
            f"document:{snapshot.document_revision_ids[0]}",
            tuple(evidence_order),
        )
        return GraphSnapshotDraft(
            snapshot,
            (fallback_node,),
            (),
            tuple(evidence_by_id[evidence_id] for evidence_id in evidence_order),
            (),
        )

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
