"""Answer text deltas surface while the structured answer streams through the gateway."""

from __future__ import annotations

import pytest
from test_litellm_model_gateway import configured_gateway, redact, success

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway
from tap.testing.deterministic_model_gateway import DeterministicModelGateway


class SpyGateway(DeterministicModelGateway):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[str] = []

    async def generate_structured(self, request):
        self.calls.append("generate")
        return await super().generate_structured(request)

    async def stream_structured(self, request):
        self.calls.append("stream")
        async for event in super().stream_structured(request):
            yield event

    async def chat(self, request):
        self.calls.append("chat")
        return await super().chat(request)


def knowledge_models() -> tuple[KnowledgeModelGateway, SpyGateway]:
    gateway = SpyGateway(configured_gateway(success)._config, scope=VALIDATION_SCOPE, redact=redact)
    models = KnowledgeModelGateway(
        gateway,
        scope=VALIDATION_SCOPE,
        redact=redact,
        embedding_alias="text-embedding-v4",
        chat_alias="qwen-plus",
        embedding_dimension=2,
        timeout_seconds=1,
    )
    return models, gateway


def collector() -> tuple[list[str], object]:
    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    return deltas, on_delta


@pytest.mark.asyncio
async def test_grounded_answer_streams_deltas_that_concatenate_to_the_final_answer():
    from test_knowledge_api import _claim_resolution_evidence

    models, gateway = knowledge_models()
    deltas, on_delta = collector()

    result = await models.answer(
        "query", (_claim_resolution_evidence(),), "quick-hybrid-v1", on_delta=on_delta
    )

    assert result.text
    assert len(deltas) > 1
    assert "".join(deltas) == result.text
    assert gateway.calls == ["stream"]


@pytest.mark.asyncio
async def test_grounded_answer_without_callback_uses_generate_structured():
    from test_knowledge_api import _claim_resolution_evidence

    models, gateway = knowledge_models()

    result = await models.answer("query", (_claim_resolution_evidence(),), "quick-hybrid-v1")

    assert result.text
    assert gateway.calls == ["generate"]


@pytest.mark.asyncio
async def test_structured_direct_chat_streams_and_plain_chat_does_not():
    import json

    from test_litellm_model_gateway import sse_response

    from tap.modules.ai.adapters.litellm import LiteLLMModelGateway
    from tap.modules.knowledge.application.answer_templates import assemble_answer, get_template

    content = json.dumps({"answer": "Hello there, \u4e2d friend.", "claims": []})
    paths = []

    def respond(incoming):
        payload = json.loads(incoming.content)
        paths.append("stream" if payload.get("stream") else "plain")
        if payload.get("stream"):
            return sse_response(content, size=5)
        return success(incoming)

    gateway = configured_gateway(respond)
    assert isinstance(gateway, LiteLLMModelGateway)
    models = KnowledgeModelGateway(
        gateway,
        scope=VALIDATION_SCOPE,
        redact=redact,
        embedding_alias="text-embedding-v4",
        chat_alias="qwen-plus",
        embedding_dimension=2,
        timeout_seconds=1,
    )
    deltas, on_delta = collector()
    no_evidence = assemble_answer(
        template_id="general",
        template_version="1",
        template_digest=get_template("general", "1").digest,
        original_question="hello",
        standalone_question="hello",
        evidence_map={},
    )

    structured = await models.chat(
        "hello",
        model_alias="qwen-plus",
        answer_plan_id="plan-b",
        answer_input=no_evidence,
        on_delta=on_delta,
    )
    assert structured.text == "Hello there, 中 friend."
    assert len(deltas) > 1
    assert "".join(deltas) == structured.text
    assert paths == ["stream"]

    plain = await models.chat("hello", model_alias="qwen-plus", on_delta=on_delta)
    assert plain.text == "Grounded"
    assert paths == ["stream", "plain"]
    assert "".join(deltas) == structured.text


@pytest.mark.asyncio
async def test_knowledge_api_threads_the_callback_to_answer_generation():
    from test_knowledge_api import FakeModelPort, FakeSearchPort, search_hit

    from tests.integration.test_answer_plan_execution import (
        execution,
        knowledge,
        planned,
        policy,
        request,
    )

    received = []

    class Models(FakeModelPort):
        async def answer(self, query, evidence, profile_id, **kwargs):
            received.append(kwargs.get("on_delta"))
            return await super().answer(query, evidence, profile_id)

    _, on_delta = collector()
    plan = await planned()
    await knowledge(FakeSearchPort((search_hit(),)), Models()).answer_frozen(
        request(), policy(), governance=None, answer_execution=execution(plan), on_delta=on_delta
    )
    assert received == [on_delta]


@pytest.mark.asyncio
async def test_answer_service_threads_the_callback_to_the_frozen_knowledge_boundary():
    from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
    from tests.unit.knowledge.test_answer_service import Gateway, answer_response, ready, service

    received = []

    class Frozen(Gateway):
        async def answer_frozen(self, request, policy, **kwargs):
            received.append(kwargs.get("on_delta"))
            return answer_response()

    answer_service, _, _ = service()
    answer_service._knowledge = Frozen()
    _, on_delta = collector()
    frozen = (ready(),)
    from tests.unit.knowledge.test_answer_service import request_for

    await answer_service.answer_frozen(
        request_for("doc_a"),
        frozen,
        build_demo_policy_context(frozen),
        governance=None,
        on_delta=on_delta,
    )
    assert received == [on_delta]


@pytest.mark.asyncio
async def test_conversation_answer_threads_the_callback_to_model_only_chat():
    from tap.contracts.http import RetrievalAnswerRequest
    from tap.interfaces.http.knowledge_service import KnowledgeHttpService
    from tap.modules.chat.domain.conversations import TurnInput, content_digest
    from tap.modules.knowledge.ports.models import AnswerGeneration

    received = []

    class Models:
        scope = VALIDATION_SCOPE

        async def chat(self, query, **kwargs):
            received.append(kwargs.get("on_delta"))
            return AnswerGeneration("Hi", (), "qwen-plus", "direct-chat-v1", None)

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
    _, on_delta = collector()
    await service.answer_conversation(
        RetrievalAnswerRequest(query="Hello"), frozen, on_delta=on_delta
    )
    assert received == [on_delta]
