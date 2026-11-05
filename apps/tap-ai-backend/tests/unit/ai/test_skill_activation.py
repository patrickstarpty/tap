from __future__ import annotations

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.assets import AssetRevisionStatus, SkillRevision, text_digest
from tap.modules.ai.domain.skill_activation import (
    MAX_CATALOG_ENTRIES,
    SKILL_TURN_BUDGET_BYTES,
    PromptSkill,
    SkillCatalogEntry,
    TurnSkillContext,
    activation_rejection,
    build_skill_catalog,
    catalog_entry,
    render_skill_catalog,
    skill_text_bytes,
    truncate_description,
)
from tap.modules.ai.domain.skill_metadata import SKILL_CATEGORIES, SkillCategory


def _skill(
    skill_id: str,
    *,
    name: str | None = None,
    category: SkillCategory | None = "other",
    description: str | None = "does things",
    status: AssetRevisionStatus = AssetRevisionStatus.ENABLED,
    applicable_tasks: frozenset[str] = frozenset({"knowledge.answer"}),
    instruction: str = "instruction",
    attached_references: str | None = None,
    frontmatter: dict[str, object] | None = None,
) -> SkillRevision:
    return SkillRevision(
        revision_id=f"{skill_id}-rev",
        asset_id=skill_id,
        display_name=name or skill_id,
        scope=VALIDATION_SCOPE,
        content_digest=text_digest(skill_id),
        instruction_template_digest=text_digest(instruction + (attached_references or "")),
        applicable_tasks=applicable_tasks,
        status=status,
        instruction_template=instruction,
        description=description,
        attached_references=attached_references,
        category=category,
        frontmatter=frontmatter,
    )


def _build(candidates, *, excluded=frozenset(), loaded=frozenset()):
    return build_skill_catalog(
        candidates,
        task="knowledge.answer",
        excluded_skill_ids=frozenset(excluded),
        loaded_skill_ids=frozenset(loaded),
    )


def test_description_truncated_to_200_chars_with_ellipsis():
    out = truncate_description("x" * 300)
    assert len(out) == 200 and out.endswith("…")


def test_catalog_line_collapses_newlines_and_counts_cjk_chars():
    out = truncate_description("第一行\n" + "测" * 250)
    assert "\n" not in out and len(out) == 200


def test_budget_boundaries():
    e, none = SkillCatalogEntry("s1", "r1", "A", "d", "other"), frozenset()
    assert (
        activation_rejection(
            e, loaded_skill_ids=none, auto_count=5, loaded_bytes=0, candidate_bytes=1
        )
        == "budget_count"
    )
    assert (
        activation_rejection(
            e, loaded_skill_ids=none, auto_count=0, loaded_bytes=96 * 1024 - 10, candidate_bytes=10
        )
        is None
    )
    assert (
        activation_rejection(
            e, loaded_skill_ids=none, auto_count=0, loaded_bytes=96 * 1024 - 10, candidate_bytes=11
        )
        == "budget_bytes"
    )
    assert (
        activation_rejection(
            e, loaded_skill_ids=frozenset({"s1"}), auto_count=0, loaded_bytes=0, candidate_bytes=1
        )
        == "already_loaded"
    )
    assert (
        activation_rejection(
            None, loaded_skill_ids=none, auto_count=0, loaded_bytes=0, candidate_bytes=0
        )
        == "not_in_catalog"
    )


def test_description_normalization_short_and_none():
    assert truncate_description(None) == ""
    assert truncate_description("a\r\nb\rc\n  d\t e") == "a b c d e"
    assert truncate_description("y" * 200) == "y" * 200


def test_skill_text_bytes_counts_template_and_references_utf8():
    skill = _skill("s", instruction="测", attached_references="ab")
    assert skill_text_bytes(skill) == 5
    assert SKILL_TURN_BUDGET_BYTES == 96 * 1024


def test_catalog_capped_at_60_round_robin_by_category():
    # 50 analysis + 11 writing = 61 candidates: the cap keeps every writing
    # Skill by taking categories in turn rather than filling up on analysis.
    candidates = [_skill(f"analysis-{i:02d}", category="analysis") for i in range(50)]
    candidates += [_skill(f"writing-{i:02d}", category="writing") for i in range(11)]

    build = _build(list(reversed(candidates)))

    assert len(build.entries) == MAX_CATALOG_ENTRIES == 60
    assert build.candidate_count == 61
    assert build.overflow is True
    names = [entry.name for entry in build.entries]
    assert sum(name.startswith("writing-") for name in names) == 11
    # Within a category, names ascend, so only the last analysis Skill is cut.
    assert "analysis-49" not in names
    assert [entry.category for entry in build.entries] == sorted(
        (entry.category for entry in build.entries), key=SKILL_CATEGORIES.index
    )


def test_catalog_under_cap_is_not_overflow():
    build = _build([_skill("b", category="writing"), _skill("a", category="test-design")])
    assert build.candidate_count == 2 and build.overflow is False
    assert [entry.skill_id for entry in build.entries] == ["a", "b"]


def test_catalog_skips_excluded_loaded_disabled_and_inapplicable():
    build = _build(
        [
            _skill("keep", description="Keep\nme"),
            _skill("excluded"),
            _skill("loaded"),
            _skill("disabled", status=AssetRevisionStatus.DISABLED),
            _skill("other-task", applicable_tasks=frozenset({"test-plan.generate"})),
        ],
        excluded={"excluded"},
        loaded={"loaded"},
    )

    assert build.candidate_count == 1
    assert build.entries == (SkillCatalogEntry("keep", "keep-rev", "keep", "Keep me", "other"),)


def test_catalog_category_falls_back_to_frontmatter_then_other():
    build = _build(
        [
            _skill("fm", category=None, frontmatter={"metadata": {"category": "automation"}}),
            _skill("plain", category=None),
        ]
    )
    assert {entry.skill_id: entry.category for entry in build.entries} == {
        "fm": "automation",
        "plain": "other",
    }


def test_render_catalog_lists_name_dash_description_and_call_rule():
    text = render_skill_catalog(
        (
            SkillCatalogEntry("s1", "r1", "Alpha", "first", "other"),
            SkillCatalogEntry("s2", "r2", "Beta", "second", "writing"),
        )
    )
    lines = text.splitlines()
    assert lines[0] == "Available Skills"
    assert "Alpha — first" in lines and "Beta — second" in lines
    assert "skill_activate" in lines[-1] and "only" in lines[-1]
    assert render_skill_catalog(()) == ""


def test_catalog_entry_is_exact_case_sensitive_match():
    entries = (SkillCatalogEntry("s1", "r1", "Alpha", "first", "other"),)
    assert catalog_entry("Alpha", entries) == entries[0]
    assert catalog_entry("alpha", entries) is None
    assert catalog_entry("Alpha ", entries) is None


def test_prompt_skill_bytes_and_default_turn_context():
    skill = PromptSkill("s1", "r1", "Alpha", "测试", "auto")
    assert skill.bytes == 6
    assert TurnSkillContext() == TurnSkillContext(catalog=(), blocks=())
