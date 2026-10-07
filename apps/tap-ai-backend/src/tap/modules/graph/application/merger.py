"""Merge Fragment drafts into one Project graph draft: entities, edges, communities,
aliases, provenance and the merge log.
"""

from __future__ import annotations

from typing import Literal, Mapping, Sequence

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.application.alignment import ResolvedEntity, merge_edges, resolve_entities
from tap.modules.graph.application.communities import (
    OTHER_COMMUNITY_ID,
    OTHER_COMMUNITY_LABEL,
    community_label,
    propagate_labels,
)
from tap.modules.graph.domain.models import GraphNode, GraphSnapshotDraft
from tap.modules.graph.domain.project import (
    Alias,
    Community,
    EdgeEvidence,
    FragmentRecord,
    MergeLogEntry,
    NodeSource,
    ProjectEdge,
    ProjectGraphDraft,
    ProjectNode,
    fragment_digest,
)
from tap.modules.graph.domain.vocabulary import normalize_key


def _find_node(draft: GraphSnapshotDraft, node_id: str) -> GraphNode:
    for node in draft.nodes:
        if node.node_id == node_id:
            return node
    raise KeyError(node_id)


class ProjectGraphMerger:
    def __init__(self, *, align_threshold: float = 0.92) -> None:
        self._align_threshold = align_threshold

    def merge(
        self,
        scope: ProjectScopeContext,
        fragments: Sequence[FragmentRecord],
        *,
        embeddings: Mapping[tuple[str, str], tuple[float, ...]] | None = None,
    ) -> ProjectGraphDraft:
        for record in fragments:
            if record.draft.snapshot.project_id != scope.project_id:
                raise ValueError("fragment is outside Project scope")

        digest = fragment_digest(fragments)
        if not fragments:
            return ProjectGraphDraft(digest, (), (), (), (), (), (), ())

        drafts = tuple(record.draft for record in fragments)
        draft_by_snapshot = {draft.snapshot.snapshot_id: draft for draft in drafts}
        entities = resolve_entities(drafts, embeddings=embeddings, threshold=self._align_threshold)

        entity_of: dict[tuple[str, str], str] = {
            member: entity.node_id for entity in entities for member in entity.members
        }
        merged_edges = merge_edges(drafts, entity_of)

        degree: dict[str, int] = {entity.node_id: 0 for entity in entities}
        for edge, _ in merged_edges:
            degree[edge.source_node_id] += 1
            degree[edge.target_node_id] += 1

        community_edges = [(edge.source_node_id, edge.target_node_id) for edge, _ in merged_edges]
        community_assignment = propagate_labels(sorted(degree), community_edges)

        label_by_node = {entity.node_id: entity.label for entity in entities}
        members_by_community: dict[str, list[str]] = {}
        for node_id, community_id in community_assignment.items():
            members_by_community.setdefault(community_id, []).append(node_id)

        communities = tuple(
            Community(
                community_id,
                OTHER_COMMUNITY_LABEL
                if community_id == OTHER_COMMUNITY_ID
                else community_label(members, degree, label_by_node),
                len(members),
            )
            for community_id, members in sorted(members_by_community.items())
        )

        nodes = tuple(
            ProjectNode(
                node_id=entity.node_id,
                label=entity.label,
                node_type=entity.node_type,
                canonical_key=entity.canonical_key,
                degree=degree[entity.node_id],
                community_id=community_assignment[entity.node_id],
                aliases=entity.aliases,
            )
            for entity in entities
        )
        edges = tuple(edge for edge, _ in merged_edges)

        aliases = tuple(
            alias for entity in entities for alias in _aliases_for(entity, draft_by_snapshot)
        )
        node_sources = tuple(
            source for entity in entities for source in _node_sources(entity, draft_by_snapshot)
        )
        edge_evidence = tuple(
            evidence
            for edge, members in merged_edges
            for evidence in _edge_evidence(edge, members, draft_by_snapshot)
        )
        merge_log = tuple(
            MergeLogEntry(entity.node_id, entity.members, entity.rule)
            for entity in entities
            if len(entity.members) > 1
        )

        return ProjectGraphDraft(
            fragment_digest=digest,
            nodes=nodes,
            edges=edges,
            node_sources=node_sources,
            edge_evidence=edge_evidence,
            aliases=aliases,
            communities=communities,
            merge_log=merge_log,
        )


def _aliases_for(
    entity: ResolvedEntity, draft_by_snapshot: Mapping[str, GraphSnapshotDraft]
) -> tuple[Alias, ...]:
    seen_norms: set[str] = set()
    aliases: list[Alias] = []

    def add(text: str, origin: Literal["LABEL", "MODEL", "MERGE"]) -> None:
        norm = normalize_key(text)
        if not norm or norm in seen_norms:
            return
        seen_norms.add(norm)
        aliases.append(Alias(norm, entity.node_id, origin))

    add(entity.label, "LABEL")
    member_labels: list[str] = []
    member_aliases: list[str] = []
    for snapshot_id, node_id in entity.members:
        node = _find_node(draft_by_snapshot[snapshot_id], node_id)
        member_labels.append(node.label)
        member_aliases.extend(node.aliases)
    for other_label in member_labels:
        if other_label != entity.label:
            add(other_label, "MERGE")
    for alias_text in member_aliases:
        add(alias_text, "MODEL")
    return tuple(aliases)


def _node_sources(
    entity: ResolvedEntity, draft_by_snapshot: Mapping[str, GraphSnapshotDraft]
) -> tuple[NodeSource, ...]:
    sources: list[NodeSource] = []
    for snapshot_id, node_id in entity.members:
        draft = draft_by_snapshot[snapshot_id]
        node = _find_node(draft, node_id)
        evidence_by_id = {item.evidence_id: item for item in draft.evidence}
        for evidence_id in node.evidence_ids:
            evidence = evidence_by_id[evidence_id]
            sources.append(
                NodeSource(
                    node_id=entity.node_id,
                    source_revision_id=evidence.source_revision_id,
                    document_revision_id=evidence.document_revision_id,
                    chunk_id=evidence.chunk_id,
                    anchor=evidence.anchor,
                    fragment_snapshot_id=snapshot_id,
                    fragment_node_id=node_id,
                )
            )
    return tuple(sources)


def _edge_evidence(
    edge: ProjectEdge,
    members: tuple[tuple[str, str], ...],
    draft_by_snapshot: Mapping[str, GraphSnapshotDraft],
) -> tuple[EdgeEvidence, ...]:
    evidence_items: list[EdgeEvidence] = []
    for snapshot_id, fragment_edge_id in members:
        draft = draft_by_snapshot[snapshot_id]
        fragment_edge = next(item for item in draft.edges if item.edge_id == fragment_edge_id)
        evidence_by_id = {item.evidence_id: item for item in draft.evidence}
        for evidence_id in fragment_edge.evidence_ids:
            evidence = evidence_by_id[evidence_id]
            evidence_items.append(
                EdgeEvidence(
                    edge_id=edge.edge_id,
                    source_revision_id=evidence.source_revision_id,
                    document_revision_id=evidence.document_revision_id,
                    chunk_id=evidence.chunk_id,
                    anchor=evidence.anchor,
                    content_digest=evidence.content_digest,
                    fragment_snapshot_id=snapshot_id,
                    fragment_edge_id=fragment_edge_id,
                )
            )
    return tuple(evidence_items)
