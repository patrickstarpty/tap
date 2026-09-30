"""Project-scoped visual flowchart extraction through the governed model gateway."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import cast

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.models import ModelOperation, ModelRequest, schema_digest, text_digest
from tap.modules.ai.ports.gateway import ModelGateway
from tap.modules.knowledge.domain.documents import (
    DocumentSource,
    NormalizedArtifact,
    canonical_sha256,
)
from tap.modules.knowledge.domain.flowcharts import FlowchartRejected, normalize_flowchart
from tap.platform.telemetry import span

FLOWCHART_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["coordinate_space", "nodes", "edges"],
    "properties": {
        "coordinate_space": {"type": "string", "enum": ["normalized_1000"]},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "label", "box", "lane"],
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "box": {
                        "type": "array",
                        "maxItems": 4,
                        "items": {"type": "integer", "minimum": 0, "maximum": 1000},
                    },
                    "lane": {"type": "string"},
                },
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "target", "condition", "certain"],
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "condition": {"type": "string"},
                    "certain": {"type": "boolean"},
                },
            },
        },
    },
}
FLOWCHART_PROMPT = (
    "Interpret the supplied flowchart image. Return only the requested JSON schema. "
    "Read all node labels, arrow direction, branch conditions and swimlanes. "
    "Declare coordinate_space=normalized_1000. Use integer boxes [left, top, right, bottom] "
    "on a 0..1000 grid: top-left is [0,0], bottom-right is [1000,1000]. "
    "Normalize x against the entire supplied image width and y against its entire height, "
    "independently; do not emit pixel coordinates. Include the complete node shape in each box. "
    "Every node needs a unique id, its label, box and lane; use an empty lane if absent. "
    "Every edge needs source and target node ids, condition (empty if absent), and certain. "
    "Only set certain=true when the arrow endpoints and direction are visually clear. "
    "For crossing, disconnected, or directionless lines use certain=false. "
    "Do not invent nodes, conditions or connections. Preserve the original language."
)


class ModelGatewayFlowchartVision:
    def __init__(
        self,
        gateway: ModelGateway,
        scope: ProjectScopeContext,
        *,
        alias: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._gateway = gateway
        self._scope = scope
        self._alias = alias
        self._timeout_seconds = timeout_seconds

    async def analyze(
        self, source: DocumentSource, parsed: NormalizedArtifact
    ) -> NormalizedArtifact:
        with span("flowchart.recognize"):
            if source.revision_id is None:
                raise FlowchartRejected()
            vision = parsed.vision_input
            if vision is None:
                raise FlowchartRejected()
            image_size = vision.original_size
            sent_size = vision.sent_size
            image_bytes = vision.content
            image_media_type = vision.media_type.value
            result = await self._gateway.generate_structured(
                ModelRequest(
                    scope=self._scope,
                    alias=self._alias,
                    operation=ModelOperation.STRUCTURED,
                    prompt=FLOWCHART_PROMPT,
                    prompt_digest=text_digest(FLOWCHART_PROMPT),
                    context=json.dumps(
                        {
                            "task": "Extract reviewable flowchart nodes and directed connections.",
                            "image_width": sent_size[0],
                            "image_height": sent_size[1],
                            "coordinate_system": "normalized_1000",
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    timeout_seconds=self._timeout_seconds,
                    idempotency_key=f"flowchart-{source.revision_id}",
                    schema=FLOWCHART_SCHEMA,
                    schema_digest=schema_digest(FLOWCHART_SCHEMA),
                    image_bytes=image_bytes,
                    image_media_type=image_media_type,
                )
            )
            if (
                not isinstance(result.output, dict)
                or set(result.output) != {"coordinate_space", "nodes", "edges"}
                or result.output["coordinate_space"] != "normalized_1000"
            ):
                raise FlowchartRejected()
            visual_output = {"nodes": result.output["nodes"], "edges": result.output["edges"]}
            # Validate the declared grid before conversion; never guess units from box values.
            normalize_flowchart(source, parsed, (1000, 1000), visual_output)
            nodes = []
            for item in cast(list[dict[str, object]], visual_output["nodes"]):
                left, top, right, bottom = cast(list[int], item["box"])
                # Map directly to original pixels to avoid rounding twice after image resizing.
                nodes.append(
                    {
                        **item,
                        "box": [
                            left * image_size[0] // 1000,
                            top * image_size[1] // 1000,
                            (right * image_size[0] + 999) // 1000,
                            (bottom * image_size[1] + 999) // 1000,
                        ],
                    }
                )
            visual_output = {**visual_output, "nodes": nodes}
            normalized = normalize_flowchart(source, parsed, image_size, visual_output)
            provenance = {
                "parserConfigDigest": parsed.parser_config_digest,
                "visionAlias": self._alias,
                "visionModel": result.actual_model,
                "visionProvider": result.actual_provider,
                "promptDigest": text_digest(FLOWCHART_PROMPT),
                "schemaDigest": schema_digest(FLOWCHART_SCHEMA),
                "visionInputDigest": canonical_sha256(image_bytes),
            }
            return replace(
                normalized,
                parser_config_digest=text_digest(
                    json.dumps(provenance, sort_keys=True, separators=(",", ":"))
                ),
            )
