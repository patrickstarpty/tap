"""Which texts may name a graph entity: one rule set for extraction and alignment.

Contract text refers to things by deixis ("本合同", "this Policy", "本公司") and by
structure ("第十九条", "Article 5", "附表1"). Those words mean a different thing in
every document, so as a label or alias they merge unrelated entities across
documents (in the real-model run, "本合同" merged a group medical product with a
long-term care product and "第十九条" merged two unrelated clauses). They are
rejected here, with numbers, numbering and names shorter than two characters.
"""

from __future__ import annotations

import re

from tap.modules.graph.domain.vocabulary import normalize_key

# An alias is another short name for the entity, not a sentence quoted from it.
ALIAS_LENGTH_MAX = 32

# Compared after ``normalize_key`` (case, width, whitespace and punctuation folded).
_CHINESE_REFERENCES = frozenset(
    {
        "上述",
        "前述",
        "下述",
        "前款",
        "前条",
        "前项",
        "下列",
        "以下",
        "如下",
        "同上",
        "其他",
        "其它",
        "本人",
        "我们",
        "我司",
        "你们",
        "他们",
        "她们",
        "它们",
        "对方",
        "双方",
        "各方",
    }
)
_ENGLISH_REFERENCES = frozenset(
    {
        "we",
        "us",
        "our",
        "ours",
        "you",
        "your",
        "yours",
        "it",
        "its",
        "they",
        "them",
        "their",
        "he",
        "she",
        "him",
        "her",
        "above",
        "theabove",
        "aforementioned",
        "aforesaid",
        "hereof",
        "herein",
        "hereunder",
        "hereto",
        "thereof",
        "therein",
        "same",
        "thesame",
    }
)
_NUMERAL = "0-9零〇一二三四五六七八九十百千两壹贰叁肆伍陆柒捌玖拾佰"
_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        # 本合同, 本附加合同, 该产品, 此条款, 上述条款, 本公司, ...
        r"(本|该|此|上述|前述|下述)(主|附加)?(保险)?"
        r"(合同|条款|保险|产品|计划|公司|协议|保单|险种|条|款|项|章|节|附表|附件|附录"
        r"|文件|规定|办法|细则|约定|内容|情形)",
        # 第十九条, 第3章, 第二节, ...
        rf"第[{_NUMERAL}]+[条章节款项部篇编目段页点]",
        # 附表1, 附件二, 释义四, 条款, ...
        rf"(附表|附件|附录|释义|条款|章节|条|款|项|表|图)[{_NUMERAL}a-z]*",
        # 19, 十九, （一）, 1.2, 19条, ...
        rf"[{_NUMERAL}]+[条章节款项点]?",
        # a1, 1a
        r"[a-z][0-9]+|[0-9]+[a-z]",
        # this contract, the policy, said agreement, ...
        r"(this|the|such|said|that|these|those)"
        r"(contract|contracts|policy|policies|agreement|plan|product|company|clause|section"
        r"|article|schedule|document|insurance|rider|terms|provision|provisions|above|same)",
        # Article 5, Section 3a, § 4, Schedule B, Part IV, ...
        r"(section|sec|article|art|clause|chapter|chap|schedule|appendix|annex|exhibit|part"
        r"|item|paragraph|para|§)([0-9]+[a-z]?|[ivxlc]+|[a-z])",
    )
)


def is_reference_name(text: str) -> bool:
    """True when ``text`` cannot name an entity on its own (see the module doc)."""

    key = normalize_key(text)
    if len(key) < 2 or key in _CHINESE_REFERENCES or key in _ENGLISH_REFERENCES:
        return True
    return any(pattern.fullmatch(key) for pattern in _PATTERNS)


def usable_alias(text: str) -> bool:
    """True when ``text`` may be kept as a model-provided alias."""

    return len(text.strip()) <= ALIAS_LENGTH_MAX and not is_reference_name(text)
