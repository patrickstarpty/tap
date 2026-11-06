import json

import pytest
from langgraph.checkpoint.memory import MemorySaver

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.models import (
    ModelCallAudit,
    ModelOperation,
    ModelResult,
    ModelToolCall,
    ModelUsage,
)
from tap.modules.workflows.application.native_graph import NativeWorkflowGraph
from tap.modules.workflows.domain.delegation import can_dispatch


def test_waiting_dependency_does_not_take_a_worker_slot():
    assert not can_dispatch(("ba",), {"ba": "running"}, 2)
    assert can_dispatch(("ba",), {"ba": "succeeded"}, 1)
    assert not can_dispatch((), {}, 0)


class ScriptedGateway:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def chat(self, request):
        self.calls.append(request)
        output, tools = self.results.pop(0)
        return ModelResult(
            output=output,
            actual_model="fixture",
            usage=ModelUsage(1, 1),
            actual_provider="fixture",
            audit=ModelCallAudit(
                scope=request.scope,
                alias=request.alias,
                operation=ModelOperation.CHAT,
                prompt_digest=request.prompt_digest,
                schema_digest=None,
                context_digest=request.prompt_digest,
                idempotency_key=request.idempotency_key,
                actual_provider="fixture",
                actual_model="fixture",
                usage=ModelUsage(1, 1),
            ),
            tool_calls=tools,
        )


@pytest.mark.asyncio
async def test_direct_answer_uses_one_model_call_and_freezes_route():
    gateway = ScriptedGateway(
        [
            (
                json.dumps(
                    {
                        "route": {
                            "kind": "native",
                            "reason": "question",
                            "confidence": 0.9,
                            "required_capabilities": [],
                        },
                        "answer": "Hello",
                    }
                ),
                (),
            )
        ]
    )
    graph = NativeWorkflowGraph(gateway, checkpointer=MemorySaver())
    result = await graph.run(VALIDATION_SCOPE, "task-1", "node-root", "Say hello", "qwen-plus")
    assert result["result"]["answer"] == "Hello"
    assert result["route_decision"]["kind"] == "native"
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_two_delegations_return_waiting_without_root_model_recall():
    def arguments(agent):
        return json.dumps(
            {
                "agentId": agent,
                "objective": "review",
                "inputRefs": [],
                "expectedOutput": "findings",
                "dependsOn": [],
            }
        )

    gateway = ScriptedGateway(
        [
            (
                json.dumps(
                    {
                        "route": {
                            "kind": "native",
                            "reason": "review",
                            "confidence": 0.9,
                            "required_capabilities": [],
                        }
                    }
                ),
                (
                    ModelToolCall("call-a", "workflow_delegate", arguments("ba")),
                    ModelToolCall("call-b", "workflow_delegate", arguments("qae")),
                ),
            )
        ]
    )
    delegated = []

    async def delegate(request, call_id):
        delegated.append((call_id, request.agent_id))
        return f"node-{request.agent_id}"

    graph = NativeWorkflowGraph(gateway, checkpointer=MemorySaver(), delegate=delegate)
    result = await graph.run(VALIDATION_SCOPE, "task-2", "node-root", "Review", "qwen-plus")
    assert result["waiting_reason"] == "children"
    assert delegated == [("call-a", "ba"), ("call-b", "qae")]
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_root_resumes_after_children_with_only_summaries_and_same_route():
    route = {
        "route": {
            "kind": "native",
            "reason": "review",
            "confidence": 0.9,
            "required_capabilities": [],
        }
    }
    gateway = ScriptedGateway(
        [
            (
                json.dumps(route),
                (
                    ModelToolCall(
                        "call-a",
                        "workflow_delegate",
                        json.dumps(
                            {
                                "agentId": "ba",
                                "objective": "rules",
                                "inputRefs": [],
                                "expectedOutput": "summary",
                                "dependsOn": [],
                            }
                        ),
                    ),
                    ModelToolCall(
                        "call-b",
                        "workflow_delegate",
                        json.dumps(
                            {
                                "agentId": "qae",
                                "objective": "tests",
                                "inputRefs": [],
                                "expectedOutput": "summary",
                                "dependsOn": [],
                            }
                        ),
                    ),
                ),
            ),
            ("Combined answer", ()),
        ]
    )

    async def delegate(request, call_id):
        return f"node-{request.agent_id}"

    graph = NativeWorkflowGraph(gateway, checkpointer=MemorySaver(), delegate=delegate)
    first = await graph.run(VALIDATION_SCOPE, "task-resume", "root", "Review", "qwen-plus")
    assert first["waiting_reason"] == "children"
    second = await graph.run(
        VALIDATION_SCOPE,
        "task-resume",
        "root",
        "Review",
        "qwen-plus",
        child_results=(
            {"call_id": "call-a", "summary": "Rules found"},
            {"call_id": "call-b", "summary": "Tests found"},
        ),
    )
    assert second["result"]["answer"] == "Combined answer"
    assert len(gateway.calls) == 2
    assert "Rules found" in [m.content for m in gateway.calls[1].messages]
    assert "Tests found" in [m.content for m in gateway.calls[1].messages]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["coding", "execute"])
async def test_unavailable_route_is_frozen_and_returns_honest_response(kind):
    gateway = ScriptedGateway(
        [
            (
                json.dumps(
                    {
                        "route": {
                            "kind": kind,
                            "reason": "needs capability",
                            "confidence": 0.9,
                            "required_capabilities": ["code.write"]
                            if kind == "coding"
                            else ["artifact.execute"],
                        },
                        "answer": "I did it",
                    }
                ),
                (),
            )
        ]
    )
    committed = []

    async def commit(decision):
        committed.append(decision)

    graph = NativeWorkflowGraph(gateway, checkpointer=MemorySaver(), route_commit=commit)
    result = await graph.run(VALIDATION_SCOPE, f"task-{kind}", "root", "Do it", "qwen-plus")
    assert result["route_decision"]["kind"] == kind
    assert result["result"]["outcome"] == "unavailable"
    assert "不可用" in result["result"]["answer"]
    assert len(committed) == 1


