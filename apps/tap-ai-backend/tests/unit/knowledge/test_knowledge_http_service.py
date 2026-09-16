from __future__ import annotations

import pytest

from tap.contracts.http import RetrievalAnswerRequest, RetrievalSearchRequest
from tap.interfaces.http.knowledge_service import KnowledgeHttpService
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.domain.conversations import TurnInput, content_digest
from tap.modules.knowledge.domain.models import (
    ModelCallProvenance,
    RetrievalProfileId,
    SearchRequest,
    SearchResponse,
)
from tap.modules.knowledge.ports.models import AnswerGeneration


class Searches:
    def __init__(self) -> None:
        self.requests: list[SearchRequest] = []

    async def search(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        return SearchResponse(
            trace_id="trace-a",
            query_plan_id="plan-a",
            context_snapshot_id="context-a",
            corpus_version="tapper-demo-v1",
            retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
            evidence=(),
            embedding_provenance=ModelCallProvenance("tapper-embedding", None),
        )


@pytest.mark.asyncio
async def test_internal_search_maps_through_the_composed_service_without_an_http_route() -> None:
    searches = Searches()
    service = KnowledgeHttpService(
        documents=object(),  # type: ignore[arg-type]
        answers=object(),  # type: ignore[arg-type]
        citations=object(),  # type: ignore[arg-type]
        searches=searches,
    )

    response = await service.search(
        RetrievalSearchRequest.model_validate(
            {
                "query": "What is the rule?",
                "resourceRefs": [{"family": "doc", "sourceId": "doc-a", "mode": "scope"}],
            }
        )
    )

    assert response.hits == []
    assert len(searches.requests) == 1
    assert searches.requests[0].resource_refs[0].source_id == "doc-a"


@pytest.mark.asyncio
async def test_model_only_conversation_returns_an_ungrounded_answer_without_citations() -> None:
    class Models:
        scope = VALIDATION_SCOPE

        async def chat(self, query, *, model_alias, governance):
            assert query == "Hello"
            assert model_alias == "tapper-chat"
            assert governance is None
            return AnswerGeneration(
                "Hello from the model.",
                (),
                model_alias,
                "direct-chat-v1",
                "provider-request-1",
                gateway_call_id="gateway-call-1",
                provider_model_id="dashscope/qwen-plus",
            )

    service = KnowledgeHttpService(
        documents=object(),  # type: ignore[arg-type]
        answers=object(),  # type: ignore[arg-type]
        citations=object(),  # type: ignore[arg-type]
        models=Models(),  # type: ignore[arg-type]
    )
    frozen = TurnInput(
        message="Hello",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="tapper-chat",
        acl_digest=content_digest({"mode": "model-only", "resources": []}),
        retrieval_policy_digest=content_digest({"mode": "model-only", "retrieval": "not-selected"}),
    )

    response = await service.answer_conversation(
        RetrievalAnswerRequest(query="Hello", sources=["doc"]), frozen
    )

    assert response.answer == "Hello from the model."
    assert response.abstained is False
    assert response.citations == []
    assert response.claims == []
    assert response.graph_context_status == "NOT_SELECTED"
    assert response.retrieval_profile_id == "direct-chat-v1"
