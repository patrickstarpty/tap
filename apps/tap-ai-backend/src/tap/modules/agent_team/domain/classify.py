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
    """`skill_of` maps a bound `skill_revision_id` to its owning `skill_id`."""
    for field in _CONTENT_FIELDS:
        if getattr(previous, field) != getattr(current, field):
            return "revised"
    if len(previous.skill_revision_ids) != len(current.skill_revision_ids):
        return "revised"
    any_changed = False
    for old_id, new_id in zip(previous.skill_revision_ids, current.skill_revision_ids, strict=True):
        if old_id == new_id:
            continue
        any_changed = True
        if skill_of.get(old_id) != skill_of.get(new_id):
            return "revised"
    if not any_changed:
        return "revised"
    return "skill_upgraded"
