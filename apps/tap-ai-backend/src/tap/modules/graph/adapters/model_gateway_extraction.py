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
from tap.modules.graph.domain.vocabulary import (
    NODE_ALIAS_LENGTH_MAX,
    NODE_ALIAS_MAX,
    NODE_TYPES,
    RELATION_LABEL_MAX,
    RELATION_TYPES,
    normalize_key,
)
from tap.platform.telemetry import span

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
                "required": ["id", "label", "type", "canonicalKey", "evidenceIds", "aliases"],
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": sorted(NODE_TYPES),
                        "description": (
                            "Use ACTOR for people or roles, SYSTEM for named software or "
                            "services, REQUIREMENT for requirements or controls, CONCEPT "
                            "for abstract ideas, and ENTITY only when none of the specific "
                            "types applies."
                        ),
                    },
                    "canonicalKey": {"type": "string"},
                    "evidenceIds": {"type": "array", "items": {"type": "string"}},
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": NODE_ALIAS_MAX,
                    },
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
                    "relationLabel",
                    "origin",
                    "confidence",
                    "evidenceIds",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "sourceNodeId": {"type": "string"},
                    "targetNodeId": {"type": "string"},
                    "relationType": {"type": "string", "enum": sorted(RELATION_TYPES)},
                    "relationLabel": {"type": "string"},
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
    "record its input edge IDs as provenance. Do not invent facts. Input also carries "
    "documentTitle and knownEntities from earlier batches of the same document. When an edge "
    "in this batch references a known entity, include that known entity in nodes with its "
    "existing id, label, type, and canonicalKey exactly as given -- never emit a new node "
    "with a different id for the same real-world thing, and never omit it from nodes just "
    "because it was already known. Classify processes, steps, or workflow stages as PROCESS. "
    "Use relationType only from the provided enum; put the document's own wording for the "
    "relation in relationLabel (at most 64 characters). List up to five aliases for a node "
    "when the text names it differently."
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

    async def extract(self, request: GraphExtractionRequest) -> GraphSnapshotDraft | None:
        with span(
            "graph.extract_batch", {"tap.graph.batch_index": request.batch_index}
        ) as current_span:
            context = json.dumps(
                {
                    "snapshotId": request.snapshot.snapshot_id,
                    "batchIndex": request.batch_index,
                    "documentTitle": request.document_title,
                    "knownEntities": list(request.known_entities),
                    "chunks": list(request.chunks),
                },
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
                        _sanitize_aliases(_optional_strings(item, "aliases")),
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
                node_ids = {node.node_id for node in nodes}
                kept_edges_raw, kept_provenance_raw, dropped = _keep_edges(
                    edges_raw, provenance_raw, node_ids
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
                        _string(item, "relationLabel")[:RELATION_LABEL_MAX],
                    )
                    for item in kept_edges_raw
                )
                provenance = tuple(
                    InferenceProvenance(
                        _string(item, "id"),
                        snapshot_id,
                        _string(item, "edgeId"),
                        _strings(item, "inputFactIds"),
                        _string(item, "ruleDigest"),
                    )
                    for item in kept_provenance_raw
                )
            except (KeyError, TypeError) as error:
                raise ValueError("graph extraction output is malformed") from error
            current_span.set_attribute("tap.graph.dropped_edges", dropped)
        if not nodes:
            # The model grounded nothing in this batch (boilerplate chunks, no
            # facts to extract): a successful batch with nothing to contribute,
            # not a failure. ``GraphSnapshotDraft`` requires at least one node, so
            # there is no way to represent "empty" as a draft; the worker treats
            # ``None`` as this batch's signal and skips it at assembly.
            return None
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


def _keep_edges(
    edges_raw: tuple[dict[str, object], ...],
    provenance_raw: tuple[dict[str, object], ...],
    node_ids: set[str],
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...], int]:
    """Drop edges outside the vocabulary or batch, then cascade to their provenance.

    An edge is dropped when its relationType is outside RELATION_TYPES or either endpoint
    is not among this batch's node ids. Dropping an edge also drops the provenance record
    that cites it (by edgeId), and any INFERRED edge whose provenance lists a dropped edge
    id as an input fact is dropped in turn, repeating until no further edge is dropped.
    """
    provenance_by_edge: dict[object, dict[str, object]] = {
        item.get("edgeId"): item for item in provenance_raw
    }
    dropped_ids: set[object] = {
        item.get("id")
        for item in edges_raw
        if item.get("relationType") not in RELATION_TYPES
        or item.get("sourceNodeId") not in node_ids
        or item.get("targetNodeId") not in node_ids
    }
    changed = True
    while changed:
        changed = False
        for item in edges_raw:
            edge_id = item.get("id")
            if edge_id in dropped_ids or item.get("origin") != "INFERRED":
                continue
            provenance_item = provenance_by_edge.get(edge_id)
            input_facts = provenance_item.get("inputFactIds") if provenance_item else None
            if isinstance(input_facts, list) and dropped_ids.intersection(input_facts):
                dropped_ids.add(edge_id)
                changed = True
    kept_edges = tuple(item for item in edges_raw if item.get("id") not in dropped_ids)
    kept_provenance = tuple(
        item for item in provenance_raw if item.get("edgeId") not in dropped_ids
    )
    return kept_edges, kept_provenance, len(dropped_ids)


def _sanitize_aliases(raw: tuple[str, ...]) -> tuple[str, ...]:
    """Clean model-provided aliases before constructing a ``GraphNode``.

    Strips surrounding whitespace, drops blank or over-length (>128 char) entries,
    dedupes case/width-insensitively (via ``normalize_key``, keeping the
    first-seen spelling), and caps the result at ``NODE_ALIAS_MAX``. Without this,
    a model that returns a blank, over-length, or merely differently-cased
    duplicate alias would make ``GraphNode`` construction raise.
    """

    seen: set[str] = set()
    sanitized: list[str] = []
    for alias in raw:
        stripped = alias.strip()
        if not stripped or len(stripped) > NODE_ALIAS_LENGTH_MAX:
            continue
        key = normalize_key(stripped)
        if key in seen:
            continue
        seen.add(key)
        sanitized.append(stripped)
        if len(sanitized) >= NODE_ALIAS_MAX:
            break
    return tuple(sanitized)


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


def _optional_strings(value: dict[str, object], key: str) -> tuple[str, ...]:
    if key not in value:
        return ()
    return _strings(value, key)


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
