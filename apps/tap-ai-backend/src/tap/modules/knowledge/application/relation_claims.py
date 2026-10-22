"""Reconcile generated claims against R-labeled relation evidence.

The model may cite an `R` label without the claim text actually naming both
endpoints of that edge, cite a label that does not exist in this turn's
`RelationContext`, cite one minted under a stale graph version, or cite a
relation whose own support is not actually grounded for this claim. The
endpoint-mention text checks (`claim_mentions_both_endpoints`,
`relation_support_mentions_both_endpoints`) are necessary but not
sufficient on their own -- a chunk can name both endpoints verbatim while
being about something else entirely (e.g. an unrelated definitions clause
that merely lists one endpoint among several examples) -- so a further,
claim-shaped check applies:

- When the claim cites its own S evidence, a relation's support must either
  be one of those same chunks, or -- for a snippet/other, not-yet-cited
  chunk -- share both the source revision and the section (nearest heading)
  of one of them. An S-cited chunk elsewhere in the answer, or no section
  information at all, does not establish this. A shared nearest heading is
  not trusted, however, when that heading is itself a definitions section
  (ends with 释义/定义/名词解释, or the chunk's full heading path contains
  one): such a section's chunks commonly share one heading while each
  defining an unrelated term, so only an exact chunk match grounds a
  citation there.
- When the claim cites no S evidence at all, there is nothing from the
  claim itself to check support against. The relation's support must then
  be part of the answer's own retrieved context (an S-labelled chunk, cited
  by this claim or not) rather than a snippet never retrieved this turn --
  a fresh snippet with no S anchor in this same claim has nothing to ground
  it. This, together with the endpoint-mention checks, replaces this
  module's earlier purely self-evidencing behavior for claims with no S,
  which let an unrelated snippet-only support through on text alone.

This module re-checks every `R` label on every claim after generation and
strips what does not hold, dropping a claim entirely only if nothing
legitimate is left to cite it.
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from tap.modules.graph.domain.vocabulary import normalize_key
from tap.modules.knowledge.application.relation_analysis import (
    RelationContext,
    RelationContextStatus,
    RelationEvidence,
    RelationSupport,
)
from tap.modules.knowledge.ports.models import AnswerGeneration, GeneratedClaim

__all__ = [
    "ClaimChunkSection",
    "ReconciledClaims",
    "claim_mentions_both_endpoints",
    "nearest_heading",
    "normalized_heading_path",
    "reconcile_relation_claims",
    "relation_support_is_grounded_for_claim",
    "relation_support_mentions_both_endpoints",
]


@dataclass(frozen=True, slots=True)
class ClaimChunkSection:
    """One S-cited chunk's identity and location, as needed to judge
    whether a relation's support shares its section: `chunk_id` for the
    exact-chunk check; `source_revision_id` and `heading_path` (the
    chunk's full heading path, each entry already normalized by
    `normalized_heading_path`, or `()` when the chunk carries no heading
    path) for the same-section fallback -- the *full* path, not just the
    nearest heading, because detecting a definitions section can require
    an ancestor heading the chunk's own nearest heading does not repeat."""

    chunk_id: str
    source_revision_id: str
    heading_path: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReconciledClaims:
    claims: tuple[GeneratedClaim, ...]
    text: str
    stripped: int
    dropped: int


