import json
from dataclasses import replace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.schema import check_schema
from tap.modules.ai.domain.models import ModelCallAudit, ModelOperation, ModelResult, ModelUsage
from tap.modules.graph.adapters.model_gateway_extraction import ModelGatewayGraphExtraction
from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.models import GraphSnapshot
from tap.modules.graph.domain.vocabulary import NODE_TYPES, RELATION_TYPES


class Gateway:
    def __init__(self, output):
        self.output = output
        self.requests = []

    async def generate_structured(self, request):
        self.requests.append(request)
        return ModelResult(
            output=self.output,
            actual_model="provider/model",
            actual_provider="provider",
            usage=ModelUsage(10, 10),
            audit=ModelCallAudit(
                scope=VALIDATION_SCOPE,
                alias=request.alias,
                operation=ModelOperation.STRUCTURED,
                prompt_digest=request.prompt_digest,
                schema_digest=request.schema_digest,
                context_digest="sha256:" + "c" * 64,
                idempotency_key=request.idempotency_key,
                actual_provider="provider",
                actual_model="provider/model",
                usage=ModelUsage(10, 10),
            ),
        )


def _request():
    return GraphExtractionRequest(
        scope=VALIDATION_SCOPE,
        snapshot=GraphSnapshot.create(
            snapshot_id="snapshot-1",
            project_id=VALIDATION_SCOPE.project_id,
            source_revision_ids=("source-revision-1",),
            document_revision_ids=("document-revision-1",),
        ),
        chunks=(
            {
                "sourceRevisionId": "source-revision-1",
                "documentRevisionId": "document-revision-1",
                "chunkId": "chunk-1",
                "content": "Policy governs claims.",
                "contentDigest": "sha256:" + "a" * 64,
            },
        ),
        model_alias="tapper-graph",
        idempotency_key="graph:snapshot-1",
    )


def _output():
    return {
        "nodes": [
            {
                "id": "node-1",
                "label": "Policy",
                "type": "ENTITY",
                "canonicalKey": "policy",
                "evidenceIds": ["evidence-1"],
            },
            {
                "id": "node-2",
                "label": "Claim",
                "type": "ENTITY",
                "canonicalKey": "claim",
                "evidenceIds": ["evidence-1"],
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "sourceNodeId": "node-1",
                "targetNodeId": "node-2",
                "relationType": "REQUIRES",
                "relationLabel": "governs",
                "origin": "EXTRACTED",
                "confidence": 1.0,
                "evidenceIds": ["evidence-1"],
            }
        ],
        "evidence": [
            {
                "id": "evidence-1",
                "sourceRevisionId": "source-revision-1",
                "documentRevisionId": "document-revision-1",
                "chunkId": "chunk-1",
                "anchorJson": '{"kind":"text","start":0,"end":22}',
                "contentDigest": "sha256:" + "a" * 64,
            }
        ],
        "provenance": [],
    }


@pytest.mark.asyncio
async def test_model_gateway_extraction_is_schema_locked_and_grounded():
    from tap.modules.graph.adapters.model_gateway_extraction import GRAPH_EXTRACTION_SCHEMA

    check_schema(GRAPH_EXTRACTION_SCHEMA)
    gateway = Gateway(_output())
    draft = await ModelGatewayGraphExtraction(gateway).extract(_request())
    assert draft.edges[0].evidence_ids == ("evidence-1",)
    assert gateway.requests[0].operation is ModelOperation.STRUCTURED
    assert gateway.requests[0].schema_digest.startswith("sha256:")
    assert gateway.requests[0].timeout_seconds == 15.0


