"""Flowchart meaning must retain direction, conditions, and uncertain connections."""

from __future__ import annotations

import io
import json
import os

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.artifact_codecs import (
    decode_normalized_artifact,
    encode_normalized_artifact,
)
from tap.modules.knowledge.adapters.document_chunker import StructuralChunker
from tap.modules.knowledge.adapters.document_parsers import ParserRegistry
from tap.modules.knowledge.adapters.flowchart_vision import ModelGatewayFlowchartVision
from tap.modules.knowledge.domain.documents import (
    PARSER_VERSION,
    DocumentId,
    DocumentSource,
    MediaType,
    RevisionId,
    canonical_sha256,
    revision_id_for,
)
from tap.modules.knowledge.domain.flowcharts import FlowchartRejected, normalize_flowchart
from tap.modules.knowledge.domain.parse_inventory import ParseInventoryKind, ParseInventoryStatus


def source_and_image():
    output = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(output, format="PNG")
    source = DocumentSource(
        "approval.png", MediaType.PNG, output.getvalue(), DocumentId("doc_1"), RevisionId("rev_1")
    )
    return source, ParserRegistry().parse(source)


def test_flowchart_meaning_preserves_branch_direction_and_review_items():
    source, parsed = source_and_image()
    normalized = normalize_flowchart(
        source,
        parsed,
        (200, 100),
        {
            "nodes": [
                {"id": "start", "label": "提交申请", "box": [10, 10, 50, 30], "lane": "申请人"},
                {"id": "approve", "label": "经理审批", "box": [90, 10, 150, 30], "lane": "经理"},
            ],
            "edges": [
                {
                    "source": "start",
                    "target": "approve",
                    "condition": "金额大于1000",
                    "certain": True,
                },
            ],
        },
    )

    assert [item.kind for item in normalized.parse_inventory] == [
        ParseInventoryKind.IMAGE,
        ParseInventoryKind.FLOW_NODE,
        ParseInventoryKind.FLOW_NODE,
        ParseInventoryKind.FLOW_EDGE,
    ]
    assert all(
        item.status is ParseInventoryStatus.NEEDS_REVIEW for item in normalized.parse_inventory
    )
    assert "提交申请 → 经理审批" in normalized.blocks[-1].text
    assert "金额大于1000" in normalized.blocks[-1].text
    assert normalized.blocks[-1].inventory_item_id == normalized.parse_inventory[-1].item_id
    node_anchor = StructuralChunker().chunk(normalized)[0].anchor_json
    assert '"bbox":[10,10,50,30]' in node_anchor
    assert normalized.blocks[-1].bbox == (10, 10, 150, 30)


def test_same_label_nodes_keep_distinct_edge_direction_after_roundtrip():
    source, parsed = source_and_image()
    output = {
        "nodes": [
            {"id": "a", "label": "审批", "box": [1, 1, 20, 20], "lane": "申请人"},
            {"id": "b", "label": "审批", "box": [40, 1, 70, 20], "lane": "经理"},
        ],
        "edges": [{"source": "a", "target": "b", "condition": "通过", "certain": True}],
    }
    forward = normalize_flowchart(source, parsed, (200, 100), output)
    backward = normalize_flowchart(
        source,
        parsed,
        (200, 100),
        {**output, "edges": [{"source": "b", "target": "a", "condition": "通过", "certain": True}]},
    )
    assert forward.blocks[-1].text != backward.blocks[-1].text
    assert "a → b" in forward.blocks[-1].text
    assert "b → a" in backward.blocks[-1].text


def test_isolated_parser_vision_pixels_survive_transport_without_metadata():
    output = io.BytesIO()
    metadata = PngInfo()
    metadata.add_text("Comment", "secret-marker")
    Image.new("RGB", (20, 10), "white").save(output, format="PNG", pnginfo=metadata)
    document_id = DocumentId("doc_" + "1" * 32)
    content = output.getvalue()
    revision_id = revision_id_for(document_id, canonical_sha256(content), PARSER_VERSION)
    source = DocumentSource("flow.png", MediaType.PNG, content, document_id, revision_id)
    parsed = ParserRegistry().parse(source)
    restored = decode_normalized_artifact(
        encode_normalized_artifact(str(revision_id), parsed), expected_revision=str(revision_id)
    )
    assert restored.vision_input is not None
    assert restored.vision_input.original_size == (20, 10)
    assert b"secret-marker" not in restored.vision_input.content


