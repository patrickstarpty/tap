"""Validate visual hypotheses before creating reviewable flowchart knowledge."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from tap.modules.knowledge.domain.documents import (
    BlockKind,
    DocumentSource,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    canonical_sha256,
)
from tap.modules.knowledge.domain.parse_inventory import (
    ParseInventoryItem,
    ParseInventoryKind,
    ParseInventoryStatus,
    parse_inventory_digest,
)


class FlowchartRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("invalid-flowchart-analysis")


def _text(value: object, *, maximum: int, blank: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not blank and not value.strip()):
        raise FlowchartRejected()
    return value.strip()


def _box(value: object, width: int, height: int) -> tuple[int, int, int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(type(item) is not int for item in value)
    ):
        raise FlowchartRejected()
    left, top, right, bottom = value
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise FlowchartRejected()
    return left, top, right, bottom


def normalize_flowchart(
    source: DocumentSource,
    parsed: NormalizedArtifact,
    image_size: tuple[int, int],
    output: Mapping[str, object],
) -> NormalizedArtifact:
    """Turn bounded node/arrow hypotheses into review items and searchable candidates."""
    if (
        source.media_type not in {MediaType.PNG, MediaType.JPEG}
        or parsed.source_hash != canonical_sha256(source.content)
        or parsed.revision_id != source.revision_id
        or parsed.document_id != source.document_id
        or len(parsed.parse_inventory) != 1
        or parsed.parse_inventory[0].kind is not ParseInventoryKind.IMAGE
        or len(image_size) != 2
        or any(type(value) is not int or value < 1 for value in image_size)
        or set(output) != {"nodes", "edges"}
    ):
        raise FlowchartRejected()
    width, height = image_size
    nodes = output["nodes"]
    edges = output["edges"]
    if (
        not isinstance(nodes, list)
        or not 1 <= len(nodes) <= 100
        or not isinstance(edges, list)
        or len(edges) > 200
    ):
        raise FlowchartRejected()
    seen: dict[str, tuple[str, tuple[int, int, int, int]]] = {}
    inventory: list[ParseInventoryItem] = list(parsed.parse_inventory)
    blocks: list[NormalizedBlock] = []
    cursor = 0

    def add(
        kind: ParseInventoryKind,
        locator: str,
        value: dict[str, object],
        text: str | None,
        reason: str = "visual-confirmation-required",
        bbox: tuple[int, int, int, int] | None = None,
    ) -> None:
        nonlocal cursor
        item = ParseInventoryItem.create(
            source_revision_id=str(source.revision_id or parsed.source_hash),
            kind=kind,
            locator=locator,
            status=ParseInventoryStatus.NEEDS_REVIEW,
            reason=reason,
            artifact_digest=canonical_sha256(
                json.dumps(
                    value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ),
            original_alignment_reason="image-region-not-text",
        )
        inventory.append(item)
        if text is not None:
            if blocks:
                cursor += 2
            blocks.append(
                NormalizedBlock(
                    block_id=f"b_{len(blocks):06d}",
                    kind=BlockKind.PARAGRAPH,
                    text=text,
                    heading_path=(source.filename,),
                    page=None,
                    paragraph_index=len(blocks),
                    start_offset=cursor,
                    end_offset=cursor + len(text),
                    inventory_item_id=item.item_id,
                    bbox=bbox,
                )
            )
            cursor += len(text)

    for node in nodes:
        if not isinstance(node, dict) or set(node) != {"id", "label", "box", "lane"}:
            raise FlowchartRejected()
        identifier = _text(node["id"], maximum=32)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier) or identifier in seen:
            raise FlowchartRejected()
        label = _text(node["label"], maximum=200)
        lane = _text(node["lane"], maximum=100, blank=True)
        box = _box(node["box"], width, height)
        seen[identifier] = (label, box)
        canonical: dict[str, object] = {"id": identifier, "label": label, "box": box, "lane": lane}
        phrase = f"流程图节点 {identifier}：{label}" + (f"；泳道：{lane}" if lane else "")
        add(ParseInventoryKind.FLOW_NODE, f"image:1/node:{identifier}", canonical, phrase, bbox=box)

    for index, edge in enumerate(edges, start=1):
        if not isinstance(edge, dict) or set(edge) != {"source", "target", "condition", "certain"}:
            raise FlowchartRejected()
        start = _text(edge["source"], maximum=32)
        end = _text(edge["target"], maximum=32)
        condition = _text(edge["condition"], maximum=200, blank=True)
        certain = edge["certain"]
        if start not in seen or end not in seen or type(certain) is not bool:
            raise FlowchartRejected()
        canonical = {"source": start, "target": end, "condition": condition, "certain": certain}
        edge_phrase: str | None = (
            f"流程图连线 {start} → {end}：{seen[start][0]} → {seen[end][0]}"
            + (f"；条件：{condition}" if condition else "")
            if certain
            else None
        )
        start_box, end_box = seen[start][1], seen[end][1]
        connection_box = (
            min(start_box[0], end_box[0]),
            min(start_box[1], end_box[1]),
            max(start_box[2], end_box[2]),
            max(start_box[3], end_box[3]),
        )
        add(
            ParseInventoryKind.FLOW_EDGE,
            f"image:1/edge:{index}",
            canonical,
            edge_phrase,
            reason="visual-confirmation-required" if certain else "uncertain-connection",
            bbox=connection_box if certain else None,
        )

    items = tuple(inventory)
    return NormalizedArtifact(
        filename=parsed.filename,
        media_type=parsed.media_type,
        source_hash=parsed.source_hash,
        blocks=tuple(blocks),
        document_id=parsed.document_id,
        revision_id=parsed.revision_id,
        parse_inventory=items,
        parser_config_digest=parsed.parser_config_digest,
        parse_inventory_digest=parse_inventory_digest(items),
        flowchart_data=json.dumps(
            output, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
    )