def reconcile_relation_claims(
    generation: AnswerGeneration,
    context: RelationContext | None,
    *,
    graph_version: str | None,
    evidence_content: Mapping[str, str] = MappingProxyType({}),
    label_chunk_sections: Mapping[str, ClaimChunkSection] = MappingProxyType({}),
) -> ReconciledClaims:
    """`evidence_content` maps a chunk ID to its text, for every chunk the
    current turn's S evidence selection resolved (i.e. `Evidence.content`
    keyed by `Evidence.chunk_id`). It lets an S-backed relation support
    (`RelationSupport.evidence_label` set, `snippet` unset by design -- see
    `RelationSupport`) be checked against its own text instead of being
    trusted on label match alone; a snippet-only support already carries its
    text and needs no lookup. A chunk missing from the mapping is treated as
    unverifiable, not invalid, so omitting this argument (or passing a
    partial mapping) cannot strip a citation that the stricter check below
    was never given the means to evaluate.

    `label_chunk_sections` maps an S evidence label to its `ClaimChunkSection`
    (i.e. built from `Evidence.chunk_id`/`.source.revision`/`.source.anchor`,
    keyed by `Evidence.evidence_label`). It drives `relation_support_is_grounded_for_claim`:
    a relation's support must match one of *this claim's own* S citations by
    chunk ID or by (revision, section) -- a chunk cited as S only by some
    other claim in the answer does not count, by design (see that function's
    docstring for why "cited somewhere in the answer" was tried and
    reverted). Omitting this argument (or passing a partial mapping)
    degrades to "no section information for this label," which is treated
    as not matching, not as unverifiable -- unlike `evidence_content`, there
    is no claim-independent default here that would not risk hiding the
    same false-positive the claim-shaped check exists to catch."""
    by_label = (
        context.by_label()
        if context is not None and context.status is RelationContextStatus.APPLIED
        else {}
    )
    retained: list[GeneratedClaim] = []
    stripped = 0
    dropped = 0
    for claim in generation.claims:
        claim_sections = tuple(
            label_chunk_sections[label]
            for label in claim.evidence_labels
            if label.startswith("S") and label in label_chunk_sections
        )
        kept_labels: list[str] = []
        for label in claim.evidence_labels:
            if not label.startswith("R"):
                kept_labels.append(label)
                continue
            relation = by_label.get(label)
            is_valid = (
                relation is not None
                and context is not None
                and context.graph_version == graph_version
                and claim_mentions_both_endpoints(claim.text, relation)
                and relation_support_mentions_both_endpoints(relation, evidence_content)
                and relation_support_is_grounded_for_claim(relation, claim_sections)
            )
            if is_valid:
                kept_labels.append(label)
            else:
                stripped += 1
        if kept_labels:
            if len(kept_labels) == len(claim.evidence_labels):
                retained.append(claim)
            else:
                retained.append(dataclasses.replace(claim, evidence_labels=tuple(kept_labels)))
        else:
            dropped += 1
    dropped_texts = tuple(
        claim.text
        for claim in generation.claims
        if claim.text not in {kept.text for kept in retained}
    )
    text = answer_without_claims(generation.text, dropped_texts, retained)
    return ReconciledClaims(claims=tuple(retained), text=text, stripped=stripped, dropped=dropped)


_EMPTY_LIST_LINE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)]|#{1,6})?[ \t]*$")


def answer_without_claims(
    text: str, dropped_texts: tuple[str, ...], retained: Sequence[GeneratedClaim]
) -> str:
    """The answer with only the dropped claims' spans removed (span-level
    citations: uncited text and list markup stay visible as unsupported text),
    plus any line left holding nothing but a list marker. Falls back to the
    retained claims' texts joined by blank lines -- the former behavior --
    when a dropped claim cannot be located exactly once; with no claim
    retained the answer is empty, as before."""

    if not retained:
        return ""
    result = text
    for dropped in dropped_texts:
        if result.count(dropped) != 1:
            return "\n\n".join(claim.text for claim in retained)
        start = result.index(dropped)
        line_start = result.rfind("\n", 0, start) + 1
        line_end = result.find("\n", start + len(dropped))
        line_end = len(result) if line_end == -1 else line_end
        line = result[line_start:start] + result[start + len(dropped) : line_end]
        if _EMPTY_LIST_LINE.match(line):
            result = result[:line_start] + result[min(line_end + 1, len(result)) :]
        else:
            result = result[:start] + result[start + len(dropped) :]
    if dropped_texts:
        # A removed paragraph leaves its blank-line separators behind.
        result = re.sub(r"\n{3,}", "\n\n", result).strip("\n")
    return result


# A single trailing full- or half-width parenthetical group, e.g. the
# "（非切开心包手术）" in "冠状动脉介入手术（非切开心包手术）": a merged graph
# node's label commonly carries a disambiguating qualifier that prose naming
# the same entity typically omits. Only the one group at the very end is a
# candidate -- not a general bracket-stripping pass -- so a label whose
# meaningful parenthetical content sits mid-string is left alone.
_TRAILING_PARENTHETICAL = re.compile(r"[（(][^（）()]*[）)]\s*$")
# A normalized key shorter than this is rejected as a match even if it
# literally occurs in the text: a single ideograph or character (after
# punctuation/whitespace folding) is common enough as a substring of
# unrelated text that treating it as naming the entity is an unacceptable
# false-positive risk (e.g. a one-character label matching any sentence that
# happens to contain that character for an unrelated reason).
_MIN_MATCH_KEY_LENGTH = 2


def claim_mentions_both_endpoints(text: str, relation: RelationEvidence) -> bool:
    normalized_text = normalize_key(text)
    subject_terms = _endpoint_term_variants(relation.subject_label, relation.subject_aliases)
    object_terms = _endpoint_term_variants(relation.object_label, relation.object_aliases)
    return _mentions_any(normalized_text, subject_terms, object_terms) and _mentions_any(
        normalized_text, object_terms, subject_terms
    )


