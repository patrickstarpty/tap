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
        "Use ACTOR for people, roles, or organizations -- including an insurance "
        "company or institution (保险公司, 某保险股份有限公司) -- SYSTEM only for a named "
        "software application or information service, never an organization or an "
        "insurance product, REQUIREMENT for requirements or controls, CONCEPT for "
        "abstract ideas including an insurance product or contract (重大疾病保险, "
        "保险合同), and ENTITY only when none of the specific types applies."
    )
    assert (
        "Classify every person, role, or organization -- including an insurance "
        "company or institution -- as ACTOR; classify a named software application or "
        "information service, never an organization or an insurance product, as "
        "SYSTEM; classify every stated requirement or control as REQUIREMENT; "
        "classify an insurance product or contract as CONCEPT. Use ENTITY only if "
        "none of ACTOR, SYSTEM, REQUIREMENT, or CONCEPT applies." in gateway.requests[0].prompt
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
            | {"edges": [value["edges"][0] | {"origin": "INFERRED", "chunks": []}]},
            "provenance",
        ),
    ],
)
async def test_invalid_model_facts_fail_closed(mutate, message):
    with pytest.raises(ValueError, match=message):
        await ModelGatewayGraphExtraction(Gateway(mutate(_output()))).extract(_request())


@pytest.mark.asyncio
async def test_a_dangling_ref_is_dropped_not_fatal(span_recorder):
    # The model occasionally cites a ref from outside the batch (e.g. a stale c12
    # when only c1 was sent); the extra ref is dropped and the fact keeps whatever
    # evidence remains instead of failing the whole batch closed.
    output = _output()
    output["nodes"][0] = output["nodes"][0] | {"chunks": ["c1", "c2"]}

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())

    node = next(node for node in draft.nodes if node.node_id == "node-1")
    assert node.evidence_ids == ("c1",)
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dangling_refs"] == 1
    assert spans[0].attributes["tap.graph.dropped_no_evidence_nodes"] == 0


@pytest.mark.asyncio
async def test_a_node_with_no_surviving_evidence_is_dropped_with_its_edges(span_recorder):
    # Every ref node-1 cited was dangling: no evidence survives for the fact, so the
    # node is dropped, and the edge that depended on it follows once its endpoint is
    # gone -- the batch still succeeds instead of failing closed.
    output = _output()
    output["nodes"][0] = output["nodes"][0] | {"chunks": ["c2"]}

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())

    assert "node-1" not in {node.node_id for node in draft.nodes}
    assert draft.edges == ()
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dangling_refs"] == 1
    assert spans[0].attributes["tap.graph.dropped_no_evidence_nodes"] == 1
    assert spans[0].attributes["tap.graph.dropped_edges"] == 1


@pytest.mark.asyncio
async def test_an_extracted_edge_with_only_dangling_refs_is_dropped(span_recorder):
    # The edge itself cited only a dangling ref; its endpoints are untouched, so only
    # the edge (and, had one existed, its provenance) is dropped.
    output = _output()
    output["edges"][0] = output["edges"][0] | {"chunks": ["chunk-1"]}

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())

    assert draft.edges == ()
    assert {node.node_id for node in draft.nodes} == {"node-1", "node-2"}
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dangling_refs"] == 1
    assert spans[0].attributes["tap.graph.dropped_edges"] == 1
    assert spans[0].attributes["tap.graph.dropped_no_evidence_edges"] == 1


@pytest.mark.asyncio
async def test_a_node_with_already_empty_chunks_keeps_previous_behavior():
    # A node the model emitted with chunks=[] from the start (not emptied by
    # stripping an out-of-batch ref) keeps its previous behavior: it is still kept,
    # with no evidence, instead of being dropped by the new no-evidence rule.
    output = _output()
    output["nodes"][0] = output["nodes"][0] | {"chunks": []}

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())

    node = next(node for node in draft.nodes if node.node_id == "node-1")
    assert node.evidence_ids == ()


