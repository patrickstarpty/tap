from __future__ import annotations

from dataclasses import replace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.assets import (
    AiAgentRevision,
    AssetRevisionRejected,
    AssetRevisionStatus,
    SkillRevision,
    text_digest,
)
from tap.modules.ai.domain.models import ModelCapability, ModelDescriptor


def skill_revision(revision_id: str, *, status=AssetRevisionStatus.ENABLED) -> SkillRevision:
    return SkillRevision(
        revision_id=revision_id,
        asset_id=f"{revision_id}-asset",
        display_name=revision_id,
        scope=VALIDATION_SCOPE,
        content_digest=text_digest(revision_id),
        instruction_template_digest=text_digest(f"{revision_id} instruction"),
        applicable_tasks=frozenset({"knowledge.answer"}),
        status=status,
        instruction_template=f"{revision_id} instruction",
    )


class MemoryAgentStore:
    """In-memory authoring store used by unit and contract tests."""

    def __init__(self, skills: tuple[SkillRevision, ...] = ()) -> None:
        self.skills = {item.revision_id: item for item in skills}
        self.agents: dict[str, AiAgentRevision] = {}
        self.numbers: dict[str, int] = {}

    async def get_skill(self, scope, revision_id):
        value = self.skills.get(revision_id)
        if value is None or value.status is not AssetRevisionStatus.ENABLED:
            raise AssetRevisionRejected()
        return value

    async def latest_agent_revision(self, scope, asset_id):
        candidates = [item for item in self.agents.values() if item.asset_id == asset_id]
        if not candidates:
            raise AssetRevisionRejected()
        return max(candidates, key=lambda item: self.numbers[item.revision_id])

    async def insert_agent_revision(self, scope, revision):
        siblings = [item for item in self.agents.values() if item.asset_id == revision.asset_id]
        latest = (
            max(siblings, key=lambda item: self.numbers[item.revision_id]) if siblings else None
        )
        if (None if latest is None else latest.revision_id) != revision.adopted_from_revision_id:
            raise AssetRevisionRejected()
        self.agents[revision.revision_id] = revision
        self.numbers[revision.revision_id] = len(siblings) + 1
        return revision

    async def set_agent_status(self, scope, revision_id, status):
        if revision_id not in self.agents:
            raise AssetRevisionRejected()
        self.agents[revision_id] = replace(self.agents[revision_id], status=status)
        return self.agents[revision_id]

    async def get_agent(self, scope, revision_id):
        value = self.agents.get(revision_id)
        if value is None or value.status is not AssetRevisionStatus.ENABLED:
            raise AssetRevisionRejected()
        return value


class Models:
    scope = VALIDATION_SCOPE

    async def list_models(self, scope):
        return (
            ModelDescriptor(
                "qwen-plus",
                "Qwen Plus",
                frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED}),
            ),
            ModelDescriptor("qwen-max", "Qwen Max", frozenset({ModelCapability.STRUCTURED})),
            ModelDescriptor("qwen-flash", "Qwen Flash", frozenset({ModelCapability.CHAT})),
        )


def authoring_service(store: MemoryAgentStore):
    from tap.modules.ai.application.agent_authoring import AgentAuthoringService
    from tap.modules.ai.application.tool_registry import ToolRegistry, builtin_tools

    return AgentAuthoringService(store, ToolRegistry(builtin_tools()), Models())


def draft(**overrides):
    from tap.modules.ai.application.agent_authoring import AgentDraft

    values = {
        "display_name": "Release agent",
        "description": "Answers release questions.",
        "system_instruction": "Answer from release notes only.",
        "skill_revision_ids": ("skill-a",),
        "tool_allowlist": frozenset({"knowledge.search"}),
        "model_alias": "qwen-plus",
    }
    values.update(overrides)
    return AgentDraft(**values)


@pytest.mark.asyncio
async def test_create_builds_an_enabled_editor_revision_with_the_default_answer_schema() -> None:
    from tap.modules.ai.application.assets import VALIDATION_OUTPUT_SCHEMA
    from tap.modules.ai.domain.models import schema_digest

    store = MemoryAgentStore((skill_revision("skill-a"), skill_revision("skill-b")))
    service = authoring_service(store)

    created = await service.create(
        VALIDATION_SCOPE, draft(skill_revision_ids=("skill-b", "skill-a"))
    )

    assert created.status is AssetRevisionStatus.ENABLED
    assert created.origin == "editor"
    assert created.adopted_from_revision_id is None
    assert created.display_name == "Release agent"
    assert created.description == "Answers release questions."
    assert created.system_instruction == "Answer from release notes only."
    assert created.skill_revision_ids == ("skill-b", "skill-a")
    assert created.model_alias == "qwen-plus"
    # The governed answer capability is always granted; it is never a callable tool.
    assert created.tool_allowlist == frozenset({"knowledge.search", "knowledge.answer"})
    assert created.output_schema_digest == schema_digest(VALIDATION_OUTPUT_SCHEMA)
    assert created.output_schema_json is not None
    assert store.agents == {created.revision_id: created}


