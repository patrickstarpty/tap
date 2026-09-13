"""Deterministic local Graph extraction used by the isolated demo composition."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)
from tap.modules.knowledge.domain.documents import (
    ChunkDraft,
    ChunkId,
    DocumentId,
    LogicalChunkId,
)


def deterministic_draft(
    snapshot: GraphSnapshot, chunks: tuple[ChunkDraft, ...], *, filename: str
) -> GraphSnapshotDraft:
    document_node_id = "grn_" + hashlib.sha256(snapshot.snapshot_id.encode()).hexdigest()[:32]
    evidence: list[Evidence] = []
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    for item in chunks[:499]:
        suffix = hashlib.sha256(str(item.chunk_id).encode()).hexdigest()[:32]
        evidence_id = f"gre_{suffix}"
        node_id = f"grn_{suffix}"
        evidence.append(
            Evidence(
                evidence_id,
                snapshot.snapshot_id,
                snapshot.source_revision_ids[0],
                snapshot.document_revision_ids[0],
                str(item.chunk_id),
                json.loads(item.anchor_json),
                item.chunk_content_hash,
            )
        )
        nodes.append(
            GraphNode(
                node_id,
                snapshot.snapshot_id,
                f"Section {len(nodes) + 1}",
                "CONCEPT",
                f"chunk:{item.logical_chunk_id}",
                (evidence_id,),
            )
        )
        edges.append(
            GraphEdge(
                f"ged_{suffix}",
                snapshot.snapshot_id,
                document_node_id,
                node_id,
                "CONTAINS",
                RelationOrigin.EXTRACTED,
                1.0,
                (evidence_id,),
            )
        )
    root_evidence = tuple(item.evidence_id for item in evidence)
    nodes.insert(
        0,
        GraphNode(
            document_node_id,
            snapshot.snapshot_id,
            filename,
            "ENTITY",
            f"document:{snapshot.document_revision_ids[0]}",
            root_evidence,
        ),
    )
    return GraphSnapshotDraft(
        replace(snapshot, status="CANDIDATE"), tuple(nodes), tuple(edges), tuple(evidence), ()
    )


class DeterministicGraphExtraction:
    """Extract predictable grounded facts without a model for isolated E2E."""

    async def extract(self, request: GraphExtractionRequest) -> GraphSnapshotDraft:
        chunks = tuple(
            ChunkDraft(
                chunk_id=ChunkId(str(item["chunkId"])),
                logical_chunk_id=LogicalChunkId(str(item["chunkId"])),
                root_id=DocumentId(str(request.snapshot.document_revision_ids[0])),
                parent_id=None,
                content=str(item["content"]),
                anchor_json=json.dumps(item["anchor"], sort_keys=True, separators=(",", ":")),
                source_content_hash=str(item["contentDigest"]),
                chunk_content_hash=str(item["contentDigest"]),
            )
            for item in request.chunks
        )
        return deterministic_draft(
            request.snapshot,
            chunks,
            filename=request.snapshot.document_revision_ids[0],
        )
