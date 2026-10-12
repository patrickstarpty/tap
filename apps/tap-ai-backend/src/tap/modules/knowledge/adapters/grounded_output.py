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
        if claim is None:
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


def _bounded_utf8_string(value: object, *, maximum: int) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
