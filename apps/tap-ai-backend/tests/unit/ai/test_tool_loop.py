"""The bounded Agent tool loop over the deterministic gateway's tool script."""

from __future__ import annotations

import json

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.litellm import LiteLLMModelGatewayConfig
from tap.modules.ai.adapters.litellm_catalog import ModelRoles
from tap.modules.ai.application.tool_loop import (
    DEFAULT_CONTEXT_TOKENS,
    ToolLoop,
    ToolLoopLimits,
    validate_tool_arguments,
)
from tap.modules.ai.application.tool_registry import ToolRegistry, builtin_tools
from tap.modules.ai.domain.models import (
    ChatMessage,
    ModelOperation,
    ModelRequest,
    text_digest,
)
from tap.modules.ai.domain.tools import (
    ToolDescriptor,
    ToolEffect,
    ToolOrigin,
    ToolRejected,
    ToolResult,
)
from tap.testing.deterministic_model_gateway import DeterministicModelGateway, ToolTurnScript

ECHO = "mcp.local.echo"
BOOM = "mcp.local.boom"
PROMPT = "Use tools when they help."
CONTEXT = '{"query":"What is the retention period?"}'


async def redact(text: str) -> str:
    return text.replace("owner@example.com", "[email]")


class Executor:
    def __init__(self, content: str = "ok", *, error: Exception | None = None, evidence=()):
        self.calls: list[dict[str, object]] = []
        self._content = content
        self._error = error
        self._evidence = evidence

    async def invoke(self, scope, arguments):
        assert scope == VALIDATION_SCOPE
        self.calls.append(dict(arguments))
        if self._error is not None:
            raise self._error
        return ToolResult(self._content, evidence=self._evidence)


class McpSource:
    def __init__(self, executors: dict[str, Executor], *, enabled: bool = True) -> None:
        self.executors = executors
        self.enabled = enabled

    async def descriptors(self, scope):
        return tuple(
            ToolDescriptor(
                tool_id=tool_id,
                display_name=tool_id.rsplit(".", 1)[-1],
                description="Echo text.",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string", "maxLength": 20}},
                    "required": ["text"],
                },
                effect=ToolEffect.READ,
                origin=ToolOrigin.MCP,
                server_id="server-1",
                enabled=self.enabled,
            )
            for tool_id in self.executors
        )

    async def executor(self, scope, tool_id):
        return self.executors[tool_id]


def gateway(*script: ToolTurnScript) -> DeterministicModelGateway:
    config = LiteLLMModelGatewayConfig(
        base_url="https://litellm.example",
        api_key="private-provider-key",
        roles=ModelRoles("qwen-plus", "text-embedding-v4", None),
        embedding_dimension=2,
    )
    return DeterministicModelGateway(
        config, scope=VALIDATION_SCOPE, redact=redact, tool_script=script
    )


def request(allowlist: frozenset[str]) -> ModelRequest:
    return ModelRequest(
        VALIDATION_SCOPE,
        "qwen-plus",
        ModelOperation.CHAT,
        PROMPT,
        text_digest(PROMPT),
        CONTEXT,
        1.0,
        "turn-1",
        tool_allowlist=allowlist,
        governance_digests=(text_digest("agent"),),
    )


def opening() -> tuple[ChatMessage, ...]:
    return (ChatMessage("system", PROMPT), ChatMessage("user", CONTEXT))


def script(*calls: tuple[str, dict[str, object]]) -> ToolTurnScript:
    return ToolTurnScript(tuple(calls))


class Recording(DeterministicModelGateway):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.offered: list[tuple[str, ...]] = []

    async def chat_with_tools(self, request, messages, tools):
        self.offered.append(tuple(item.name for item in tools))
        return await super().chat_with_tools(request, messages, tools)


def recording(*turns: ToolTurnScript) -> Recording:
    base = gateway()
    return Recording(base._config, scope=VALIDATION_SCOPE, redact=redact, tool_script=turns)


def loop(model, executors, *, limits=None, search=None, enabled=True) -> ToolLoop:
    registry = ToolRegistry(
        builtin_tools(knowledge_search=search), mcp=McpSource(executors, enabled=enabled)
    )
    return ToolLoop(model, registry, limits or ToolLoopLimits(token_budget=10_000), redact=redact)


ALLOW = frozenset({"knowledge.answer", ECHO})


def tool_payloads(outcome) -> list[dict[str, object]]:
    return [json.loads(item.content) for item in outcome.transcript if item.role == "tool"]


