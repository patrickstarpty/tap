"""Bounded, fail-soft graph context for frozen Knowledge answers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.graph.domain.models import GraphSearchQuery
from tap.modules.graph.ports.store import GraphFactNotFound, GraphStorePort


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
    def __init__(self, store: GraphStorePort, *, node_limit: int = 20) -> None:
        if not 1 <= node_limit <= 50:
            raise ValueError("answer Graph node limit must be between 1 and 50")
        self._store = store
        self._node_limit = node_limit

    async def enrich(
        self,
        scope: ProjectScopeContext,
        source_revision_ids: tuple[str, ...],
        query: str,
    ) -> GraphAnswerContext:
        if not source_revision_ids:
            return GraphAnswerContext(GraphContextStatus.NOT_SELECTED)
        try:
            snapshot = await self._store.active_snapshot(scope, source_revision_ids)
            if snapshot is None:
                return GraphAnswerContext(GraphContextStatus.NOT_READY)
            graph = await self._store.search(
                scope,
                GraphSearchQuery(snapshot.snapshot_id, query, self._node_limit),
            )
        except GraphFactNotFound:
            return GraphAnswerContext(GraphContextStatus.FAILED)
        except Exception:
            return GraphAnswerContext(GraphContextStatus.UNAVAILABLE)
        allowed_sources = set(source_revision_ids)
        if any(item.source_revision_id not in allowed_sources for item in graph.evidence):
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
        for edge in graph.edges:
            facts.append(
                {
                    "kind": "edge",
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
        return GraphAnswerContext(
            GraphContextStatus.APPLIED,
            snapshot.snapshot_id,
            tuple(facts),
        )


def _evidence_locator(item) -> dict[str, object]:
    return {
        "sourceRevisionId": item.source_revision_id,
        "documentRevisionId": item.document_revision_id,
        "chunkId": item.chunk_id,
        "anchor": dict(item.anchor),
        "contentDigest": item.content_digest,
    }