def test_rotated_jpeg_is_rejected_before_vision_coordinates_are_used():
    output = io.BytesIO()
    image = Image.new("RGB", (20, 10), "white")
    exif = Image.Exif()
    exif[274] = 6
    image.save(output, format="JPEG", exif=exif)
    source = DocumentSource(
        "rotated.jpg", MediaType.JPEG, output.getvalue(), DocumentId("doc_1"), RevisionId("rev_1")
    )
    with pytest.raises(Exception) as error:
        ParserRegistry().parse(source)
    assert getattr(error.value, "code", None) == "document-too-complex"


def test_uncertain_arrow_does_not_become_searchable_fact():
    source, parsed = source_and_image()
    normalized = normalize_flowchart(
        source,
        parsed,
        (200, 100),
        {
            "nodes": [
                {"id": "a", "label": "开始", "box": [1, 1, 20, 20], "lane": ""},
                {"id": "b", "label": "结束", "box": [40, 1, 70, 20], "lane": ""},
            ],
            "edges": [{"source": "a", "target": "b", "condition": "", "certain": False}],
        },
    )

    assert not any("开始 → 结束" in block.text for block in normalized.blocks)
    assert normalized.parse_inventory[-1].reason == "uncertain-connection"


def test_flowchart_region_survives_immutable_artifact_roundtrip():
    sample, _ = source_and_image()
    document_id = DocumentId("doc_1")
    revision_id = revision_id_for(document_id, canonical_sha256(sample.content), PARSER_VERSION)
    source = DocumentSource(
        sample.filename, sample.media_type, sample.content, document_id, revision_id
    )
    normalized = normalize_flowchart(
        source,
        ParserRegistry().parse(source),
        (200, 100),
        {
            "nodes": [{"id": "a", "label": "开始", "box": [1, 2, 20, 30], "lane": ""}],
            "edges": [],
        },
    )

    restored = decode_normalized_artifact(
        encode_normalized_artifact(str(revision_id), normalized),
        expected_revision=str(revision_id),
    )

    assert restored.blocks[0].bbox == (1, 2, 20, 30)
    assert json.loads(restored.flowchart_data or "{}") == {
        "nodes": [{"id": "a", "label": "开始", "box": [1, 2, 20, 30], "lane": ""}],
        "edges": [],
    }
    assert '"bbox":[1,2,20,30]' in StructuralChunker().chunk(restored)[0].anchor_json


def test_flowchart_rejects_missing_endpoint_and_out_of_bounds_box():
    source, parsed = source_and_image()
    nodes = [{"id": "a", "label": "开始", "box": [1, 1, 20, 20], "lane": ""}]
    with pytest.raises(FlowchartRejected):
        normalize_flowchart(
            source,
            parsed,
            (200, 100),
            {
                "nodes": nodes,
                "edges": [{"source": "a", "target": "missing", "condition": "", "certain": True}],
            },
        )
    with pytest.raises(FlowchartRejected):
        normalize_flowchart(
            source,
            parsed,
            (200, 100),
            {
                "nodes": [{**nodes[0], "box": [1, 1, 201, 20]}],
                "edges": [],
            },
        )


