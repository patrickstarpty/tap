"""Immutable, Project-scoped knowledge graph facts and bounded queries."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, Mapping

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_GRAPH_DEPTH = 2
MAX_GRAPH_NODES = 500


def _identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"{name} must be a bounded identifier")


def source_set_digest(source_revision_ids: tuple[str, ...]) -> str:
    if not source_revision_ids or len(source_revision_ids) != len(set(source_revision_ids)):
        raise ValueError("source revisions must be nonempty and unique")
    ordered = sorted(source_revision_ids)
    material = json.dumps(ordered, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(material.encode()).hexdigest()


class RelationOrigin(StrEnum):
    EXTRACTED = "EXTRACTED"
    INFERRED = "INFERRED"


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    snapshot_id: str
    project_id: str
    source_revision_ids: tuple[str, ...]
    document_revision_ids: tuple[str, ...]
    source_set_digest: str
    status: Literal["CANDIDATE", "READY", "FAILED"] = "CANDIDATE"

    def __post_init__(self) -> None:
        _identifier("snapshot_id", self.snapshot_id)
        _identifier("project_id", self.project_id)
        if self.source_revision_ids != tuple(sorted(self.source_revision_ids)):
            raise ValueError("source revisions must be canonical")
        if self.document_revision_ids != tuple(sorted(self.document_revision_ids)):
            raise ValueError("document revisions must be canonical")
        if self.source_set_digest != source_set_digest(self.source_revision_ids):
            raise ValueError("source set digest does not match snapshot")
        if self.status not in {"CANDIDATE", "READY", "FAILED"}:
            raise ValueError("invalid snapshot status")

    @classmethod
    def create(
        cls,
        *,
        snapshot_id: str,
        project_id: str,
        source_revision_ids: tuple[str, ...],
        document_revision_ids: tuple[str, ...],
        status: Literal["CANDIDATE", "READY", "FAILED"] = "CANDIDATE",
    ) -> GraphSnapshot:
        sources = tuple(sorted(source_revision_ids))
        documents = tuple(sorted(document_revision_ids))
        if not documents or len(documents) != len(set(documents)):
            raise ValueError("document revisions must be nonempty and unique")
        return cls(
            snapshot_id,
            project_id,
            sources,
            documents,
            source_set_digest(sources),
            status,
        )


@dataclass(frozen=True, slots=True)
class GraphNode:
    node_id: str
    snapshot_id: str
    label: str
    node_type: str
    canonical_key: str
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (("node_id", self.node_id), ("snapshot_id", self.snapshot_id)):
            _identifier(name, value)
        if (
            not self.label.strip()
            or len(self.label) > 512
            or not self.canonical_key.strip()
            or len(self.canonical_key) > 512
        ):
            raise ValueError("node label and canonical key must be nonblank")
        if self.node_type not in {"ENTITY", "CONCEPT", "REQUIREMENT", "SYSTEM", "ACTOR"}:
            raise ValueError("unknown node type")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("node evidence identities must be unique")


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    snapshot_id: str
    source_revision_id: str
    document_revision_id: str
    chunk_id: str
    anchor: Mapping[str, object]
    content_digest: str

    def __post_init__(self) -> None:
        for name, value in (
            ("evidence_id", self.evidence_id),
            ("snapshot_id", self.snapshot_id),
            ("source_revision_id", self.source_revision_id),
            ("document_revision_id", self.document_revision_id),
            ("chunk_id", self.chunk_id),
        ):
            _identifier(name, value)
        if _DIGEST.fullmatch(self.content_digest) is None:
            raise ValueError("evidence content digest must be canonical SHA-256")
        anchor = dict(self.anchor)
        if anchor.get("kind") == "text":
            start, end = anchor.get("start"), anchor.get("end")
            if (
                set(anchor) != {"kind", "start", "end"}
                or not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or not 0 <= start < end <= 10_000_000
            ):
                raise ValueError("evidence text anchor is not resolvable")
        elif anchor.get("kind") == "page":
            page = anchor.get("page")
            if (
                set(anchor) != {"kind", "page"}
                or not isinstance(page, int)
                or isinstance(page, bool)
                or not 1 <= page <= 1_000_000
            ):
                raise ValueError("evidence page anchor is not resolvable")
        elif anchor.get("type") == "document":
            required = {"type", "startOffset", "endOffset", "headingPath"}
            start, end = anchor.get("startOffset"), anchor.get("endOffset")
            headings, page = anchor.get("headingPath"), anchor.get("page")
            if (
                not required <= set(anchor) <= required | {"page"}
                or not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or not 0 <= start < end <= 10_000_000
                or not isinstance(headings, list)
                or len(headings) > 32
                or not all(isinstance(item, str) and len(item) <= 512 for item in headings)
                or (
                    page is not None
                    and (
                        not isinstance(page, int)
                        or isinstance(page, bool)
                        or not 1 <= page <= 1_000_000
                    )
                )
            ):
                raise ValueError("evidence document anchor is not resolvable")
        else:
            raise ValueError("evidence anchor is not resolvable")
        object.__setattr__(self, "anchor", MappingProxyType(anchor))


@dataclass(frozen=True, slots=True)
class GraphEdge:
    edge_id: str
    snapshot_id: str
    source_node_id: str
    target_node_id: str
    relation_type: str
    origin: RelationOrigin
    confidence: float
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("edge_id", self.edge_id),
            ("snapshot_id", self.snapshot_id),
            ("source_node_id", self.source_node_id),
            ("target_node_id", self.target_node_id),
        ):
            _identifier(name, value)
        if self.source_node_id == self.target_node_id:
            raise ValueError("self edges are not allowed")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", self.relation_type):
            raise ValueError("unknown relation type")
        if not isinstance(self.origin, RelationOrigin):
            raise TypeError("origin must be a RelationOrigin")
        if type(self.confidence) is not float or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between zero and one")
        if self.origin is RelationOrigin.EXTRACTED and not self.evidence_ids:
            raise ValueError("extracted relation requires evidence")
        if self.origin is RelationOrigin.INFERRED and self.evidence_ids:
            raise ValueError("inferred relation cannot claim direct evidence")


@dataclass(frozen=True, slots=True)
class InferenceProvenance:
    provenance_id: str
    snapshot_id: str
    edge_id: str
    input_fact_ids: tuple[str, ...]
    rule_digest: str

    def __post_init__(self) -> None:
        if not self.input_fact_ids or len(self.input_fact_ids) != len(set(self.input_fact_ids)):
            raise ValueError("inference provenance requires unique input facts")
        if _DIGEST.fullmatch(self.rule_digest) is None:
            raise ValueError("invalid inference rule digest")


@dataclass(frozen=True, slots=True)
class GraphSnapshotDraft:
    snapshot: GraphSnapshot
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    evidence: tuple[Evidence, ...]
    provenance: tuple[InferenceProvenance, ...]

    def __post_init__(self) -> None:
        snapshot_id = self.snapshot.snapshot_id
        if any(item.snapshot_id != snapshot_id for item in self.nodes):
            raise ValueError("graph fact belongs to a different snapshot")
        if any(item.snapshot_id != snapshot_id for item in self.edges):
            raise ValueError("graph fact belongs to a different snapshot")
        if any(item.snapshot_id != snapshot_id for item in self.evidence):
            raise ValueError("graph fact belongs to a different snapshot")
        if any(item.snapshot_id != snapshot_id for item in self.provenance):
            raise ValueError("graph fact belongs to a different snapshot")
        node_ids = {item.node_id for item in self.nodes}
        evidence_ids = {item.evidence_id for item in self.evidence}
        edge_ids = {item.edge_id for item in self.edges}
        provenance_ids = {item.provenance_id for item in self.provenance}
        if not self.nodes or len(self.nodes) > MAX_GRAPH_NODES or len(self.edges) > 2_000:
            raise ValueError("graph snapshot exceeds fact bounds")
        if (
            len(node_ids) != len(self.nodes)
            or len(edge_ids) != len(self.edges)
            or len(evidence_ids) != len(self.evidence)
            or len(provenance_ids) != len(self.provenance)
        ):
            raise ValueError("graph identities must be unique")
        if any(set(node.evidence_ids) - evidence_ids for node in self.nodes):
            raise ValueError("node evidence is dangling")
        for edge in self.edges:
            if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
                raise ValueError("edge endpoint is dangling")
            if set(edge.evidence_ids) - evidence_ids:
                raise ValueError("edge evidence is dangling")
        provenance_edges = {item.edge_id for item in self.provenance}
        if len(provenance_edges) != len(self.provenance):
            raise ValueError("inferred relation provenance must be unique")
        fact_ids = node_ids | edge_ids
        if any(set(item.input_fact_ids) - fact_ids for item in self.provenance):
            raise ValueError("inference provenance input fact is dangling")
        for edge in self.edges:
            if edge.origin is RelationOrigin.INFERRED and edge.edge_id not in provenance_edges:
                raise ValueError("inferred relation requires provenance")


@dataclass(frozen=True, slots=True)
class GraphSubgraph:
    snapshot_id: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphSearchQuery:
    snapshot_id: str
    text: str
    node_limit: int = 50

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("search query must be nonblank")
        _node_limit(self.node_limit)


@dataclass(frozen=True, slots=True)
class NeighborQuery:
    snapshot_id: str
    node_id: str
    depth: int = 1
    node_limit: int = 50

    def __post_init__(self) -> None:
        if type(self.depth) is not int or not 1 <= self.depth <= MAX_GRAPH_DEPTH:
            raise ValueError("depth must be between one and two")
        _node_limit(self.node_limit)


@dataclass(frozen=True, slots=True)
class PathQuery:
    snapshot_id: str
    source_node_id: str
    target_node_id: str
    node_limit: int = 50

    def __post_init__(self) -> None:
        _node_limit(self.node_limit)


def _node_limit(value: int) -> None:
    if type(value) is not int or not 1 <= value <= MAX_GRAPH_NODES:
        raise ValueError("node limit must be between one and 500")