@pytest.mark.asyncio
async def test_an_extracted_edge_with_already_empty_chunks_still_fails_closed():
    # An EXTRACTED edge the model emitted with chunks=[] from the start (not emptied
    # by stripping an out-of-batch ref) keeps its previous behavior: it still fails
    # the whole batch closed at GraphEdge, instead of being silently dropped by the
    # new no-evidence rule (which only applies to evidence lost to stripping).
    output = _output()
    output["edges"][0] = output["edges"][0] | {"chunks": []}

    with pytest.raises(ValueError, match="evidence"):
        await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())


@pytest.mark.asyncio
async def test_a_known_entity_copy_with_only_a_dangling_ref_is_still_remapped(span_recorder):
    # The copy of a known entity cites only an out-of-batch ref. It must still be
    # remapped to the known id -- dropping it instead (as a naive no-evidence rule
    # would) would orphan the edge that cites its local id.
    output = _output()
    output["nodes"][0] = output["nodes"][0] | {"chunks": ["c12"]}

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_known_policy_request())

    by_id = {node.node_id: node for node in draft.nodes}
    assert set(by_id) == {"b0-n1", "node-2"}
    assert [(edge.source_node_id, edge.target_node_id) for edge in draft.edges] == [
        ("b0-n1", "node-2")
    ]
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.repeated_known_entities"] == 1
    assert spans[0].attributes["tap.graph.dropped_no_evidence_nodes"] == 0


@pytest.mark.asyncio
async def test_a_duplicate_node_with_only_a_dangling_ref_is_still_remapped(span_recorder):
    # A duplicate of node-1 under a different local id cites only an out-of-batch
    # ref. It must still be merged into the survivor -- dropping it instead would
    # orphan the edge that cites its local id.
    output = _output()
    output["nodes"].append(output["nodes"][0] | {"id": "node-1b", "chunks": ["c12"]})
    output["edges"].append(
        output["edges"][0] | {"id": "edge-2", "sourceNodeId": "node-1b", "relationType": "USES"}
    )

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_request())

    assert [node.node_id for node in draft.nodes] == ["node-1", "node-2"]
    assert {(edge.edge_id, edge.source_node_id) for edge in draft.edges} == {
        ("edge-1", "node-1"),
        ("edge-2", "node-1"),
    }
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.duplicate_nodes"] == 1
    assert spans[0].attributes["tap.graph.dropped_no_evidence_nodes"] == 0


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
    # chunk-2's own text must name both endpoints (Policy, Claim) for the edge to
    # clear the unsupported-evidence check and reach the evidence backfill under
    # test; chunk-1's "Policy governs claims." would not do, since the edge now
    # cites chunk-2 only.
    request = _two_chunk_request()
    request = replace(
        request,
        chunks=(request.chunks[0], request.chunks[1] | {"content": "Policy requires Claim."}),
    )

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(request)

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


