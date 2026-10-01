"""Explicitly opted-in smoke for the prompt suggestion refresh against the
real LiteLLM model route and whatever demo knowledge is currently ready."""

from __future__ import annotations

import logging
import os
import re

import pytest

from tap.contracts.http import DocumentStatus
from tap.entrypoints.tapper_runtime import TapperSettings, _create_embeddings, create_api_runtime
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.litellm import LiteLLMModelGateway
from tap.modules.chat.domain.suggestions import SuggestionKey

_CJK_PATTERN = re.compile(r"[一-鿿]")


async def _refresh_through_production_graph(settings: TapperSettings) -> None:
    runtime = await create_api_runtime(settings)
    try:
        readiness = runtime.http_services.readiness
        knowledge = runtime.http_services.knowledge
        service = runtime.http_services.prompt_suggestions
        if readiness is None or knowledge is None or service is None:
            pytest.skip("the Tapper production graph does not expose prompt suggestions")
        if (await readiness.check()).status != "ready":
            pytest.skip("the Tapper production graph is not ready")

        page = await knowledge.list_documents(cursor=None, limit=50)
        ready_document = next(
            (item for item in page.items if item.status is DocumentStatus.READY),
            None,
        )
        if ready_document is None:
            pytest.skip("the Tapper production graph has no ready knowledge for suggestions")

        key = SuggestionKey(actor_id=VALIDATION_SCOPE.actor_id, locale="zh")
        suggestions = await service.refresh(key)

        if not suggestions:
            raise AssertionError("refresh against ready demo knowledge produced no suggestions")
        for suggestion in suggestions:
            if not suggestion.question.strip():
                raise AssertionError("a returned suggestion has an empty question")
            if not _CJK_PATTERN.search(suggestion.question):
                raise AssertionError(
                    f"suggestion {suggestion.suggestion_id!r} is not in the requested locale"
                )
            if not suggestion.sources:
                raise AssertionError(
                    f"suggestion {suggestion.suggestion_id!r} has no grounding sources"
                )
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_real_model_refresh_produces_grounded_zh_suggestions() -> None:
    if os.environ.get("TAP_RUN_TAPPER_REAL_MODEL_SMOKE") != "1":
        pytest.skip("real prompt suggestion model smoke requires explicit opt-in")

    previous_log_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    model = None
    try:
        settings = TapperSettings.from_mapping(os.environ)
        assert not settings.e2e_mode
        assert settings.model_backend == "litellm"
        model = _create_embeddings(settings, max_retries=0)
        assert type(model.gateway) is LiteLLMModelGateway
        await _refresh_through_production_graph(settings)
    finally:
        if model is not None:
            await model.aclose()
        logging.disable(previous_log_disable)
