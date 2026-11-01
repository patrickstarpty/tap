from __future__ import annotations

import inspect

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.assets import AssetRevisionStatus, SkillRevision, text_digest
from tap.modules.chat.application.skill_selection import (
    SkillBudgetExceeded,
    merge_skill_selection,
    pinned_revisions,
)


def _skill(
    revision_id: str,
    *,
    asset_id: str | None = None,
    instruction: str = "instruction",
    status: AssetRevisionStatus = AssetRevisionStatus.ENABLED,
    applicable_tasks: frozenset[str] = frozenset({"knowledge.answer"}),
    attached_references: str | None = None,
) -> SkillRevision:
    combined = instruction + (attached_references or "")
    return SkillRevision(
        revision_id=revision_id,
        asset_id=asset_id or f"{revision_id}-asset",
        display_name=revision_id,
        scope=VALIDATION_SCOPE,
        content_digest=text_digest(f"{revision_id}-content"),
        instruction_template_digest=text_digest(combined),
        applicable_tasks=applicable_tasks,
        status=status,
        instruction_template=instruction,
        attached_references=attached_references,
    )


def test_manual_skills_come_before_pinned() -> None:
    manual = _skill("skill-manual")
    pinned = _skill("skill-pinned")

    selection = merge_skill_selection([manual], [pinned], task="knowledge.answer")

    assert [item.revision_id for item in selection.applied] == ["skill-manual", "skill-pinned"]
    assert selection.modes == ("manual", "pinned")
    assert selection.notices == ()


def test_same_skill_manual_and_pinned_records_manual_once() -> None:
    manual_old = _skill("skill-r1", asset_id="skill-a")
    pinned_latest = _skill("skill-r2", asset_id="skill-a")
    manual_again = _skill("skill-r1", asset_id="skill-a")

    selection = merge_skill_selection(
        [manual_old, manual_again], [pinned_latest], task="knowledge.answer"
    )

    assert [item.revision_id for item in selection.applied] == ["skill-r1"]
    assert selection.modes == ("manual",)
    assert selection.notices == ()


def test_disabled_pinned_skill_is_skipped_with_notice() -> None:
    disabled = _skill("skill-pinned", status=AssetRevisionStatus.DISABLED)
    manual = _skill("skill-manual")

    selection = merge_skill_selection([manual], [disabled], task="knowledge.answer")

    assert [item.revision_id for item in selection.applied] == ["skill-manual"]
    assert selection.modes == ("manual",)
    assert selection.notices == ("skill_disabled:skill-pinned",)


def test_inapplicable_skill_is_skipped() -> None:
    manual = _skill("skill-wrong-task", applicable_tasks=frozenset({"test-plan.generate"}))
    pinned = _skill("skill-pinned-wrong", applicable_tasks=frozenset({"test-plan.generate"}))

    selection = merge_skill_selection([manual], [pinned], task="knowledge.answer")

    assert selection.applied == ()
    assert selection.modes == ()
    assert selection.notices == (
        "skill_inapplicable:skill-wrong-task",
        "skill_inapplicable:skill-pinned-wrong",
    )


def test_combined_budget_over_96kb_raises() -> None:
    half = 48 * 1024
    manual = _skill("skill-manual", instruction="x" * half)
    pinned = _skill("skill-pinned", instruction="y" * (half - 4), attached_references="refs!")

    at_budget = merge_skill_selection(
        [manual], [_skill("skill-fits", instruction="y" * half)], task="knowledge.answer"
    )
    assert len(at_budget.applied) == 2

    with pytest.raises(SkillBudgetExceeded) as excinfo:
        merge_skill_selection([manual], [pinned], task="knowledge.answer")
    assert excinfo.value.code == "skill_budget_exceeded"


def test_agent_bound_revisions_are_no_longer_read() -> None:
    # The merge sees only the Turn's manual Skills and the Agent's pinned
    # Skills; there is no Agent (and so no bound revision list) to read.
    assert list(inspect.signature(merge_skill_selection).parameters) == [
        "requested",
        "pinned",
        "task",
    ]
    selection = merge_skill_selection([], [], task="knowledge.answer")

    assert selection.applied == ()
    assert selection.modes == ()
    assert selection.notices == ()


def test_pinned_ids_resolve_to_latest_library_revision_or_unavailable() -> None:
    latest = _skill("skill-r2", asset_id="skill-a")
    other = _skill("skill-b-r1", asset_id="skill-b")

    revisions, notices = pinned_revisions(("skill-a", "skill-gone"), [other, latest])

    assert revisions == (latest,)
    assert notices == ("skill_unavailable:skill-gone",)