def _real_youxiang_dangling_ref_replay():
    from pathlib import Path

    sample = json.loads(
        (Path(__file__).parents[1] / "fixtures/graph/real_extraction_replay.json").read_text()
    )["youxiangChangban2025Batch3DanglingRef"]
    request = replace(
        _request(),
        batch_index=sample["batchIndex"],
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
    return request, {
        "nodes": sample["nodes"],
        "edges": sample["edges"],
        "provenance": sample["provenance"],
    }


@pytest.mark.asyncio
async def test_real_dangling_ref_batch_succeeds_instead_of_failing_closed(span_recorder):
    # Replay of the real batch that used to raise "graph extraction cites a dangling
    # chunk ref" and fail the whole batch: node n22 ("ICD-10") cited c4, c5 and an
    # out-of-batch c12 (only c1-c9 were sent). The batch must now succeed, keeping
    # n22 with its remaining evidence instead of losing all 40 nodes / 60 edges.
    request, output = _real_youxiang_dangling_ref_replay()

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(request)

    assert draft is not None
    icd10 = next(node for node in draft.nodes if node.node_id == "n22")
    assert icd10.evidence_ids == ("c4", "c5")
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dangling_refs"] == 1
    assert spans[0].attributes["tap.graph.dropped_no_evidence_nodes"] == 0


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

    # This batch also names a node "王先生" ("n11"): a brochure's illustrative
    # example person, rejected as a graph name since naming.is_reference_name
    # covers it (shared with alignment), which drops its one non-duplicate edge
    # (e10, n11->n12) along with it. Two real edges (e12, e19, not involving n1)
    # cite a chunk that only paraphrases their target (一次性交纳保费 for 趸缴保险
    # 费; a dividend clause with no literal 现金红利 but most of its characters) --
    # the character-overlap-ratio design (rather than a literal substring
    # requirement) is exactly what keeps both.
    #
    # n1 itself (友邦附加增利宝趸交年金保险（分红型）, typed SYSTEM -- this is really
    # the product/contract, not a software system) is no longer exempt from
    # either check just by type: narrowing the exemption to actual organization
    # references (rv3 follow-up fix) means n1's own long name, referred to in
    # most of its chunks only as "本附加合同", clears the 0.6 character-overlap
    # bar for some of its edges (e.g. e23, n1->n15 DEFINES, checked below) but
    # not others whose chunks use it even more sparingly -- 7 of n1's edges
    # (e3-e6, e9, e24, e25's shapes) now drop below 0.6 and are gone. A further
    # edge (e17, 被保险人->受益人) is dropped too: node dedup merges "被保险人"
    # into the survivor "被保险人（王先生之子）" (the brochure example's alias),
    # and that merged label no longer ends with the 保险人 organization suffix,
    # so it is checked for real and its own chunk does not name it well enough.
    # This is the accepted trade-off of narrowing the exemption rather than a
    # regression: 27 - 7 - 1 = 19 unique edges survive.
    kept = [(e.source_node_id, e.relation_type, e.target_node_id) for e in draft.edges]
    assert len(kept) == len(set(kept)) == len(set(shapes)) - 1 - 8
    assert not any(node.node_id == "n11" for node in draft.nodes)
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
    # edge-1's evidence must actually name both real (post-reference-resolution)
    # endpoints for it to clear the unsupported-evidence check below.
    request = _request()
    request = replace(
        request,
        chunks=(request.chunks[0] | {"content": "友邦尊享智选康惠团体医疗保险由友邦保险承保。"},),
    )

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(request)

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


@pytest.mark.asyncio
async def test_an_edge_to_a_known_id_not_sent_in_this_request_is_dropped():
    # "b0-n9" exists in an earlier batch but fell outside this request's 50-entity
    # cap, so the model cannot have been told about it: the edge is not grounded.
    output = _output()
    output["edges"].append(
        output["edges"][0] | {"id": "edge-2", "sourceNodeId": "b0-n9", "relationType": "USES"}
    )

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_known_policy_request())

    assert [edge.edge_id for edge in draft.edges] == ["edge-1"]
    assert "b0-n9" not in {node.node_id for node in draft.nodes}


def _adjacent_entries_request():
    return replace(
        _request(),
        chunks=(
            {
                "sourceRevisionId": "source-revision-1",
                "documentRevisionId": "document-revision-1",
                "chunkId": "chunk-1",
                "content": "6）重度黄疸\n指血清总胆红素超过标准值并持续恶化的严重肝损伤。",
                "anchor": {"kind": "text", "start": 0, "end": 10},
                "contentDigest": "sha256:" + "a" * 64,
            },
            {
                "sourceRevisionId": "source-revision-1",
                "documentRevisionId": "document-revision-1",
                "chunkId": "chunk-2",
                "content": "7）多个肢体缺失\n指因疾病或意外导致两个以上肢体完全性断离。",
                "anchor": {"kind": "text", "start": 10, "end": 20},
                "contentDigest": "sha256:" + "b" * 64,
            },
        ),
    )


