from __future__ import annotations

import hashlib

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.adapters.fake_extraction import DeterministicGraphExtraction
from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.models import GraphSnapshot
from tap.modules.graph.domain.vocabulary import RELATION_TYPES


def _request(*contents: str) -> GraphExtractionRequest:
    return GraphExtractionRequest(
        scope=VALIDATION_SCOPE,
        snapshot=GraphSnapshot.create(
            snapshot_id="snapshot-1",
            project_id=VALIDATION_SCOPE.project_id,
            source_revision_ids=("revision-1",),
            document_revision_ids=("revision-1",),
        ),
        chunks=tuple(
            {
                "sourceRevisionId": "revision-1",
                "documentRevisionId": "revision-1",
                "chunkId": f"chunk-{index}",
                "content": content,
                "anchor": {
                    "type": "document",
                    "startOffset": index * 1000,
                    "endOffset": index * 1000 + len(content),
                    "headingPath": [],
                },
                "contentDigest": "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
            }
            for index, content in enumerate(contents)
        ),
        model_alias="qwen-plus",
        idempotency_key="graph:revision-1",
    )


@pytest.mark.asyncio
async def test_rule_based_extracts_chinese_relations():
    draft = await DeterministicGraphExtraction().extract(
        _request("核保流程需要健康告知。健康告知由核保员负责。")
    )
    assert len(draft.nodes) == 3
    assert len(draft.edges) == 2
    by_key = {node.canonical_key: node for node in draft.nodes}
    assert by_key["核保流程"].node_type == "PROCESS" and by_key["核保员"].node_type == "ACTOR"
    relations = {
        (edge.source_node_id, edge.relation_type, edge.target_node_id, edge.relation_label)
        for edge in draft.edges
    }
    assert relations == {
        (by_key["核保流程"].node_id, "REQUIRES", by_key["健康告知"].node_id, "需要"),
        (by_key["核保员"].node_id, "RESPONSIBLE_FOR", by_key["健康告知"].node_id, "由…负责"),
    }


@pytest.mark.asyncio
async def test_rule_based_extracts_english_relations_with_stable_ids():
    first = await DeterministicGraphExtraction().extract(
        _request("Refund requests require finance review.")
    )
    second = await DeterministicGraphExtraction().extract(
        _request("Finance review precedes payout.")
    )
    assert len(first.nodes) == 2
    assert len(first.edges) == 1
    finance = [node for node in first.nodes if node.canonical_key == "financereview"][0]
    assert finance.node_id in {node.node_id for node in second.nodes}
    assert first.edges[0].relation_type == "REQUIRES"
    assert first.edges[0].relation_label == "require"
    assert first.edges[0].evidence_ids == (first.evidence[0].evidence_id,)


@pytest.mark.asyncio
async def test_rule_based_does_not_match_a_trigger_embedded_in_a_longer_word():
    """ "requires" must not also fire the shorter "require" pattern on itself."""
    draft = await DeterministicGraphExtraction().extract(
        _request("The claim process requires medical evidence.")
    )
    assert len(draft.nodes) == 2
    assert len(draft.edges) == 1
    by_key = {node.canonical_key: node for node in draft.nodes}
    assert draft.edges[0].relation_label == "requires"
    assert draft.edges[0].source_node_id == by_key["theclaimprocess"].node_id
    assert draft.edges[0].target_node_id == by_key["medicalevidence"].node_id


@pytest.mark.asyncio
async def test_rule_based_does_not_match_require_inside_requirement():
    """ "requirement" must not spuriously fire the "require" REQUIRES pattern."""
    draft = await DeterministicGraphExtraction().extract(
        _request("Refund requirement applies to all claims.")
    )
    assert len(draft.nodes) == 2
    assert len(draft.edges) == 1
    assert draft.edges[0].relation_type == "APPLIES_TO"
    assert draft.edges[0].relation_label == "applies to"
    by_key = {node.canonical_key: node for node in draft.nodes}
    assert draft.edges[0].source_node_id == by_key["refundrequirement"].node_id
    assert draft.edges[0].target_node_id == by_key["allclaims"].node_id


@pytest.mark.asyncio
async def test_rule_based_does_not_match_uses_inside_causes():
    """ "causes" must not spuriously fire the "uses" USES pattern."""
    draft = await DeterministicGraphExtraction().extract(
        _request("This rule causes delays in payout.")
    )
    assert all(edge.relation_type != "USES" for edge in draft.edges)
    assert draft.edges == ()
    assert draft.nodes[0].canonical_key.startswith("document:")
    assert set(draft.nodes[0].evidence_ids) == {item.evidence_id for item in draft.evidence}


@pytest.mark.asyncio
async def test_rule_based_prefers_the_longer_chinese_trigger_on_overlap():
    """须提供 is a substring of 必须提供; the longer trigger must win, not both."""
    draft = await DeterministicGraphExtraction().extract(_request("客户必须提供身份证明。"))
    assert len(draft.nodes) == 2
    assert len(draft.edges) == 1
    assert draft.edges[0].relation_label == "必须提供"
    by_key = {node.canonical_key: node for node in draft.nodes}
    assert draft.edges[0].source_node_id == by_key["客户"].node_id
    assert draft.edges[0].target_node_id == by_key["身份证明"].node_id


@pytest.mark.asyncio
async def test_rule_based_extracts_the_e2e_fixture_requires_edge():
    draft = await DeterministicGraphExtraction().extract(
        _request("Tapper refund requests above five thousand units require finance review.")
    )
    assert len(draft.edges) == 1
    assert draft.edges[0].relation_type == "REQUIRES"
    assert draft.edges[0].relation_label == "require"


@pytest.mark.asyncio
async def test_rule_based_falls_back_to_a_document_node():
    draft = await DeterministicGraphExtraction().extract(_request("今天天气很好。"))
    assert len(draft.nodes) == 1
    assert draft.nodes[0].canonical_key.startswith("document:")
    assert draft.edges == ()
    assert set(draft.nodes[0].evidence_ids) == {item.evidence_id for item in draft.evidence}


@pytest.mark.asyncio
async def test_rule_based_only_emits_vocabulary_relations_and_bounded_nodes():
    sentences = [f"实体{i}需要实体{i + 1}。" for i in range(400)]
    draft = await DeterministicGraphExtraction().extract(_request(*sentences))
    assert {edge.relation_type for edge in draft.edges} <= RELATION_TYPES
    assert len(draft.nodes) <= 300
    node_ids = {node.node_id for node in draft.nodes}
    assert all(
        edge.source_node_id in node_ids and edge.target_node_id in node_ids for edge in draft.edges
    )
