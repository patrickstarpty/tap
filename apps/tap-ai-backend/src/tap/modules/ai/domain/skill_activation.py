"""Skill Hub activation rules (spec 1.2/1.3): per-turn budgets, the
progressive-loading candidate catalog and its prompt rendering, the
`skill.activate` rejection order, and the Skill blocks a Turn renders.

Pure functions: freezing builds the catalog, the prompt renders it, and the
tool executor checks each activation against these rules."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from tap.modules.ai.domain.assets import AssetRevisionStatus, SkillRevision
from tap.modules.ai.domain.skill_metadata import (
    SKILL_CATEGORIES,
    ActivationMode,
    SkillCategory,
    category_from_frontmatter,
)

#: A turn's loaded Skill text (template + attached references, UTF-8) may not
#: exceed this many bytes.
SKILL_TURN_BUDGET_BYTES = 96 * 1024
MAX_AUTO_ACTIVATIONS = 5
MAX_CATALOG_ENTRIES = 60
CATALOG_DESCRIPTION_CHARS = 200

_ELLIPSIS = "…"

ActivationRejection = Literal[
    "not_in_catalog", "already_loaded", "skill_disabled", "budget_count", "budget_bytes"
]


def skill_text_bytes(skill: SkillRevision) -> int:
    """UTF-8 size of the text a loaded Skill contributes to the prompt."""
    # `getattr`: callers also pass revision-shaped doubles without references.
    body = (skill.instruction_template or "") + (getattr(skill, "attached_references", None) or "")
    return len(body.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    skill_id: str
    revision_id: str
    name: str
    description: str
    category: SkillCategory


@dataclass(frozen=True, slots=True)
class CatalogBuild:
    entries: tuple[SkillCatalogEntry, ...]
    # Candidates before the `MAX_CATALOG_ENTRIES` cap.
    candidate_count: int

    @property
    def overflow(self) -> bool:
        return self.candidate_count > MAX_CATALOG_ENTRIES


def truncate_description(text: str | None) -> str:
    """One catalog line's description: whitespace runs (newlines included)
    collapsed to single spaces, at most `CATALOG_DESCRIPTION_CHARS`
    characters including a trailing ellipsis when cut."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= CATALOG_DESCRIPTION_CHARS:
        return collapsed
    return collapsed[: CATALOG_DESCRIPTION_CHARS - 1].rstrip() + _ELLIPSIS


def _category_of(skill: SkillRevision) -> SkillCategory:
    return skill.category or category_from_frontmatter(skill.frontmatter) or "other"


def build_skill_catalog(
    candidates: Sequence[SkillRevision],
    *,
    task: str,
    excluded_skill_ids: frozenset[str],
    loaded_skill_ids: frozenset[str],
) -> CatalogBuild:
    """The auto-activation catalog: enabled, task-applicable Skills the Agent
    does not exclude and the Turn has not already loaded (manual/pinned).

    `candidates` are each Skill's latest revision. Entries are ordered by
    category (closed-set order) then name. Past `MAX_CATALOG_ENTRIES`, the
    categories take turns contributing their next Skill by name, so no
    category crowds out the others."""
    by_category: dict[SkillCategory, list[SkillCatalogEntry]] = {
        category: [] for category in SKILL_CATEGORIES
    }
    seen: set[str] = set()
    for skill in candidates:
        if (
            skill.asset_id in seen
            or skill.status is not AssetRevisionStatus.ENABLED
            or task not in skill.applicable_tasks
            or skill.asset_id in excluded_skill_ids
            or skill.asset_id in loaded_skill_ids
        ):
            continue
        seen.add(skill.asset_id)
        category = _category_of(skill)
        by_category[category].append(
            SkillCatalogEntry(
                skill_id=skill.asset_id,
                revision_id=skill.revision_id,
                name=skill.display_name,
                description=truncate_description(skill.description),
                category=category,
            )
        )
    for entries in by_category.values():
        entries.sort(key=lambda entry: entry.name)

    candidate_count = sum(len(entries) for entries in by_category.values())
    kept: dict[SkillCategory, int] = {category: 0 for category in SKILL_CATEGORIES}
    remaining = min(candidate_count, MAX_CATALOG_ENTRIES)
    while remaining:
        for category in SKILL_CATEGORIES:
            if remaining and kept[category] < len(by_category[category]):
                kept[category] += 1
                remaining -= 1
    return CatalogBuild(
        entries=tuple(
            entry
            for category in SKILL_CATEGORIES
            for entry in by_category[category][: kept[category]]
        ),
        candidate_count=candidate_count,
    )


def render_skill_catalog(entries: Sequence[SkillCatalogEntry]) -> str:
    """The system prompt's "Available Skills" section; empty without entries."""
    if not entries:
        return ""
    lines = ["Available Skills"]
    lines.extend(f"{entry.name} — {entry.description}" for entry in entries)
    lines.append(
        "Call skill_activate with a Skill's exact name only when the task needs that Skill."
    )
    return "\n".join(lines)


def catalog_entry(name: str, catalog: Sequence[SkillCatalogEntry]) -> SkillCatalogEntry | None:
    """The catalog entry whose name matches exactly (case-sensitive)."""
    return next((entry for entry in catalog if entry.name == name), None)


def activation_rejection(
    entry: SkillCatalogEntry | None,
    *,
    loaded_skill_ids: frozenset[str],
    auto_count: int,
    loaded_bytes: int,
    candidate_bytes: int,
) -> ActivationRejection | None:
    """Why a `skill.activate` call may not load `entry`, or `None` when it may.
    (`skill_disabled` is decided when the Skill's text is loaded.)"""
    if entry is None:
        return "not_in_catalog"
    if entry.skill_id in loaded_skill_ids:
        return "already_loaded"
    if auto_count >= MAX_AUTO_ACTIVATIONS:
        return "budget_count"
    if loaded_bytes + candidate_bytes > SKILL_TURN_BUDGET_BYTES:
        return "budget_bytes"
    return None


@dataclass(frozen=True, slots=True)
class PromptSkill:
    skill_id: str
    revision_id: str
    name: str
    instruction: str
    mode: ActivationMode

    @property
    def bytes(self) -> int:
        return len(self.instruction.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class TurnSkillContext:
    catalog: tuple[SkillCatalogEntry, ...] = ()
    # Skills outside the generation governance that the prompt renders itself.
    blocks: tuple[PromptSkill, ...] = ()
