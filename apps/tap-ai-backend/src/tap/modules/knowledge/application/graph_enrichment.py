"""Bounded, fail-soft graph context for frozen Knowledge answers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.graph.domain.index import SnapshotIndex
from tap.modules.graph.ports.store import GraphFactNotFound, GraphStorePort
from tap.modules.knowledge.application.graph_expansion import GraphContextExpander
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
    index: SnapshotIndex | None = None

    def __post_init__(self) -> None:
        if (self.status is GraphContextStatus.APPLIED) != bool(self.snapshot_id):
            raise ValueError("only applied Graph context can identify a snapshot")
        if self.status is not GraphContextStatus.APPLIED and self.facts:
            raise ValueError("non-applied Graph context cannot carry facts")
        object.__setattr__(
            self,
            "facts",
            tuple(MappingProxyType(dict(item)) for item in self.facts),
        )


class GraphAnswerEnricher:
    def __init__(
        self,
        store: GraphStorePort,
        *,
        node_limit: int = 20,
        publication_authority: PublishedKnowledgeAuthority | None = None,
    ) -> None:
        if not 1 <= node_limit <= 50:
            raise ValueError("answer Graph node limit must be between 1 and 50")
        self._store = store
        self._node_limit = node_limit
        self._publication_authority = publication_authority

    async def authorize_facts(
        self,
        scope: ProjectScopeContext,
        index: SnapshotIndex,
        edge_ids: tuple[str, ...],
    ) -> None:
        """Reopen publication authority for every edge and endpoint a tool will expose."""
        if index.snapshot.project_id != scope.project_id:
            raise AuthorizationDenied("graph snapshot is outside Turn scope")
        evidence_ids: set[str] = set()
        for identity in edge_ids:
            edge = index.edges[identity]
            evidence_ids.update(edge.evidence_ids)
            evidence_ids.update(index.nodes[edge.source_node_id].evidence_ids)
            evidence_ids.update(index.nodes[edge.target_node_id].evidence_ids)
        evidence = [index.evidence[identity] for identity in sorted(evidence_ids)]
        if any(
            item.source_revision_id not in index.snapshot.source_revision_ids
            or item.document_revision_id not in index.snapshot.document_revision_ids
            for item in evidence
        ):
            raise AuthorizationDenied("graph evidence is outside Turn snapshot")
        if self._publication_authority is not None:
            publication = await self._publication_authority.authorize_selection(
                scope.project_id, index.snapshot.source_revision_ids
            )
            for item in evidence:
                approved = item.anchor.get("inventoryItemId")
                await self._publication_authority.authorize_evidence(
                    scope.project_id,
                    source_revision_id=item.source_revision_id,
                    document_revision_id=item.document_revision_id,
                    approved_item_id=approved if isinstance(approved, str) else None,
                )
            await self._publication_authority.revalidate(publication)

    async def enrich(
        self,
        scope: ProjectScopeContext,
        source_revision_ids: tuple[str, ...],
        query: str,
        *,
        chunk_ids: tuple[str, ...] = (),
        evidence_labels: Mapping[str, str] | None = None,
        hops: int = 1,
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
                snapshot = await self._store.active_snapshot(scope, source_revision_ids)
                if snapshot is None:
                    return GraphAnswerContext(GraphContextStatus.NOT_READY)
                index = await self._store.load_index(scope, snapshot)
                graph = GraphContextExpander().expand(index, chunk_ids, query, hops=hops)
            except GraphFactNotFound:
                return GraphAnswerContext(GraphContextStatus.FAILED)
            except AuthorizationDenied:
                return GraphAnswerContext(GraphContextStatus.FAILED)
            except Exception:
                return GraphAnswerContext(GraphContextStatus.UNAVAILABLE)
            allowed_sources = set(source_revision_ids)
            if any(
                item.source_revision_id not in allowed_sources
                or item.document_revision_id not in snapshot.document_revision_ids
                for item in graph.evidence
            ):
                return GraphAnswerContext(GraphContextStatus.FAILED)
            if self._publication_authority is not None and publication is not None:
                try:
                    for item in graph.evidence:
                        approved_item_id = item.anchor.get("inventoryItemId")
                        await self._publication_authority.authorize_evidence(
                            scope.project_id,
                            source_revision_id=item.source_revision_id,
                            document_revision_id=item.document_revision_id,
                            approved_item_id=(
                                approved_item_id if isinstance(approved_item_id, str) else None
                            ),
                        )
                    await self._publication_authority.revalidate(publication)
                except Exception:
                    return GraphAnswerContext(GraphContextStatus.FAILED)
            if not graph.nodes:
                return GraphAnswerContext(GraphContextStatus.NOT_READY)
            evidence = {item.evidence_id: item for item in graph.evidence}
            facts: list[Mapping[str, object]] = []
            for node in graph.nodes:
                facts.append(
                    {
                        "kind": "node",
                        "id": node.node_id,
                        "label": node.label,
                        "type": node.node_type,
                        "evidence": [
                            _evidence_locator(evidence[item])
                            for item in node.evidence_ids
                            if item in evidence
                        ],
                    }
                )
            nodes = {item.node_id: item for item in graph.nodes}
            for position, edge in enumerate(graph.edges, 1):
                facts.append(
                    {
                        "kind": "edge",
                        "relationLabel": f"R{position}",
                        "snapshotId": snapshot.snapshot_id,
                        "sourceLabel": nodes[edge.source_node_id].label,
                        "targetLabel": nodes[edge.target_node_id].label,
                        "confidence": edge.confidence,
                        "evidenceLabels": list(
                            dict.fromkeys(
                                (evidence_labels or {})[evidence[item].chunk_id]
                                for item in edge.evidence_ids
                                if item in evidence
                                and evidence[item].chunk_id in (evidence_labels or {})
                            )
                        ),
                        "id": edge.edge_id,
                        "sourceNodeId": edge.source_node_id,
                        "targetNodeId": edge.target_node_id,
                        "relationType": edge.relation_type,
                        "origin": edge.origin.value,
                        "evidence": [
                            _evidence_locator(evidence[item])
                            for item in edge.evidence_ids
                            if item in evidence
                        ],
                    }
                )
            current_span.set_attribute("tap.graph.snapshot_id", snapshot.snapshot_id)
            current_span.set_attribute("tap.graph.node_count", len(graph.nodes))
            current_span.set_attribute("tap.graph.edge_count", len(graph.edges))
            return GraphAnswerContext(
                GraphContextStatus.APPLIED,
                snapshot.snapshot_id,
                tuple(facts),
                index,
            )


def _evidence_locator(item) -> dict[str, object]:
    return {
        "sourceRevisionId": item.source_revision_id,
        "documentRevisionId": item.document_revision_id,
        "chunkId": item.chunk_id,
        "anchor": dict(item.anchor),
        "contentDigest": item.content_digest,
    }
