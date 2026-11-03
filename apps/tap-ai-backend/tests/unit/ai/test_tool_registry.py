from __future__ import annotations

from collections.abc import Mapping

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.application.tool_registry import ToolRegistry, builtin_tools
from tap.modules.ai.domain.tools import (
    RESERVED_TOOLS,
    ToolDescriptor,
    ToolEffect,
    ToolExecutor,
    ToolOrigin,
    ToolRejected,
    ToolResult,
    validate_allowlist,
    validate_tool_id,
)


def _descriptor(
    tool_id: str,
    *,
    effect: ToolEffect = ToolEffect.READ,
    origin: ToolOrigin = ToolOrigin.BUILTIN,
    server_id: str | None = None,
    enabled: bool = True,
) -> ToolDescriptor:
    return ToolDescriptor(
        tool_id=tool_id,
        display_name=tool_id,
        description=f"{tool_id} tool",
        input_schema={"type": "object", "properties": {}},
        effect=effect,
        origin=origin,
        server_id=server_id,
        enabled=enabled,
    )


class _Echo:
    async def invoke(
        self, scope: ProjectScopeContext, arguments: Mapping[str, object]
    ) -> ToolResult:
        return ToolResult(content=str(dict(arguments)))


class _FakeMcp:
    def __init__(self, descriptors: tuple[ToolDescriptor, ...]) -> None:
        self._descriptors = descriptors

    async def descriptors(self, scope: ProjectScopeContext) -> tuple[ToolDescriptor, ...]:
        return self._descriptors

    async def executor(self, scope: ProjectScopeContext, tool_id: str) -> ToolExecutor:
        return _Echo()


@pytest.mark.parametrize(
    "value", ["knowledge.search", "mcp.jira.search_issues", "graph.neighbors", "mcp.a-b.c-d"]
)
def test_tool_ids_accept_dotted_lowercase_names(value: str) -> None:
    assert validate_tool_id(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "Knowledge.search",
        "knowledge..search",
        "knowledge.",
        ".search",
        "knowledge",
        "a.b.c.d.e",
        "knowledge.search " + "x",
        "https://untrusted.example/tool",
        "k." + "x" * 127,
        "",
        1,
    ],
)
def test_tool_ids_reject_malformed_names(value: object) -> None:
    with pytest.raises(ToolRejected):
        validate_tool_id(value)  # type: ignore[arg-type]


def test_allowlist_is_bounded_to_32_tools() -> None:
    values = [f"mcp.server.tool_{index}" for index in range(33)]
    assert validate_allowlist(values[:32]) == frozenset(values[:32])
    with pytest.raises(ToolRejected):
        validate_allowlist(values)
    with pytest.raises(ToolRejected):
        validate_allowlist(["knowledge.search", "Bash"])


def test_reserved_answer_capability_is_not_callable() -> None:
    tools = builtin_tools()
    assert RESERVED_TOOLS == frozenset({"knowledge.answer"})
    answer, answer_executor = tools["knowledge.answer"]
    search, _ = tools["knowledge.search"]
    assert answer.callable is False and answer_executor is None
    assert search.callable is True and search.effect is ToolEffect.READ
    properties = search.input_schema["properties"]
    assert isinstance(properties, dict)
    assert properties["query"] == {"type": "string", "minLength": 1, "maxLength": 500}
    assert properties["limit"] == {"type": "integer", "minimum": 1, "maximum": 8}


def test_descriptor_rejects_inconsistent_origin_and_unbounded_schema() -> None:
    with pytest.raises(ToolRejected):
        _descriptor("mcp.jira.search", origin=ToolOrigin.MCP)
    with pytest.raises(ToolRejected):
        _descriptor("knowledge.search", origin=ToolOrigin.MCP, server_id="server")
    with pytest.raises(ToolRejected):
        ToolDescriptor(
            tool_id="knowledge.search",
            display_name="Search",
            description="x" * 1025,
            input_schema={"type": "object"},
            effect=ToolEffect.READ,
            origin=ToolOrigin.BUILTIN,
        )
    with pytest.raises(ToolRejected):
        ToolDescriptor(
            tool_id="knowledge.search",
            display_name="Search",
            description="Search",
            input_schema={"type": "string"},
            effect=ToolEffect.READ,
            origin=ToolOrigin.BUILTIN,
        )


