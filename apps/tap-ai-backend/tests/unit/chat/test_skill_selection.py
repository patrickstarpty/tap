from __future__ import annotations

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.assets import (
    AiAgentRevision,
    AssetRevisionStatus,
    SkillRevision,
    text_digest,
)
from tap.modules.ai.domain.models import schema_digest
from tap.modules.chat.application.skill_selection import (
    SkillBudgetExceeded,
    merge_skill_selection,
)

_AGENT_OUTPUT_SCHEMA = {"type": "object"}


def _agent(skill_revision_ids: tuple[str, ...] = ()) -> AiAgentRevision:
    return AiAgentRevision(
        revision_id="agent-revision",
        asset_id="knowledge-agent",
        display_name="Knowledge agent",
        scope=VALIDATION_SCOPE,
        content_digest=text_digest("agent-v1"),
        system_instruction_digest=text_digest("server-instruction"),
        tool_allowlist=frozenset({"knowledge.search"}),
        output_schema_digest=schema_digest(_AGENT_OUTPUT_SCHEMA),
        status=AssetRevisionStatus.ENABLED,
        system_instruction="server-instruction",
        output_schema_json='{"type":"object"}',
        skill_revision_ids=skill_revision_ids,
    )


def _skill(
    revision_id: str,
    *,
    instruction: str = "instruction",
    status: AssetRevisionStatus = AssetRevisionStatus.ENABLED,
    applicable_tasks: frozenset[str] = frozenset({"knowledge.answer"}),
    attached_references: str | None = None,
) -> SkillRevision:
    combined = instruction + (attached_references or "")
    return SkillRevision(
        revision_id=revision_id,
        asset_id=f"{revision_id}-asset",
        display_name=revision_id,
        scope=VALIDATION_SCOPE,
        content_digest=text_digest(f"{revision_id}-content"),
        instruction_template_digest=text_digest(combined),
        applicable_tasks=applicable_tasks,
        status=status,
        instruction_template=instruction,
        attached_references=attached_references,
    )


def test_agent_skills_precede_requested_and_dedupe() -> None:
    bound = _skill("skill-bound")
    requested_dupe = _skill("skill-bound")  # same id requested again, explicitly
    requested_extra = _skill("skill-extra")
    agent = _agent(skill_revision_ids=("skill-bound",))

    selection = merge_skill_selection(
        agent, [bound, requested_dupe, requested_extra], task="knowledge.answer"
    )

    assert [item.revision_id for item in selection.applied] == ["skill-bound", "skill-extra"]
    assert selection.notices == ()


def test_disabled_bound_skill_is_skipped_with_notice() -> None:
    bound = _skill("skill-bound", status=AssetRevisionStatus.DISABLED)
    agent = _agent(skill_revision_ids=("skill-bound",))

    selection = merge_skill_selection(agent, [bound], task="knowledge.answer")

    assert selection.applied == ()
    assert selection.notices == ("skill_disabled:skill-bound",)


def test_inapplicable_skill_is_skipped_with_notice() -> None:
    skill = _skill("skill-wrong-task", applicable_tasks=frozenset({"test-plan.generate"}))

    selection = merge_skill_selection(None, [skill], task="knowledge.answer")

    assert selection.applied == ()
    assert selection.notices == ("skill_inapplicable:skill-wrong-task",)


def test_budget_over_96kb_rejected() -> None:
    oversized = _skill("skill-big", instruction="x" * (96 * 1024 + 1))

    with pytest.raises(SkillBudgetExceeded) as excinfo:
        merge_skill_selection(None, [oversized], task="knowledge.answer")
    assert excinfo.value.code == "skill_budget_exceeded"


def test_merge_skill_selection_with_no_agent_and_no_requested_skills_is_empty() -> None:
    selection = merge_skill_selection(None, [], task="knowledge.answer")

    assert selection.applied == ()
    assert selection.notices == ()
