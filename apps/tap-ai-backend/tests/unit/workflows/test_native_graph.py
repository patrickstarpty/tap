import json
from copy import deepcopy

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
async def test_explicit_specialists_survive_tool_round_before_final_answer():
    gateway = ScriptedGateway(
        [
            (
                json.dumps(
                    {
                        "route": {"kind": "native", "reason": "review", "confidence": 1.0},
                        "answer": "",
                    }
                ),
                (ModelToolCall("search-1", "knowledge_search", json.dumps({"query": "check"})),),
            ),
            ("Final after search", ()),
        ]
    )
    delegated = []

    async def delegate(request, call_id):
        delegated.append((request.agent_id, call_id))
        return f"node-{request.agent_id}"

    async def ordinary_tool(name, arguments, call_id):
        return "search result"

    graph = NativeWorkflowGraph(
        gateway,
        checkpointer=MemorySaver(),
        delegate=delegate,
        ordinary_tool=ordinary_tool,
        allowed_tool_ids=("knowledge.search",),
    )
    result = await graph.run(
        VALIDATION_SCOPE,
        "task-explicit-tools",
        "root",
        "Review",
        "qwen-plus",
        requested_ids=("ba", "qae"),
    )
    assert result["waiting_reason"] == "children"
    assert [item[0] for item in delegated] == ["ba", "qae"]
    assert "ba" in gateway.calls[0].prompt and "qae" in gateway.calls[0].prompt


@pytest.mark.asyncio
async def test_partial_explicit_delegation_resumes_only_missing_specialist():
    gateway = ScriptedGateway(
        [
            (
                json.dumps({"route": {"kind": "native", "reason": "review", "confidence": 1.0}}),
                (
                    ModelToolCall(
                        "manual-ba",
                        "workflow_delegate",
                        json.dumps(
                            {
                                "agentId": "ba",
                                "objective": "review",
                                "inputRefs": [],
                                "expectedOutput": "findings",
                                "dependsOn": [],
                            }
                        ),
                    ),
                ),
            ),
            ("Final", ()),
        ]
    )
    delegated = []

    async def delegate(request, call_id):
        delegated.append((request.agent_id, call_id))
        return f"node-{request.agent_id}"

    graph = NativeWorkflowGraph(gateway, checkpointer=MemorySaver(), delegate=delegate)
    first = await graph.run(
        VALIDATION_SCOPE,
        "task-explicit-partial",
        "root",
        "Review",
        "qwen-plus",
        requested_ids=("ba", "qae"),
    )
    assert first["waiting_reason"] == "children"
    second = await graph.run(
        VALIDATION_SCOPE,
        "task-explicit-partial",
        "root",
        "Review",
        "qwen-plus",
        requested_ids=("ba", "qae"),
        child_results=({"call_id": "manual-ba", "summary": "BA complete"},),
    )
    assert second["waiting_reason"] == "children"
    assert [item[0] for item in delegated] == ["ba", "qae"]


@pytest.mark.asyncio
async def test_legacy_checkpoint_recovers_completed_manual_selection_without_redelegating():
    class PreFieldCheckpoint(MemorySaver):
        legacy_replay = False

        async def aget_tuple(self, config):
            entry = await super().aget_tuple(config)
            if entry is None or not self.legacy_replay:
                return entry
            checkpoint = deepcopy(entry.checkpoint)
            checkpoint["channel_values"].pop("fulfilled_requested_ids", None)
            return entry._replace(checkpoint=checkpoint)

    gateway = ScriptedGateway(
        [
            (
                json.dumps({"route": {"kind": "native", "reason": "review", "confidence": 1.0}}),
                (
                    ModelToolCall(
                        "manual-ba",
                        "workflow_delegate",
                        json.dumps(
                            {
                                "agentId": "ba",
                                "objective": "review",
                                "inputRefs": [],
                                "expectedOutput": "findings",
                                "dependsOn": [],
                            }
                        ),
                    ),
                ),
            ),
            ("Final", ()),
        ]
    )
    delegated = []

    async def delegate(request, call_id):
        delegated.append((request.agent_id, call_id))
        return f"node-{request.agent_id}"

    checkpoint = PreFieldCheckpoint()
    graph = NativeWorkflowGraph(gateway, checkpointer=checkpoint, delegate=delegate)
    first = await graph.run(
        VALIDATION_SCOPE,
        "task-pre-field",
        "root",
        "Review",
        "qwen-plus",
        requested_ids=("ba", "qae"),
    )
    assert first["waiting_reason"] == "children"
    checkpoint.legacy_replay = True
    second = await graph.run(
        VALIDATION_SCOPE,
        "task-pre-field",
        "root",
        "Review",
        "qwen-plus",
        requested_ids=("ba", "qae"),
        child_results=({"call_id": "manual-ba", "summary": "BA complete"},),
    )
    assert second["waiting_reason"] == "children"
    assert delegated == [("ba", "manual-ba"), ("qae", "requested:qae")]


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
        model_timeout_seconds=3,
    )
    await graph.run(VALIDATION_SCOPE, "task-config", "specialist", "Review login", "qwen-plus")
    request = gateway.calls[0]
    assert "Only review login flows" in request.prompt
    assert "Cite the source" in request.prompt
    assert "route object" not in request.prompt
    assert {tool.tool_id for tool in request.tools} == {"knowledge.search"}
    assert request.tool_allowlist == frozenset({"knowledge.search", "knowledge.answer"})
    assert request.timeout_seconds == 3


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

    async def activate(name, prior, call_id):
        loaded.append((name, tuple(prior), call_id))
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
    assert loaded == [("Login Review", (), "skill-call")]
    assert "CHECK LOGIN EDGE CASES" in gateway.calls[1].prompt
    assert "skill_activate" in [tool.wire_name for tool in gateway.calls[0].tools]


