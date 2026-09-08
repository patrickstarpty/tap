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

    agent = AiAgentRevision(
        revision_id="agent-revision",
        asset_id="knowledge-agent",
        display_name="Knowledge agent",
        scope=VALIDATION_SCOPE,
        content_digest=text_digest("agent-v1"),
        system_instruction_digest=text_digest("server-instruction"),
        tool_allowlist=frozenset({"knowledge.search"}),
        output_schema_digest=text_digest("schema-v1"),
        status=AssetRevisionStatus.ENABLED,
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
            output_schema_digest=text_digest("schema-v1"),
        )
        == agent
    )
    for scope, revision_id, tools, digest in (
        (VALIDATION_SCOPE, "unknown", frozenset(), text_digest("schema-v1")),
        (other, "agent-revision", frozenset(), text_digest("schema-v1")),
        (
            VALIDATION_SCOPE,
            "agent-revision",
            frozenset({"knowledge.delete"}),
            text_digest("schema-v1"),
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
