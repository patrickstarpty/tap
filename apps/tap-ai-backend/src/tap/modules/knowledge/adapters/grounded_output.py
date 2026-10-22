"""Provider-neutral closed validation for grounded answer output."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence

from tap.modules.knowledge.domain.models import DocumentAnchor, Evidence
from tap.modules.knowledge.ports.models import GeneratedClaim


def parse_grounded_answer_payload(
    payload: object,
    evidence: tuple[Evidence, ...],
    *,
    max_answer_chars: int,
    max_claims: int,
    max_claim_chars: int,
    max_labels_per_claim: int,
    extra_labels: frozenset[str] = frozenset(),
) -> tuple[str, tuple[GeneratedClaim, ...]]:
    """Validate one closed grounded payload without provider-specific behavior."""

    if not isinstance(payload, dict) or set(payload) != {"answer", "claims"}:
        raise ValueError("grounded answer output must use the closed schema")
    answer = payload["answer"]
    raw_claims = payload["claims"]
    if raw_claims == []:
        if answer == "" or _bounded_utf8_string(answer, maximum=max_answer_chars):
            return "", ()
        raise ValueError("answer is outside the closed bound")
    if not _bounded_utf8_string(answer, maximum=max_answer_chars):
        raise ValueError("answer is outside the closed bound")
    if not isinstance(raw_claims, list) or not 1 <= len(raw_claims) <= max_claims:
        raise ValueError("claim count is outside the closed bound")

    allowed_labels = {item.evidence_label for item in evidence} | extra_labels
    label_documents = {item.evidence_label: item.source.source_id for item in evidence}
    document_names = _document_names(evidence)
    answer_paragraphs = answer.split("\n\n")
    claims: list[GeneratedClaim] = []
    seen_claims: set[str] = set()
    all_claims_are_unique_paragraphs = True
    for raw in raw_claims:
        # A claim that cannot be aligned with the answer and its evidence is dropped rather
        # than failing the whole answer; with no claim left the caller abstains.
        claim = _grounded_claim(
            raw,
            answer,
            answer_paragraphs,
            allowed_labels,
            seen_claims,
            max_claim_chars=max_claim_chars,
            max_labels_per_claim=max_labels_per_claim,
        )
        if claim is None or _borrows_another_document(claim, label_documents, document_names):
            continue
        all_claims_are_unique_paragraphs &= answer_paragraphs.count(claim.text) == 1
        seen_claims.add(claim.text)
        claims.append(claim)
    if not claims:
        return "", ()
    canonical_answer = (
        answer
        if all_claims_are_unique_paragraphs and len(claims) == len(raw_claims)
        else "\n\n".join(claim.text for claim in claims)
    )
    return canonical_answer, tuple(claims)


_CODE_SPAN = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
# Label grammar only (`S1`..`S999`, `R1`..`R999`, `I1`..`I999` -- PR 5 Task 8
# adds Insights metric labels; the real allow-list below bounds this further
# to the evidence/relation/insights labels actually in play) so a Markdown
# link (`[docs](url)`), a literal `[x]`, or an unrelated `a[0]` index is never
# mistaken for a citation marker. Spaces/tabs just inside the brackets are
# tolerated: real qwen-plus output intermittently pads markers as `[ S1 ]`,
# and an unmatched padded marker left the sentence without a claim, so the
# whole turn abstained despite a grounded answer.
_MARKER = re.compile(r"\[[ \t]*([SRI][1-9][0-9]{0,2})[ \t]*\]")
# CJK terminators always end a sentence; a newline always does (so a Markdown
# list item/heading line is its own sentence). An ASCII `.!?` only ends a
# sentence when whitespace, end of text, or a citation marker follows --
# never mid-token, so "v 1.2", a decimal, or a period inside a URL/Markdown
# link target (e.g. "example.com/robots.txt") never splits (review ruling,
# 2026-10-16 round 2).
_CJK_TERMINATORS = "。！？"
_ASCII_TERMINATORS = ".!?"
_LIST_OR_HEADING_PREFIX = re.compile(r"^[ \t]*(?:[-*+][ \t]+|\d+\.[ \t]+|#{1,6}[ \t]+)")


def _sentence_delimiter_at(masked: str, index: int) -> bool:
    character = masked[index]
    if character == "\n" or character in _CJK_TERMINATORS:
        return True
    if character not in _ASCII_TERMINATORS:
        return False
    next_index = index + 1
    if next_index >= len(masked):
        return True
    next_character = masked[next_index]
    if next_character in " \t\n":
        return True
    return _MARKER.match(masked, next_index) is not None


def _masked_for_markers(text: str) -> str:
    """`text` with every code span's characters blanked out, same length and
    offsets, so marker and sentence-delimiter detection can run position-for-
    position against it while code-span content is never mistaken for either."""

    masked = list(text)
    for match in _CODE_SPAN.finditer(text):
        for index in range(match.start(), match.end()):
            masked[index] = "\x00"
    return "".join(masked)


def _sentence_spans(text: str, masked: str) -> list[tuple[int, int]]:
    """Contiguous `[start, end)` spans tiling the whole string, one per sentence:
    a run of text up to and including a `。！？.!?` or newline delimiter, plus any
    marker tokens immediately following it (so a marker placed just after the
    terminal punctuation still belongs to the sentence it cites)."""

    length = len(text)
    spans: list[tuple[int, int]] = []
    sentence_start = 0
    index = 0
    while index < length:
        if _sentence_delimiter_at(masked, index):
            end = index + 1
            while True:
                probe = end
                while probe < length and masked[probe] in " \t":
                    probe += 1
                marker = _MARKER.match(masked, probe)
                if marker is None:
                    break
                end = marker.end()
            spans.append((sentence_start, end))
            sentence_start = end
            index = end
        else:
            index += 1
    if sentence_start < length and text[sentence_start:].strip():
        spans.append((sentence_start, length))
    return spans


def _strip_real_markers(text: str, masked: str) -> str:
    """`text` with every real (non-code-span) marker's characters removed and
    everything else -- whitespace, newlines, Markdown headings/lists -- left
    exactly as written. This is the only transformation applied to produce the
    answer body: no whitespace collapsing, so a stray double space left behind
    where a marker was removed is accepted rather than risking a rewrite of
    unrelated text (e.g. "v 1.2")."""

    chars = list(text)
    for match in _MARKER.finditer(masked):
        for index in range(match.start(), match.end()):
            chars[index] = ""
    return "".join(chars)


def _claim_text_and_labels(
    segment: str, masked_segment: str, allowed_labels: set[str]
) -> tuple[str, list[str]]:
    """For one sentence span: strip its real markers and any leading Markdown
    list/heading prefix (`- `, `* `, `1. `, `#`) to get the quoted claim text
    (this does not affect the answer body, which keeps that markup), and
    return the labels found, in first-seen order, deduplicated, restricted to
    `allowed_labels` -- an unknown label is dropped from the label list but its
    token is still removed from the text like any other marker."""

    labels: list[str] = []
    seen: set[str] = set()
    chars = list(segment)
    for match in _MARKER.finditer(masked_segment):
        for index in range(match.start(), match.end()):
            chars[index] = ""
        label = match.group(1)
        if label in allowed_labels and label not in seen:
            seen.add(label)
            labels.append(label)
    cleaned = "".join(chars).strip()
    cleaned = _LIST_OR_HEADING_PREFIX.sub("", cleaned).strip()
    return cleaned, labels


def parse_inline_answer(
    text: str,
    evidence: Sequence[Evidence],
    *,
    extra_labels: frozenset[str] = frozenset(),
    max_answer_chars: int,
    max_claims: int,
    max_claim_chars: int,
    max_labels_per_claim: int,
) -> tuple[str, tuple[GeneratedClaim, ...]]:
    """Parse Markdown answer text carrying inline `[S1]`/`[R1]` citation markers
    (PR 3) into the same `(answer, claims)` shape `parse_grounded_answer_payload`
    produces for the closed JSON schema. A claim is a sentence (split on
    `。！？.!?` or a newline) that carries at least one marker resolving to an
    evidence or relation label; markers inside a fenced or inline code span are
    not markers. The returned answer is `text` with every real marker removed
    and everything else -- whitespace, headings, list markup -- preserved
    verbatim; an uncited sentence (no marker, an unknown label, or a candidate
    later dropped as a duplicate/too-long/borrowed-document) stays visible in
    the answer, it just is not one of the returned claims. When no sentence
    yields a valid claim at all, the caller abstains exactly as it does for an
    empty-claims JSON payload."""

    if not isinstance(text, str) or not text.strip():
        return "", ()

    allowed_labels = set({item.evidence_label for item in evidence} | extra_labels)
    label_documents = {item.evidence_label: item.source.source_id for item in evidence}
    document_names = _document_names(tuple(evidence))

    masked = _masked_for_markers(text)
    spans = _sentence_spans(text, masked)

    candidates: list[tuple[str, list[str]]] = []
    for start, end in spans:
        claim_text, labels = _claim_text_and_labels(
            text[start:end], masked[start:end], allowed_labels
        )
        if claim_text and labels:
            candidates.append((claim_text, labels))

    if not candidates:
        return "", ()
    if len(candidates) > max_claims:
        raise ValueError("claim count is outside the closed bound")

    answer = _strip_real_markers(text, masked)
    if not _bounded_utf8_string(answer, maximum=max_answer_chars):
        raise ValueError("answer is outside the closed bound")
    answer_paragraphs = answer.split("\n\n")

    claims: list[GeneratedClaim] = []
    seen_claims: set[str] = set()
    for claim_text, labels in candidates:
        claim = _grounded_claim(
            {"text": claim_text, "evidenceLabels": labels},
            answer,
            answer_paragraphs,
            allowed_labels,
            seen_claims,
            max_claim_chars=max_claim_chars,
            max_labels_per_claim=max_labels_per_claim,
        )
        if claim is None or _borrows_another_document(claim, label_documents, document_names):
            continue
        seen_claims.add(claim.text)
        claims.append(claim)
    if not claims:
        return "", ()
    return answer, tuple(claims)


def _grounded_claim(
    raw: object,
    answer: str,
    answer_paragraphs: list[str],
    allowed_labels: set[str] | frozenset[str],
    seen_claims: set[str],
    *,
    max_claim_chars: int,
    max_labels_per_claim: int,
) -> GeneratedClaim | None:
    if not isinstance(raw, dict) or set(raw) != {"text", "evidenceLabels"}:
        return None
    text, labels = raw["text"], raw["evidenceLabels"]
    if not _bounded_utf8_string(text, maximum=max_claim_chars) or "\n\n" in text:
        return None
    is_unique_paragraph = answer_paragraphs.count(text) == 1
    # A complete single-line sentence stands on its own even when the model summarised it
    # differently: the canonical answer is rebuilt from claims, so every displayed sentence
    # stays a cited claim.
    is_complete = text[-1] in ".!?。！？"
    is_unique_statement = is_complete and answer.count(text) == 1
    is_complete_sentence = is_complete and "\n" not in text
    if not (is_unique_paragraph or is_unique_statement or is_complete_sentence):
        return None
    if text in seen_claims:
        return None
    if not isinstance(labels, list) or not 1 <= len(labels) <= max_labels_per_claim:
        return None
    if any(
        not _bounded_utf8_string(label, maximum=64) or label not in allowed_labels
        for label in labels
    ):
        return None
    if len(set(labels)) != len(labels):
        return None
    return GeneratedClaim(text=text, evidence_labels=tuple(labels))


# Clause headings ("第二条 投保范围", "一、", "1.2") are sections, not document names.
_CLAUSE_HEADING = re.compile(
    r"^(第[一二三四五六七八九十百零〇\d]+[条章节款]|[一二三四五六七八九十]+、|\d)"
)
_FILE_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}$")
_MIN_DOCUMENT_NAME_CHARS = 4


def _normalized(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _document_names(evidence: tuple[Evidence, ...]) -> dict[str, frozenset[str]]:
    """Names (file stem, document title) each evidence document can be referred to by.

    A name shared by several documents identifies none of them and is ignored.
    """

    names: dict[str, set[str]] = {}
    for item in evidence:
        anchor = item.source.anchor
        if not isinstance(anchor, DocumentAnchor):
            continue
        candidates: list[str] = []
        if item.title:
            candidates.append(_FILE_EXTENSION.sub("", item.title))
        if anchor.heading_path:
            root = anchor.heading_path[0].strip()
            if _CLAUSE_HEADING.match(root) is None:
                candidates.append(root)
        for candidate in candidates:
            name = _normalized(candidate)
            if len(name) >= _MIN_DOCUMENT_NAME_CHARS:
                names.setdefault(name, set()).add(item.source.source_id)
    by_document: dict[str, set[str]] = {}
    for name, documents in names.items():
        if len(documents) == 1:
            by_document.setdefault(next(iter(documents)), set()).add(name)
    return {document: frozenset(items) for document, items in by_document.items()}


def _borrows_another_document(
    claim: GeneratedClaim,
    label_documents: Mapping[str, str],
    document_names: Mapping[str, frozenset[str]],
) -> bool:
    """True when the claim names a document but cites evidence only from other documents."""

    cited = {label_documents[label] for label in claim.evidence_labels if label in label_documents}
    if not cited:
        return False
    text = _normalized(claim.text)
    named = {
        document
        for document, names in document_names.items()
        if any(name in text for name in names)
    }
    return bool(named) and not named & cited


def _bounded_utf8_string(value: object, *, maximum: int) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
