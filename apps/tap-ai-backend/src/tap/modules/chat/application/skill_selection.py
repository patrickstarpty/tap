"""Merge an Agent's bound Skills with a Turn's explicitly requested Skills.

This runs where the Turn input is frozen (the HTTP layer, before `TurnInput` is
built) -- never at LangGraph graph time, so a checkpoint resume/retry reuses the
already-applied selection instead of re-merging it (see `entrypoints/tapper_chat_turn.py`,
graph `tapper-chat-v2`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tap.modules.ai.domain.assets import AiAgentRevision, AssetRevisionStatus, SkillRevision

#: A single turn's combined Skill instruction text (template + attached references)
#: may not exceed this many bytes (Global Constraints: "单轮 Skill 文本...合计 ≤ 96 KB").
SKILL_TURN_BUDGET_BYTES = 96 * 1024


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
    notices: tuple[str, ...]


def _skill_text_bytes(skill: SkillRevision) -> int:
    body = (skill.instruction_template or "") + (getattr(skill, "attached_references", None) or "")
    return len(body.encode("utf-8"))


def merge_skill_selection(
    agent: AiAgentRevision | None, requested: Sequence[SkillRevision], *, task: str
) -> SkillSelection:
    """Order an Agent's bound Skills first, then the Turn's explicitly requested
    Skills, de-duplicating by revision id. A disabled or task-inapplicable Skill
    is skipped with a notice rather than failing the turn; the combined applied
    text is bounded by `SKILL_TURN_BUDGET_BYTES`.

    `requested` must already include every candidate Skill revision this merge
    should consider -- both the Agent's bound ids and the Turn's explicitly
    requested ids -- resolved tolerantly (disabled Skills included, so this
    function can decide whether to skip them); a revision that genuinely does
    not exist is the caller's concern, not this function's.
    """

    bound_ids = tuple(getattr(agent, "skill_revision_ids", ()) or ()) if agent is not None else ()
    by_id: dict[str, SkillRevision] = {}
    for skill in requested:
        by_id.setdefault(skill.revision_id, skill)

    order: list[str] = []
    for revision_id in bound_ids:
        if revision_id not in order:
            order.append(revision_id)
    for skill in requested:
        if skill.revision_id not in order:
            order.append(skill.revision_id)

    applied: list[SkillRevision] = []
    notices: list[str] = []
    total_bytes = 0
    for revision_id in order:
        candidate = by_id.get(revision_id)
        if candidate is None:
            continue
        if candidate.status is not AssetRevisionStatus.ENABLED:
            notices.append(f"skill_disabled:{revision_id}")
            continue
        if task not in candidate.applicable_tasks:
            notices.append(f"skill_inapplicable:{revision_id}")
            continue
        total_bytes += _skill_text_bytes(candidate)
        applied.append(candidate)

    if total_bytes > SKILL_TURN_BUDGET_BYTES:
        raise SkillBudgetExceeded()

    return SkillSelection(applied=tuple(applied), notices=tuple(notices))
