from __future__ import annotations

import pytest

from tap.modules.graph.domain.naming import (
    ALIAS_LENGTH_MAX,
    clean_alias,
    is_bare_grade,
    is_reference_name,
    usable_alias,
)


@pytest.mark.parametrize(
    "text",
    [
        # Chinese deictic and structural references (the real over-merging aliases).
        "本合同",
        "本附加合同",
        "本保险合同",
        "本条款",
        "本保险",
        "该产品",
        "本公司",
        "上述",
        "前款",
        "您",
        "我们",
        "第十九条",
        "第 3 章",
        "第二节",
        "附表1",
        "附件二",
        "释义四",
        "条款",
        # Pure numbers and numbering.
        "19",
        "（一）",
        "十九",
        "1.2",
        "二〇二五",
        "三十九",
        "一百零五",
        "19条",
        # Bare day counts, illustrative example people, a role restated with a
        # bare age qualifier, and clause/definition locators with no term
        # attached (the real over-merging aliases, second batch). Classification
        # grades ("IV 级") are not here -- see test_bare_grades_* below.
        "十日",
        "九十日",
        "王先生",
        "王先生的儿子",
        "李女士",
        "5岁被保险人",
        "三岁以上被保险人",
        "证明",
        "疾病定义一.9",
        "释义七",
        # English counterparts.
        "This Contract",
        "the Policy",
        "this agreement",
        "Article 5",
        "Section 3a",
        "§ 4",
        "Schedule B",
        "Part IV",
        "Annex A1",
        "Section A1",
        "Article12",
        "Part C",
        "aforementioned",
        "we",
        "We",
        "You",
        "it",
        "It",
        "us",
        # Too short once normalized.
        "x",
        "",
        " - ",
    ],
)
def test_reference_names_are_rejected(text):
    assert is_reference_name(text)
    assert not usable_alias(text)


@pytest.mark.parametrize(
    "text",
    [
        "友邦保险",
        "保单贷款",
        "本金",
        "等待期",
        "投保人",
        "被保险人",
        "第三者",
        "六十日宽限期",
        "功能分类Ⅲ级",
        "心肌病",
        "死亡证明",
        "全残",
        "明确说明与如实告知",
        "条款编号",
        "Policy holder",
        "Cash value",
        "Claims",
        "Article review",
        "Civil",
        "5G 网络",
        # Acronyms that fold onto pronouns, codes, plurals and names that only look
        # like numbering.
        "IT",
        "US",
        "WE",
        "5G",
        "3D",
        "A1",
        "e1",
        "Items",
        "Parts",
        "Arts",
        "Sections",
        "Exhibits",
        "Annexe",
        "Parties",
        "三九",
        "九九",
    ],
)
def test_real_names_are_kept(text):
    assert not is_reference_name(text)
    assert usable_alias(text)


@pytest.mark.parametrize(
    "text",
    [
        # v9 of the real-model run had real nodes legitimately named exactly this
        # way, with their own edges (心功能状态分级 PART_OF Ⅱ级); is_reference_name
        # must not reject these, or extraction drops the node and its edges.
        "IV 级",
        "Ⅲ级",
        "Ⅳ级",
        "四级",
        "0级",
        "1级",
    ],
)
def test_bare_grades_are_kept_as_node_names_but_rejected_as_aliases(text):
    assert is_bare_grade(text)
    assert not is_reference_name(text)
    assert not usable_alias(text)
    assert clean_alias(text) is None


@pytest.mark.parametrize(
    "text",
    [
        # A grade with its qualifying noun is a different, longer string: not a
        # bare grade, kept as both a name and an alias.
        "功能分类Ⅲ级",
        "心功能分级Ⅳ级",
    ],
)
def test_a_qualified_grade_is_not_a_bare_grade(text):
    assert not is_bare_grade(text)
    assert not is_reference_name(text)
    assert usable_alias(text)


def test_aliases_are_short_names_not_sentences():
    assert ALIAS_LENGTH_MAX == 32
    assert usable_alias("甲" * 32)
    # Real outputs used whole clauses as aliases, costing output tokens for nothing.
    assert not usable_alias(
        "本公司在合同订立时已经知道投保人未如实告知的情况的，本公司不得解除合同"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A clause number or interpretation note is only ever an affix: strip it
        # and keep the real term underneath.
        ("第二十五条 明确说明与如实告知", "明确说明与如实告知"),
        ("全残（释义七）", "全残"),
        ("全残(释义7)", "全残"),
        ("第9条 保险事故通知", "保险事故通知"),
        # "部分" is a two-character counter word ("第一部分" = "Part One"); a leading
        # article match must consume it whole, not strand "分" on the remainder.
        ("第一部分 总则", "总则"),
        # An article number immediately followed by its own parenthetical term
        # (not a trailing locator note) unwraps to the bare term.
        ("第十九条（责任免除）", "责任免除"),
        # A definition-note parenthetical generalizes beyond "释义N" to
        # "疾病定义N(.M)?" and "条款释义N", wherever it trails a real term.
        ("严重非恶性颅内肿瘤（疾病定义一.9）", "严重非恶性颅内肿瘤"),
        ("单目失明（疾病定义三.20）", "单目失明"),
        ("保险事故通知（条款释义4）", "保险事故通知"),
        # A real term with no such affix passes through unchanged.
        ("保险事故通知（第十条）", "保险事故通知（第十条）"),
        ("友邦保险", "友邦保险"),
    ],
)
def test_clean_alias_strips_clause_and_definition_affixes(text, expected):
    assert clean_alias(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # A pure locator has no term left once its affix is gone.
        "疾病定义一.9",
        "疾病定义一.52",
        "（疾病定义一.52）",
        "释义七",
        "第二十五条",
        "第一部分",
        # Nothing to strip, and the whole text is itself a reference name.
        "王先生",
        "十日",
        "Ⅳ级",
    ],
)
def test_clean_alias_rejects_what_usable_alias_rejects(text):
    assert clean_alias(text) is None
