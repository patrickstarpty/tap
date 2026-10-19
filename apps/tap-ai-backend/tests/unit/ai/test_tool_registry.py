from __future__ import annotations

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE


def test_registry_lists_built_in_tools() -> None:
    from tap.modules.ai.domain.tools import TOOL_REGISTRY, known_tool_ids

    assert known_tool_ids() == frozenset({"knowledge.search", "knowledge.answer"})
    assert len(TOOL_REGISTRY) == 2
    for tool_id, definition in TOOL_REGISTRY.items():
        assert definition.tool_id == tool_id
        assert definition.names["en"].strip()
        assert definition.names["zh"].strip()
        assert definition.descriptions["en"].strip()
        assert definition.descriptions["zh"].strip()
        assert definition.required is True


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
        names={"en": "Test tool", "zh": "测试工具"},
        descriptions={"en": "Test only.", "zh": "仅用于测试。"},
        required=False,
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
