"""Schema-locked Graph extraction through the shared ModelGateway."""

from __future__ import annotations

import json
import math
from typing import cast

from tap.modules.ai.domain.models import ModelOperation, ModelRequest, schema_digest, text_digest
from tap.modules.ai.ports.gateway import ModelGateway
from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshotDraft,
    InferenceProvenance,
    RelationOrigin,
)

GRAPH_EXTRACTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["nodes", "edges", "evidence", "provenance"],
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "label", "type", "canonicalKey", "evidenceIds"],
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["ENTITY", "CONCEPT", "REQUIREMENT", "SYSTEM", "ACTOR"],
                        "description": (
                            "Use ACTOR for people or roles, SYSTEM for named software or "
                            "services, REQUIREMENT for requirements or controls, CONCEPT "
                            "for abstract ideas, and ENTITY only when none of the specific "
                            "types applies."
                        ),
                    },
                    "canonicalKey": {"type": "string"},
                    "evidenceIds": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "sourceNodeId",
                    "targetNodeId",
                    "relationType",
                    "origin",
                    "confidence",
                    "evidenceIds",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "sourceNodeId": {"type": "string"},
                    "targetNodeId": {"type": "string"},
                    "relationType": {"type": "string"},
                    "origin": {
                        "type": "string",
                        "enum": ["EXTRACTED", "INFERRED"],
                    },
                    "confidence": {"type": "number"},
                    "evidenceIds": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "sourceRevisionId",
                    "documentRevisionId",
                    "chunkId",
                    "anchorJson",
                    "contentDigest",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "sourceRevisionId": {"type": "string"},
                    "documentRevisionId": {"type": "string"},
                    "chunkId": {"type": "string"},
                    "anchorJson": {"type": "string"},
                    "contentDigest": {"type": "string"},
                },
            },
        },
        "provenance": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "edgeId", "inputFactIds", "ruleDigest"],
                "properties": {
                    "id": {"type": "string"},
                    "edgeId": {"type": "string"},
                    "inputFactIds": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "ruleDigest": {"type": "string"},
                },
            },
        },
    },
}
GRAPH_EXTRACTION_PROMPT = (
    "Extract only grounded graph facts from the supplied chunks and return exactly the JSON "
    "schema. Use stable nonblank IDs. Every node must cite existing evidence IDs. "
    "Classify every person or role as ACTOR, every named software application or service "
    "as SYSTEM, and every stated requirement or control as REQUIREMENT. Use ENTITY only "
    "if none of ACTOR, SYSTEM, REQUIREMENT, or CONCEPT applies. Every "
    "EXTRACTED edge must cite existing evidence IDs. An INFERRED edge must have an empty "
    "evidenceIds array and exactly one provenance item whose edgeId matches it and whose "
    "inputFactIds are nonempty existing node, edge, or evidence IDs. Copy sourceRevisionId, "
    "documentRevisionId, chunkId, and contentDigest exactly from an authorized input chunk. "
    "Set anchorJson to the compact JSON string of that chunk's anchor. Never emit a self-edge; "
    "canonicalize aliases into one node and omit any alias edge that would become a self-edge. "
    "Preserve uppercase relation tokens explicitly declared by the document. Apply any explicit "
    "inference rule declared by the document, copy its declared canonical sha256 rule digest, and "
    "record its input edge IDs as provenance. Do not invent facts."
)
GRAPH_EXTRACTION_PROFILE_DIGEST = text_digest(
    GRAPH_EXTRACTION_PROMPT + "\n" + schema_digest(GRAPH_EXTRACTION_SCHEMA)
)


