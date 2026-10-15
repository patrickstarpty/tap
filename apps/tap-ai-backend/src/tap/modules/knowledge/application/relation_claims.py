"""Reconcile generated claims against R-labeled relation evidence.

The model may cite an `R` label without the claim text actually naming both
endpoints of that edge, cite a label that does not exist in this turn's
`RelationContext`, cite one minted under a stale graph version, or cite a
relation whose own supporting evidence text never actually discusses both
endpoints together (the merged graph can associate an edge with a chunk that
only happens to share surface terms with the edge's labels, e.g. an
unrelated definitions clause). This module re-checks every `R` label on
every claim after generation and strips what does not hold, dropping a
claim entirely only if nothing legitimate is left to cite it.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
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
    "ReconciledClaims",
    "claim_mentions_both_endpoints",
    "reconcile_relation_claims",
    "relation_support_mentions_both_endpoints",
]


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
    was never given the means to evaluate."""
    by_label = (
        context.by_label()
        if context is not None and context.status is RelationContextStatus.APPLIED
        else {}
    )
    retained: list[GeneratedClaim] = []
    stripped = 0
    dropped = 0
    for claim in generation.claims:
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
    text = "\n\n".join(claim.text for claim in retained)
    return ReconciledClaims(claims=tuple(retained), text=text, stripped=stripped, dropped=dropped)


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
    return _mentions_any(normalized_text, subject_terms) and _mentions_any(
        normalized_text, object_terms
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


def _mentions_any(normalized_text: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        key = normalize_key(term) if term else ""
        if len(key) >= _MIN_MATCH_KEY_LENGTH and key in normalized_text:
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
