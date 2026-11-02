"""Skill Hub metadata rules (spec 1.1/1.2): closed category and activation-mode
sets, SemVer version labels read from SKILL.md frontmatter, and the mapping
from a frozen Turn input to its manual/pinned Skill activations. Pure
functions, shared by migration `0038_skill_hub`'s backfill and the runtime."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

SkillCategory = Literal["test-design", "automation", "knowledge", "analysis", "writing", "other"]
SKILL_CATEGORIES: tuple[SkillCategory, ...] = (
    "test-design",
    "automation",
    "knowledge",
    "analysis",
    "writing",
    "other",
)
ActivationMode = Literal["manual", "pinned", "auto"]
OriginKind = Literal["files", "zip", "paste", "authored", "built_in", "unknown"]

_VERSION_LABEL_MAX = 32
# semver.org's recommended expression for a full SemVer 2.0.0 version.
_SEMVER = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
)


def _metadata_value(frontmatter: Mapping[str, object] | None, key: str) -> object:
    if not isinstance(frontmatter, Mapping):
        return None
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    return metadata.get(key)


def category_from_frontmatter(frontmatter: Mapping[str, object] | None) -> SkillCategory | None:
    """`metadata.category` when it names a closed-set category, else `None`."""
    value = _metadata_value(frontmatter, "category")
    if isinstance(value, str) and value in SKILL_CATEGORIES:
        return cast(SkillCategory, value)
    return None


def version_label_from_frontmatter(frontmatter: Mapping[str, object] | None) -> str | None:
    """`metadata.version` when it is a SemVer string of at most 32 characters."""
    value = _metadata_value(frontmatter, "version")
    if (
        isinstance(value, str)
        and len(value) <= _VERSION_LABEL_MAX
        and _SEMVER.fullmatch(value) is not None
    ):
        return value
    return None


def display_version(version_label: str | None, revision_number: int) -> str:
    """The SemVer label when present, otherwise `r<revision_number>`."""
    return version_label if version_label is not None else f"r{revision_number}"


def compatibility_of(frontmatter: Mapping[str, object] | None) -> str | None:
    """Top-level `compatibility`, shown verbatim; blank or non-string reads as absent."""
    if not isinstance(frontmatter, Mapping):
        return None
    value = frontmatter.get("compatibility")
    if isinstance(value, str) and value.strip():
        return value
    return None


def pinned_from_bound(
    bound_revision_ids: Sequence[str], skill_of: Mapping[str, str]
) -> tuple[str, ...]:
    """Map bound Skill revisions to their Skill ids: order-preserving, deduped,
    revisions without a known Skill dropped.

    Frozen by migration 0038 (the binding backfill calls it): keep these
    semantics unchanged."""
    pinned: dict[str, None] = {}
    for revision_id in bound_revision_ids:
        skill_id = skill_of.get(revision_id)
        if skill_id is not None:
            pinned.setdefault(skill_id, None)
    return tuple(pinned)


@dataclass(frozen=True, slots=True)
class FrozenActivation:
    skill_revision_id: str
    mode: Literal["manual", "pinned"]
    bytes: int


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def frozen_activations(
    snapshot: Mapping[str, object], agent_bound_revision_ids: frozenset[str]
) -> tuple[FrozenActivation, ...]:
    """Split a frozen Turn input's applied Skill revisions into manual/pinned.

    A snapshot carrying `requested_skill_revision_ids` (frozen since that field
    existed, possibly empty) marks exactly those as manual and the rest as
    pinned (they came from the Agent binding). Older snapshots without the key
    fall back to the Turn's Agent revision binding: bound ids are pinned, the
    rest manual. Bytes are the UTF-8 size of the matching frozen instruction
    template, or 0 when the snapshot froze no templates.

    Frozen by migration 0038 (the activation backfill calls it): keep these
    semantics unchanged.
    """
    revision_ids = _strings(snapshot.get("skill_revision_ids"))
    templates = _strings(snapshot.get("skill_instruction_templates"))
    if len(templates) != len(revision_ids):
        templates = ("",) * len(revision_ids)
    if "requested_skill_revision_ids" in snapshot:
        requested = frozenset(_strings(snapshot["requested_skill_revision_ids"]))

        def mode_of(revision_id: str) -> Literal["manual", "pinned"]:
            return "manual" if revision_id in requested else "pinned"

    else:

        def mode_of(revision_id: str) -> Literal["manual", "pinned"]:
            return "pinned" if revision_id in agent_bound_revision_ids else "manual"

    return tuple(
        FrozenActivation(
            skill_revision_id=revision_id,
            mode=mode_of(revision_id),
            bytes=len(template.encode("utf-8")),
        )
        for revision_id, template in zip(revision_ids, templates, strict=True)
    )