@pytest.mark.asyncio
async def test_adjacent_entry_mislink_edge_is_dropped_as_unsupported(span_recorder):
    # The real rv3 failure mode: two neighboring clause entries (here, two unrelated
    # disease definitions) get connected to each other, but the edge's own cited
    # chunk (c1, 重度黄疸's clause) never mentions the other endpoint (多个肢体缺失) --
    # it is grounded in the wrong place, the same shape as report-3's "硬化性胆管炎
    # RELATED_TO 毒品" and "严重自身免疫性肝炎 PART_OF 严重慢性复发性胰腺炎" edges.
    output = {
        "nodes": [
            {
                "id": "node-1",
                "label": "重度黄疸",
                "type": "CONCEPT",
                "canonicalKey": "重度黄疸",
                "chunks": ["c1"],
                "aliases": [],
            },
            {
                "id": "node-2",
                "label": "多个肢体缺失",
                "type": "CONCEPT",
                "canonicalKey": "多个肢体缺失",
                "chunks": ["c2"],
                "aliases": [],
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "sourceNodeId": "node-1",
                "targetNodeId": "node-2",
                "relationType": "RELATED_TO",
                "relationLabel": "相关",
                "origin": "EXTRACTED",
                "confidence": 1.0,
                "chunks": ["c1"],
            }
        ],
        "provenance": [],
    }

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_adjacent_entries_request())

    assert draft.edges == ()
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dropped_ungrounded_edges"] == 1
    assert spans[0].attributes["tap.graph.dropped_cross_item_edges"] == 0


@pytest.mark.asyncio
async def test_unsupported_edge_check_does_not_drop_a_correctly_grounded_edge(span_recorder):
    # The same two entries, correctly related to themselves: an edge cannot be a
    # self-edge, so this checks a same-clause relation (the disease to its own
    # alias) is not mass false-dropped by the new check.
    output = {
        "nodes": [
            {
                "id": "node-1",
                "label": "重度黄疸",
                "type": "CONCEPT",
                "canonicalKey": "重度黄疸",
                "chunks": ["c1"],
                "aliases": [],
            },
            {
                "id": "node-2",
                "label": "血清总胆红素",
                "type": "CONCEPT",
                "canonicalKey": "血清总胆红素",
                "chunks": ["c1"],
                "aliases": [],
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "sourceNodeId": "node-1",
                "targetNodeId": "node-2",
                "relationType": "REQUIRES",
                "relationLabel": "指血清总胆红素超过标准值",
                "origin": "EXTRACTED",
                "confidence": 1.0,
                "chunks": ["c1"],
            }
        ],
        "provenance": [],
    }

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_adjacent_entries_request())

    assert [edge.edge_id for edge in draft.edges] == ["edge-1"]
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dropped_ungrounded_edges"] == 0
    assert spans[0].attributes["tap.graph.dropped_cross_item_edges"] == 0


@pytest.mark.asyncio
async def test_organization_reference_endpoints_are_exempt_from_the_unsupported_edge_check(
    span_recorder,
):
    # The insurer is routinely referred to in its own clause only by a pronoun
    # (本公司), never by its real name, because ``usable_alias`` deliberately
    # keeps such references out of every node's aliases. Requiring the literal
    # name here would drop a large share of genuinely grounded insurer facts,
    # not just adjacent-entry mislinks, so an organization-name label (ends
    # with 公司 here) is exempt -- but only that narrow case, not every ACTOR or
    # SYSTEM node (see the next test).
    output = {
        "nodes": [
            {
                "id": "node-1",
                "label": "保险公司",
                "type": "ACTOR",
                "canonicalKey": "保险公司",
                "chunks": ["c1"],
                "aliases": [],
            },
            {
                "id": "node-2",
                "label": "身故保险金",
                "type": "CONCEPT",
                "canonicalKey": "身故保险金",
                "chunks": ["c1"],
                "aliases": [],
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "sourceNodeId": "node-1",
                "targetNodeId": "node-2",
                "relationType": "RESPONSIBLE_FOR",
                "relationLabel": "本公司将给付身故保险金",
                "origin": "EXTRACTED",
                "confidence": 1.0,
                "chunks": ["c1"],
            }
        ],
        "provenance": [],
    }
    request = replace(
        _request(),
        chunks=(
            {
                "sourceRevisionId": "source-revision-1",
                "documentRevisionId": "document-revision-1",
                "chunkId": "chunk-1",
                "content": "若被保险人身故，本公司将给付身故保险金予受益人。",
                "anchor": {"kind": "text", "start": 0, "end": 10},
                "contentDigest": "sha256:" + "a" * 64,
            },
        ),
    )

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(request)

    assert [edge.edge_id for edge in draft.edges] == ["edge-1"]
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dropped_ungrounded_edges"] == 0
    assert spans[0].attributes["tap.graph.dropped_cross_item_edges"] == 0