def _strip_trailing_qualifier(term: str) -> str | None:
    stripped = _TRAILING_PARENTHETICAL.sub("", term, count=1)
    return stripped if stripped != term else None


def _endpoint_term_variants(label: str, aliases: tuple[str, ...]) -> tuple[str, ...]:
    """A node's label or alias, plus (when present) that same text with its
    trailing parenthetical qualifier stripped -- either form naming the
    entity is accepted, since `normalize_key` already folds width, case,
    accents, whitespace and punctuation but cannot know a qualifier is
    optional context rather than part of the name itself."""
    variants: list[str] = []
    for term in (label, *aliases):
        variants.append(term)
        stripped = _strip_trailing_qualifier(term)
        if stripped is not None:
            variants.append(stripped)
    return tuple(variants)


# Known same-domain role labels where a shorter one is textually a suffix
# of a longer one while naming a different role: "保险人" (the insurer) is
# a substring of "被保险人" (the insured); "投保人" (the policyholder) is
# listed alongside them even though it happens not to contain "保险人" as a
# substring, since it is the third member of the same commonly-confused
# triad and a future label/alias in this family should not have to be
# special-cased again. An occurrence of the shorter term found only inside
# one of these longer ones must not count as naming the shorter term's own
# entity.
_OVERLAPPING_ROLE_LABELS: tuple[str, ...] = ("投保人", "被保险人", "保险人")


def _excluding_longer_terms(key: str, other_terms: tuple[str, ...]) -> tuple[str, ...]:
    """Normalized terms strictly longer than `key` and containing it as a
    substring, drawn from the fixed overlapping-role triad plus the other
    endpoint's own terms: an occurrence of `key` found only inside one of
    these must not count as a match for `key` itself -- "被保险人" naming
    the insured must never also register as a "保险人" sighting naming the
    insurer, and symmetrically the other endpoint's own longer term must
    never let a shorter unrelated term it happens to contain pass as a
    mention of that shorter entity."""
    candidates = (*_OVERLAPPING_ROLE_LABELS, *other_terms)
    longer: list[str] = []
    for candidate in candidates:
        normalized_candidate = normalize_key(candidate) if candidate else ""
        if (
            normalized_candidate
            and normalized_candidate != key
            and key in normalized_candidate
            and normalized_candidate not in longer
        ):
            longer.append(normalized_candidate)
    return tuple(longer)


def _mentions_any(
    normalized_text: str, terms: tuple[str, ...], other_terms: tuple[str, ...] = ()
) -> bool:
    for term in terms:
        key = normalize_key(term) if term else ""
        if len(key) < _MIN_MATCH_KEY_LENGTH:
            continue
        longer_terms = _excluding_longer_terms(key, other_terms)
        if not longer_terms:
            if key in normalized_text:
                return True
            continue
        reduced = normalized_text
        for longer in longer_terms:
            reduced = reduced.replace(longer, "\x00" * len(longer))
        if key in reduced:
            return True
    return False


def _support_text(support: RelationSupport, evidence_content: Mapping[str, str]) -> str | None:
    if support.snippet is not None:
        return support.snippet
    if support.evidence_label is not None:
        return evidence_content.get(support.chunk_id)
    return None


def relation_support_mentions_both_endpoints(
    relation: RelationEvidence, evidence_content: Mapping[str, str]
) -> bool:
    """A relation's own supporting evidence -- not just the generated claim
    text -- must actually discuss both endpoints together, or the citation
    is not grounded: the merged graph can associate an edge with a chunk
    that only shares surface terms with its subject/object labels (e.g. a
    definitions clause that happens to name the object but is about a
    different rule entirely). Checked against every support item in turn
    (not just the first) because a relation can carry several; one relevant
    support is enough. A support whose text cannot be determined here (not a
    snippet, and its chunk is outside `evidence_content`) is skipped rather
    than treated as a failure -- this check only strips a citation it can
    actually evaluate as irrelevant. If no support text is available at all,
    the relation is not penalized (returns True): absence of means to check
    is not evidence of a mismatch."""
    texts = [
        text
        for support in relation.support
        if (text := _support_text(support, evidence_content)) is not None
    ]
    if not texts:
        return True
    return any(claim_mentions_both_endpoints(text, relation) for text in texts)


