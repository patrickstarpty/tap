"""Merge Fragment drafts into one Project graph draft: entities, edges, communities,
aliases, provenance and the merge log.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Hashable, Iterable, Literal, Mapping, Sequence, TypeVar

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.application.alignment import ResolvedEntity, merge_edges, resolve_entities
from tap.modules.graph.domain.naming import clean_alias
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

# graph_project_alias.alias_norm is a MySQL String(255) column (see
# adapters/mysql_project.py); a merged alias whose normalized form exceeds
# that bound would fail publication, so it is dropped here instead.
_MAX_ALIAS_NORM_LENGTH = 255

_Row = TypeVar("_Row")


def _unique_by(rows: Iterable[_Row], key: Callable[[_Row], Hashable]) -> tuple[_Row, ...]:
    seen: set[Hashable] = set()
    kept: list[_Row] = []
    for row in rows:
        identity = key(row)
        if identity not in seen:
            seen.add(identity)
            kept.append(row)
    return tuple(kept)


def dedupe_on_primary_keys(draft: ProjectGraphDraft) -> ProjectGraphDraft:
    """Keep the first node, edge, provenance row and alias per merged-table
    primary key, in input order.

    A model batch often repeats one edge or one entity under several fragment
    ids grounded in the same chunk; once aligned they share a merged primary
    key, and inserting every copy fails publication with an IntegrityError.
    """

    return replace(
        draft,
        nodes=_unique_by(draft.nodes, lambda node: node.node_id),
        edges=_unique_by(draft.edges, lambda edge: edge.edge_id),
        node_sources=_unique_by(
            draft.node_sources,
            lambda source: (source.node_id, source.source_revision_id, source.chunk_id),
        ),
        edge_evidence=_unique_by(
            draft.edge_evidence,
            lambda evidence: (evidence.edge_id, evidence.source_revision_id, evidence.chunk_id),
        ),
        aliases=_unique_by(draft.aliases, lambda alias: (alias.alias_norm, alias.node_id)),
    )


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

        all_labels = {normalize_key(entity.label) for entity in entities}
        aliases = tuple(
            alias
            for entity in entities
            for alias in _aliases_for(
                entity, draft_by_snapshot, all_labels - {normalize_key(entity.label)}
            )
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

        return dedupe_on_primary_keys(
            ProjectGraphDraft(
                fragment_digest=digest,
                nodes=nodes,
                edges=edges,
                node_sources=node_sources,
                edge_evidence=edge_evidence,
                aliases=aliases,
                communities=communities,
                merge_log=merge_log,
            )
        )


def _aliases_for(
    entity: ResolvedEntity,
    draft_by_snapshot: Mapping[str, GraphSnapshotDraft],
    other_entity_labels: frozenset[str] = frozenset(),
) -> tuple[Alias, ...]:
    """Build this entity's displayed/persisted alias rows from its own label and
    its Fragment members' labels and model aliases.

    Every candidate is cleaned the same way a node's own displayed aliases are
    (see ``alignment.resolve_entities``): a reference word, bare grade, example
    name or pure clause/definition locator is dropped, and a clause- or
    interpretation-numbered alias keys on the real term underneath. A model
    alias that, after cleaning, names a *different* entity in this merge
    outright (``other_entity_labels``, normalized) is not displayed on this
    one either: the real-model run had a "责任免除" node whose aliases
    absorbed concrete exclusion items ("故意杀害", "故意伤害", ...) that are
    themselves named, evidenced entities elsewhere in the same document --
    most likely the extractor offering a sub-item as if it were a synonym.

    This only ever stops the *alias row* from being displayed; it cannot stop
    the ALIAS-level *merge* itself when both sides are the same node type, the
    same shape as a wanted alias-to-named-entity merge ("Underwriting"
    aliased "核保", merging into the node actually labelled "核保") with
    nothing structural to tell the two apart -- see the report.
    """

    seen_norms: set[str] = set()
    aliases: list[Alias] = []

    def add(text: str, origin: Literal["LABEL", "MODEL", "MERGE"]) -> None:
        norm = normalize_key(text)
        if not norm or norm in seen_norms or len(norm) > _MAX_ALIAS_NORM_LENGTH:
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
        cleaned = clean_alias(other_label)
        if cleaned is not None and cleaned != entity.label:
            add(cleaned, "MERGE")
    for alias_text in member_aliases:
        cleaned = clean_alias(alias_text)
        if cleaned is not None and normalize_key(cleaned) not in other_entity_labels:
            add(cleaned, "MODEL")
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
