"""Opt-in visual extraction check; this does not attest the review/publish UI journey."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from tap.entrypoints.tapper_runtime import TapperSettings, _create_embeddings
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.litellm import LiteLLMModelGateway
from tap.modules.knowledge.adapters.document_parsers import ParserRegistry
from tap.modules.knowledge.adapters.flowchart_vision import ModelGatewayFlowchartVision
from tap.modules.knowledge.domain.documents import (
    PARSER_VERSION,
    DocumentId,
    DocumentSource,
    MediaType,
    canonical_sha256,
    revision_id_for,
)


@pytest.mark.asyncio
async def test_real_vision_preserves_underwriting_nodes_and_return_paths() -> None:
    if os.environ.get("TAP_RUN_TAPPER_REAL_MODEL_SMOKE") != "1":
        pytest.skip("real flowchart model smoke requires explicit opt-in")

    previous_log_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    model = None
    failed = False
    try:
        # Uses LITELLM_BASE_URL, LITELLM_MASTER_KEY, LITELLM_MODEL and
        # LITELLM_TAPPER_VISION_MODEL through the same settings/factory as runtime.
        settings = TapperSettings.from_mapping(os.environ)
        assert not settings.e2e_mode and settings.vision_model
        assert settings.model_backend == "litellm"
        model = _create_embeddings(settings, max_retries=0)
        assert type(model.gateway) is LiteLLMModelGateway
        sample = (
            Path(__file__).resolve().parents[4]
            / "docs/assets/flowchart-image/2026-09-27-underwriting-approval-flow.png"
        )
        content = sample.read_bytes()
        document_id = DocumentId("doc_flowchart_real_smoke")
        source = DocumentSource(
            sample.name,
            MediaType.PNG,
            content,
            document_id,
            revision_id_for(document_id, canonical_sha256(content), PARSER_VERSION),
        )
        parsed = ParserRegistry().parse(source)
        result = await ModelGatewayFlowchartVision(
            model.gateway,
            VALIDATION_SCOPE,
            timeout_seconds=settings.vision_timeout_seconds,
        ).analyze(source, parsed)
        graph = json.loads(result.flowchart_data or "{}")
        assert len(graph["nodes"]) == 14
        assert len(graph["edges"]) == 16
        assert result.blocks
        labels = {
            node["id"]: " ".join(node["label"].lower().split()).rstrip("?")
            for node in graph["nodes"]
        }
        for node in graph["nodes"]:
            left, top, right, bottom = node["box"]
            assert all(type(coordinate) is int for coordinate in node["box"])
            assert 0 <= left < right <= 1500
            assert 0 <= top < bottom <= 900
        certain_edges = {
            (labels[edge["source"]], labels[edge["target"]])
            for edge in graph["edges"]
            if edge["certain"] is True
        }
        assert {
            ("submit application", "validate application"),
            ("validate application", "disclosure complete"),
            ("disclosure complete", "assess risk"),
            ("assess risk", "high risk"),
            ("high risk", "senior review"),
            ("high risk", "automatic approval"),
            ("senior review", "compliance approval"),
            ("compliance approval", "prepare quote"),
            ("automatic approval", "prepare quote"),
            ("prepare quote", "payment confirmed"),
            ("issue policy", "receive policy"),
            ("complete missing disclosure", "validate application"),
            ("disclosure complete", "complete missing disclosure"),
            ("retry failed payment", "payment confirmed"),
            ("payment confirmed", "retry failed payment"),
            ("payment confirmed", "issue policy"),
        } == certain_edges
    except Exception:
        # Provider/configuration exceptions must never expose credentials or raw payloads.
        failed = True
    finally:
        try:
            if model is not None:
                await model.aclose()
        except Exception:
            failed = True
        finally:
            logging.disable(previous_log_disable)
    if failed:
        pytest.fail("flowchart real-model extraction or semantic checks failed", pytrace=False)
