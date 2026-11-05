"""Merge a Turn's manual Skills with the selected Agent's pinned Skills
(Skill Hub spec 1.2: manual > pinned; auto activation happens later, in the
tool loop).

This runs where the Turn input is frozen (the HTTP layer, before `TurnInput` is
built) -- never at LangGraph graph time, so a checkpoint resume/retry reuses the
already-applied selection instead of re-merging it
(see `modules/chat/application/turn_execution.py`, graph `tapper-chat-v2`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tap.modules.ai.domain.assets import AssetRevisionStatus, SkillRevision
from tap.modules.ai.domain.skill_activation import SKILL_TURN_BUDGET_BYTES, skill_text_bytes
from tap.modules.ai.domain.skill_metadata import ActivationMode


class SkillBudgetExceeded(Exception):
    """The applied Skills' combined instruction text exceeds the per-turn budget."""

    code = "skill_budget_exceeded"

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or (
            "combined Skill instructions for this turn exceed the 96 KB budget"
        )
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class SkillSelection:
    applied: tuple[SkillRevision, ...]
    # Parallel to `applied`: how each applied Skill was activated.
    modes: tuple[ActivationMode, ...]
    notices: tuple[str, ...]


def pinned_revisions(
    pinned_skill_ids: Sequence[str], library: Sequence[SkillRevision]
) -> tuple[tuple[SkillRevision, ...], tuple[str, ...]]:
    """Resolve an Agent's pinned Skill ids to each Skill's latest library
    revision (`library` holds one latest revision per Skill, any status).
    An id missing from the library yields `skill_unavailable:<skill_id>`."""
    by_skill = {skill.asset_id: skill for skill in library}
    revisions: list[SkillRevision] = []
    notices: list[str] = []
    for skill_id in dict.fromkeys(pinned_skill_ids):
        skill = by_skill.get(skill_id)
        if skill is None:
            notices.append(f"skill_unavailable:{skill_id}")
        else:
            revisions.append(skill)
    return tuple(revisions), tuple(notices)


def merge_skill_selection(
    requested: Sequence[SkillRevision], pinned: Sequence[SkillRevision], *, task: str
) -> SkillSelection:
    """Order the Turn's manual Skills first, then the Agent's pinned Skills.

    A revision listed twice is considered once; a Skill already applied (by
    `asset_id`) is not applied again, so a Skill both manual and pinned is
    recorded once, as manual. A disabled or task-inapplicable revision is
    skipped with a notice rather than failing the turn; the combined applied
    text is bounded by `SKILL_TURN_BUDGET_BYTES`.

    Both sequences are resolved tolerantly (disabled Skills included, so this
    function can decide whether to skip them).
    """

    sources: tuple[tuple[ActivationMode, Sequence[SkillRevision]], ...] = (
        ("manual", requested),
        ("pinned", pinned),
    )
    candidates: dict[str, tuple[SkillRevision, ActivationMode]] = {}
    for mode, skills in sources:
        for skill in skills:
            candidates.setdefault(skill.revision_id, (skill, mode))

    applied: list[SkillRevision] = []
    modes: list[ActivationMode] = []
    notices: list[str] = []
    applied_skill_ids: set[str] = set()
    total_bytes = 0
    for revision_id, (candidate, mode) in candidates.items():
        if candidate.asset_id in applied_skill_ids:
            continue
        if candidate.status is not AssetRevisionStatus.ENABLED:
            notices.append(f"skill_disabled:{revision_id}")
            continue
        if task not in candidate.applicable_tasks:
            notices.append(f"skill_inapplicable:{revision_id}")
            continue
        total_bytes += skill_text_bytes(candidate)
        applied.append(candidate)
        modes.append(mode)
        applied_skill_ids.add(candidate.asset_id)

    if total_bytes > SKILL_TURN_BUDGET_BYTES:
        raise SkillBudgetExceeded()

    return SkillSelection(applied=tuple(applied), modes=tuple(modes), notices=tuple(notices))
