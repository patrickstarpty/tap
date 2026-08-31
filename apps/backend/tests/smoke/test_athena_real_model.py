"""Explicitly opted-in smoke for Athena's production LiteLLM route."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import pytest

from tap.contracts.http import (
    AnswerMode,
    DocumentStatus,
    ResourceMode,
    ResourceRef,
    RetrievalAnswerRequest,
    SourceFamily,
)
from tap.entrypoints.athena_runtime import (
    AthenaSettings,
    _create_embeddings,
    create_api_runtime,
)
from tap.modules.knowledge.adapters.litellm import LiteLLMAdapter

_CHAT_ALIAS = "athena-chat"
_EMBEDDING_ALIAS = "athena-embedding"
_EMBEDDING_DIMENSION = 1_536
_SMOKE_QUERY = "请仅依据所选来源概括其主要内容，并给出可核验引用。"

_T = TypeVar("_T")


async def _timed_alias(alias: str, operation: Callable[[], Awaitable[_T]]) -> _T:
    started = time.monotonic_ns()
    try:
        result = await operation()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
        failure = f"alias={alias} status=failure elapsed_ms={elapsed_ms}"
    else:
        elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
        print(f"alias={alias} status=success elapsed_ms={elapsed_ms}")
        return result

    print(failure)
    pytest.fail(failure, pytrace=False)


async def _embed_through_production_route() -> AthenaSettings:
    settings = AthenaSettings.from_mapping(os.environ)
    if (
        settings.model_backend != "litellm"
        or settings.e2e_mode
        or settings.chat_alias != _CHAT_ALIAS
        or settings.embedding_alias != _EMBEDDING_ALIAS
        or settings.embedding_dimension != _EMBEDDING_DIMENSION
    ):
        raise AssertionError("the fixed Athena model route is not configured")

    model = _create_embeddings(settings)
    if not isinstance(model, LiteLLMAdapter):
        raise AssertionError("the production model adapter is not LiteLLM")
    try:
        embedding = await model.embed(_SMOKE_QUERY)
    finally:
        await model.close()
    if (
        model.embedding_model_id != _EMBEDDING_ALIAS
        or embedding.model_id != _EMBEDDING_ALIAS
        or len(embedding.vector) != settings.embedding_dimension
    ):
        raise AssertionError("the fixed embedding route returned incompatible metadata")
    return settings


async def _answer_through_production_graph(settings: AthenaSettings) -> None:
    runtime = await create_api_runtime(settings)
    try:
        readiness = runtime.http_services.readiness
        knowledge = runtime.http_services.knowledge
        if readiness is None or knowledge is None or (await readiness.check()).status != "ready":
            raise AssertionError("the Athena production graph is not ready")

        page = await knowledge.list_documents(cursor=None, limit=50)
        ready_document = next(
            (item for item in page.items if item.status is DocumentStatus.READY),
            None,
        )
        if ready_document is None:
            raise AssertionError("the Athena production graph has no ready source")

        response = await knowledge.answer(
            RetrievalAnswerRequest(
                query=_SMOKE_QUERY,
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
        if (
            response.abstained
            or response.retrieval_profile_id != settings.retrieval_profile
            or not response.answer.strip()
            or not response.claims
            or not response.citations
            or any(not claim.citation_ids for claim in response.claims)
        ):
            raise AssertionError("the grounded answer is incomplete")

        citation_ids = {citation.citation_id for citation in response.citations}
        if not citation_ids or any(
            not set(claim.citation_ids) <= citation_ids for claim in response.claims
        ):
            raise AssertionError("the grounded answer has unresolved claim references")
        for citation_id in citation_ids:
            preview = await knowledge.citation(citation_id)
            if preview.citation_id != citation_id:
                raise AssertionError("a returned citation did not resolve exactly")
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_real_athena_aliases_produce_grounded_cited_answer() -> None:
    if os.environ.get("TAP_RUN_ATHENA_REAL_MODEL_SMOKE") != "1":
        pytest.skip("real Athena model smoke requires explicit opt-in")

    previous_log_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        settings = await _timed_alias(_EMBEDDING_ALIAS, _embed_through_production_route)
        await _timed_alias(
            _CHAT_ALIAS,
            lambda: _answer_through_production_graph(settings),
        )
    finally:
        logging.disable(previous_log_disable)
