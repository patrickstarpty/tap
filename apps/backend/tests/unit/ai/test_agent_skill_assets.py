from __future__ import annotations

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE


@pytest.mark.asyncio
async def test_catalog_rejects_unknown_disabled_cross_project_and_escalated_agent_selection() -> (
    None
):
    from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
    from tap.modules.ai.application.assets import ApprovedAssetCatalog
    from tap.modules.ai.domain.assets import (
        AiAgentRevision,
        AssetRevisionRejected,
        AssetRevisionStatus,
        SkillRevision,
        text_digest,
    )
    from tap.modules.ai.domain.models import schema_digest

    schema = {"type": "object"}

    agent = AiAgentRevision(
        revision_id="agent-revision",
        asset_id="knowledge-agent",
        display_name="Knowledge agent",
        scope=VALIDATION_SCOPE,
        content_digest=text_digest("agent-v1"),
        system_instruction_digest=text_digest("server-instruction"),
        tool_allowlist=frozenset({"knowledge.search"}),
        output_schema_digest=schema_digest(schema),
        status=AssetRevisionStatus.ENABLED,
        system_instruction="server-instruction",
        output_schema_json='{"type":"object"}',
    )
    disabled_skill = SkillRevision(
        revision_id="skill-revision",
        asset_id="citation-skill",
        display_name="Citation skill",
        scope=VALIDATION_SCOPE,
        content_digest=text_digest("skill-v1"),
        instruction_template_digest=text_digest("server-template"),
        applicable_tasks=frozenset({"knowledge.answer"}),
        status=AssetRevisionStatus.DISABLED,
    )
    catalog = ApprovedAssetCatalog(agents=(agent,), skills=(disabled_skill,))
    other = ProjectScopeContext(
        enterprise_id="local",
        project_id="other-project",
        actor_id="other-actor",
        identity_mode=IdentityMode.VALIDATION,
    )

    assert (
        await catalog.resolve_agent(
            VALIDATION_SCOPE,
            "agent-revision",
            tools=frozenset({"knowledge.search"}),
            output_schema_digest=schema_digest(schema),
        )
        == agent
    )
    for scope, revision_id, tools, digest in (
        (VALIDATION_SCOPE, "unknown", frozenset(), schema_digest(schema)),
        (other, "agent-revision", frozenset(), schema_digest(schema)),
        (
            VALIDATION_SCOPE,
            "agent-revision",
            frozenset({"knowledge.delete"}),
            schema_digest(schema),
        ),
        (VALIDATION_SCOPE, "agent-revision", frozenset(), text_digest("other-schema")),
    ):
        with pytest.raises(AssetRevisionRejected):
            await catalog.resolve_agent(
                scope, revision_id, tools=tools, output_schema_digest=digest
            )
    with pytest.raises(AssetRevisionRejected):
        await catalog.resolve_skill(VALIDATION_SCOPE, "skill-revision")
    assert (
        await catalog.resolve_historical_skill(VALIDATION_SCOPE, "skill-revision") == disabled_skill
    )


def test_revisions_are_immutable_and_reject_executable_or_unapproved_content() -> None:
    from dataclasses import FrozenInstanceError

    from tap.modules.ai.domain.assets import AiAgentRevision, AssetRevisionRejected, text_digest

    with pytest.raises(AssetRevisionRejected):
        AiAgentRevision(
            revision_id="agent-revision",
            asset_id="agent",
            display_name="Agent",
            scope=VALIDATION_SCOPE,
            content_digest=text_digest("agent"),
            system_instruction_digest=text_digest("instruction"),
            tool_allowlist=frozenset({"https://untrusted.example/tool"}),
            output_schema_digest=text_digest("schema"),
        )
    revision = AiAgentRevision(
        revision_id="agent-revision",
        asset_id="agent",
        display_name="Agent",
        scope=VALIDATION_SCOPE,
        content_digest=text_digest("agent"),
        system_instruction_digest=text_digest("instruction"),
        tool_allowlist=frozenset({"knowledge.search"}),
        output_schema_digest=text_digest("schema"),
    )
    with pytest.raises(FrozenInstanceError):
        revision.content_digest = text_digest("changed")  # type: ignore[misc]


def test_revisions_reject_mutable_collections_and_adoption_self_links() -> None:
    from tap.modules.ai.domain.assets import AiAgentRevision, AssetRevisionRejected, text_digest

    for tools, adopted_from_revision_id in (
        ({"knowledge.search"}, None),
        (frozenset({"knowledge.search"}), "agent-v1"),
    ):
        with pytest.raises(AssetRevisionRejected):
            AiAgentRevision(
                revision_id="agent-v1",
                asset_id="agent",
                display_name="Agent",
                scope=VALIDATION_SCOPE,
                content_digest=text_digest("agent"),
                system_instruction_digest=text_digest("instruction"),
                tool_allowlist=tools,  # type: ignore[arg-type]
                output_schema_digest=text_digest("schema"),
                adopted_from_revision_id=adopted_from_revision_id,
            )


def test_validation_revisions_carry_digest_bound_execution_content() -> None:
    import json

    from tap.modules.ai.application.assets import validation_asset_seed
    from tap.modules.ai.domain.assets import text_digest
    from tap.modules.ai.domain.models import schema_digest

    seed = validation_asset_seed(VALIDATION_SCOPE)
    agent = seed.agents[0]
    skill = seed.skills[0]
    assert text_digest(agent.system_instruction) == agent.system_instruction_digest
    assert schema_digest(json.loads(agent.output_schema_json)) == agent.output_schema_digest
    assert text_digest(skill.instruction_template) == skill.instruction_template_digest


def test_answer_authority_rejects_missing_tool_wrong_task_and_unrecoverable_content() -> None:
    from dataclasses import replace

    from tap.modules.ai.application.assets import (
        resolve_agent_selection,
        resolve_skill_selection,
        validation_asset_seed,
    )
    from tap.modules.ai.domain.assets import AssetRevisionRejected

    seed = validation_asset_seed(VALIDATION_SCOPE)
    agent = seed.agents[0]
    skill = seed.skills[0]
    with pytest.raises(AssetRevisionRejected):
        resolve_agent_selection(
            replace(agent, tool_allowlist=frozenset({"knowledge.search"})),
            tools=frozenset({"knowledge.answer"}),
            output_schema_digest=agent.output_schema_digest,
        )
    with pytest.raises(AssetRevisionRejected):
        resolve_agent_selection(
            replace(agent, system_instruction=None, output_schema_json=None),
            tools=frozenset({"knowledge.answer"}),
            output_schema_digest=agent.output_schema_digest,
        )
    with pytest.raises(AssetRevisionRejected):
        resolve_skill_selection(
            replace(skill, applicable_tasks=frozenset({"automation.generate"})),
            task="knowledge.answer",
        )
