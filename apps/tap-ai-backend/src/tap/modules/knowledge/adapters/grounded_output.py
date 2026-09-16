"""Provider-neutral closed validation for grounded answer output."""

from __future__ import annotations

from tap.modules.knowledge.domain.models import Evidence
from tap.modules.knowledge.ports.models import GeneratedClaim


def parse_grounded_answer_payload(
    payload: object,
    evidence: tuple[Evidence, ...],
    *,
    max_answer_chars: int,
    max_claims: int,
    max_claim_chars: int,
    max_labels_per_claim: int,
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

    allowed_labels = {item.evidence_label for item in evidence}
    answer_paragraphs = answer.split("\n\n")
    claims: list[GeneratedClaim] = []
    seen_claims: set[str] = set()
    all_claims_are_unique_paragraphs = True
    for raw in raw_claims:
        if not isinstance(raw, dict) or set(raw) != {"text", "evidenceLabels"}:
            raise ValueError("claim must use the closed schema")
        text, labels = raw["text"], raw["evidenceLabels"]
        if not _bounded_utf8_string(text, maximum=max_claim_chars) or "\n\n" in text:
            raise ValueError("claim text is outside the closed bound")
        is_unique_paragraph = answer_paragraphs.count(text) == 1
        is_unique_statement = answer.count(text) == 1 and text[-1] in ".!?。！？"
        if not (is_unique_paragraph or is_unique_statement):
            raise ValueError("claim text is not one unique complete answer statement")
        all_claims_are_unique_paragraphs &= is_unique_paragraph
        if text in seen_claims:
            raise ValueError("claim text is duplicated")
        if not isinstance(labels, list) or not 1 <= len(labels) <= max_labels_per_claim:
            raise ValueError("claim evidence label count is outside the closed bound")
        if any(
            not _bounded_utf8_string(label, maximum=64) or label not in allowed_labels
            for label in labels
        ):
            raise ValueError("claim references an unknown evidence label")
        if len(set(labels)) != len(labels):
            raise ValueError("claim evidence labels are duplicated")
        seen_claims.add(text)
        claims.append(GeneratedClaim(text=text, evidence_labels=tuple(labels)))
    canonical_answer = (
        answer if all_claims_are_unique_paragraphs else "\n\n".join(claim.text for claim in claims)
    )
    return canonical_answer, tuple(claims)


def _bounded_utf8_string(value: object, *, maximum: int) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