@pytest.mark.asyncio
async def test_steer_received_during_node_run_changes_next_model_input():
    gateway = ScriptedGateway(
        [
            (
                json.dumps(
                    {
                        "route": {
                            "kind": "native",
                            "reason": "research",
                            "confidence": 0.9,
                            "required_capabilities": [],
                        }
                    }
                ),
                (ModelToolCall("search-1", "knowledge.search", json.dumps({"query": "login"})),),
            ),
            ("Login-only answer", ()),
        ]
    )
    checks = 0

    async def before_model():
        nonlocal checks
        checks += 1
        return "只处理登录场景" if checks == 2 else ""

    async def ordinary_tool(name, arguments, call_id):
        return "Search result"

    graph = NativeWorkflowGraph(
        gateway,
        checkpointer=MemorySaver(),
        ordinary_tool=ordinary_tool,
        before_model=before_model,
    )
    result = await graph.run(
        VALIDATION_SCOPE, "task-steer", "specialist", "Review flows", "qwen-plus"
    )
    assert result["result"]["answer"] == "Login-only answer"
    assert len(gateway.calls) == 2
    assert "只处理登录场景" in gateway.calls[1].context
    assert "只处理登录场景" not in gateway.calls[0].context


@pytest.mark.asyncio
async def test_frozen_specialist_configuration_limits_actual_model_tools():
    gateway = ScriptedGateway([("Answer", ())])
    graph = NativeWorkflowGraph(
        gateway,
        checkpointer=MemorySaver(),
        is_root=False,
        agent_instruction="Only review login flows",
        skill_instructions=("Cite the source",),
        allowed_tool_ids=("knowledge.search",),
        governance_digests=("sha256:" + "a" * 64,),
    )
    await graph.run(VALIDATION_SCOPE, "task-config", "specialist", "Review login", "qwen-plus")
    request = gateway.calls[0]
    assert "Only review login flows" in request.prompt
    assert "Cite the source" in request.prompt
    assert "route object" not in request.prompt
    assert {tool.tool_id for tool in request.tools} == {"knowledge.search"}
    assert request.tool_allowlist == frozenset({"knowledge.search", "knowledge.answer"})


@pytest.mark.asyncio
async def test_specialist_rejects_tool_not_in_frozen_allowlist():
    gateway = ScriptedGateway(
        [
            (
                "Answer",
                (
                    ModelToolCall(
                        "bad",
                        "workflow_delegate",
                        json.dumps(
                            {"agentId": "ba", "objective": "wrong", "expectedOutput": "bad"}
                        ),
                    ),
                ),
            )
        ]
    )
    graph = NativeWorkflowGraph(
        gateway,
        checkpointer=MemorySaver(),
        is_root=False,
        agent_instruction="Review only",
        allowed_tool_ids=("knowledge.search",),
    )
    with pytest.raises(ValueError, match="not allowed"):
        await graph.run(VALIDATION_SCOPE, "task-forbidden", "specialist", "Review", "qwen-plus")


@pytest.mark.asyncio
async def test_low_confidence_capability_route_waits_with_visible_question():
    gateway = ScriptedGateway(
        [
            (
                json.dumps(
                    {
                        "route": {
                            "kind": "coding",
                            "reason": "unclear scope",
                            "confidence": 0.3,
                            "required_capabilities": ["code.write"],
                        }
                    }
                ),
                (),
            )
        ]
    )
    graph = NativeWorkflowGraph(gateway, checkpointer=MemorySaver())
    result = await graph.run(
        VALIDATION_SCOPE, "task-clarify", "root", "Could you fix it?", "qwen-plus"
    )
    assert result["waiting_reason"] == "clarification"
    assert "确认" in result["answer"]
    assert "result" not in result


@pytest.mark.asyncio
async def test_native_skill_activation_uses_frozen_revision_and_next_prompt():
    from tap.modules.ai.domain.skill_activation import SkillCatalogEntry

    entry = SkillCatalogEntry(
        skill_id="login-review",
        revision_id="skill-rev-1",
        name="Login Review",
        description="Checks login flows",
        category="testing",
    )
    loaded = []

    async def activate(name, prior):
        loaded.append((name, tuple(prior)))
        return {
            "skillId": "login-review",
            "revisionId": "skill-rev-1",
            "name": name,
            "instruction": "CHECK LOGIN EDGE CASES",
            "bytes": 22,
        }

    gateway = ScriptedGateway(
        [
            (
                json.dumps(
                    {
                        "route": {
                            "kind": "native",
                            "reason": "review",
                            "confidence": 0.9,
                            "required_capabilities": [],
                        }
                    }
                ),
                (
                    ModelToolCall(
                        "skill-call", "skill_activate", json.dumps({"name": "Login Review"})
                    ),
                ),
            ),
            ("Login review complete", ()),
        ]
    )
    graph = NativeWorkflowGraph(
        gateway, checkpointer=MemorySaver(), skill_catalog=(entry,), skill_activation=activate
    )
    result = await graph.run(VALIDATION_SCOPE, "task-skill", "root", "Review login", "qwen-plus")
    assert result["result"]["answer"] == "Login review complete"
    assert loaded == [("Login Review", ())]
    assert "CHECK LOGIN EDGE CASES" in gateway.calls[1].prompt
    assert "skill_activate" in [tool.wire_name for tool in gateway.calls[0].tools]