@pytest.mark.asyncio
async def test_model_gateway_extraction_defines_specific_types_before_entity_fallback():
    gateway = Gateway(_output())

    await ModelGatewayGraphExtraction(gateway).extract(_request())

    node_schema = gateway.requests[0].schema["properties"]["nodes"]["items"]
    type_schema = node_schema["properties"]["type"]
    assert type_schema["description"] == (
        "Use ACTOR for people or roles, SYSTEM for named software or services, "
        "REQUIREMENT for requirements or controls, CONCEPT for abstract ideas, and "
        "ENTITY only when none of the specific types applies."
    )
    assert (
        "Classify every person or role as ACTOR, every named software application or "
        "service as SYSTEM, and every stated requirement or control as REQUIREMENT. "
        "Use ENTITY only if none of ACTOR, SYSTEM, REQUIREMENT, or CONCEPT applies."
        in gateway.requests[0].prompt
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: "not-an-object", "object"),
        (lambda value: value | {"extra": []}, "fields"),
        (
            lambda value: value
            | {"nodes": [value["nodes"][0] | {"type": "UNKNOWN"}, value["nodes"][1]]},
            "node type",
        ),
        (
            lambda value: value
            | {
                "nodes": [
                    value["nodes"][0] | {"evidenceIds": ["missing-evidence"]},
                    value["nodes"][1],
                ]
            },
            "dangling",
        ),
        (
            lambda value: value
            | {"evidence": [value["evidence"][0] | {"contentDigest": "sha256:" + "b" * 64}]},
            "digest",
        ),
        (
            lambda value: value
            | {"edges": [value["edges"][0] | {"origin": "INFERRED", "evidenceIds": []}]},
            "provenance",
        ),
    ],
)
async def test_invalid_model_facts_fail_closed(mutate, message):
    with pytest.raises(ValueError, match=message):
        await ModelGatewayGraphExtraction(Gateway(mutate(_output()))).extract(_request())


@pytest.mark.asyncio
async def test_batch_context_carries_title_and_known_entities():
    gateway = Gateway(_output())
    request = replace(
        _request(),
        batch_index=2,
        document_title="Claims policy",
        known_entities=(
            {"id": "node-1", "label": "Policy", "type": "ENTITY", "canonicalKey": "policy"},
        ),
    )
    await ModelGatewayGraphExtraction(gateway).extract(request)
    context = json.loads(gateway.requests[0].context)
    assert context["batchIndex"] == 2 and context["documentTitle"] == "Claims policy"
    assert context["knownEntities"][0]["id"] == "node-1"
    assert "knownEntities" in gateway.requests[0].prompt


@pytest.mark.asyncio
async def test_edges_outside_vocabulary_are_dropped_not_fatal():
    output = _output()
    output["edges"].append({**output["edges"][0], "id": "edge-2", "relationType": "GOVERNS"})
    output["edges"].append({**output["edges"][0], "id": "edge-3", "targetNodeId": "missing-node"})
    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())
    assert [edge.edge_id for edge in draft.edges] == ["edge-1"]
    assert draft.edges[0].relation_label == "governs"


@pytest.mark.asyncio
async def test_dropped_edge_removes_its_provenance():
    output = _output()
    output["edges"].append(
        {
            **output["edges"][0],
            "id": "edge-missing",
            "targetNodeId": "missing-node",
            "relationType": "USES",
            "origin": "INFERRED",
            "evidenceIds": [],
        }
    )
    output["provenance"].append(
        {
            "id": "provenance-missing",
            "edgeId": "edge-missing",
            "inputFactIds": ["node-1"],
            "ruleDigest": "sha256:" + "d" * 64,
        }
    )
    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())
    assert [edge.edge_id for edge in draft.edges] == ["edge-1"]
    assert draft.provenance == ()


@pytest.mark.asyncio
async def test_dropped_input_fact_cascades_to_inferred_edge(span_recorder):
    output = _output()
    output["edges"].append({**output["edges"][0], "id": "edge-x", "relationType": "GOVERNS"})
    output["edges"].append(
        {
            **output["edges"][0],
            "id": "edge-y",
            "relationType": "USES",
            "origin": "INFERRED",
            "evidenceIds": [],
        }
    )
    output["provenance"].append(
        {
            "id": "provenance-y",
            "edgeId": "edge-y",
            "inputFactIds": ["edge-x"],
            "ruleDigest": "sha256:" + "d" * 64,
        }
    )
    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())
    assert [edge.edge_id for edge in draft.edges] == ["edge-1"]
    assert draft.provenance == ()
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dropped_edges"] == 2


def test_schema_locks_vocabulary_and_process_type():
    from tap.modules.graph.adapters.model_gateway_extraction import GRAPH_EXTRACTION_SCHEMA

    nodes = GRAPH_EXTRACTION_SCHEMA["properties"]["nodes"]["items"]["properties"]
    edges = GRAPH_EXTRACTION_SCHEMA["properties"]["edges"]["items"]["properties"]
    assert set(nodes["type"]["enum"]) == NODE_TYPES and nodes["aliases"]["maxItems"] == 5
    assert set(edges["relationType"]["enum"]) == RELATION_TYPES
    assert "relationLabel" in GRAPH_EXTRACTION_SCHEMA["properties"]["edges"]["items"]["required"]