@pytest.mark.asyncio
async def test_registry_resolves_builtins_and_mcp_tools() -> None:
    mcp = _FakeMcp(
        (
            _descriptor("mcp.jira.search_issues", origin=ToolOrigin.MCP, server_id="jira-server"),
            _descriptor(
                "mcp.jira.create_issue",
                origin=ToolOrigin.MCP,
                server_id="jira-server",
                effect=ToolEffect.EXTERNAL,
            ),
            _descriptor(
                "mcp.jira.disabled", origin=ToolOrigin.MCP, server_id="jira-server", enabled=False
            ),
        )
    )
    registry = ToolRegistry(builtin_tools(), mcp)

    listed = {item.tool_id for item in await registry.list(VALIDATION_SCOPE)}
    assert {"knowledge.search", "knowledge.answer", "mcp.jira.search_issues"} <= listed
    assert (await registry.get(VALIDATION_SCOPE, "mcp.jira.search_issues")).server_id == (
        "jira-server"
    )
    assert await registry.require_allowlist(
        VALIDATION_SCOPE, ["knowledge.search", "knowledge.answer", "mcp.jira.create_issue"]
    ) == frozenset({"knowledge.search", "knowledge.answer", "mcp.jira.create_issue"})
    assert isinstance(await registry.executor(VALIDATION_SCOPE, "mcp.jira.search_issues"), _Echo)

    for rejected in (["mcp.jira.unknown"], ["mcp.jira.disabled"], ["Bash"]):
        with pytest.raises(ToolRejected):
            await registry.require_allowlist(VALIDATION_SCOPE, rejected)
    with pytest.raises(ToolRejected):
        await registry.require_allowlist(VALIDATION_SCOPE, ["mcp.jira.create_issue"], system=True)
    assert await registry.require_allowlist(
        VALIDATION_SCOPE, ["mcp.jira.search_issues"], system=True
    ) == frozenset({"mcp.jira.search_issues"})
    with pytest.raises(ToolRejected):
        await registry.get(VALIDATION_SCOPE, "mcp.jira.unknown")
    with pytest.raises(ToolRejected):
        await registry.executor(VALIDATION_SCOPE, "knowledge.answer")
    with pytest.raises(ToolRejected):
        await registry.executor(VALIDATION_SCOPE, "mcp.jira.disabled")


@pytest.mark.asyncio
async def test_registry_without_mcp_accepts_existing_revision_allowlist() -> None:
    from tap.modules.ai.application.assets import validation_asset_seed

    agent = validation_asset_seed(VALIDATION_SCOPE).agents[0]
    assert agent.tool_allowlist == frozenset({"knowledge.search", "knowledge.answer"})
    registry = ToolRegistry(builtin_tools())
    assert await registry.require_allowlist(VALIDATION_SCOPE, agent.tool_allowlist) == (
        agent.tool_allowlist
    )
    with pytest.raises(ToolRejected):
        await registry.require_allowlist(VALIDATION_SCOPE, ["mcp.jira.search_issues"])


def test_revisions_accept_well_formed_tools_outside_builtins() -> None:
    from tap.modules.ai.domain.assets import AiAgentRevision, AssetRevisionRejected, text_digest
    from tap.modules.ai.domain.models import GenerationGovernance, schema_digest
    from tap.modules.chat.domain.conversations import TurnInput

    def agent(tools: frozenset[str]) -> AiAgentRevision:
        return AiAgentRevision(
            revision_id="agent-revision",
            asset_id="agent",
            display_name="Agent",
            scope=VALIDATION_SCOPE,
            content_digest=text_digest("agent"),
            system_instruction_digest=text_digest("instruction"),
            tool_allowlist=tools,
            output_schema_digest=text_digest("schema"),
        )

    assert agent(frozenset({"mcp.jira.search_issues", "knowledge.answer"}))
    with pytest.raises(AssetRevisionRejected):
        agent(frozenset({"Bash"}))
    with pytest.raises(AssetRevisionRejected):
        agent(frozenset(f"mcp.server.tool_{index}" for index in range(33)))

    governance = {
        "model_alias": "chat",
        "system_instruction": "instruction",
        "system_instruction_digest": text_digest("instruction"),
        "skill_instructions": (),
        "skill_instruction_digests": (),
        "output_schema": {"type": "object"},
        "output_schema_digest": schema_digest({"type": "object"}),
        "revision_digests": (text_digest("agent"),),
    }
    GenerationGovernance(
        tool_allowlist=frozenset({"knowledge.answer", "mcp.jira.search_issues"}),
        **governance,  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError):
        GenerationGovernance(tool_allowlist=frozenset({"Bash"}), **governance)  # type: ignore[arg-type]

    turn = {"message": "hello", "actor_id": "actor", "identity_mode": "validation"}
    TurnInput(
        model_alias="chat",
        agent_tool_allowlist=("knowledge.answer", "mcp.jira.search_issues"),
        **turn,
    )
    with pytest.raises(ValueError):
        TurnInput(model_alias="chat", agent_tool_allowlist=("Bash",), **turn)