@pytest.mark.asyncio
async def test_system_node_that_is_really_a_disease_is_not_exempt(span_recorder):
    # rv3 follow-up: a SYSTEM-typed diagnostic procedure (骨髓穿刺检查) wrongly
    # linked to an unrelated disease is a real production mislink that the old,
    # blanket ACTOR/SYSTEM exemption let through untouched. Its label is not a
    # reference pronoun, not a known entity, and does not end with an
    # organization suffix, so it must be checked like any other endpoint and
    # dropped here (its own chunk never mentions it at all).
    output = {
        "nodes": [
            {
                "id": "node-1",
                "label": "严重克罗恩病",
                "type": "CONCEPT",
                "canonicalKey": "严重克罗恩病",
                "chunks": ["c1"],
                "aliases": [],
            },
            {
                "id": "node-2",
                "label": "骨髓穿刺检查",
                "type": "SYSTEM",
                "canonicalKey": "骨髓穿刺检查",
                "chunks": ["c1"],
                "aliases": [],
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "sourceNodeId": "node-1",
                "targetNodeId": "node-2",
                "relationType": "APPLIES_TO",
                "relationLabel": "须根据组织病理学特点诊断",
                "origin": "EXTRACTED",
                "confidence": 1.0,
                "chunks": ["c1"],
            }
        ],
        "provenance": [],
    }
    request = replace(
        _request(),
        chunks=(
            {
                "sourceRevisionId": "source-revision-1",
                "documentRevisionId": "document-revision-1",
                "chunkId": "chunk-1",
                "content": "27）严重克罗恩病\n指一种慢性肉芽肿性肠炎，须根据组织病理学特点诊断。",
                "anchor": {"kind": "text", "start": 0, "end": 10},
                "contentDigest": "sha256:" + "a" * 64,
            },
        ),
    )

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(request)

    assert draft.edges == ()
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dropped_ungrounded_edges"] == 1


def _numbered_list_request():
    return replace(
        _request(),
        chunks=(
            {
                "sourceRevisionId": "source-revision-1",
                "documentRevisionId": "document-revision-1",
                "chunkId": "chunk-1",
                "content": (
                    "37）严重急性坏死性胰腺炎\n"
                    "指由专科医生确诊为急性坏死性胰腺炎，并进行坏死组织清除、病灶切除或胰腺部分切除的手术治疗。\n"
                    "38）严重自身免疫性肝炎\n"
                    "自身免疫性肝炎是一种原因不明的慢性肝脏的坏死性炎性疾病，须满足高γ球蛋白血症等条件。"
                ),
                "anchor": {"kind": "text", "start": 0, "end": 10},
                "contentDigest": "sha256:" + "a" * 64,
            },
        ),
    )


@pytest.mark.asyncio
async def test_cross_item_edge_in_a_dense_numbered_list_is_dropped(span_recorder):
    # Both "严重急性坏死性胰腺炎" and "严重自身免疫性肝炎" clear the whole-chunk
    # character-overlap check on their own (they share common disease-naming
    # morphemes -- 严重/性/炎 -- with each other's item), so only a check scoped to
    # a single numbered item catches that this edge mislinks across item 37 and 38.
    output = {
        "nodes": [
            {
                "id": "node-1",
                "label": "严重急性坏死性胰腺炎",
                "type": "CONCEPT",
                "canonicalKey": "严重急性坏死性胰腺炎",
                "chunks": ["c1"],
                "aliases": [],
            },
            {
                "id": "node-2",
                "label": "严重自身免疫性肝炎",
                "type": "CONCEPT",
                "canonicalKey": "严重自身免疫性肝炎",
                "chunks": ["c1"],
                "aliases": [],
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "sourceNodeId": "node-1",
                "targetNodeId": "node-2",
                "relationType": "RELATED_TO",
                "relationLabel": "相关",
                "origin": "EXTRACTED",
                "confidence": 1.0,
                "chunks": ["c1"],
            }
        ],
        "provenance": [],
    }

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_numbered_list_request())

    assert draft.edges == ()
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dropped_ungrounded_edges"] == 0
    assert spans[0].attributes["tap.graph.dropped_cross_item_edges"] == 1


