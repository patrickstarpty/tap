"""Reconcile generated claims against R-labeled relation evidence.

The model may cite an `R` label without the claim text actually naming both
endpoints of that edge, or cite a label that does not exist in this turn's
`RelationContext`, or one minted under a stale graph version. This module
re-checks every `R` label on every claim after generation and strips what
does not hold, dropping a claim entirely only if nothing legitimate is left
to cite it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from tap.modules.graph.domain.vocabulary import normalize_key
from tap.modules.knowledge.application.relation_analysis import (
    RelationContext,
    RelationContextStatus,
    RelationEvidence,
)
from tap.modules.knowledge.ports.models import AnswerGeneration, GeneratedClaim

__all__ = ["ReconciledClaims", "claim_mentions_both_endpoints", "reconcile_relation_claims"]


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
) -> ReconciledClaims:
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


def claim_mentions_both_endpoints(text: str, relation: RelationEvidence) -> bool:
    normalized_text = normalize_key(text)
    subject_terms = (relation.subject_label, *relation.subject_aliases)
    object_terms = (relation.object_label, *relation.object_aliases)
    return _mentions_any(normalized_text, subject_terms) and _mentions_any(
        normalized_text, object_terms
    )


def _mentions_any(normalized_text: str, terms: tuple[str, ...]) -> bool:
    return any(term and normalize_key(term) in normalized_text for term in terms)