@pytest.mark.asyncio
async def test_two_tool_steps_then_text_with_labelled_untrusted_results(span_recorder):
    echo = Executor("echo owner@example.com")
    model = recording(script((ECHO, {"text": "a"})), script((ECHO, {"text": "b"})))

    outcome = await loop(model, {ECHO: echo}).run(
        VALIDATION_SCOPE, request(ALLOW), opening(), ALLOW
    )

    assert outcome.steps == 2
    assert outcome.stopped_by == "model"
    assert echo.calls == [{"text": "a"}, {"text": "b"}]
    assert model.offered == [(ECHO,), (ECHO,), (ECHO,)]
    assert outcome.tool_labels == {"T1": ECHO, "T2": ECHO}
    payloads = tool_payloads(outcome)
    assert [item["label"] for item in payloads] == ["T1", "T2"]
    assert payloads[0]["trust"] == "untrusted"
    assert payloads[0]["data"] == "echo [email]"
    assert outcome.transcript[-1].role == "assistant" and outcome.transcript[-1].content
    names = [item.name for item in span_recorder.get_finished_spans()]
    assert names.count("tool.loop.step") == 2
    assert names.count(f"tool.{ECHO}") == 2


@pytest.mark.asyncio
async def test_fifth_model_call_is_forced_without_tools():
    echo = Executor()
    turns = [script((ECHO, {"text": str(index)})) for index in range(6)]
    model = recording(*turns)

    outcome = await loop(model, {ECHO: echo}).run(
        VALIDATION_SCOPE, request(ALLOW), opening(), ALLOW
    )

    assert outcome.steps == 4
    assert outcome.stopped_by == "steps"
    assert len(echo.calls) == 4
    assert model.offered == [(ECHO,)] * 4 + [()]
    assert outcome.transcript[-1].role == "assistant"
    assert outcome.transcript[-1].tool_calls == ()


@pytest.mark.asyncio
async def test_unknown_or_unallowlisted_tool_is_not_executed():
    echo, boom = Executor(), Executor()
    model = recording(script((BOOM, {"text": "x"}), ("knowledge.answer", {})))

    outcome = await loop(model, {ECHO: echo, BOOM: boom}).run(
        VALIDATION_SCOPE, request(ALLOW), opening(), ALLOW
    )

    assert boom.calls == [] and echo.calls == []
    assert [(item["status"], item["data"]) for item in tool_payloads(outcome)] == [
        ("error", "tool-not-allowed"),
        ("error", "tool-not-allowed"),
    ]


@pytest.mark.asyncio
async def test_only_three_parallel_calls_execute_in_one_step():
    echo = Executor()
    model = recording(script(*((ECHO, {"text": str(index)}) for index in range(4))))

    outcome = await loop(model, {ECHO: echo}).run(
        VALIDATION_SCOPE, request(ALLOW), opening(), ALLOW
    )

    assert len(echo.calls) == 3
    assert [item["data"] for item in tool_payloads(outcome)][-1] == "tool-call-limit"
    assert outcome.tool_labels == {f"T{index}": ECHO for index in range(1, 5)}


@pytest.mark.asyncio
async def test_token_budget_exhaustion_stops_with_a_final_call_without_tools():
    echo = Executor()
    model = recording(script((ECHO, {"text": "a"})), script((ECHO, {"text": "b"})))

    outcome = await loop(model, {ECHO: echo}, limits=ToolLoopLimits(token_budget=1)).run(
        VALIDATION_SCOPE, request(ALLOW), opening(), ALLOW
    )

    assert outcome.stopped_by == "budget"
    assert outcome.steps == 1
    assert len(echo.calls) == 1
    assert model.offered == [(ECHO,), ()]


@pytest.mark.asyncio
async def test_tool_exception_becomes_an_error_result_and_the_loop_continues():
    boom = Executor(error=RuntimeError("secret stack detail"))
    allow = frozenset({"knowledge.answer", BOOM})
    model = recording(script((BOOM, {"text": "a"})))

    outcome = await loop(model, {BOOM: boom}).run(
        VALIDATION_SCOPE, request(allow), opening(), allow
    )

    assert outcome.stopped_by == "model"
    payload = tool_payloads(outcome)[0]
    assert payload["status"] == "error"
    assert payload["data"] == "tool-failed"
    assert "secret" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_invalid_arguments_are_rejected_without_invoking_the_tool():
    echo = Executor()
    model = recording(
        script((ECHO, {"text": "x" * 21}), (ECHO, {}), (ECHO, {"text": 3})),
    )

    outcome = await loop(model, {ECHO: echo}).run(
        VALIDATION_SCOPE, request(ALLOW), opening(), ALLOW
    )

    assert echo.calls == []
    assert {item["data"] for item in tool_payloads(outcome)} == {"tool-arguments-invalid"}