def normalized_heading_path(headings: object) -> tuple[str, ...]:
    """Every entry of a `headingPath`-shaped list, each normalized (NFKC,
    casefold, whitespace-fold) the same way `retrieve.py`'s `_clause_heading`
    normalizes a chunk's last heading -- except here the *whole* path is
    kept, not just the last entry, because detecting a definitions section
    (`_heading_path_has_definitions_section`) can require an ancestor
    heading a chunk's own nearest heading does not repeat (e.g. a specific
    glossary entry's nearest heading is its own term, while its section
    heading two levels up is "...释义"). Exported so `retrieve.py` can build
    matching `ClaimChunkSection.heading_path` values from the claim side
    with the exact same normalization used here for the support side."""
    if not isinstance(headings, (list, tuple)):
        return ()
    normalized: list[str] = []
    for item in headings:
        if not isinstance(item, str):
            continue
        value = " ".join(unicodedata.normalize("NFKC", item).casefold().split())
        if value:
            normalized.append(value)
    return tuple(normalized)


def nearest_heading(headings: object) -> str | None:
    """The normalized last heading in a `headingPath`-shaped list, or
    `None` when there is none to normalize. A thin convenience wrapper over
    `normalized_heading_path` for a caller that only needs the nearest
    heading, not the full path."""
    path = normalized_heading_path(headings)
    return path[-1] if path else None


# A heading ending in one of these names a definitions/glossary section
# whose entries commonly share one heading while each defining an unrelated
# term (the real report's "第二十七条 释义" is exactly this shape: many
# distinct glossary chunks, one shared section heading). A shared nearest
# heading is too coarse a signal there; see
# `relation_support_is_grounded_for_claim`.
_DEFINITIONS_HEADING_SUFFIXES = ("释义", "定义", "名词解释")


def _is_definitions_heading(heading: str) -> bool:
    return heading.endswith(_DEFINITIONS_HEADING_SUFFIXES)


def _heading_path_has_definitions_section(heading_path: tuple[str, ...]) -> bool:
    """Whether any heading in the path -- not just the nearest one -- names
    a definitions section, so a shared nearest heading elsewhere in the
    same path still gets caught."""
    return any(_is_definitions_heading(heading) for heading in heading_path)


def relation_support_is_grounded_for_claim(
    relation: RelationEvidence,
    claim_sections: tuple[ClaimChunkSection, ...],
) -> bool:
    """The claim-shaped grounding check: when the claim cites its own S
    evidence (`claim_sections` non-empty), a relation's support must be one
    of those same chunks, or -- for a snippet/other chunk not yet cited --
    share both the source revision and the section (nearest heading) of one
    of them, unless that shared heading is itself a definitions section (see
    `_heading_path_has_definitions_section`), in which case only the exact-
    chunk match above counts. A chunk cited as S only by some *other* claim
    in the answer, or with no section information on either side, does not
    establish this. ("Cited anywhere in the answer" was tried first and
    reverted: it let an unrelated support through whenever the answer
    happened to cite S evidence elsewhere, and -- backwards -- stripped a
    legitimate R-only claim whenever any other claim's S chunk did not
    happen to overlap, while an answer with no S evidence anywhere passed
    everything. Judging each claim only against its own S evidence removes
    both.)

    When the claim cites no S evidence at all (`claim_sections` empty),
    there is nothing from the claim itself to check support against, so the
    relation's support must instead be part of the answer's own retrieved
    context -- i.e. an S-labelled chunk (`RelationSupport.evidence_label`
    set, meaning it matched one of the chunks this turn's retrieval or
    augmentation actually surfaced; see `relation_analysis.py`'s
    `_resolve_support` and `RelationContext.rebind`), cited by this claim or
    not. A snippet-only support (never retrieved this turn, known only
    through the merged graph's own fragment evidence) has no S anchor to
    fall back on in this claim and is not grounded, regardless of what its
    text says -- `claim_mentions_both_endpoints` and
    `relation_support_mentions_both_endpoints` alone previously let such a
    support through on text-only grounds; this closes that gap."""
    if not claim_sections:
        return any(support.evidence_label is not None for support in relation.support)
    claim_chunk_ids = {section.chunk_id for section in claim_sections}
    support_chunk_ids = {support.chunk_id for support in relation.support}
    if support_chunk_ids & claim_chunk_ids:
        return True
    claim_locations = {
        (section.source_revision_id, section.heading_path[-1]): section.heading_path
        for section in claim_sections
        if section.heading_path
    }
    if not claim_locations:
        return False
    for support in relation.support:
        support_path = normalized_heading_path(support.anchor.get("headingPath"))
        if not support_path:
            continue
        claim_path = claim_locations.get((support.source_revision_id, support_path[-1]))
        if claim_path is None:
            continue
        if _heading_path_has_definitions_section(
            claim_path
        ) or _heading_path_has_definitions_section(support_path):
            continue
        return True
    return False