@pytest.mark.asyncio
async def test_vision_adapter_requests_image_and_returns_reviewable_meaning():
    source, parsed = source_and_image()
    requests = []

    class Gateway:
        async def generate_structured(self, request):
            requests.append(request)
            return type(
                "Result",
                (),
                {
                    "actual_model": "qwen3-vl-plus",
                    "actual_provider": "dashscope",
                    "output": {
                        "coordinate_space": "normalized_1000",
                        "nodes": [
                            {"id": "a", "label": "开始", "box": [1, 1, 20, 20], "lane": ""},
                            {"id": "b", "label": "结束", "box": [40, 1, 70, 20], "lane": ""},
                        ],
                        "edges": [
                            {"source": "a", "target": "b", "condition": "通过", "certain": True}
                        ],
                    },
                },
            )()

    result = await ModelGatewayFlowchartVision(Gateway(), VALIDATION_SCOPE).analyze(source, parsed)

    assert "开始 → 结束" in result.blocks[-1].text
    assert requests[0].image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert requests[0].image_media_type == "image/png"
    assert requests[0].alias == "tapper-vision"
    context = json.loads(requests[0].context)
    assert context["image_width"] == 200
    assert context["image_height"] == 100
    assert context["coordinate_system"] == "normalized_1000"
    assert result.parser_config_digest != parsed.parser_config_digest


@pytest.mark.asyncio
async def test_vision_sends_pixels_without_embedded_image_metadata():
    output = io.BytesIO()
    metadata = PngInfo()
    metadata.add_text("Comment", "secret-marker")
    Image.new("RGB", (100, 50), "white").save(output, format="PNG", pnginfo=metadata)
    source = DocumentSource(
        "diagram.png", MediaType.PNG, output.getvalue(), DocumentId("doc_1"), RevisionId("rev_1")
    )
    requests = []

    class Gateway:
        async def generate_structured(self, request):
            requests.append(request)
            return type(
                "Result",
                (),
                {
                    "actual_model": "qwen3-vl-plus",
                    "actual_provider": "dashscope",
                    "output": {
                        "coordinate_space": "normalized_1000",
                        "nodes": [{"id": "a", "label": "开始", "box": [1, 1, 20, 20], "lane": ""}],
                        "edges": [],
                    },
                },
            )()

    await ModelGatewayFlowchartVision(Gateway(), VALIDATION_SCOPE).analyze(
        source, ParserRegistry().parse(source)
    )

    assert b"secret-marker" in source.content
    assert b"secret-marker" not in requests[0].image_bytes


@pytest.mark.asyncio
async def test_large_valid_image_is_bounded_for_vision_and_boxes_map_to_original():
    output = io.BytesIO()
    Image.frombytes("RGB", (2200, 2200), os.urandom(2200 * 2200 * 3)).save(output, format="PNG")
    source = DocumentSource(
        "large.png", MediaType.PNG, output.getvalue(), DocumentId("doc_1"), RevisionId("rev_1")
    )
    parsed = ParserRegistry().parse(source)
    requests = []

    class Gateway:
        async def generate_structured(self, request):
            requests.append(request)
            return type(
                "Result",
                (),
                {
                    "actual_model": "qwen3-vl-plus",
                    "actual_provider": "dashscope",
                    "output": {
                        "coordinate_space": "normalized_1000",
                        "nodes": [
                            {
                                "id": "start",
                                "label": "开始",
                                "box": [0, 0, 1000, 1000],
                                "lane": "",
                            }
                        ],
                        "edges": [],
                    },
                },
            )()

    result = await ModelGatewayFlowchartVision(Gateway(), VALIDATION_SCOPE).analyze(source, parsed)

    assert len(requests[0].image_bytes) <= 4 * 1024 * 1024
    assert result.parse_inventory[1].kind is ParseInventoryKind.FLOW_NODE
    with Image.open(io.BytesIO(requests[0].image_bytes)) as sent:
        sent_width, sent_height = sent.size
    context = json.loads(requests[0].context)
    assert (context["image_width"], context["image_height"]) == (sent_width, sent_height)
    assert (sent_width, sent_height) != (2200, 2200)
    assert result.blocks[0].bbox == (0, 0, 2200, 2200)