@pytest.mark.asyncio
async def test_exposure_requires_the_registry_allowlist_and_callable_tools():
    echo = Executor()
    model = recording(script((ECHO, {"text": "a"})))

    with pytest.raises(ToolRejected):
        await loop(model, {ECHO: echo}, enabled=False).run(
            VALIDATION_SCOPE, request(ALLOW), opening(), ALLOW
        )
    with pytest.raises(ToolRejected):
        await loop(model, {ECHO: echo}).run(
            VALIDATION_SCOPE, request(ALLOW), opening(), frozenset({"mcp.missing.tool"})
        )

    allow = frozenset({"knowledge.answer", "knowledge.search", ECHO})
    outcome = await loop(model, {ECHO: echo}).run(
        VALIDATION_SCOPE, request(allow), opening(), allow
    )
    assert model.offered[0] == (ECHO,)
    assert outcome.steps == 1


@pytest.mark.asyncio
async def test_tool_evidence_is_collected_in_call_order():
    search = Executor("[E1] alpha", evidence=("evidence-a", "evidence-b"))
    allow = frozenset({"knowledge.answer", "knowledge.search"})
    model = recording(script(("knowledge.search", {"query": "alpha", "limit": 2})))

    outcome = await loop(model, {}, search=search).run(
        VALIDATION_SCOPE, request(allow), opening(), allow
    )

    assert search.calls == [{"query": "alpha", "limit": 2}]
    assert outcome.evidence == ("evidence-a", "evidence-b")
    assert outcome.tool_labels == {"T1": "knowledge.search"}


def test_limits_default_to_half_of_a_conservative_context_window():
    limits = ToolLoopLimits.for_context(None)
    assert DEFAULT_CONTEXT_TOKENS == 32768
    assert (limits.max_steps, limits.max_parallel, limits.token_budget) == (4, 3, 16384)
    assert ToolLoopLimits.for_context(128_000).token_budget == 64_000
    for invalid in ({"token_budget": 0}, {"token_budget": 10, "max_steps": 5}):
        with pytest.raises(ValueError):
            ToolLoopLimits(**invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("schema", "value", "valid"),
    [
        ({"type": "object", "required": ["a"]}, {}, False),
        ({"type": "object", "properties": {"a": {"type": "integer"}}}, {"a": True}, False),
        (
            {"type": "object", "properties": {"a": {"type": "number", "maximum": 2}}},
            {"a": 3},
            False,
        ),
        (
            {"type": "object", "properties": {"a": {"type": "integer", "minimum": 1}}},
            {"a": 1},
            True,
        ),
        ({"type": "object", "additionalProperties": False}, {"b": 1}, False),
        ({"type": "object", "properties": {"a": {"enum": ["x"]}}}, {"a": "y"}, False),
        (
            {"type": "object", "properties": {"a": {"type": "array", "items": {"type": "string"}}}},
            {"a": ["x", 1]},
            False,
        ),
        ({"type": "object", "properties": {"a": {"type": ["string", "null"]}}}, {"a": None}, True),
        ({"type": "object"}, {"_raw": "{not json"}, False),
        (
            {"type": "object", "properties": {"a": {"type": "string", "minLength": 2}}},
            {"a": "x"},
            False,
        ),
    ],
)
def test_minimal_argument_validator(schema, value, valid):
    assert validate_tool_arguments(schema, value) is valid


@pytest.mark.asyncio
async def test_tools_whose_wire_name_exceeds_64_characters_are_not_offered():
    long_id = "mcp.local." + "x" * 60
    echo, long_tool = Executor(), Executor()
    allow = frozenset({"knowledge.answer", ECHO, long_id})
    model = recording(script((ECHO, {"text": "a"})))

    await loop(model, {ECHO: echo, long_id: long_tool}).run(
        VALIDATION_SCOPE, request(allow), opening(), allow
    )

    assert model.offered[0] == (ECHO,)


@pytest.mark.parametrize(
    ("schema", "value"),
    [
        ({"type": "string", "pattern": "^/approved/"}, "/private/secret"),
        ({"const": "approved"}, "secret"),
        ({"oneOf": [{"type": "string"}, {"type": "integer"}]}, False),
        ({"$defs": {"name": {"const": "ok"}}, "$ref": "#/$defs/name"}, "bad"),
        ({"$ref": "https://untrusted.invalid/schema"}, {}),
        ({"type": "number", "maximum": 2}, 10**1000),
    ],
)
def test_tool_arguments_reject_schema_violations_without_crashing(schema, value):
    assert validate_tool_arguments(schema, value) is False
