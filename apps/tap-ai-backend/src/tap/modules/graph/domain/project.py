"""Immutable, Project-scoped merged knowledge graph: stable node identity, provenance,
aliases, communities, and the fragment digest that pins a merge to its inputs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Literal, Mapping

from tap.modules.graph.domain.models import GraphSnapshotDraft, RelationOrigin


@dataclass(frozen=True, slots=True)
class ProjectGraphVersion:
    project_id: str
    version: int
    status: Literal["MERGING", "READY", "FAILED"]
    fragment_digest: str
    node_count: int
    edge_count: int
    merged_at: datetime | None

    @property
    def version_id(self) -> str:
        return f"gpv_{self.version}"


@dataclass(frozen=True, slots=True)
class ProjectNode:
    node_id: str
    label: str
    node_type: str
    canonical_key: str
    degree: int = 0
    community_id: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    relation_type: str
    relation_label: str
    origin: RelationOrigin
    confidence: float


@dataclass(frozen=True, slots=True)
class NodeSource:
    node_id: str
    source_revision_id: str
    document_revision_id: str
    chunk_id: str
    anchor: Mapping[str, object]
    fragment_snapshot_id: str
    fragment_node_id: str


@dataclass(frozen=True, slots=True)
class EdgeEvidence:
    edge_id: str
    source_revision_id: str
    document_revision_id: str
    chunk_id: str
    anchor: Mapping[str, object]
    content_digest: str
    fragment_snapshot_id: str
    fragment_edge_id: str


@dataclass(frozen=True, slots=True)
class Alias:
    alias_norm: str
    node_id: str
    origin: Literal["LABEL", "MODEL", "MERGE"]


@dataclass(frozen=True, slots=True)
class Community:
    community_id: str
    label: str
    size: int


@dataclass(frozen=True, slots=True)
class MergeLogEntry:
    node_id: str
    merged_from: tuple[tuple[str, str], ...]
    rule: Literal["EXACT", "ALIAS", "EMBEDDING"]


@dataclass(frozen=True, slots=True)
class ProjectGraphDraft:
    fragment_digest: str
    nodes: tuple[ProjectNode, ...]
    edges: tuple[ProjectEdge, ...]
    node_sources: tuple[NodeSource, ...]
    edge_evidence: tuple[EdgeEvidence, ...]
    aliases: tuple[Alias, ...]
    communities: tuple[Community, ...]
    merge_log: tuple[MergeLogEntry, ...]

    def __post_init__(self) -> None:
        node_ids = {node.node_id for node in self.nodes}
        evidenced_edge_ids = {evidence.edge_id for evidence in self.edge_evidence}
        for edge in self.edges:
            if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
                raise ValueError("project edge endpoint is dangling")
            if edge.origin is RelationOrigin.EXTRACTED and edge.edge_id not in evidenced_edge_ids:
                raise ValueError("extracted project edge requires evidence")
        seen_aliases: set[tuple[str, str]] = set()
        for alias in self.aliases:
            if not alias.alias_norm:
                raise ValueError("alias norm must be nonblank")
            key = (alias.alias_norm, alias.node_id)
            if key in seen_aliases:
                raise ValueError("alias must be unique per node")
            seen_aliases.add(key)


@dataclass(frozen=True, slots=True)
class ProjectSubgraph:
    version: int
    nodes: tuple[ProjectNode, ...]
    edges: tuple[ProjectEdge, ...]
    sources: tuple[NodeSource, ...] = ()
    evidence: tuple[EdgeEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectNodeDetail:
    version: int
    node: ProjectNode
    community: Community | None
    sources: tuple[NodeSource, ...]
    edges: tuple[ProjectEdge, ...]
    neighbors: tuple[ProjectNode, ...]
    evidence: tuple[EdgeEvidence, ...]


@dataclass(frozen=True, slots=True)
class FragmentRecord:
    snapshot_id: str
    revision_id: str
    status: Literal["READY", "PARTIAL"]
    content_digest: str
    draft: GraphSnapshotDraft


def fragment_digest(records: Iterable[FragmentRecord]) -> str:
    ordered = sorted((record.snapshot_id, record.content_digest) for record in records)
    material = json.dumps(ordered, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(material.encode()).hexdigest()


def project_node_id(node_type: str, key: str) -> str:
    return "gpn_" + hashlib.sha256(f"{node_type}|{key}".encode()).hexdigest()[:32]


def project_edge_id(source: str, relation_type: str, target: str) -> str:
    digest = hashlib.sha256(f"{source}|{relation_type}|{target}".encode()).hexdigest()
    return "gpe_" + digest[:32]
