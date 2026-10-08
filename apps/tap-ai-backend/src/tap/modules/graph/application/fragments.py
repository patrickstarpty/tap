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
    result is replaced by exactly one fallback node (reusing one a per-batch
    extractor already minted, if any survived the merge, since its canonical
    key -- exactly ``f"document:{revision_id}"`` for one of this snapshot's own
    revisions -- already collapsed every batch's copy into one via the
    canonical-key merge above; otherwise a fresh one is synthesized here)
    carrying every evidence id collected so far and zero edges. A per-batch
    extractor is free to mint its own such fallback node when it grounds
    nothing (see ``rule_based_draft``); when the document *does* end up with
    real edges elsewhere, any such stray fallback node is instead dropped from
    the published result rather than kept alongside real content. Either way,
    only an *exact* match against one of this snapshot's own reserved fallback
    keys identifies a fallback node -- never merely a ``"document:"`` prefix,
    since a real node (e.g. from a model) could legitimately have a
    canonical key that happens to start with it.
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
            if existing_edge.origin is edge.origin:
                evidence_ids = existing_edge.evidence_ids
                for evidence_id in edge.evidence_ids:
                    if evidence_id not in evidence_ids:
                        evidence_ids += (evidence_id,)
                merged_edge = replace(
                    existing_edge,
                    evidence_ids=evidence_ids,
                    confidence=max(existing_edge.confidence, edge.confidence),
                )
            else:
                # A same-shape duplicate can turn up with the opposite origin
                # across batches (e.g. one batch grounds a direct EXTRACTED
                # mention, another only infers the same relation). The EXTRACTED
                # edge always wins -- it carries real evidence, which an INFERRED
                # edge is forbidden from claiming -- keeping the merge's identity
                # (edge_id) stable either way. The losing INFERRED edge's
                # provenance becomes orphaned and is dropped in the finalize pass
                # below, since it no longer names an INFERRED edge.
                if edge.origin is RelationOrigin.EXTRACTED:
                    merged_edge = replace(
                        edge,
                        edge_id=existing_edge.edge_id,
                        source_node_id=source,
                        target_node_id=target,
                        confidence=max(existing_edge.confidence, edge.confidence),
                    )
                else:
                    merged_edge = replace(
                        existing_edge, confidence=max(existing_edge.confidence, edge.confidence)
                    )
            edges_by_id[existing_edge.edge_id] = merged_edge
        for evidence_item in draft.evidence:
            if evidence_item.evidence_id not in evidence_by_id:
                evidence_by_id[evidence_item.evidence_id] = evidence_item
                evidence_order.append(evidence_item.evidence_id)
        for provenance_item in draft.provenance:
            if provenance_item.provenance_id not in provenance_by_id:
                provenance_by_id[provenance_item.provenance_id] = provenance_item
                provenance_order.append(provenance_item.provenance_id)

    # Finalize provenance now that every node/edge merge decision across every
    # draft is known (edge_id_remap and node_id_remap no longer change). A raw
    # provenance item recorded above can be left domain-invalid, or redundant,
    # by those merges:
    #   - its edge_id or input_fact_ids can name a duplicate id that got
    #     remapped onto a survivor -- remap both now;
    #   - two originally-distinct input facts can collapse onto the same
    #     surviving id -- dedupe input_fact_ids (preserving first-seen order);
    #   - a provenance left with no input facts, or whose only input is its own
    #     edge, is domain-invalid -- drop it (the cascade below drops the
    #     orphaned INFERRED edge along with it, exactly as a missing provenance
    #     already does);
    #   - two batches can each carry their own provenance for what turns out to
    #     be the same merged INFERRED edge -- InferenceProvenance uniqueness
    #     forbids two rows citing the same edge_id, so only the first
    #     (draft-order) provenance survives per final edge_id;
    #   - a provenance whose edge ended up EXTRACTED (see the mixed-origin edge
    #     merge above) is dropped outright -- an EXTRACTED edge never carries
    #     provenance.
    finalized_provenance_by_id: dict[str, InferenceProvenance] = {}
    finalized_provenance_order: list[str] = []
    claimed_provenance_edge_ids: set[str] = set()
    for provenance_id in provenance_order:
        raw_item = provenance_by_id[provenance_id]
        final_edge_id = _remap_fact_id(raw_item.edge_id)
        surviving_edge = edges_by_id.get(final_edge_id)
        if surviving_edge is None or surviving_edge.origin is not RelationOrigin.INFERRED:
            continue
        if final_edge_id in claimed_provenance_edge_ids:
            continue
        input_fact_ids = tuple(
            dict.fromkeys(_remap_fact_id(fact_id) for fact_id in raw_item.input_fact_ids)
        )
        if not input_fact_ids or final_edge_id in input_fact_ids:
            continue
        finalized_provenance_by_id[provenance_id] = replace(
            raw_item, edge_id=final_edge_id, input_fact_ids=input_fact_ids
        )
        finalized_provenance_order.append(provenance_id)
        claimed_provenance_edge_ids.add(final_edge_id)
    provenance_by_id = finalized_provenance_by_id
    provenance_order = finalized_provenance_order

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

    # A fallback node is identified by an *exact* match on one of this
    # snapshot's own reserved fallback keys ("document:<revision_id>", exactly
    # as rule_based_draft/assemble_fragment build it below) -- never merely by
    # a "document:" prefix. A model is free to mint a real, edge-bearing node
    # whose own canonicalKey happens to start with "document:" (e.g.
    # "document:handbook") for an unrelated real-world thing; prefix-matching
    # would misidentify it as this snapshot's fallback and strip it while
    # leaving its edge behind, which then fails validation with a dangling
    # edge endpoint.
    fallback_canonical_keys = {
        f"document:{revision_id}" for revision_id in snapshot.document_revision_ids
    }

    if not kept_edge_order:
        # The merged document ended up with zero relations -- whether every
        # batch was empty, a batch only ever touched a lone, relation-less
        # entity, or every edge was cascade-dropped past the node/edge caps.
        # Per spec/plan, the published result in every such case is exactly one
        # document-node fallback and nothing else, never a stray real node.
        # Reuse an already-merged per-batch fallback node (its canonical key is
        # always "document:<revision>", so canonical-key merging above already
        # unioned every batch's own evidence onto it) if one survived; only
        # synthesize a fresh one (carrying whatever evidence was collected) if
        # no batch ever produced one.
        existing_fallback_id = next(
            (
                node_id
                for node_id in kept_node_order
                if nodes_by_id[node_id].canonical_key in fallback_canonical_keys
            ),
            None,
        )
        if existing_fallback_id is not None:
            fallback_node = nodes_by_id[existing_fallback_id]
        else:
            fallback_node_id = (
                "grn_" + hashlib.sha256(snapshot.snapshot_id.encode()).hexdigest()[:32]
            )
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

    # The document has real relations: drop any stray per-batch document-node
    # fallback (e.g. from a relation-less batch of an otherwise-related,
    # multi-batch document) rather than publish it alongside real content.
    published_node_order = [
        node_id
        for node_id in kept_node_order
        if nodes_by_id[node_id].canonical_key not in fallback_canonical_keys
    ]

    return GraphSnapshotDraft(
        snapshot,
        tuple(nodes_by_id[node_id] for node_id in published_node_order),
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
