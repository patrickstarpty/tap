"""Deterministic extraction of explicit relation statements in the golden corpus.

Only fixture extraction is replaced: publication, snapshot indexing and answer enrichment
use the same implementations as the application. These are not real-model extraction scores.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Sequence

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.application.queries import InMemoryGraphStore
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)
from tap.modules.knowledge.domain.documents import ChunkDraft
from tap.modules.knowledge.ports.answers import ReadyDocumentRevision

_RELATION = re.compile(r"([^\n]+?) --([A-Z_]+)--> ([^。\n]+)")


def _id(prefix: str, material: str) -> str:
    return prefix + hashlib.sha256(material.encode()).hexdigest()[:32]


async def build_golden_graph(
    chunks: Sequence[tuple[ReadyDocumentRevision, ChunkDraft]],
) -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    grouped: dict[str, list[tuple[ReadyDocumentRevision, ChunkDraft]]] = defaultdict(list)
    for revision, chunk in chunks:
        grouped[revision.revision_id].append((revision, chunk))
    for revision_id, items in grouped.items():
        snapshot = GraphSnapshot.create(
            snapshot_id=_id("grs_", revision_id),
            project_id=VALIDATION_SCOPE.project_id,
            source_revision_ids=(revision_id,),
            document_revision_ids=(revision_id,),
        )
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        evidence: list[Evidence] = []
        for _revision, chunk in items:
            matches = list(_RELATION.finditer(chunk.content))
            if not matches:
                continue
            evidence_id = _id("gre_", str(chunk.chunk_id))
            evidence.append(
                Evidence(
                    evidence_id,
                    snapshot.snapshot_id,
                    revision_id,
                    revision_id,
                    str(chunk.chunk_id),
                    json.loads(chunk.anchor_json),
                    chunk.chunk_content_hash,
                )
            )
            for match in matches:
                source, relation, target = (part.strip() for part in match.groups())
                endpoints = []
                for label in (source, target):
                    identity = _id("grn_", snapshot.snapshot_id + label)
                    previous = nodes.get(identity)
                    refs = tuple(
                        dict.fromkeys((*(previous.evidence_ids if previous else ()), evidence_id))
                    )
                    nodes[identity] = GraphNode(
                        identity, snapshot.snapshot_id, label, "SYSTEM", label, refs
                    )
                    endpoints.append(identity)
                edges.append(
                    GraphEdge(
                        _id("ged_", snapshot.snapshot_id + match.group()),
                        snapshot.snapshot_id,
                        endpoints[0],
                        endpoints[1],
                        relation,
                        RelationOrigin.EXTRACTED,
                        1.0,
                        (evidence_id,),
                    )
                )
        if edges:
            await store.publish(
                VALIDATION_SCOPE,
                GraphSnapshotDraft(
                    snapshot,
                    tuple(nodes.values()),
                    tuple(edges),
                    tuple(evidence),
                    (),
                ),
            )
    return store
