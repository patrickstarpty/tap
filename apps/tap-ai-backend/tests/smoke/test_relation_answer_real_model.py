"""Explicitly opted-in smoke for a relation question against the real
LiteLLM model route and whatever demo knowledge/graph is currently ready."""

from __future__ import annotations

import logging
import os

import pytest

from tap.contracts.http import (
    AnswerMode,
    DocumentStatus,
    ResourceMode,
    ResourceRef,
    RetrievalAnswerRequest,
    SourceFamily,
)
from tap.entrypoints.tapper_runtime import TapperSettings, _create_embeddings, create_api_runtime
from tap.modules.ai.adapters.litellm import LiteLLMModelGateway

_RELATION_QUERY = "这份资料里主要流程之间是什么关系"


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


async def _ask_relation_question_through_production_graph(settings: TapperSettings) -> None:
    runtime = await create_api_runtime(settings)
    try:
        readiness = runtime.http_services.readiness
        knowledge = runtime.http_services.knowledge
        if readiness is None or knowledge is None:
            pytest.skip("the Tapper production graph does not expose knowledge answers")
        if (await readiness.check()).status != "ready":
            pytest.skip("the Tapper production graph is not ready")

        page = await knowledge.list_documents(cursor=None, limit=50)
        ready_document = next(
            (item for item in page.items if item.status is DocumentStatus.READY),
            None,
        )
        if ready_document is None:
            pytest.skip("the Tapper production graph has no ready source for a relation question")

        response = await knowledge.answer(
            RetrievalAnswerRequest(
                query=_RELATION_QUERY,
                answer_mode=AnswerMode.QUICK,
                sources=[SourceFamily.DOC],
                resource_refs=[
                    ResourceRef(
                        family=SourceFamily.DOC,
                        source_id=ready_document.document_id,
                        mode=ResourceMode.SCOPE,
                    )
                ],
            )
        )

        if response.graph_context_status not in {"APPLIED", "EMPTY", "NOT_READY"}:
            raise AssertionError(
                f"unexpected graph_context_status {response.graph_context_status!r} "
                "for a relation question"
            )

        if response.graph_context_status == "APPLIED":
            edge_citations = [
                citation for citation in response.citations if citation.kind == "edge"
            ]
            if not edge_citations:
                raise AssertionError(
                    "APPLIED graph context produced no edge citation for a relation question"
                )
            cited_edge_ids = {citation.citation_id for citation in edge_citations}
            cited_claims = [
                claim for claim in response.claims if set(claim.citation_ids) & cited_edge_ids
            ]
            if not cited_claims:
                raise AssertionError("no claim text cites any of the returned edge citations")
            for claim in cited_claims:
                for citation in edge_citations:
                    if citation.citation_id not in claim.citation_ids:
                        continue
                    assert citation.edge is not None
                    endpoints = (citation.edge.subject.label, citation.edge.object.label)
                    normalized_text = _normalize(claim.text)
                    for label in endpoints:
                        if _normalize(label) not in normalized_text:
                            raise AssertionError(
                                f"claim {claim.claim_id!r} cites edge {citation.citation_id!r} "
                                f"but its text is missing endpoint label {label!r}"
                            )
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_real_model_relation_question_cites_an_edge_or_reports_empty() -> None:
    if os.environ.get("TAP_RUN_TAPPER_REAL_MODEL_SMOKE") != "1":
        pytest.skip("real relation answer model smoke requires explicit opt-in")

    previous_log_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    model = None
    try:
        settings = TapperSettings.from_mapping(os.environ)
        assert not settings.e2e_mode
        assert settings.model_backend == "litellm"
        assert settings.graph_reasoning
        model = _create_embeddings(settings, max_retries=0)
        assert type(model.gateway) is LiteLLMModelGateway
        await _ask_relation_question_through_production_graph(settings)
    finally:
        if model is not None:
            await model.aclose()
        logging.disable(previous_log_disable)
