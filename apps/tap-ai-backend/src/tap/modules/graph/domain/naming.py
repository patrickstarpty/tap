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
import unicodedata

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
# Pronouns compare case-sensitively on the stripped text, so the acronyms they fold
# onto ("IT", "US", "WE") stay names.
_ENGLISH_PRONOUNS = frozenset(
    form
    for word in (
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
    )
    for form in (word, word.capitalize())
)
# Compared after ``normalize_key``.
_ENGLISH_REFERENCES = frozenset(
    {
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
_CHINESE_DIGIT = "一二三四五六七八九壹贰叁肆伍陆柒捌玖"
_NUMERAL = "0-9零〇两十百千拾佰仟" + _CHINESE_DIGIT
# A well-formed positional Chinese number (十九, 三十九, 一百零五, 两千); a run of
# bare digits such as 三九 or 九九 is a name, not a number.
_CHINESE_POSITIONAL = re.compile(
    rf"(?:[{_CHINESE_DIGIT}两]?[千仟])?零?(?:[{_CHINESE_DIGIT}两]?[百佰])?零?"
    rf"(?:[{_CHINESE_DIGIT}]?[十拾])?[{_CHINESE_DIGIT}]?"
)
# Digit by digit with a zero (二〇二五, 一〇一).
_CHINESE_DIGIT_RUN = re.compile(rf"[{_CHINESE_DIGIT}零〇]*[零〇][{_CHINESE_DIGIT}零〇]*")
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
        # this contract, the policy, said agreement, ...
        r"(this|the|such|said|that|these|those)"
        r"(contract|contracts|policy|policies|agreement|plan|product|company|clause|section"
        r"|article|schedule|document|insurance|rider|terms|provision|provisions|above|same)",
        # 十日, 九十日: a bare day count with no qualifying noun. Real-model output
        # used this shape ("十日") as an alias of the statutory cooling-off period,
        # which collides with any other period a different product happens to set
        # at the same number of days. A count that keeps its noun ("六十日宽限期")
        # is a different, longer string and is not matched here.
        r"[0-9〇零一二三四五六七八九十百千]+[日天]",
        # IV 级, Ⅲ级, 四级, 十级: a bare classification grade. Real-model output merged
        # three unrelated classification systems (NYHA heart function, rheumatoid
        # arthritis function) across one document this way. A grade that keeps its
        # qualifying noun ("心功能分级Ⅲ级") is a different, longer string and is not
        # matched here.
        r"(?:[ivxlc]+|[0-9]+|[一二三四五六七八九十]+)级",
        # 王先生, 王先生的儿子, 李女士: a brochure's illustrative example person, never
        # a real entity name.
        r"[一-鿿]{1,2}(先生|女士|小姐)(的[一-鿿]{1,10})?",
        # 5岁被保险人, 三岁以上被保险人: a role restated with a bare age qualifier, not
        # an alternate name for the role.
        r"[0-9〇零一二三四五六七八九十百]+(周)?岁(以上|以下)?(被保险人|投保人|受益人)",
        # 疾病定义一.9, 释义七: a clause/definition locator with no term attached (the
        # "." is punctuation and already gone from ``key`` by this point, so "一.9"
        # and "一9" are the same match here; contrast with "全残（释义七）", where the
        # parenthetical is only a suffix on a real term and is stripped by
        # ``clean_alias`` instead of rejecting the whole alias).
        r"(疾病定义|释义|条款释义)[0-9〇零一二三四五六七八九十]+",
    )
)
# A single overly generic noun: real enough to mention, far too broad to identify one
# entity across documents ("死亡证明"'s alias "证明" could mean any certificate at all).
_GENERIC_ALONE = frozenset({"证明"})
# Article 5, Section 3a, § 4, Schedule B, Part IV, Annex A1: matched on the text
# with punctuation turned into spaces, so a letter suffix must stand apart from the
# structural word ("Part C", never "Parts"; "Annex A1", never "Annexe").
_STRUCTURAL_WORD = (
    r"(section|sec|article|art|clause|chapter|chap|schedule|appendix|annex|exhibit|part"
    r"|item|paragraph|para|§)"
)
_STRUCTURAL_REFERENCE = re.compile(
    rf"{_STRUCTURAL_WORD}(\s*[0-9]+[a-z]?|\s+([ivxlc]+|[a-z][0-9]*))"
)


def _is_number(key: str) -> bool:
    """19, 十九, 二〇二五, 1.2 (folded to 12), 19条, ...: a number or numbering."""

    if key[-1] in "条章节款项点":
        key = key[:-1]
    if not key:
        return False
    if key.isascii():
        return key.isdigit()
    return bool(
        _CHINESE_POSITIONAL.fullmatch(key)
        and any(unit in key for unit in "十拾百佰千仟")
        or _CHINESE_DIGIT_RUN.fullmatch(key)
    )


def _spaced(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(
        "".join(" " if unicodedata.category(ch).startswith("P") else ch for ch in folded).split()
    )


def is_reference_name(text: str) -> bool:
    """True when ``text`` cannot name an entity on its own (see the module doc)."""

    key = normalize_key(text)
    if (
        len(key) < 2
        or key in _CHINESE_REFERENCES
        or key in _ENGLISH_REFERENCES
        or key in _GENERIC_ALONE
        or text.strip() in _ENGLISH_PRONOUNS
        or _is_number(key)
    ):
        return True
    if _STRUCTURAL_REFERENCE.fullmatch(_spaced(text)):
        return True
    return any(pattern.fullmatch(key) for pattern in _PATTERNS)


def usable_alias(text: str) -> bool:
    """True when ``text`` may be kept as a model-provided alias."""

    return len(text.strip()) <= ALIAS_LENGTH_MAX and not is_reference_name(text)


# A leading clause/article number ("第二十五条 ", "第9章"), matched (and stripped) on
# the raw text rather than ``key`` so the readable remainder keeps its own spacing.
_LEADING_ARTICLE = re.compile(rf"^第[{_NUMERAL}]+[条章节款项部篇编目段页点]\s*")
# A trailing interpretation-note parenthetical ("（释义七）", "(释义3)").
_TRAILING_DEFINITION_NOTE = re.compile(
    r"[（(]\s*释义[0-9〇零一二三四五六七八九十百]+\s*[）)]\s*$"
)


def clean_alias(text: str) -> str | None:
    """Return ``text`` usable as a displayed or merge-key alias, or ``None``.

    A clause-numbered or interpretation-noted alias ("第二十五条 明确说明与如实
    告知", "全残（释义七）") carries a real term; only the affix around it is a
    reference. Stripping it first -- before falling back to the plain
    ``usable_alias`` check -- turns both into their real term ("明确说明与如实
    告知", "全残") instead of leaving the affix on display or keying alignment
    on a string two documents would spell differently. A pure locator with no
    term attached ("疾病定义一.9", "释义七" on its own) has nothing left once the
    affix is gone and is rejected outright by ``usable_alias`` already.
    """

    stripped = text.strip()
    if not stripped:
        return None
    core = _TRAILING_DEFINITION_NOTE.sub("", _LEADING_ARTICLE.sub("", stripped, count=1), count=1)
    core = core.strip()
    if core and core != stripped and usable_alias(core):
        return core
    return stripped if usable_alias(stripped) else None
