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
                "anchor": {"kind": "text", "start": 0, "end": 22},
                "contentDigest": "sha256:" + "a" * 64,
            },
        ),
        model_alias="tapper-graph",
        idempotency_key="graph:snapshot-1",
    )


def _output():
    # The model cites evidence only by the batch-local chunk ref; the server
    # backfills revision ids, chunk id, anchor and digest from the request.
    return {
        "nodes": [
            {
                "id": "node-1",
                "label": "Policy",
                "type": "ENTITY",
                "canonicalKey": "policy",
                "chunks": ["c1"],
            },
            {
                "id": "node-2",
                "label": "Claim",
                "type": "ENTITY",
                "canonicalKey": "claim",
                "chunks": ["c1"],
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
                "chunks": ["c1"],
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
    assert draft.edges[0].evidence_ids == ("c1",)
    assert gateway.requests[0].operation is ModelOperation.STRUCTURED
    assert gateway.requests[0].schema_digest.startswith("sha256:")
    # Extraction has its own deadline and output budget, independent of the
    # interactive model timeout and the gateway's 2048-token default.
    assert gateway.requests[0].timeout_seconds == 240.0
    assert gateway.requests[0].max_output_tokens == 8192


@pytest.mark.asyncio
async def test_extraction_deadline_and_budget_are_configurable_and_bounded():
    gateway = Gateway(_output())

    await ModelGatewayGraphExtraction(gateway, timeout_seconds=240, max_output_tokens=4096).extract(
        _request()
    )

    assert gateway.requests[0].timeout_seconds == 240
    assert gateway.requests[0].max_output_tokens == 4096
    for timeout in (0, 301, float("nan")):
        with pytest.raises(ValueError):
            ModelGatewayGraphExtraction(gateway, timeout_seconds=timeout)
    for budget in (0, 32769):
        with pytest.raises(ValueError):
            ModelGatewayGraphExtraction(gateway, max_output_tokens=budget)


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
                    value["nodes"][0] | {"chunks": ["c2"]},
                    value["nodes"][1],
                ]
            },
            "dangling",
        ),
        (
            lambda value: value | {"edges": [value["edges"][0] | {"chunks": ["chunk-1"]}]},
            "dangling",
        ),
        (
            lambda value: value
            | {"edges": [value["edges"][0] | {"origin": "INFERRED", "chunks": []}]},
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
            "chunks": [],
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
            "chunks": [],
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


@pytest.mark.asyncio
async def test_empty_model_output_returns_none_instead_of_raising():
    # A model returning zero nodes for a boilerplate chunk must not raise (the
    # domain forbids an empty-nodes GraphSnapshotDraft): it is a successful batch
    # that simply grounded nothing, not a failure to retry.
    output = {"nodes": [], "edges": [], "provenance": []}
    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())
    assert draft is None


@pytest.mark.asyncio
async def test_node_aliases_are_sanitized_before_construction():
    output = _output()
    output["nodes"][0] = output["nodes"][0] | {
        "aliases": [
            "  Policy Doc  ",
            "policy doc",  # dedupes case/width-insensitively with the entry above
            "",  # blank, dropped
            "   ",  # blank after stripping, dropped
            "x" * 129,  # over the 128-char limit, dropped
            "Alias 2",
            "Alias 3",
            "Alias 4",
            "Alias 5",
            "Alias 6",  # sixth distinct alias, dropped by the 5-alias cap
        ]
    }
    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())
    node = next(node for node in draft.nodes if node.node_id == "node-1")
    assert node.aliases == ("Policy Doc", "Alias 2", "Alias 3", "Alias 4", "Alias 5")


def test_schema_locks_vocabulary_and_process_type():
    from tap.modules.graph.adapters.model_gateway_extraction import GRAPH_EXTRACTION_SCHEMA

    nodes = GRAPH_EXTRACTION_SCHEMA["properties"]["nodes"]["items"]["properties"]
    edges = GRAPH_EXTRACTION_SCHEMA["properties"]["edges"]["items"]["properties"]
    assert set(nodes["type"]["enum"]) == NODE_TYPES
    # Aliases are capped at five by the adapter, not by the schema: an over-long
    # alias list must not fail (or split) a batch.
    assert "maxItems" not in nodes["aliases"] and "up to five" in nodes["aliases"]["description"]
    assert set(edges["relationType"]["enum"]) == RELATION_TYPES
    assert "relationLabel" in GRAPH_EXTRACTION_SCHEMA["properties"]["edges"]["items"]["required"]


