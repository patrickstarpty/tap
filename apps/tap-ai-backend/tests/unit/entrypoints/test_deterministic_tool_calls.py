"""Task 12: the fake E2E gateway's native tool-calling behaviour -- only active
when a request carries `tools`, driven purely by the request (no held state)."""

from __future__ import annotations

import hashlib
import json

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.litellm import LiteLLMModelGatewayConfig
from tap.modules.ai.adapters.litellm_catalog import ModelRoles
from tap.modules.ai.domain.models import (
    ModelMessage,
    ModelOperation,
    ModelRequest,
    ModelToolCall,
    model_tool_specs,
)
from tap.testing.deterministic_model_gateway import DeterministicModelGateway


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


async def _redact(text: str) -> str:
    return text


def _gateway() -> DeterministicModelGateway:
    config = LiteLLMModelGatewayConfig(
        base_url="https://litellm.example",
        api_key="x" * 20,
        roles=ModelRoles("qwen-plus", "text-embedding-v4", None),
        embedding_dimension=2,
    )
    return DeterministicModelGateway(
        config, scope=VALIDATION_SCOPE, redact=_redact, answer_delay_seconds=0
    )


def _request(
    question: str,
    *,
    tools: tuple[str, ...] = (),
    messages: tuple[ModelMessage, ...] = (),
) -> ModelRequest:
    context = json.dumps({"query": question})
    prompt = "Use supplied context."
    return ModelRequest(
        scope=VALIDATION_SCOPE,
        alias="qwen-plus",
        operation=ModelOperation.CHAT,
        prompt=prompt,
        prompt_digest=digest(prompt),
        context=context,
        timeout_seconds=1,
        idempotency_key="deterministic-tool-call-1",
        tools=model_tool_specs(tools),
        messages=messages,
    )


def _assistant_message(call: ModelToolCall) -> ModelMessage:
    return ModelMessage(role="assistant", content="", tool_calls=(call,))


def _tool_result_message(call_id: str) -> ModelMessage:
    return ModelMessage(
        role="tool", content=json.dumps({"status": "completed"}), tool_call_id=call_id
    )


@pytest.mark.asyncio
async def test_markers_are_consumed_in_order_then_answer() -> None:
    gateway = _gateway()
    question = (
        "请比较文档 [e2e-tool:knowledge.search:keyword-a] 和 [e2e-tool:knowledge.search:keyword-b]"
    )

    # Round 1: nothing consumed yet -- the first marker wins.
    first = await gateway.chat(_request(question, tools=("knowledge.search",)))
    assert len(first.tool_calls) == 1
    assert first.tool_calls[0].name == "knowledge_search"
    assert first.tool_calls[0].call_id == "fake-0"
    assert json.loads(first.tool_calls[0].arguments_json) == {"query": "keyword-a"}

    # Round 2: the first marker's call is now in the transcript -- the
    # second marker wins.
    messages_after_round_1 = (
        _assistant_message(first.tool_calls[0]),
        _tool_result_message(first.tool_calls[0].call_id),
    )
    second = await gateway.chat(
        _request(question, tools=("knowledge.search",), messages=messages_after_round_1)
    )
    assert len(second.tool_calls) == 1
    assert second.tool_calls[0].call_id == "fake-1"
    assert json.loads(second.tool_calls[0].arguments_json) == {"query": "keyword-b"}

    # Round 3: both markers are consumed -- the fake falls through to its
    # existing final-answer logic instead of issuing a third call.
    messages_after_round_2 = (
        *messages_after_round_1,
        _assistant_message(second.tool_calls[0]),
        _tool_result_message(second.tool_calls[0].call_id),
    )
    third = await gateway.chat(
        _request(question, tools=("knowledge.search",), messages=messages_after_round_2)
    )
    assert third.tool_calls == ()
    assert isinstance(third.output, str) and third.output


@pytest.mark.asyncio
async def test_insights_marker_uses_fixed_arguments_and_is_consumed_as_a_whole() -> None:
    gateway = _gateway()
    question = "本周的首次通过率如何 [e2e-tool:insights.query:ignored]"

    first = await gateway.chat(_request(question, tools=("insights.query",)))

    assert len(first.tool_calls) == 1
    assert first.tool_calls[0].name == "insights_query"
    arguments = json.loads(first.tool_calls[0].arguments_json)
    assert arguments["metricIds"] == ["first_pass_rate"]
    assert arguments["filters"] == {}
    assert arguments["from"] < arguments["to"]

    # A prior insights.query call consumes any insights.query marker,
    # regardless of its (unused) argument text.
    messages = (
        _assistant_message(first.tool_calls[0]),
        _tool_result_message(first.tool_calls[0].call_id),
    )
    second = await gateway.chat(_request(question, tools=("insights.query",), messages=messages))
    assert second.tool_calls == ()


@pytest.mark.asyncio
async def test_relation_question_calls_graph_relations_once() -> None:
    gateway = _gateway()
    question = "核保流程和健康告知是什么关系"

    first = await gateway.chat(_request(question, tools=("graph.relations",)))

    assert len(first.tool_calls) == 1
    assert first.tool_calls[0].name == "graph_relations"
    assert json.loads(first.tool_calls[0].arguments_json) == {"query": question}

    # Once `graph.relations` has already been called this turn, the same
    # relation question does not trigger it again.
    messages = (
        _assistant_message(first.tool_calls[0]),
        _tool_result_message(first.tool_calls[0].call_id),
    )
    second = await gateway.chat(_request(question, tools=("graph.relations",), messages=messages))
    assert second.tool_calls == ()


@pytest.mark.asyncio
async def test_non_relation_question_with_no_marker_never_calls_a_tool() -> None:
    gateway = _gateway()

    result = await gateway.chat(_request("今天天气如何", tools=("knowledge.search",)))

    assert result.tool_calls == ()


@pytest.mark.asyncio
async def test_tools_offered_but_tool_id_not_visible_falls_through() -> None:
    """A marker naming a tool id this round does not offer must not produce
    a call for it -- the fake only ever calls tools actually in `request.tools`."""

    gateway = _gateway()
    question = "请查 [e2e-tool:graph.relations:keyword]"

    result = await gateway.chat(_request(question, tools=("knowledge.search",)))

    assert result.tool_calls == ()


@pytest.mark.asyncio
async def test_clarify_marker_produces_clarification() -> None:
    gateway = _gateway()
    question = "这个产品怎么样 [e2e-clarify]"

    result = await gateway.chat(_request(question))

    assert result.tool_calls == ()
    assert result.output == "CLARIFY: 请说明你指的是哪个产品或版本？"


@pytest.mark.asyncio
async def test_no_tools_offered_never_produces_a_tool_call() -> None:
    """The deterministic fake must only ever call tools when the request
    carries them -- a marker in the question is not enough on its own."""

    gateway = _gateway()
    question = "请查 [e2e-tool:knowledge.search:keyword]"

    result = await gateway.chat(_request(question, tools=()))

    assert result.tool_calls == ()