class ModelGatewayGraphExtraction:
    def __init__(self, gateway: ModelGateway, *, timeout_seconds: float = 15.0) -> None:
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
            raise ValueError("graph extraction timeout must be between 0 and 60 seconds")
        self._gateway = gateway
        self._timeout_seconds = timeout_seconds

    async def extract(self, request: GraphExtractionRequest) -> GraphSnapshotDraft:
        context = json.dumps(
            {"snapshotId": request.snapshot.snapshot_id, "chunks": list(request.chunks)},
            sort_keys=True,
            separators=(",", ":"),
        )
        result = await self._gateway.generate_structured(
            ModelRequest(
                scope=request.scope,
                alias=request.model_alias,
                operation=ModelOperation.STRUCTURED,
                prompt=GRAPH_EXTRACTION_PROMPT,
                prompt_digest=text_digest(GRAPH_EXTRACTION_PROMPT),
                context=context,
                timeout_seconds=self._timeout_seconds,
                idempotency_key=request.idempotency_key,
                schema=GRAPH_EXTRACTION_SCHEMA,
                schema_digest=schema_digest(GRAPH_EXTRACTION_SCHEMA),
            )
        )
        if not isinstance(result.output, dict):
            raise ValueError("graph extraction output must be an object")
        output = cast(dict[str, object], result.output)
        if set(output) != {"nodes", "edges", "evidence", "provenance"}:
            raise ValueError("graph extraction output has invalid fields")
        try:
            nodes_raw = _objects(output["nodes"], "nodes")
            edges_raw = _objects(output["edges"], "edges")
            evidence_raw = _objects(output["evidence"], "evidence")
            provenance_raw = _objects(output["provenance"], "provenance")
            snapshot_id = request.snapshot.snapshot_id
            nodes = tuple(
                GraphNode(
                    _string(item, "id"),
                    snapshot_id,
                    _string(item, "label"),
                    _string(item, "type"),
                    _string(item, "canonicalKey"),
                    _strings(item, "evidenceIds"),
                )
                for item in nodes_raw
            )
            evidence = tuple(
                Evidence(
                    _string(item, "id"),
                    snapshot_id,
                    _string(item, "sourceRevisionId"),
                    _string(item, "documentRevisionId"),
                    _string(item, "chunkId"),
                    _json_mapping(item, "anchorJson"),
                    _string(item, "contentDigest"),
                )
                for item in evidence_raw
            )
            edges = tuple(
                GraphEdge(
                    _string(item, "id"),
                    snapshot_id,
                    _string(item, "sourceNodeId"),
                    _string(item, "targetNodeId"),
                    _string(item, "relationType"),
                    RelationOrigin(_string(item, "origin")),
                    _float(item, "confidence"),
                    _strings(item, "evidenceIds"),
                )
                for item in edges_raw
            )
            provenance = tuple(
                InferenceProvenance(
                    _string(item, "id"),
                    snapshot_id,
                    _string(item, "edgeId"),
                    _strings(item, "inputFactIds"),
                    _string(item, "ruleDigest"),
                )
                for item in provenance_raw
            )
        except (KeyError, TypeError) as error:
            raise ValueError("graph extraction output is malformed") from error
        self._validate_evidence_against_chunks(request, evidence)
        return GraphSnapshotDraft(request.snapshot, nodes, edges, evidence, provenance)

    @staticmethod
    def _validate_evidence_against_chunks(
        request: GraphExtractionRequest, evidence: tuple[Evidence, ...]
    ) -> None:
        chunks = {
            (
                item.get("sourceRevisionId"),
                item.get("documentRevisionId"),
                item.get("chunkId"),
            ): item.get("contentDigest")
            for item in request.chunks
        }
        for item in evidence:
            key = (item.source_revision_id, item.document_revision_id, item.chunk_id)
            if key not in chunks or chunks[key] != item.content_digest:
                raise ValueError("evidence digest does not resolve to an authorized chunk")


def _objects(value: object, name: str) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"graph extraction {name} must be object arrays")
    return tuple(cast(dict[str, object], item) for item in value)


def _string(value: dict[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str):
        raise TypeError(key)
    return item


def _strings(value: dict[str, object], key: str) -> tuple[str, ...]:
    item = value[key]
    if not isinstance(item, list) or any(not isinstance(part, str) for part in item):
        raise TypeError(key)
    return tuple(cast(list[str], item))


def _float(value: dict[str, object], key: str) -> float:
    item = value[key]
    if type(item) not in {int, float}:
        raise TypeError(key)
    return float(cast(float, item))


def _json_mapping(value: dict[str, object], key: str) -> dict[str, object]:
    raw = _string(value, key)
    try:
        item = json.loads(raw)
    except json.JSONDecodeError as error:
        raise TypeError(key) from error
    if not isinstance(item, dict):
        raise TypeError(key)
    return cast(dict[str, object], item)