def _two_chunk_request():
    return replace(
        _request(),
        chunks=(
            _request().chunks[0],
            {
                "sourceRevisionId": "source-revision-1",
                "documentRevisionId": "document-revision-1",
                "chunkId": "chunk-2",
                "content": "理赔需要提交保险合同。",
                "anchor": {
                    "type": "document",
                    "startOffset": 22,
                    "endOffset": 33,
                    "headingPath": ["第三条 保险责任"],
                    "page": 2,
                    "inventoryItemId": "pi_" + "e" * 64,
                },
                "contentDigest": "sha256:" + "f" * 64,
            },
        ),
        document_title="理赔手册",
        known_entities=(
            {"id": "n9", "label": "保险合同", "type": "ENTITY", "canonicalKey": "保险合同"},
        ),
    )


@pytest.mark.asyncio
async def test_context_carries_short_chunk_refs_and_unescaped_chinese():
    gateway = Gateway(_output())

    await ModelGatewayGraphExtraction(gateway).extract(_two_chunk_request())

    raw = gateway.requests[0].context
    assert "\\u" not in raw and "理赔需要提交保险合同。" in raw and "保险合同" in raw
    context = json.loads(raw)
    assert context["chunks"] == [
        {"ref": "c1", "content": "Policy governs claims."},
        {"ref": "c2", "content": "理赔需要提交保险合同。"},
    ]
    # Identifiers the model never needs to echo stay out of its input.
    for hidden in ("contentDigest", "anchor", "chunkId", "sha256:", "pi_", "snapshotId"):
        assert hidden not in raw
    assert context["documentTitle"] == "理赔手册"


@pytest.mark.asyncio
async def test_server_backfills_evidence_from_cited_refs_only():
    output = _output()
    output["nodes"][0]["chunks"] = ["c2", "c1", "c2"]
    output["edges"][0]["chunks"] = ["c2"]

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_two_chunk_request())

    by_id = {item.evidence_id: item for item in draft.evidence}
    assert [item.evidence_id for item in draft.evidence] == ["c1", "c2"]
    assert by_id["c2"].chunk_id == "chunk-2"
    assert by_id["c2"].content_digest == "sha256:" + "f" * 64
    assert by_id["c2"].source_revision_id == "source-revision-1"
    assert by_id["c2"].document_revision_id == "document-revision-1"
    assert dict(by_id["c2"].anchor)["headingPath"] == ["第三条 保险责任"]
    assert dict(by_id["c1"].anchor) == {"kind": "text", "start": 0, "end": 22}
    node = next(item for item in draft.nodes if item.node_id == "node-1")
    assert node.evidence_ids == ("c2", "c1")
    assert draft.edges[0].evidence_ids == ("c2",)


@pytest.mark.asyncio
async def test_uncited_chunks_produce_no_evidence():
    draft = await ModelGatewayGraphExtraction(Gateway(_output())).extract(_two_chunk_request())

    assert [item.evidence_id for item in draft.evidence] == ["c1"]


def test_schema_and_prompt_never_ask_the_model_to_copy_hashes_or_anchors():
    from tap.modules.graph.adapters.model_gateway_extraction import (
        GRAPH_EXTRACTION_PROMPT,
        GRAPH_EXTRACTION_SCHEMA,
    )

    serialized = json.dumps(GRAPH_EXTRACTION_SCHEMA)
    for copied in ("contentDigest", "anchorJson", "chunkId", "sourceRevisionId", "evidence"):
        assert copied not in serialized
    assert set(GRAPH_EXTRACTION_SCHEMA["required"]) == {"nodes", "edges", "provenance"}
    assert "contentDigest" not in GRAPH_EXTRACTION_PROMPT
    assert "anchorJson" not in GRAPH_EXTRACTION_PROMPT
    assert "c1" in GRAPH_EXTRACTION_PROMPT


def test_schema_and_prompt_bound_each_batch_output():
    # A real 1500-character batch once wrote 142 nodes and no edges, or 125+ copies
    # of one edge, until the 8192-token budget cut it off; every list is capped.
    from tap.modules.graph.adapters.model_gateway_extraction import (
        GRAPH_BATCH_MAX_EDGES,
        GRAPH_BATCH_MAX_NODES,
        GRAPH_EXTRACTION_PROMPT,
        GRAPH_EXTRACTION_SCHEMA,
    )

    properties = GRAPH_EXTRACTION_SCHEMA["properties"]
    assert (GRAPH_BATCH_MAX_NODES, GRAPH_BATCH_MAX_EDGES) == (40, 60)
    assert properties["nodes"]["maxItems"] == 40
    assert properties["edges"]["maxItems"] == 60
    assert properties["provenance"]["maxItems"] == 60
    assert "at most 40 nodes and at most 60 edges" in GRAPH_EXTRACTION_PROMPT
    assert "only entities and relations stated in this batch's chunks" in GRAPH_EXTRACTION_PROMPT
    assert "at most one edge per source, relationType and target" in GRAPH_EXTRACTION_PROMPT


