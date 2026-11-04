"""Agent Turns whose allowlist holds callable tools run the bounded tool loop."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_litellm_model_gateway import configured_gateway, redact, success

from tap.contracts.chat_stream import ChatEventEnvelope
from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.interfaces.http.knowledge_service import KnowledgeHttpService
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.mcp_servers import (
    InMemoryMcpServerRepository,
    McpServerRecord,
    RepositoryMcpToolSource,
)
from tap.modules.ai.application.tool_loop import AgentToolRuntime
from tap.modules.ai.domain.mcp import McpToolSpec
from tap.modules.ai.domain.tools import ToolResult
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.domain.conversations import TurnInput, content_digest
from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway
from tap.testing.deterministic_model_gateway import DeterministicModelGateway, ToolTurnScript

ECHO = "mcp.local.echo"
INSTRUCTION = "Use the echo tool to confirm the request."
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


class EchoClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def list_tools(self, url, headers):
        raise AssertionError("tool loop must not re-sync MCP servers")

    async def call_tool(self, url, headers, name, arguments):
        self.calls.append((name, dict(arguments)))
        return ToolResult(f"echo: {arguments['text']}")


class RecordingGateway(DeterministicModelGateway):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.tool_calls = 0
        self.final_contexts: list[str] = []

    async def chat_with_tools(self, request, messages, tools):
        self.tool_calls += 1
        return await super().chat_with_tools(request, messages, tools)

    async def stream_structured(self, request):
        self.final_contexts.append(request.context)
        async for event in super().stream_structured(request):
            yield event


def _turn_input(allowlist: tuple[str, ...]) -> TurnInput:
    schema_json = json.dumps(SCHEMA, sort_keys=True, separators=(",", ":"))
    return TurnInput(
        message="Please confirm hello.",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode="validation",
        model_alias="qwen-plus",
        agent_revision_id="agent-revision-1",
        agent_revision_digest="sha256:" + "a" * 64,
        agent_system_instruction=INSTRUCTION,
        agent_system_instruction_digest=_sha(INSTRUCTION),
        agent_tool_allowlist=allowlist,
        agent_output_schema_json=schema_json,
        agent_output_schema_digest=_sha(schema_json),
        acl_digest=content_digest({"mode": "model-only", "resources": []}),
        retrieval_policy_digest=content_digest({"mode": "model-only", "retrieval": "not-selected"}),
    )


def _composition(script: tuple[ToolTurnScript, ...]):
    gateway = RecordingGateway(
        configured_gateway(success)._config,
        scope=VALIDATION_SCOPE,
        redact=redact,
        tool_script=script,
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
    scoped = SimpleNamespace(scope=VALIDATION_SCOPE)
    knowledge = KnowledgeHttpService(
        documents=scoped,  # type: ignore[arg-type]
        answers=scoped,  # type: ignore[arg-type]
        citations=scoped,  # type: ignore[arg-type]
        models=models,
    )
    repository = InMemoryMcpServerRepository()
    repository.servers[(VALIDATION_SCOPE.enterprise_id, VALIDATION_SCOPE.project_id, "s1")] = (
        McpServerRecord(
            server_id="s1",
            slug="local",
            display_name="Local tools",
            url="http://127.0.0.1:9000/mcp",
            auth_header_env=None,
            tools=(
                McpToolSpec(
                    "echo",
                    "Echo the supplied text.",
                    {
                        "type": "object",
                        "properties": {"text": {"type": "string", "maxLength": 200}},
                        "required": ["text"],
                    },
                    read_only=True,
                ),
            ),
        )
    )
    client = EchoClient()
    tools = AgentToolRuntime(
        gateway,
        mcp=RepositoryMcpToolSource(repository, client, extra_hosts=frozenset()),
        redact=redact,
    )
    return gateway, knowledge, client, tools


async def _run(allowlist: tuple[str, ...], script: tuple[ToolTurnScript, ...]):
    gateway, knowledge, client, tools = _composition(script)
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    await service.create("conversation-1", "turn-1", "request-1", _turn_input(allowlist))
    worker = GenerationWorker(
        service, knowledge, checkpointer=InMemorySaver(), agent_tools=tools, delta_max_chars=4
    )
    assert await worker.run_once(limit=1) == 1
    return gateway, client, await service.load("conversation-1")


def _envelope(event) -> dict[str, object]:
    return {
        "eventId": event.event_id,
        "sequence": event.sequence,
        "chatId": "conversation-1",
        "turnId": event.turn_id or "turn-1",
        "occurredAt": event.occurred_at.isoformat(),
        "schemaVersion": 1,
        "event": {"type": event.event_type, "payload": dict(event.payload)},
    }


@pytest.mark.asyncio
async def test_agent_with_an_mcp_tool_runs_the_loop_and_answers(span_recorder):
    gateway, client, conversation = await _run(
        ("knowledge.answer", ECHO),
        (ToolTurnScript(((ECHO, {"text": "hello"}),)),),
    )

    turn = conversation.turns[0]
    assert turn.state == "completed"
    assert turn.answer_snapshot.value.answer == "Grounded"
    assert client.calls == [("echo", {"text": "hello"})]
    assert gateway.tool_calls == 2
    tool_results = json.loads(gateway.final_contexts[0])["toolResults"]
    assert tool_results == [
        {
            "label": "T1",
            "tool": ECHO,
            "status": "ok",
            "truncated": False,
            "trust": "untrusted",
            "data": "echo: hello",
        }
    ]
    deltas = [event for event in conversation.events if event.event_type == "answer.delta"]
    assert "".join(event.payload["text"] for event in deltas) == "Grounded"
    for event in conversation.events:
        ChatEventEnvelope.model_validate(_envelope(event))
    names = [item.name for item in span_recorder.get_finished_spans()]
    assert names.count("tool.loop.step") == 1
    assert f"tool.{ECHO}" in names
    assert "tool.mcp.call" in names


@pytest.mark.asyncio
async def test_agent_with_only_the_reserved_answer_tool_keeps_the_one_shot_path(span_recorder):
    gateway, client, conversation = await _run(
        ("knowledge.answer",),
        (ToolTurnScript(((ECHO, {"text": "hello"}),)),),
    )

    assert conversation.turns[0].state == "completed"
    assert gateway.tool_calls == 0
    assert client.calls == []
    assert gateway.final_contexts == ["Please confirm hello."]
    names = [item.name for item in span_recorder.get_finished_spans()]
    assert "tool.loop.step" not in names


@pytest.mark.asyncio
async def test_retrieval_turn_merges_granted_tool_evidence_for_cited_answers():
    from dataclasses import replace

    from test_knowledge_api import (
        SOURCE_HASH,
        FakeModelPort,
        api,
        policy_context,
        ranked_hit,
        search_hit,
    )

    from tap.modules.access.domain.policy import ResourceGrant
    from tap.modules.knowledge.domain.models import AnswerRequest, SourceFamily
    from tap.modules.knowledge.ports.models import AnswerGeneration, GeneratedClaim
    from tap.modules.knowledge.ports.search import ToolPhaseResult

    first, second = (
        search_hit(),
        ranked_hit(family=SourceFamily.CODE, local_rank=1, score=0.5, suffix="5"),
    )

    class SequencedSearch:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def search(self, execution):
            self.queries.append(execution.plan.sanitized_query)
            return (first,) if len(self.queries) == 1 else (first, second)

    class Models(FakeModelPort):
        def __init__(self) -> None:
            super().__init__()
            self.tool_results: list[object] = []

        async def answer(self, query, evidence, profile_id, **kwargs):
            self.answer_evidence.append(evidence)
            self.tool_results.append(kwargs.get("tool_results"))
            return AnswerGeneration(
                text="Payments are authorized server-side.",
                claims=(
                    GeneratedClaim(
                        text="Payments are authorized server-side.", evidence_labels=("S2",)
                    ),
                ),
                model_id="tap-answer-v1",
                profile_id="grounded-answer-v1",
                provider_request_id="answer-request-1",
            )

    async def tool_phase(query, evidence, search):
        found = await search("payment authorization", 2)
        outside = replace(
            found[-1],
            chunk_id="h_" + "9" * 64,
            source=replace(found[-1].source, source_id="repo:checkout:secrets.py"),
        )
        record = {"label": "T2", "tool": ECHO, "status": "ok", "data": "echo"}
        return ToolPhaseResult((*found, outside), (record,))

    grant = ResourceGrant(
        family="code",
        source_id="repo:checkout:payment.py",
        revision_kind="git_commit",
        revision="a" * 40,
        source_content_hash=SOURCE_HASH,
        allowed_anchor_keys=frozenset({"code:checkout:payment.py:authorize:10:25"}),
    )
    search, models = SequencedSearch(), Models()
    response = await api(search, models).answer_frozen(  # type: ignore[arg-type]
        AnswerRequest(query="Where are payments authorized?"),
        policy_context(resource_grants=(grant,)),
        governance=None,
        tool_phase=tool_phase,
    )

    assert search.queries == ["Where are payments authorized?", "payment authorization"]
    evidence = models.answer_evidence[0]
    assert [item.evidence_label for item in evidence] == ["S1", "S2"]
    assert [item.chunk_id for item in evidence] == [first.chunk_id, second.chunk_id]
    assert models.tool_results == [({"label": "T2", "tool": ECHO, "status": "ok", "data": "echo"},)]
    assert response.abstained is False
    assert response.claims[0].citation_ids == (evidence[1].citation_id,)
    assert len(response.citations) == 2


@pytest.mark.asyncio
async def test_agent_tool_loop_does_not_inherit_strict_parent_checkpoint_serialization():
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    gateway, knowledge, client, tools = _composition(
        (ToolTurnScript(((ECHO, {"text": "hello"}),)),)
    )
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    await service.create(
        "strict-chat", "strict-turn", "strict-request", _turn_input(("knowledge.answer", ECHO))
    )
    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=None))
    await GenerationWorker(service, knowledge, checkpointer=saver, agent_tools=tools).run_once(
        limit=1
    )
    turn = (await service.load("strict-chat")).turns[0]
    assert turn.state == "completed"
    assert client.calls == [("echo", {"text": "hello"})]
    assert gateway.tool_calls == 2
    assert set(saver.storage["strict-turn"]) == {""}
