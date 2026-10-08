"""Bounded, fail-soft graph context for frozen Knowledge answers, read from the
merged project graph (one graph per project, versioned) rather than a
per-document fragment snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.graph.domain.project import EdgeEvidence, NodeSource, ProjectEdge, ProjectNode
from tap.modules.graph.ports.project_store import (
    GraphFactNotFound,
    ProjectGraphNotReady,
    ProjectGraphStorePort,
)
from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority
from tap.platform.telemetry import span


class GraphContextStatus(StrEnum):
    APPLIED = "APPLIED"
    NOT_READY = "NOT_READY"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_SELECTED = "NOT_SELECTED"


@dataclass(frozen=True, slots=True)
class GraphAnswerContext:
    status: GraphContextStatus
    snapshot_id: str | None = None
    facts: tuple[Mapping[str, object], ...] = ()
    graph_version: int | None = None
    seed_node_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.status is GraphContextStatus.APPLIED) != bool(self.snapshot_id):
            raise ValueError("only applied Graph context can identify a snapshot")
        if (self.status is GraphContextStatus.APPLIED) != (self.graph_version is not None):
            raise ValueError("graph_version must accompany an applied Graph context")
        if self.status is not GraphContextStatus.APPLIED and self.facts:
            raise ValueError("non-applied Graph context cannot carry facts")
        object.__setattr__(
            self,
            "facts",
            tuple(MappingProxyType(dict(item)) for item in self.facts),
        )


class GraphAnswerEnricher:
    """Seeds an answer's graph context from the project graph's aliases and
    question-chunk hits, then expands one hop (two, if fewer than three seeds)
    bounded by `node_limit`/`edge_limit`. `seed_node_ids` and `graph_version`
    on the resulting `GraphAnswerContext` are the inputs PR 3's relation
    analysis pins its own subgraph queries to."""

    def __init__(
        self,
        store: ProjectGraphStorePort,
        *,
        node_limit: int = 20,
        edge_limit: int = 60,
        publication_authority: PublishedKnowledgeAuthority | None = None,
    ) -> None:
        if not 1 <= node_limit <= 60:
            raise ValueError("answer Graph node limit must be between 1 and 60")
        if not 1 <= edge_limit <= 200:
            raise ValueError("answer Graph edge limit must be between 1 and 200")
        self._store = store
        self._node_limit = node_limit
        self._edge_limit = edge_limit
        self._publication_authority = publication_authority

    async def enrich(
        self,
        scope: ProjectScopeContext,
        source_revision_ids: tuple[str, ...],
        query: str,
        *,
        chunk_ids: tuple[str, ...] = (),
    ) -> GraphAnswerContext:
        with span("graph.enrich") as current_span:
            if not source_revision_ids:
                return GraphAnswerContext(GraphContextStatus.NOT_SELECTED)
            try:
                publication = (
                    None
                    if self._publication_authority is None
                    else await self._publication_authority.authorize_selection(
                        scope.project_id, source_revision_ids
                    )
                )
                current = await self._store.get_current(scope)
                if current is None:
                    return GraphAnswerContext(GraphContextStatus.NOT_READY)
                version = current.version

                chunk_seed_ids = (
                    await self._store.nodes_for_chunks(scope, chunk_ids, version=version)
                    if chunk_ids
                    else ()
                )
                alias_matches = await self._store.match_aliases(scope, query, version=version)

                seed_ids: list[str] = []
                seen_seeds: set[str] = set()
                for node_id in chunk_seed_ids:
                    if node_id not in seen_seeds:
                        seen_seeds.add(node_id)
                        seed_ids.append(node_id)
                for match in alias_matches:
                    if match.node_id not in seen_seeds:
                        seen_seeds.add(match.node_id)
                        seed_ids.append(match.node_id)
                if not seed_ids:
                    return GraphAnswerContext(GraphContextStatus.NOT_READY)

                depth = 1 if len(seed_ids) >= 3 else 2
                nodes_by_id: dict[str, ProjectNode] = {}
                edges_by_id: dict[str, ProjectEdge] = {}
                sources_by_node_id: dict[str, dict[tuple[object, ...], NodeSource]] = {}
                evidence_by_edge_id: dict[str, dict[tuple[object, ...], EdgeEvidence]] = {}
                for seed_id in seed_ids:
                    subgraph = await self._store.neighbors(
                        scope,
                        seed_id,
                        depth=depth,
                        node_limit=self._node_limit,
                        source_revision_ids=source_revision_ids,
                        version=version,
                    )
                    for node in subgraph.nodes:
                        nodes_by_id.setdefault(node.node_id, node)
                    for edge in subgraph.edges:
                        edges_by_id.setdefault(edge.edge_id, edge)
                    for source in subgraph.sources:
                        node_bucket = sources_by_node_id.setdefault(source.node_id, {})
                        node_bucket.setdefault(_source_key(source), source)
                    for evidence_item in subgraph.evidence:
                        edge_bucket = evidence_by_edge_id.setdefault(evidence_item.edge_id, {})
                        edge_bucket.setdefault(_evidence_key(evidence_item), evidence_item)

                seed_id_set = set(seed_ids)
                if not seed_id_set & nodes_by_id.keys():
                    # Every seed's evidence lies outside `source_revision_ids`,
                    # so none survived the project graph's source filter.
                    return GraphAnswerContext(GraphContextStatus.NOT_READY)

                ordered_node_ids = [node_id for node_id in seed_ids if node_id in nodes_by_id] + [
                    node_id for node_id in nodes_by_id if node_id not in seed_id_set
                ]
                selected_node_ids = tuple(ordered_node_ids[: self._node_limit])
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
                other_edges = [edge for edge in candidate_edges if edge not in seed_adjacent_edges]
                selected_edges = tuple((seed_adjacent_edges + other_edges)[: self._edge_limit])
                selected_edge_ids = {edge.edge_id for edge in selected_edges}

                evidence_items: list[NodeSource | EdgeEvidence] = []
                for node_id in selected_node_ids:
                    evidence_items.extend(sources_by_node_id.get(node_id, {}).values())
                for edge_id in selected_edge_ids:
                    evidence_items.extend(evidence_by_edge_id.get(edge_id, {}).values())

                allowed_sources = set(source_revision_ids)
                if any(item.source_revision_id not in allowed_sources for item in evidence_items):
                    return GraphAnswerContext(GraphContextStatus.FAILED)

                if self._publication_authority is not None and publication is not None:
                    for proof in evidence_items:
                        approved_item_id = proof.anchor.get("inventoryItemId")
                        await self._publication_authority.authorize_evidence(
                            scope.project_id,
                            source_revision_id=proof.source_revision_id,
                            document_revision_id=proof.document_revision_id,
                            approved_item_id=(
                                approved_item_id if isinstance(approved_item_id, str) else None
                            ),
                        )
                    await self._publication_authority.revalidate(publication)
            except (ProjectGraphNotReady, GraphFactNotFound, AuthorizationDenied):
                return GraphAnswerContext(GraphContextStatus.FAILED)
            except Exception:
                return GraphAnswerContext(GraphContextStatus.UNAVAILABLE)

            facts: list[Mapping[str, object]] = []
            for node_id in selected_node_ids:
                node = nodes_by_id[node_id]
                facts.append(
                    {
                        "kind": "node",
                        "id": node.node_id,
                        "label": node.label,
                        "type": node.node_type,
                        "aliases": list(node.aliases),
                        "communityId": node.community_id,
                        "seed": node.node_id in seed_id_set,
                        "evidence": [
                            _evidence_locator(item)
                            for item in sources_by_node_id.get(node.node_id, {}).values()
                        ],
                    }
                )
            for edge in selected_edges:
                facts.append(
                    {
                        "kind": "edge",
                        "id": edge.edge_id,
                        "sourceNodeId": edge.source_node_id,
                        "targetNodeId": edge.target_node_id,
                        "relationType": edge.relation_type,
                        "relationLabel": edge.relation_label,
                        "origin": edge.origin.value,
                        "confidence": edge.confidence,
                        "evidence": [
                            _evidence_locator(item)
                            for item in evidence_by_edge_id.get(edge.edge_id, {}).values()
                        ],
                    }
                )

            current_span.set_attribute("tap.graph.version", version)
            current_span.set_attribute("tap.graph.seed_count", len(seed_ids))
            current_span.set_attribute("tap.graph.node_count", len(selected_node_ids))
            current_span.set_attribute("tap.graph.edge_count", len(selected_edges))
            return GraphAnswerContext(
                GraphContextStatus.APPLIED,
                current.version_id,
                tuple(facts),
                version,
                tuple(seed_ids),
            )


def _source_key(item: NodeSource) -> tuple[object, ...]:
    return (
        item.node_id,
        item.source_revision_id,
        item.document_revision_id,
        item.chunk_id,
        item.fragment_snapshot_id,
        item.fragment_node_id,
    )


def _evidence_key(item: EdgeEvidence) -> tuple[object, ...]:
    return (
        item.edge_id,
        item.source_revision_id,
        item.document_revision_id,
        item.chunk_id,
        item.fragment_snapshot_id,
        item.fragment_edge_id,
    )


def _evidence_locator(item: NodeSource | EdgeEvidence) -> dict[str, object]:
    return {
        "sourceRevisionId": item.source_revision_id,
        "documentRevisionId": item.document_revision_id,
        "chunkId": item.chunk_id,
        "anchor": dict(item.anchor),
        "contentDigest": getattr(item, "content_digest", None),
    }