@pytest.mark.asyncio
async def test_same_item_edge_in_a_dense_numbered_list_is_kept(span_recorder):
    # A disease and its own criterion, both named within item 38's own text, must
    # not be mass-dropped by the new per-item guard just because the chunk also
    # contains another numbered item (37).
    output = {
        "nodes": [
            {
                "id": "node-1",
                "label": "严重自身免疫性肝炎",
                "type": "CONCEPT",
                "canonicalKey": "严重自身免疫性肝炎",
                "chunks": ["c1"],
                "aliases": [],
            },
            {
                "id": "node-2",
                "label": "高γ球蛋白血症",
                "type": "CONCEPT",
                "canonicalKey": "高γ球蛋白血症",
                "chunks": ["c1"],
                "aliases": [],
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "sourceNodeId": "node-1",
                "targetNodeId": "node-2",
                "relationType": "REQUIRES",
                "relationLabel": "须满足",
                "origin": "EXTRACTED",
                "confidence": 1.0,
                "chunks": ["c1"],
            }
        ],
        "provenance": [],
    }

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_numbered_list_request())

    assert [edge.edge_id for edge in draft.edges] == ["edge-1"]
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dropped_ungrounded_edges"] == 0
    assert spans[0].attributes["tap.graph.dropped_cross_item_edges"] == 0


def _requirement_criterion_request(content: str):
    return replace(
        _request(),
        chunks=(
            {
                "sourceRevisionId": "source-revision-1",
                "documentRevisionId": "document-revision-1",
                "chunkId": "chunk-1",
                "content": content,
                "anchor": {"kind": "text", "start": 0, "end": 10},
                "contentDigest": "sha256:" + "a" * 64,
            },
        ),
    )


