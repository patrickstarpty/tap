#!/usr/bin/env python3
"""Run the generated QUALITY-GRAPH-01 candidate through the real Graph path."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from tap.entrypoints.tapper_runtime import (
    TapperSettings,
    _create_embeddings,
    _open_database,
)
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.adapters.model_gateway_extraction import (
    ModelGatewayGraphExtraction,
)
from tap.modules.graph.adapters.mysql import MysqlGraphStore
from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.models import GraphSnapshot
from tap.modules.ai.ports.gateway import ModelGateway

from tap.quality.evidence import (
    candidate_digest,
    candidate_run_id,
    canonical_digest,
    graph_candidate_sets,
    graph_materials,
)


class CapturingGateway:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.identities: set[tuple[str, str]] = set()
        self.receipts: dict[str, tuple[str, str, str, str]] = {}

    async def generate_structured(self, request):  # type: ignore[no-untyped-def]
        result = await self.delegate.generate_structured(request)
        self.identities.add((result.actual_provider, result.actual_model))
        if (
            not isinstance(result.provider_request_id, str)
            or not result.provider_request_id
        ):
            raise ValueError("real Graph result requires a provider request id")
        request_digest = canonical_digest(
            {
                "alias": request.alias,
                "operation": request.operation.value,
                "promptDigest": request.prompt_digest,
                "schemaDigest": request.schema_digest,
                "contextDigest": canonical_digest(request.context),
                "idempotencyKey": request.idempotency_key,
                "governanceDigests": list(request.governance_digests),
            }
        )
        self.receipts[request.idempotency_key] = (
            result.actual_provider,
            result.actual_model,
            result.provider_request_id,
            request_digest,
        )
        return result


_NON_HUMAN_REVIEWERS = {
    "generated-candidate-requires-independent-human-review",
    "machine-observation-requires-independent-human-review",
}


def validate_approved_review(profile: object) -> None:
    if not isinstance(profile, dict):
        raise ValueError("quality profile must be an object")
    dataset = profile.get("dataset")
    labels = profile.get("labels")
    if not isinstance(dataset, dict) or dataset.get("reviewStatus") != "approved":
        raise ValueError("real Graph run requires approved human review")
    if not isinstance(labels, list) or not labels:
        raise ValueError("real Graph run requires reviewed labels")
    for label in labels:
        reviewer = label.get("reviewer") if isinstance(label, dict) else None
        if (
            not isinstance(reviewer, str)
            or not reviewer.strip()
            or reviewer in _NON_HUMAN_REVIEWERS
        ):
            raise ValueError("real Graph run requires an independent human reviewer")


def _endpoint_open(uri: str) -> None:
    parsed = urlparse(uri)
    if parsed.hostname is None or parsed.port is None:
        raise ValueError("Milvus URI must include an explicit host and port")
    with socket.create_connection((parsed.hostname, parsed.port), timeout=5):
        pass


def _observed(label: dict[str, Any], draft) -> tuple[bool, bool, bool]:  # type: ignore[no-untyped-def]
    expected = label["expected"]
    nodes = {node.node_id: node for node in draft.nodes}
    by_key = {node.canonical_key: node for node in draft.nodes}
    evidence = {item.evidence_id: item for item in draft.evidence}
    if label["kind"] == "node":
        node = by_key.get(expected["canonicalKey"])
        predicted = node is not None and node.node_type == expected["nodeType"]
        evidence_ids = () if node is None else node.evidence_ids
        return (
            predicted,
            predicted,
            bool(evidence_ids) and all(x in evidence for x in evidence_ids),
        )
    if label["kind"] == "merge":
        matches = [
            node
            for node in draft.nodes
            if node.canonical_key == expected["canonicalKey"]
        ]
        predicted = len(matches) == 1
        resolvable = predicted and bool(matches[0].evidence_ids)
        return predicted, predicted is bool(expected["shouldMerge"]), resolvable
    matched = None
    for edge in draft.edges:
        source = nodes.get(edge.source_node_id)
        target = nodes.get(edge.target_node_id)
        if source is None or target is None:
            continue
        if (
            source.canonical_key,
            edge.relation_type,
            target.canonical_key,
        ) == (expected["source"], expected["relationType"], expected["target"]):
            matched = edge
            break
    predicted = matched is not None
    resolvable = bool(matched and matched.evidence_ids) and all(
        item in evidence for item in (() if matched is None else matched.evidence_ids)
    )
    provenance = label["origin"] != "INFERRED" or bool(
        matched
        and any(
            item.edge_id == matched.edge_id and item.input_fact_ids
            for item in draft.provenance
        )
    )
    return predicted, resolvable, provenance


async def run(profile: dict[str, Any]) -> dict[str, Any]:
    settings = TapperSettings.from_mapping(dict(os.environ))
    if settings.graph_extraction_mode != "model":
        raise ValueError("candidate run requires TAPPER_GRAPH_EXTRACTION_MODE=model")
    _endpoint_open(settings.milvus_uri)
    engine, sessions = _open_database(settings)
    models = _create_embeddings(settings, max_retries=0)
    capture = CapturingGateway(models.gateway)
    extractor = ModelGatewayGraphExtraction(
        cast(ModelGateway, capture),
        timeout_seconds=settings.model_timeout_seconds,
    )
    store = MysqlGraphStore(sessions)
    observations = deepcopy(profile)
    try:
        labels_by_document: dict[str, list[dict[str, Any]]] = {}
        for label in observations["labels"]:
            labels_by_document.setdefault(label["documentId"], []).append(label)
            label["reviewer"] = "pending-independent-human-review"
        observations["dataset"]["reviewStatus"] = "pending"
        documents_by_revision = {
            document["documentId"]
            + "-revision-"
            + document["contentDigest"].removeprefix("sha256:")[:12]: document
            for document in observations["documents"]
        }
        candidate_outputs: list[dict[str, Any]] = []
        for case_id, revision_ids in graph_candidate_sets(observations):
            selected_documents = [
                documents_by_revision[value] for value in revision_ids
            ]
            snapshot = GraphSnapshot.create(
                snapshot_id="quality-" + case_id,
                project_id=VALIDATION_SCOPE.project_id,
                source_revision_ids=revision_ids,
                document_revision_ids=revision_ids,
            )
            request = GraphExtractionRequest(
                scope=VALIDATION_SCOPE,
                snapshot=snapshot,
                chunks=tuple(
                    {
                        "sourceRevisionId": revision_id,
                        "documentRevisionId": revision_id,
                        "chunkId": document["documentId"] + "-chunk",
                        "content": document["content"],
                        "anchor": {
                            "kind": "text",
                            "start": 0,
                            "end": len(document["content"]),
                        },
                        "contentDigest": document["contentDigest"],
                    }
                    for revision_id, document in zip(
                        revision_ids, selected_documents, strict=True
                    )
                ),
                model_alias=settings.chat_alias,
                idempotency_key="quality-graph:" + case_id,
            )
            draft = await extractor.extract(request)
            await store.publish(VALIDATION_SCOPE, draft)
            for document in selected_documents:
                for label in labels_by_document[document["documentId"]]:
                    predicted, correct_or_resolvable, provenance = _observed(
                        label, draft
                    )
                    label["predicted"] = predicted
                    label["correct"] = (
                        predicted if label["kind"] != "merge" else correct_or_resolvable
                    )
                    label["evidenceResolvable"] = (
                        correct_or_resolvable
                        if label["kind"] != "merge"
                        else provenance
                    )
                    label["provenanceComplete"] = provenance
            output = {
                "sourceRevisionIds": list(draft.snapshot.source_revision_ids),
                "documentRevisionIds": list(draft.snapshot.document_revision_ids),
                "nodes": [
                    {
                        "nodeId": item.node_id,
                        "snapshotId": item.snapshot_id,
                        "label": item.label,
                        "nodeType": item.node_type,
                        "canonicalKey": item.canonical_key,
                        "evidenceIds": list(item.evidence_ids),
                    }
                    for item in draft.nodes
                ],
                "edges": [
                    {
                        "edgeId": item.edge_id,
                        "snapshotId": item.snapshot_id,
                        "sourceNodeId": item.source_node_id,
                        "targetNodeId": item.target_node_id,
                        "relationType": item.relation_type,
                        "origin": item.origin.value,
                        "confidence": item.confidence,
                        "evidenceIds": list(item.evidence_ids),
                    }
                    for item in draft.edges
                ],
                "evidence": [
                    {
                        "evidenceId": item.evidence_id,
                        "snapshotId": item.snapshot_id,
                        "sourceRevisionId": item.source_revision_id,
                        "documentRevisionId": item.document_revision_id,
                        "chunkId": item.chunk_id,
                        "anchor": dict(item.anchor),
                        "contentDigest": item.content_digest,
                    }
                    for item in draft.evidence
                ],
                "provenance": [
                    {
                        "provenanceId": item.provenance_id,
                        "snapshotId": item.snapshot_id,
                        "edgeId": item.edge_id,
                        "inputFactIds": list(item.input_fact_ids),
                        "ruleDigest": item.rule_digest,
                    }
                    for item in draft.provenance
                ],
            }
            provider, model, provider_request_id, request_digest = capture.receipts[
                request.idempotency_key
            ]
            candidate_outputs.append(
                {
                    "caseId": case_id,
                    "status": "completed",
                    "executionMode": "real",
                    "requestId": provider_request_id,
                    "requestDigest": request_digest,
                    "output": output,
                    "outputDigest": canonical_digest(output),
                    "provider": provider,
                    "model": model,
                    "reviewJudgments": [],
                }
            )
        if len(capture.identities) != 1:
            raise ValueError("real model identity changed during candidate run")
        provider, model = next(iter(capture.identities))
        observations["bindings"]["actualModel"] = f"{provider}/{model}"
        observations["dataset"]["modelObservationStatus"] = "captured"
        dataset_material, config_material, model_material = graph_materials(
            observations
        )
        dataset_digest = canonical_digest(dataset_material)
        config_digest = canonical_digest(config_material)
        model_digest = canonical_digest(model_material)
        for item in candidate_outputs:
            provider_receipt = {
                "provider": item.pop("provider"),
                "model": item.pop("model"),
                "providerRequestId": item["requestId"],
                "requestDigest": item["requestDigest"],
                "outputDigest": item["outputDigest"],
            }
            receipt_digest = canonical_digest(provider_receipt)
            item.update(
                providerReceipt=provider_receipt,
                receiptDigest=receipt_digest,
                datasetDigest=dataset_digest,
                configDigest=config_digest,
                modelDigest=model_digest,
            )
            item["candidateDigest"] = candidate_digest(
                case_id=str(item["caseId"]),
                request_id=str(item["requestId"]),
                request_digest=str(item["requestDigest"]),
                receipt_digest=receipt_digest,
                output_digest=str(item["outputDigest"]),
                dataset_digest=dataset_digest,
                config_digest=config_digest,
                model_digest=model_digest,
            )
        observations["runEvidence"] = {
            "schemaVersion": "quality-candidate-evidence-v1",
            "runId": candidate_run_id(candidate_outputs),
            "runStatus": "awaiting_review",
            "executionMode": "real",
            "datasetDigest": dataset_digest,
            "configDigest": config_digest,
            "modelDigest": model_digest,
            "cases": candidate_outputs,
        }
        return observations
    finally:
        await models.aclose()
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", type=Path)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--require-approved-review", action="store_true")
    arguments = parser.parse_args()
    profile = json.loads(arguments.profile.read_text(encoding="utf-8"))
    if arguments.require_approved_review:
        raise ValueError(
            "candidate generation cannot consume prefilled approval; review observations after the run"
        )
    observations = asyncio.run(run(profile))
    arguments.observations.parent.mkdir(parents=True, exist_ok=True)
    arguments.observations.write_text(
        json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
