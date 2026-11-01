"""Skill Hub metadata rules (spec 1.1/1.2): closed category set, SemVer version
labels, binding-to-pinned mapping and the frozen manual/pinned split that
migration `0038_skill_hub` backfills `turn_skill_activation` from."""

from __future__ import annotations

from tap.modules.ai.domain.skill_metadata import (
    SKILL_CATEGORIES,
    FrozenActivation,
    category_from_frontmatter,
    compatibility_of,
    display_version,
    frozen_activations,
    pinned_from_bound,
    version_label_from_frontmatter,
)


def test_category_from_frontmatter_is_closed_set() -> None:
    assert SKILL_CATEGORIES == (
        "test-design",
        "automation",
        "knowledge",
        "analysis",
        "writing",
        "other",
    )
    assert category_from_frontmatter({"metadata": {"category": "automation"}}) == "automation"
    assert category_from_frontmatter({"metadata": {"category": "ops"}}) is None
    assert category_from_frontmatter({"metadata": {"category": None}}) is None
    assert category_from_frontmatter({"metadata": "automation"}) is None
    assert category_from_frontmatter({}) is None
    assert category_from_frontmatter(None) is None


def test_version_label_requires_semver() -> None:
    assert version_label_from_frontmatter({"metadata": {"version": "1.2.0"}}) == "1.2.0"
    assert (
        version_label_from_frontmatter({"metadata": {"version": "2.0.0-rc.1+build.5"}})
        == "2.0.0-rc.1+build.5"
    )
    assert version_label_from_frontmatter({"metadata": {"version": "v2"}}) is None
    assert version_label_from_frontmatter({"metadata": {"version": 1.2}}) is None
    assert version_label_from_frontmatter({"metadata": {"version": "1.0.0-" + "a" * 40}}) is None
    assert version_label_from_frontmatter(None) is None
    assert display_version("1.2.0", 3) == "1.2.0"
    assert display_version(None, 3) == "r3"


def test_compatibility_is_passed_through_verbatim() -> None:
    assert compatibility_of({"compatibility": "Requires Python 3.12"}) == "Requires Python 3.12"
    assert compatibility_of({"compatibility": "  "}) is None
    assert compatibility_of({"compatibility": 3}) is None
    assert compatibility_of(None) is None


def test_pinned_from_bound_maps_revisions_to_unique_skills() -> None:
    assert pinned_from_bound(["r1", "r2", "rX"], {"r1": "s1", "r2": "s1"}) == ("s1",)
    assert pinned_from_bound(["r3", "r1"], {"r1": "s1", "r3": "s2"}) == ("s2", "s1")
    assert pinned_from_bound([], {"r1": "s1"}) == ()


def test_frozen_activations_split_manual_and_pinned() -> None:
    snapshot = {
        "skill_revision_ids": ["r1", "r2"],
        "skill_instruction_templates": ["ab", "数据"],
        "requested_skill_revision_ids": ["r2"],
    }
    # The key's presence wins: the Agent binding is not consulted at all.
    assert frozen_activations(snapshot, frozenset({"r2"})) == (
        FrozenActivation(skill_revision_id="r1", mode="pinned", bytes=2),
        FrozenActivation(skill_revision_id="r2", mode="manual", bytes=6),
    )


def test_frozen_activations_without_requested_key_uses_agent_binding() -> None:
    snapshot = {
        "skill_revision_ids": ["r1", "r2"],
        "skill_instruction_templates": ["ab", "数据"],
    }
    assert frozen_activations(snapshot, frozenset({"r2"})) == (
        FrozenActivation(skill_revision_id="r1", mode="manual", bytes=2),
        FrozenActivation(skill_revision_id="r2", mode="pinned", bytes=6),
    )


def test_frozen_activations_without_frozen_templates_count_zero_bytes() -> None:
    snapshot = {"skill_revision_ids": ["r1"], "requested_skill_revision_ids": []}
    assert frozen_activations(snapshot, frozenset()) == (
        FrozenActivation(skill_revision_id="r1", mode="pinned", bytes=0),
    )
    assert frozen_activations({}, frozenset()) == ()
