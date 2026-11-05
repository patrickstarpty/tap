"""Classify an Agent revise as a plain `"revised"` or a `"skill_upgraded"`
maintenance event (global constraints: capability 4 has no automatic Skill
upgrade path, so this only recognizes a *manual* revise that happens to touch
nothing but Skill bindings, each moving to a newer revision of the same
Skill)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from tap.modules.ai.domain.assets import AiAgentRevision

_CONTENT_FIELDS = (
    "display_name",
    "description",
    "content_digest",
    "system_instruction",
    "system_instruction_digest",
    "tool_allowlist",
    "output_schema_digest",
    "output_schema_json",
    "default_model_alias",
    "status",
    "source_kind",
)


def classify_revision(
    previous: AiAgentRevision,
    current: AiAgentRevision,
    skill_of: Mapping[str, str],
) -> Literal["revised", "skill_upgraded"]:
    """`skill_of` maps a bound `skill_revision_id` to its owning `skill_id`.

    Bindings compare as a `skill_id -> revision_id` mapping, not by position:
    the Agent form appends re-checked Skills, so upgrading A on `[A1, B1]`
    submits `[B1, A2]`. `skill_upgraded` needs the same Skill set with at
    least one revision changed and nothing else changed."""
    for field in _CONTENT_FIELDS:
        if getattr(previous, field) != getattr(current, field):
            return "revised"
    if len(previous.skill_revision_ids) != len(current.skill_revision_ids):
        return "revised"
    previous_ids, current_ids = set(previous.skill_revision_ids), set(current.skill_revision_ids)
    removed, added = previous_ids - current_ids, current_ids - previous_ids
    if not removed and not added:
        return "revised"
    removed_skills = [skill_of.get(revision_id) for revision_id in removed]
    added_skills = [skill_of.get(revision_id) for revision_id in added]
    # An unresolvable revision (deleted/unknown) cannot be shown to be the
    # same Skill, so it never counts as an upgrade.
    if None in removed_skills or None in added_skills:
        return "revised"
    # Each changed Skill must swap exactly one revision for one other: a
    # Skill added, dropped, or bound twice is a plain revise.
    if len(set(removed_skills)) != len(removed) or set(removed_skills) != set(added_skills):
        return "revised"
    return "skill_upgraded"