def _known_policy_request():
    return replace(
        _request(),
        batch_index=1,
        known_entities=(
            {"id": "b0-n1", "label": "Policy", "type": "ENTITY", "canonicalKey": "policy"},
        ),
    )


@pytest.mark.asyncio
async def test_edges_reference_known_entities_without_repeating_them(span_recorder):
    from tap.modules.graph.adapters.model_gateway_extraction import GRAPH_EXTRACTION_PROMPT

    output = _output()
    # The model lists only the new node; the edge cites the known entity by id.
    output["nodes"] = [output["nodes"][1]]
    output["edges"][0] = output["edges"][0] | {"sourceNodeId": "b0-n1"}

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_known_policy_request())

    by_id = {node.node_id: node for node in draft.nodes}
    assert set(by_id) == {"b0-n1", "node-2"}
    known = by_id["b0-n1"]
    assert (known.label, known.node_type, known.canonical_key) == ("Policy", "ENTITY", "policy")
    assert known.evidence_ids == ("c1",)
    assert [(edge.source_node_id, edge.target_node_id) for edge in draft.edges] == [
        ("b0-n1", "node-2")
    ]
    assert "Never output a known entity in nodes" in GRAPH_EXTRACTION_PROMPT
    assert "when the text names it differently" in GRAPH_EXTRACTION_PROMPT


@pytest.mark.asyncio
async def test_repeated_known_entities_are_dropped_and_counted(span_recorder):
    output = _output()
    # A copy under the known id and a copy under a fresh id with the known key.
    output["nodes"].append(output["nodes"][0] | {"id": "b0-n1"})
    output["edges"].append(
        output["edges"][0] | {"id": "edge-2", "sourceNodeId": "b0-n1", "relationType": "USES"}
    )

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_known_policy_request())

    by_id = {node.node_id: node for node in draft.nodes}
    assert set(by_id) == {"b0-n1", "node-2"}
    assert {(edge.edge_id, edge.source_node_id) for edge in draft.edges} == {
        ("edge-1", "b0-n1"),
        ("edge-2", "b0-n1"),
    }
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.repeated_known_entities"] == 2


@pytest.mark.asyncio
async def test_truncated_model_output_is_reported_as_a_truncated_batch():
    from tap.modules.ai.domain.models import ModelOutputTruncated
    from tap.modules.graph.domain.extraction import GraphExtractionTruncated

    class TruncatingGateway:
        async def generate_structured(self, request):
            raise ModelOutputTruncated()

    with pytest.raises(GraphExtractionTruncated):
        await ModelGatewayGraphExtraction(TruncatingGateway()).extract(_request())


def _real_zengli_replay():
    from pathlib import Path

    sample = json.loads(
        (Path(__file__).parents[1] / "fixtures/graph/real_extraction_replay.json").read_text()
    )["zengliBatch0"]
    request = replace(
        _request(),
        chunks=tuple(
            {
                "sourceRevisionId": "source-revision-1",
                "documentRevisionId": "document-revision-1",
                "chunkId": f"chunk-{chunk['ref']}",
                "content": chunk["content"],
                "anchor": {"kind": "text", "start": 0, "end": 1},
                "contentDigest": "sha256:" + "a" * 64,
            }
            for chunk in sample["chunks"]
        ),
        document_title=sample["documentTitle"],
    )
    return request, {"nodes": sample["nodes"], "edges": sample["edges"], "provenance": []}


@pytest.mark.asyncio
async def test_real_repeated_edge_loop_is_deduplicated_and_counted(span_recorder):
    # Replay of the real batch whose output looped on one DEFINES edge until the
    # 8192-token budget cut it off (first 60 of its 124 edges, 33 of them n1->n15).
    request, output = _real_zengli_replay()
    shapes = [(e["sourceNodeId"], e["relationType"], e["targetNodeId"]) for e in output["edges"]]
    duplicates = len(shapes) - len(set(shapes))
    assert duplicates == 32

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(request)

    kept = [(e.source_node_id, e.relation_type, e.target_node_id) for e in draft.edges]
    assert len(kept) == len(set(kept)) == len(set(shapes))
    loop = next(e for e in draft.edges if (e.source_node_id, e.target_node_id) == ("n1", "n15"))
    assert loop.relation_type == "DEFINES"
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.duplicate_edges"] == duplicates