@pytest.mark.asyncio
async def test_revise_increments_the_revision_and_adopts_the_latest_one() -> None:
    store = MemoryAgentStore((skill_revision("skill-a"),))
    service = authoring_service(store)

    first = await service.create(VALIDATION_SCOPE, draft())
    second = await service.revise(
        VALIDATION_SCOPE, first.asset_id, draft(system_instruction="Be brief.", model_alias=None)
    )
    third = await service.revise(VALIDATION_SCOPE, first.asset_id, draft(skill_revision_ids=()))

    assert second.asset_id == first.asset_id == third.asset_id
    assert second.revision_id not in {first.revision_id, third.revision_id}
    assert second.adopted_from_revision_id == first.revision_id
    assert third.adopted_from_revision_id == second.revision_id
    assert [store.numbers[item.revision_id] for item in (first, second, third)] == [1, 2, 3]
    assert second.model_alias is None
    assert second.content_digest != first.content_digest
    assert store.agents[first.revision_id] == first
    with pytest.raises(AssetRevisionRejected):
        await service.revise(VALIDATION_SCOPE, "missing-agent", draft())


@pytest.mark.asyncio
async def test_set_status_disables_and_reenables_a_revision() -> None:
    store = MemoryAgentStore((skill_revision("skill-a"),))
    service = authoring_service(store)
    created = await service.create(VALIDATION_SCOPE, draft())

    disabled = await service.set_status(
        VALIDATION_SCOPE, created.revision_id, AssetRevisionStatus.DISABLED
    )
    assert disabled.status is AssetRevisionStatus.DISABLED
    enabled = await service.set_status(
        VALIDATION_SCOPE, created.revision_id, AssetRevisionStatus.ENABLED
    )
    assert enabled.status is AssetRevisionStatus.ENABLED
    with pytest.raises(AssetRevisionRejected):
        await service.set_status(VALIDATION_SCOPE, "missing", AssetRevisionStatus.DISABLED)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"display_name": " "}, "agent-name-invalid"),
        ({"display_name": "x" * 129}, "agent-name-invalid"),
        ({"description": "x" * 1025}, "agent-description-invalid"),
        ({"system_instruction": ""}, "agent-instruction-invalid"),
        ({"system_instruction": "x" * 16001}, "agent-instruction-invalid"),
        ({"skill_revision_ids": ("missing",)}, "agent-skill-unavailable"),
        ({"skill_revision_ids": ("skill-off",)}, "agent-skill-unavailable"),
        ({"skill_revision_ids": ("skill-a", "skill-a")}, "agent-skills-invalid"),
        ({"skill_revision_ids": tuple(f"s{i}" for i in range(17))}, "agent-skills-invalid"),
        ({"tool_allowlist": frozenset({"jira.search"})}, "agent-tool-unavailable"),
        ({"tool_allowlist": frozenset({"Bad Tool"})}, "agent-tool-unavailable"),
        (
            {"tool_allowlist": frozenset(f"x.t{i}" for i in range(32))},
            "agent-tool-unavailable",
        ),
        ({"model_alias": "missing-model"}, "agent-model-unsupported"),
        ({"model_alias": "qwen-flash"}, "agent-model-unsupported"),
    ],
)
async def test_create_rejects_invalid_drafts_with_specific_codes(overrides, code) -> None:
    from tap.modules.ai.application.agent_authoring import AgentAuthoringRejected

    store = MemoryAgentStore(
        (
            skill_revision("skill-a"),
            skill_revision("skill-off", status=AssetRevisionStatus.DISABLED),
        )
    )
    service = authoring_service(store)

    with pytest.raises(AgentAuthoringRejected) as raised:
        await service.create(VALIDATION_SCOPE, draft(**overrides))

    assert raised.value.code == code
    assert store.agents == {}


def test_domain_bounds_authored_agent_fields() -> None:
    from tap.modules.ai.application.assets import VALIDATION_OUTPUT_SCHEMA
    from tap.modules.ai.domain.models import schema_digest

    base = AiAgentRevision(
        revision_id="agent-1",
        asset_id="agent",
        display_name="Agent",
        scope=VALIDATION_SCOPE,
        content_digest=text_digest("agent"),
        system_instruction_digest=text_digest("x"),
        tool_allowlist=frozenset({"knowledge.answer"}),
        output_schema_digest=schema_digest(VALIDATION_OUTPUT_SCHEMA),
        model_alias="qwen-plus",
        description="ok",
        skill_revision_ids=("skill-a",),
        origin="editor",
    )
    assert base.skill_revision_ids == ("skill-a",)
    for overrides in (
        {"skill_revision_ids": ["skill-a"]},
        {"skill_revision_ids": ("skill-a", "skill-a")},
        {"skill_revision_ids": tuple(f"s{i}" for i in range(17))},
        {"model_alias": ""},
        {"description": "x" * 1025},
        {"origin": "import"},
        {
            "system_instruction": "x" * 16001,
            "system_instruction_digest": text_digest("x" * 16001),
        },
    ):
        with pytest.raises(AssetRevisionRejected):
            replace(base, **overrides)
