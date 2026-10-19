from __future__ import annotations

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE


def test_registry_lists_built_in_tools() -> None:
    from tap.modules.ai.domain.tools import TOOL_REGISTRY, known_tool_ids

    assert known_tool_ids() == frozenset(
        {"knowledge.search", "knowledge.answer", "graph.relations", "insights.query"}
    )
    assert len(TOOL_REGISTRY) == 4
    required_tool_ids = {"knowledge.search", "knowledge.answer"}
    for tool_id, definition in TOOL_REGISTRY.items():
        assert definition.tool_id == tool_id
        assert definition.names["en"].strip()
        assert definition.names["zh"].strip()
        assert definition.descriptions["en"].strip()
        assert definition.descriptions["zh"].strip()
        assert definition.required is (tool_id in required_tool_ids)


def test_registry_has_four_tools_with_wire_names() -> None:
    from tap.modules.ai.domain.tools import TOOL_REGISTRY, required_tool_ids

    assert list(TOOL_REGISTRY) == [
        "knowledge.search",
        "knowledge.answer",
        "graph.relations",
        "insights.query",
    ]
    assert TOOL_REGISTRY["knowledge.search"].wire_name == "knowledge_search"
    assert TOOL_REGISTRY["knowledge.answer"].wire_name == "knowledge_answer"
    assert TOOL_REGISTRY["graph.relations"].wire_name == "graph_relations"
    assert TOOL_REGISTRY["insights.query"].wire_name == "insights_query"
    assert TOOL_REGISTRY["knowledge.answer"].model_callable is False
    assert required_tool_ids("knowledge.answer") == {"knowledge.search", "knowledge.answer"}


def test_visible_tools_respect_agent_allowlist_and_availability() -> None:
    from tap.modules.ai.domain.tools import visible_tool_ids

    assert visible_tool_ids(
        agent_allowlist=None, relations_available=True, insights_available=False
    ) == ("knowledge.search", "graph.relations")
    assert visible_tool_ids(
        agent_allowlist=frozenset({"knowledge.search", "knowledge.answer"}),
        relations_available=True,
        insights_available=True,
    ) == ("knowledge.search",)
    assert "graph.relations" not in visible_tool_ids(
        agent_allowlist=None, relations_available=False, insights_available=True
    )


def test_built_in_agent_v3_allowlist_includes_new_tools() -> None:
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.application.assets import validation_asset_seed

    seed = validation_asset_seed(VALIDATION_SCOPE)
    assert [agent.revision_id for agent in seed.agents] == [
        "validation-knowledge-agent-v2",
        "validation-knowledge-agent-v3",
    ]
    v2, v3 = seed.agents
    assert v3.tool_allowlist == frozenset(
        {"knowledge.search", "knowledge.answer", "graph.relations", "insights.query"}
    )
    assert v2.tool_allowlist == frozenset({"knowledge.search", "knowledge.answer"})
    from tap.modules.ai.domain.assets import text_digest

    assert v2.content_digest == text_digest("validation-ai-agent-v2")


def test_domain_rejects_unknown_tool() -> None:
    from tap.modules.ai.domain.assets import AiAgentRevision, text_digest

    with pytest.raises(ValueError):
        AiAgentRevision(
            revision_id="agent-revision",
            asset_id="agent",
            display_name="Agent",
            scope=VALIDATION_SCOPE,
            content_digest=text_digest("agent"),
            system_instruction_digest=text_digest("instruction"),
            tool_allowlist=frozenset({"web.fetch"}),
            output_schema_digest=text_digest("schema"),
        )


def test_turn_input_and_governance_use_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    from tap.modules.ai.domain import tools as tools_module
    from tap.modules.ai.domain.assets import AiAgentRevision, text_digest
    from tap.modules.ai.domain.models import GenerationGovernance, schema_digest
    from tap.modules.ai.domain.tools import ToolDefinition
    from tap.modules.chat.domain.conversations import TurnInput

    extra = dict(tools_module.TOOL_REGISTRY)
    extra["x.test"] = ToolDefinition(
        tool_id="x.test",
        wire_name="x_test",
        names={"en": "Test tool", "zh": "测试工具"},
        descriptions={"en": "Test only.", "zh": "仅用于测试。"},
        required=False,
        default_enabled=False,
        model_callable=True,
        input_schema={},
        tasks=frozenset({"knowledge.answer"}),
    )
    monkeypatch.setattr(tools_module, "TOOL_REGISTRY", extra)

    turn = TurnInput(
        message="hello",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode=VALIDATION_SCOPE.identity_mode.value,
        model_alias="qwen-plus",
        agent_tool_allowlist=("x.test",),
    )
    assert turn.agent_tool_allowlist == ("x.test",)

    schema = {"type": "object"}
    governance = GenerationGovernance(
        model_alias="qwen-plus",
        system_instruction="instruction",
        system_instruction_digest=text_digest("instruction"),
        skill_instructions=(),
        skill_instruction_digests=(),
        tool_allowlist=frozenset({"x.test"}),
        output_schema=schema,
        output_schema_digest=schema_digest(schema),
        revision_digests=(text_digest("rev"),),
    )
    assert governance.tool_allowlist == frozenset({"x.test"})

    revision = AiAgentRevision(
        revision_id="agent-revision",
        asset_id="agent",
        display_name="Agent",
        scope=VALIDATION_SCOPE,
        content_digest=text_digest("agent"),
        system_instruction_digest=text_digest("instruction"),
        tool_allowlist=frozenset({"x.test"}),
        output_schema_digest=text_digest("schema"),
    )
    assert revision.tool_allowlist == frozenset({"x.test"})
