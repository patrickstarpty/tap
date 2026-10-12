from __future__ import annotations

import pytest

from tap.modules.graph.domain.naming import ALIAS_LENGTH_MAX, is_reference_name, usable_alias


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
        "九十日",
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


def test_aliases_are_short_names_not_sentences():
    assert ALIAS_LENGTH_MAX == 32
    assert usable_alias("甲" * 32)
    # Real outputs used whole clauses as aliases, costing output tokens for nothing.
    assert not usable_alias(
        "本公司在合同订立时已经知道投保人未如实告知的情况的，本公司不得解除合同"
    )
