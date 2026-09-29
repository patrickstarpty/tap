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
            embedding_provenance=ModelCallProvenance("text-embedding-v4", None),
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
            assert model_alias == "qwen-plus"
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
        model_alias="qwen-plus",
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


def _catalog_backed_service(sent: list[str]) -> KnowledgeHttpService:
    import json

    import httpx

    from tap.modules.ai.adapters.litellm import LiteLLMModelGateway, LiteLLMModelGatewayConfig
    from tap.modules.ai.adapters.litellm_catalog import LiteLLMModel, LiteLLMRoutes, ModelRoles
    from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway
    from tap.testing.deterministic_model_gateway import StaticLiteLLMCatalog

    async def redact(text: str) -> str:
        return text

    def respond(incoming: httpx.Request) -> httpx.Response:
        sent.append(json.loads(incoming.content)["model"])
        return httpx.Response(
            200,
            json={
                "model": "dashscope/qwen-max",
                "choices": [{"message": {"content": "Hello from Qwen Max."}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 4},
            },
        )

    routes = LiteLLMRoutes(
        {
            "qwen-plus": LiteLLMModel("qwen-plus", "Qwen Plus", "chat", False, True, False),
            "qwen-max": LiteLLMModel("qwen-max", "Qwen Max", "chat", False, True, False),
            "text-embedding-v4": LiteLLMModel(
                "text-embedding-v4", "text-embedding-v4", "embedding", False, False, False
            ),
        }
    )
    gateway = LiteLLMModelGateway(
        LiteLLMModelGatewayConfig(
            base_url="https://litellm.example",
            api_key="not-a-real-key",
            roles=ModelRoles("qwen-plus", "text-embedding-v4", None),
            embedding_dimension=2,
        ),
        scope=VALIDATION_SCOPE,
        redact=redact,
        catalog=StaticLiteLLMCatalog(routes),
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    models = KnowledgeModelGateway(
        gateway,
        scope=VALIDATION_SCOPE,
        redact=redact,
        embedding_alias="text-embedding-v4",
        chat_alias="qwen-plus",
        embedding_dimension=2,
        timeout_seconds=1,
    )
    return KnowledgeHttpService(
        documents=object(),  # type: ignore[arg-type]
        answers=object(),  # type: ignore[arg-type]
        citations=object(),  # type: ignore[arg-type]
        models=models,
    )


def _model_only_turn(alias: str) -> TurnInput:
    return TurnInput(
        message="Hello",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias=alias,
        acl_digest=content_digest({"mode": "model-only", "resources": []}),
        retrieval_policy_digest=content_digest({"mode": "model-only", "retrieval": "not-selected"}),
    )


@pytest.mark.asyncio
async def test_catalog_chat_model_other_than_default_answers_a_turn() -> None:
    sent: list[str] = []
    service = _catalog_backed_service(sent)

    response = await service.answer_conversation(
        RetrievalAnswerRequest(query="Hello"), _model_only_turn("qwen-max")
    )

    assert response.answer == "Hello from Qwen Max."
    assert sent == ["qwen-max"]


@pytest.mark.asyncio
async def test_turn_with_model_absent_from_catalog_is_model_unavailable() -> None:
    from tap.modules.knowledge.ports.errors import AnswerUnavailable

    sent: list[str] = []
    service = _catalog_backed_service(sent)

    with pytest.raises(AnswerUnavailable, match="^model-unavailable$"):
        await service.answer_conversation(
            RetrievalAnswerRequest(query="Hello"), _model_only_turn("removed-model")
        )
    assert sent == []