@pytest.mark.asyncio
async def test_pending_tool_checkpoint_resumes_before_new_model_input():
    saver = MemorySaver()
    gateway = ScriptedGateway(
        [
            (
                '{"answer": ""}',
                (ModelToolCall("tool-1", "knowledge_search", json.dumps({"query": "login"})),),
            ),
            ("Answered from evidence", ()),
        ]
    )
    attempts = []

    async def ordinary_tool(name, arguments, call_id):
        attempts.append(call_id)
        if len(attempts) == 1:
            raise RuntimeError("simulated interruption at pending tool")
        return "Evidence"

    graph = NativeWorkflowGraph(
        gateway, checkpointer=saver, ordinary_tool=ordinary_tool, is_root=False
    )
    with pytest.raises(RuntimeError, match="simulated interruption"):
        await graph.run(
            VALIDATION_SCOPE, "task-pending", "node-pending", "Review login", "qwen-plus"
        )
    result = await NativeWorkflowGraph(
        gateway, checkpointer=saver, ordinary_tool=ordinary_tool, is_root=False
    ).run(VALIDATION_SCOPE, "task-pending", "node-pending", "Review login", "qwen-plus")
    assert result["result"]["answer"] == "Answered from evidence"
    assert attempts == ["tool-1", "tool-1"]
    assert len(gateway.calls) == 2
    assert any(
        message.role == "tool"
        and message.tool_call_id == "tool-1"
        and message.content == "Evidence"
        for message in gateway.calls[1].messages
    )


def test_long_child_result_is_private_and_parent_receives_bounded_reference():
    import hashlib
    from types import SimpleNamespace

    from tap.modules.workflows.application.dispatch import NativeWorkflowRuntime
    from tap.modules.workflows.domain.delegation import child_result_summary

    full = "Very detailed specialist result. " * 200
    persisted_summary = child_result_summary("child", full)
    events = (
        SimpleNamespace(
            kind="node.delegated",
            payload={"parent_node_id": "root", "node_id": "child", "call_id": "call-child"},
        ),
        SimpleNamespace(
            kind="node.finished",
            payload={"node_id": "child", "summary": persisted_summary, "private_result": full},
        ),
    )
    parent = NativeWorkflowRuntime._child_results(events, "root")
    assert parent[0]["call_id"] == "call-child"
    assert len(parent[0]["summary"]) < 600
    assert full not in parent[0]["summary"]
    assert parent[0]["summary"] == persisted_summary
    assert f"sha256:{hashlib.sha256(full.encode()).hexdigest()[:16]}" in parent[0]["summary"]


@pytest.mark.asyncio
async def test_browser_goal_routes_without_selected_agent_or_manual_mode():
    gateway = ScriptedGateway(
        [
            (
                json.dumps(
                    {
                        "route": {
                            "kind": "browser",
                            "reason": "quote requires real page",
                            "confidence": 0.95,
                            "required_capabilities": ["browser"],
                            "start_url": "https://example.test/quote",
                        }
                    }
                ),
                (),
            )
        ]
    )
    graph = NativeWorkflowGraph(gateway, checkpointer=MemorySaver(), browser_available=True)
    result = await graph.run(
        VALIDATION_SCOPE, "auto-browser", "root", "Quote at https://example.test/quote", "qwen-plus"
    )
    assert result["result"]["outcome"] == "browser_handoff"
    assert result["route_decision"]["start_url"] == "https://example.test/quote"
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_browser_route_does_not_accept_an_invented_destination():
    gateway = ScriptedGateway(
        [
            (
                json.dumps(
                    {
                        "route": {
                            "kind": "browser",
                            "reason": "quote",
                            "confidence": 0.95,
                            "required_capabilities": ["browser"],
                            "start_url": "https://unrequested.test/",
                        }
                    }
                ),
                (),
            )
        ]
    )
    graph = NativeWorkflowGraph(gateway, checkpointer=MemorySaver(), browser_available=True)
    result = await graph.run(
        VALIDATION_SCOPE, "auto-missing", "root", "Give me a quote", "qwen-plus"
    )
    assert result["waiting_reason"] == "clarification"
    assert "route_decision" not in result