def _requirement_criterion_output(disease_label: str, criterion_label: str, relation_type: str):
    return {
        "nodes": [
            {
                "id": "node-1",
                "label": disease_label,
                "type": "CONCEPT",
                "canonicalKey": disease_label,
                "chunks": ["c1"],
                "aliases": [],
            },
            {
                "id": "node-2",
                "label": criterion_label,
                "type": "REQUIREMENT",
                "canonicalKey": criterion_label,
                "chunks": ["c1"],
                "aliases": [],
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "sourceNodeId": "node-1",
                "targetNodeId": "node-2",
                "relationType": relation_type,
                "relationLabel": "须满足",
                "origin": "EXTRACTED",
                "confidence": 1.0,
                "chunks": ["c1"],
            }
        ],
        "provenance": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "disease_label", "criterion_label"),
    [
        (
            # Real corpus text: 52) / 53) / 54) in one chunk. The reviewer's false
            # kill -- item 54's own REQUIREMENT criterion scored only 0.36 LCS
            # ratio against its own item because the model reordered "达到正常值
            # 上限的120%" into "≥120%正常值上限", below the 0.5 title-match floor
            # the (now-replaced) whole-body check used.
            (
                "52）严重原发性心肌病\n指因原发性心肌病导致慢性心功能损害造成永久不可逆性的心功能衰竭。\n"
                "53）严重冠心病\n指经冠状动脉造影检查，明确诊断为三支主要血管严重狭窄性病变。\n"
                "54）严重心肌炎\n指心肌的严重感染而导致至少持续180天的心功能损害。心功能损害必须满足下列全部"
                "条件：\n（1）左室腔扩大至少达到正常值上限的120%；\n（2）左室射血分数持续性低于40%。"
            ),
            "严重心肌炎",
            "左室腔扩大≥120%正常值上限",
        ),
        (
            (
                "55）肺源性心脏病\n指被保险人因慢性肺部疾病导致慢性心功能损害。\n"
                "56）感染性心内膜炎\n指由感染性微生物引致的心脏内膜炎症，须符合下列所有标准：\n"
                "（1）血液培养结果呈阳性反应，证明感染性微生物的存在；\n"
                "（2）心内膜炎引起中度心瓣膜关闭不全（指返流指数20%或以上）或中度心瓣膜狭窄（指心瓣膜开口"
                "范围小于或等于正常值的30%）。"
            ),
            "感染性心内膜炎",
            "中度心瓣膜狭窄（开口≤30%正常值）",
        ),
        (
            (
                "57）Ⅲ度房室传导阻滞\n指心房激动在房室交界区、房室束及其分支内发生阻滞，不能正常地传到心室"
                "的心脏传导性疾病，须满足下列所有条件：\n（1）心电图显示房室搏动彼此独立，心室率<50次/分钟；\n"
                "（2）出现阿-斯综合征或心力衰竭的表现；\n（3）必须持续性依赖心脏起搏器维持心脏正常功能。\n"
                "58）严重慢性缩窄性心包炎\n由于慢性心包炎症导致心包脏层和壁层广泛瘢痕粘连、增厚和钙化。"
            ),
            "Ⅲ度房室传导阻滞",
            "心电图显示房室搏动独立且心室率<50次/分",
        ),
    ],
)
async def test_requirement_criteria_in_a_dense_list_are_never_cross_item_mislinks(
    span_recorder, content, disease_label, criterion_label
):
    # These three real excerpts are the reviewer's false-kill regressions: a
    # REQUIREMENT-typed criterion of a disease's own definition, reworded by the
    # model (operators swapped, phrase order flipped), must never be dropped as a
    # cross-item mislink just because its own item's text scores low against it.
    output = _requirement_criterion_output(disease_label, criterion_label, "REQUIRES")

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(
        _requirement_criterion_request(content)
    )

    assert [edge.edge_id for edge in draft.edges] == ["edge-1"]
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dropped_ungrounded_edges"] == 0
    assert spans[0].attributes["tap.graph.dropped_cross_item_edges"] == 0


@pytest.mark.asyncio
async def test_aortic_surgery_trigger_is_not_a_cross_item_mislink(span_recorder):
    # Real corpus text: item 25 (主动脉手术)'s own body fell on the far side of a
    # chunk boundary, so this edge's single cited chunk holds only item 25's bare
    # title plus two unrelated, earlier items -- the target never names any
    # title in this chunk, so the cross-item check must not fire (it would have
    # nothing to compare), and the target's whole-chunk character overlap (0.64)
    # clears the primary check too.
    content = (
        "23）语言能力丧失\n指因疾病或意外伤害导致语言能力完全丧失，经过积极治疗至少12个月。\n"
        "24）重型再生障碍性贫血\n指因骨髓造血功能慢性持续性衰竭导致的贫血、中性粒细胞减少及血小板减少。\n"
        "25）主动脉手术"
    )
    output = {
        "nodes": [
            {
                "id": "node-1",
                "label": "主动脉手术",
                "type": "CONCEPT",
                "canonicalKey": "主动脉手术",
                "chunks": ["c1"],
                "aliases": [],
            },
            {
                "id": "node-2",
                "label": "主动脉疾病或主动脉创伤",
                "type": "CONCEPT",
                "canonicalKey": "主动脉疾病或主动脉创伤",
                "chunks": ["c1"],
                "aliases": [],
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "sourceNodeId": "node-1",
                "targetNodeId": "node-2",
                "relationType": "TRIGGERS",
                "relationLabel": "为治疗",
                "origin": "EXTRACTED",
                "confidence": 1.0,
                "chunks": ["c1"],
            }
        ],
        "provenance": [],
    }

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(
        _requirement_criterion_request(content)
    )

    assert [edge.edge_id for edge in draft.edges] == ["edge-1"]
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dropped_ungrounded_edges"] == 0
    assert spans[0].attributes["tap.graph.dropped_cross_item_edges"] == 0