@pytest.mark.asyncio
async def test_declared_normalized_boxes_map_to_original_pixels_without_guessing():
    from types import SimpleNamespace

    output = io.BytesIO()
    Image.new("RGB", (1500, 900), "white").save(output, format="PNG")
    source = DocumentSource(
        "diagram.png", MediaType.PNG, output.getvalue(), DocumentId("doc_1"), RevisionId("rev_1")
    )

    class Gateway:
        async def generate_structured(self, request):
            assert "coordinate_space" in request.schema["required"]
            assert request.schema["properties"]["coordinate_space"]["enum"] == ["normalized_1000"]
            return SimpleNamespace(
                actual_model="qwen3-vl-plus",
                actual_provider="dashscope",
                output={
                    "coordinate_space": "normalized_1000",
                    "nodes": [
                        {
                            "id": "a",
                            "label": "Automatic approval",
                            "box": [682, 857, 822, 940],
                            "lane": "",
                        }
                    ],
                    "edges": [],
                },
            )

    result = await ModelGatewayFlowchartVision(Gateway(), VALIDATION_SCOPE).analyze(
        source, ParserRegistry().parse(source)
    )
    assert result.blocks[0].bbox == (1023, 771, 1233, 846)
    graph = json.loads(result.flowchart_data)
    assert set(graph) == {"nodes", "edges"}
    assert graph["nodes"][0]["box"] == [1023, 771, 1233, 846]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"coordinate_space": None},
        {"coordinate_space": "pixels"},
        {"coordinate_space": "normalized_1000", "extra": True},
        {"nodes": [{"id": "a", "label": "A", "box": [0, 0, 1001, 1000], "lane": ""}]},
    ],
)
async def test_vision_rejects_undeclared_or_invalid_coordinate_contract(change):
    from types import SimpleNamespace

    source, parsed = source_and_image()

    class Gateway:
        async def generate_structured(self, request):
            output = {
                "coordinate_space": "normalized_1000",
                "nodes": [{"id": "a", "label": "A", "box": [0, 0, 100, 100], "lane": ""}],
                "edges": [],
            } | change
            if output["coordinate_space"] is None:
                del output["coordinate_space"]
            return SimpleNamespace(
                actual_model="qwen3-vl-plus", actual_provider="dashscope", output=output
            )

    with pytest.raises(FlowchartRejected):
        await ModelGatewayFlowchartVision(Gateway(), VALIDATION_SCOPE).analyze(source, parsed)


def test_correction_provenance_survives_normalized_and_chunk_artifacts():
    from dataclasses import replace

    from tap.modules.knowledge.adapters.artifact_codecs import (
        decode_chunks_artifact,
        encode_chunks_artifact,
    )
    from tap.modules.knowledge.domain.review import canonical_digest

    sample, _ = source_and_image()
    previous = str(
        revision_id_for(sample.document_id, canonical_sha256(sample.content), PARSER_VERSION)
    )
    graph = {
        "nodes": [{"id": "a", "label": "更正审批", "box": [1, 1, 20, 20], "lane": ""}],
        "edges": [],
    }
    version = (
        "human-correction-" + canonical_digest({"sourceRevisionId": previous, "graph": graph})[7:]
    )
    revision = revision_id_for(sample.document_id, canonical_sha256(sample.content), version)
    source = replace(sample, revision_id=revision)
    corrected = replace(
        normalize_flowchart(source, ParserRegistry().parse(source), (200, 100), graph),
        parser_version=version,
        correction_source_revision_id=previous,
    )
    restored = decode_normalized_artifact(
        encode_normalized_artifact(str(revision), corrected), expected_revision=str(revision)
    )
    assert restored.parser_version == version
    assert restored.correction_source_revision_id == previous
    chunks = StructuralChunker().chunk(restored)
    assert all(chunk.parser_version == version for chunk in chunks)
    assert (
        decode_chunks_artifact(
            encode_chunks_artifact(str(revision), chunks), expected_revision=str(revision)
        )
        == chunks
    )
    # Neither removing the parent binding nor substituting another graph may keep this identity.
    with pytest.raises(ValueError):
        replace(corrected, correction_source_revision_id=None)
    with pytest.raises(ValueError):
        replace(corrected, correction_source_revision_id="rev_other")
    with pytest.raises(ValueError):
        replace(
            corrected,
            flowchart_data=json.dumps(
                {"nodes": [], "edges": []}, sort_keys=True, separators=(",", ":")
            ),
        )