@pytest.mark.asyncio
async def test_duplicate_nodes_merge_and_their_edges_follow(span_recorder):
    output = _output()
    # The same entity twice under two ids (real batches did this, e.g. b3-n2/b3-n3),
    # plus an edge through the copy that becomes a duplicate and one that becomes a
    # self-edge once the copy is merged.
    output["nodes"].append(output["nodes"][1] | {"id": "node-2b", "chunks": ["c1"]})
    output["nodes"].append(output["nodes"][0] | {"id": "node-1"})
    output["edges"].append(output["edges"][0] | {"id": "edge-2", "targetNodeId": "node-2b"})
    output["edges"].append(
        output["edges"][0] | {"id": "edge-3", "sourceNodeId": "node-2b", "relationType": "USES"}
    )

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())

    assert [node.node_id for node in draft.nodes] == ["node-1", "node-2"]
    assert [edge.edge_id for edge in draft.edges] == ["edge-1"]
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.duplicate_nodes"] == 2
    assert spans[0].attributes["tap.graph.duplicate_edges"] == 1
    assert spans[0].attributes["tap.graph.dropped_edges"] == 1


@pytest.mark.asyncio
async def test_reference_words_never_become_aliases_or_names(span_recorder):
    from tap.modules.graph.adapters.model_gateway_extraction import GRAPH_EXTRACTION_PROMPT

    output = _output()
    # Real outputs: a product aliased "本合同", the insurer named "本公司" with its
    # real name as alias, a clause named only by its number, and a clause-long alias.
    output["nodes"] = [
        output["nodes"][0]
        | {"label": "友邦尊享智选康惠团体医疗保险", "canonicalKey": "团体医疗保险"}
        | {"aliases": ["本合同", "团体医疗保险", "第十九条", "（一）"]},
        output["nodes"][1]
        | {"label": "本公司", "canonicalKey": "本公司", "aliases": ["我们", "友邦保险"]},
        {
            "id": "node-3",
            "label": "第十九条",
            "type": "REQUIREMENT",
            "canonicalKey": "第十九条",
            "chunks": ["c1"],
            "aliases": [],
        },
        {
            "id": "node-4",
            "label": "保单贷款",
            "type": "REQUIREMENT",
            "canonicalKey": "保单贷款",
            "chunks": ["c1"],
            "aliases": ["本公司在合同订立时已经知道投保人未如实告知的情况的，本公司不得解除合同"],
        },
    ]
    output["edges"].append(
        output["edges"][0] | {"id": "edge-2", "sourceNodeId": "node-3", "targetNodeId": "node-4"}
    )

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())

    by_id = {node.node_id: node for node in draft.nodes}
    assert set(by_id) == {"node-1", "node-2", "node-4"}
    assert by_id["node-1"].aliases == ("团体医疗保险",)
    insurer = by_id["node-2"]
    assert (insurer.label, insurer.canonical_key, insurer.aliases) == ("友邦保险", "友邦保险", ())
    assert by_id["node-4"].aliases == ()
    assert [edge.edge_id for edge in draft.edges] == ["edge-1"]
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.rejected_names"] == 1
    assert "never a pronoun" in GRAPH_EXTRACTION_PROMPT and "第十九条" in GRAPH_EXTRACTION_PROMPT


@pytest.mark.asyncio
async def test_six_aliases_on_one_node_are_trimmed_without_splitting_the_batch():
    from tap.modules.ai.application.schema import validate_output
    from tap.modules.graph.adapters.model_gateway_extraction import GRAPH_EXTRACTION_SCHEMA

    output = _output()
    output["nodes"][0]["aliases"] = [f"Policy name {index}" for index in range(6)]
    output["nodes"][1]["aliases"] = []

    # The gateway's locked-schema check accepts it (nothing to split) ...
    validate_output(GRAPH_EXTRACTION_SCHEMA, output)
    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())

    # ... and the adapter keeps the first five.
    node = next(node for node in draft.nodes if node.node_id == "node-1")
    assert node.aliases == tuple(f"Policy name {index}" for index in range(5))