@pytest.mark.asyncio
async def test_real_crohns_disease_to_bone_marrow_biopsy_is_dropped_as_ungrounded(span_recorder):
    # Real corpus text and a real rv3 production mislink: the model cited only
    # item 27/28's own chunk for this edge, which never mentions 骨髓穿刺检查 (a
    # criterion that belongs to a different item, 24, reached only via a
    # different chunk) -- caught by the primary ungrounded check, not the
    # cross-item one (there is only one numbered item's worth of disease name
    # in this specific chunk's visible text for 骨髓穿刺检查 to possibly match).
    content = (
        "27）严重克罗恩病\n指一种慢性肉芽肿性肠炎，具有特征性的克罗恩病（Crohn病）病理组织学变化，"
        "须根据组织病理学特点诊断，且已经造成瘘管形成并伴有肠梗阻或肠穿孔。\n"
        "28）严重溃疡性结肠炎\n指伴有致命性电解质紊乱的急性暴发性溃疡性结肠炎，病变已经累及全结肠，"
        "表现为严重的血便和系统性症状体征，须根据组织病理学特点诊断，且已经实施了结肠切除或回肠造瘘术。\n"
        "上述二十八种重度疾病依照中国保险行业协会制定的《重大疾病保险的疾病定义使用规范（2020年修订版）》"
        "标准定义，其他重度疾病依照本附加合同约定定义。"
    )
    output = {
        "nodes": [
            {
                "id": "node-1",
                "label": "严重克罗恩病",
                "type": "CONCEPT",
                "canonicalKey": "严重克罗恩病",
                "chunks": ["c1"],
                "aliases": [],
            },
            {
                "id": "node-2",
                "label": "骨髓穿刺检查",
                "type": "CONCEPT",
                "canonicalKey": "骨髓穿刺检查",
                "chunks": ["c1"],
                "aliases": [],
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "sourceNodeId": "node-1",
                "targetNodeId": "node-2",
                "relationType": "APPLIES_TO",
                "relationLabel": "须根据组织病理学特点诊断",
                "origin": "EXTRACTED",
                "confidence": 1.0,
                "chunks": ["c1"],
            }
        ],
        "provenance": [],
    }

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(
        _requirement_criterion_request(content)
    )

    assert draft.edges == ()
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dropped_ungrounded_edges"] == 1
    assert spans[0].attributes["tap.graph.dropped_cross_item_edges"] == 0


@pytest.mark.asyncio
async def test_inferred_edge_is_never_checked_against_evidence_text(span_recorder):
    # An INFERRED edge is grounded by its provenance's input facts, not by its own
    # (deliberately empty) chunks array, so the unsupported-evidence check never
    # applies to it even when its endpoints are unrelated to the batch's text.
    output = {
        "nodes": [
            {
                "id": "node-1",
                "label": "重度黄疸",
                "type": "CONCEPT",
                "canonicalKey": "重度黄疸",
                "chunks": ["c1"],
                "aliases": [],
            },
            {
                "id": "node-2",
                "label": "多个肢体缺失",
                "type": "CONCEPT",
                "canonicalKey": "多个肢体缺失",
                "chunks": ["c2"],
                "aliases": [],
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "sourceNodeId": "node-1",
                "targetNodeId": "node-2",
                "relationType": "RELATED_TO",
                "relationLabel": "inferred",
                "origin": "INFERRED",
                "confidence": 1.0,
                "chunks": [],
            }
        ],
        "provenance": [
            {
                "id": "p1",
                "edgeId": "edge-1",
                "inputFactIds": ["node-1", "node-2"],
                "ruleDigest": "sha256:" + "c" * 64,
            }
        ],
    }

    draft = await ModelGatewayGraphExtraction(Gateway(output)).extract(_adjacent_entries_request())

    assert [edge.edge_id for edge in draft.edges] == ["edge-1"]
    spans = [
        item for item in span_recorder.get_finished_spans() if item.name == "graph.extract_batch"
    ]
    assert spans[0].attributes["tap.graph.dropped_ungrounded_edges"] == 0
    assert spans[0].attributes["tap.graph.dropped_cross_item_edges"] == 0
