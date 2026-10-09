"""Strict grounded-answer output contracts (restored from the retired LiteLLM strict suite)."""

from __future__ import annotations

import pytest

from tap.modules.knowledge.adapters.grounded_output import parse_grounded_answer_payload
from tap.modules.knowledge.domain.models import (
    CodeAnchor,
    ContentRole,
    Evidence,
    IndexRevision,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)

SOURCE_HASH = "sha256:" + "a" * 64
CHUNK_HASH = "sha256:" + "b" * 64


def evidence(*, label: str = "S1", content: str = "Current policy is required.") -> Evidence:
    return Evidence(
        family=SourceFamily.CODE,
        chunk_id="h_" + "1" * 64,
        logical_chunk_id="h_" + "2" * 64,
        title="authorize",
        content=content,
        source=SourceRevisionRef(
            source_id="repo:checkout:payment.py",
            source_type="code",
            revision_kind=RevisionKind.GIT_COMMIT,
            revision="a" * 40,
            source_content_hash=SOURCE_HASH,
            anchor=CodeAnchor(
                repo="checkout",
                path="payment.py",
                symbol="authorize",
                line_start=10,
                line_end=25,
            ),
        ),
        chunk_content_hash=CHUNK_HASH,
        content_role=ContentRole.SOURCE,
        citation_id="citation-1",
        evidence_label=label,
        index_revision=IndexRevision(
            physical_index="kb-code-v1-20260824",
            schema_version="search-schema-v1",
            corpus_version="corpus-17",
        ),
        embedding_model_version="tap-embed-fixed-v1",
        acl_decision_id="decision-17",
        score=1 / 61,
    )


def valid_grounded_payload() -> dict[str, object]:
    return {
        "answer": "退款审批需要两名审批人。\n\nKeep the original SLA term.",
        "claims": [
            {"text": "退款审批需要两名审批人。", "evidenceLabels": ["S1"]},
            {"text": "Keep the original SLA term.", "evidenceLabels": ["S2"]},
        ],
    }


def test_grounded_output_accepts_utf8_claims_and_known_unique_labels() -> None:
    answer, claims = parse_grounded_answer_payload(
        valid_grounded_payload(),
        (evidence(label="S1"), evidence(label="S2")),
        max_answer_chars=16_000,
        max_claims=64,
        max_claim_chars=4_000,
        max_labels_per_claim=16,
    )

    assert answer.startswith("退款审批")
    assert claims[1].evidence_labels == ("S2",)


def test_grounded_output_projects_unique_complete_claims_into_canonical_answer() -> None:
    answer, claims = parse_grounded_answer_payload(
        {
            "answer": "Summary: First grounded sentence. Second grounded sentence.",
            "claims": [
                {"text": "First grounded sentence.", "evidenceLabels": ["S1"]},
                {"text": "Second grounded sentence.", "evidenceLabels": ["S2"]},
            ],
        },
        (evidence(label="S1"), evidence(label="S2")),
        max_answer_chars=16_000,
        max_claims=64,
        max_claim_chars=4_000,
        max_labels_per_claim=16,
    )

    assert answer == "First grounded sentence.\n\nSecond grounded sentence."
    assert tuple(claim.text for claim in claims) == (
        "First grounded sentence.",
        "Second grounded sentence.",
    )


@pytest.mark.parametrize(
    "summary",
    [
        "Complete paragraph.",
        "Duplicate paragraph.\n\nDuplicate paragraph.",
        "A free summary that restates the claims differently.",
    ],
)
def test_grounded_output_rebuilds_answer_from_complete_claims_absent_from_summary(
    summary: str,
) -> None:
    answer, claims = parse_grounded_answer_payload(
        {
            "answer": summary,
            "claims": [
                {"text": "Approvals need two reviewers.", "evidenceLabels": ["S1"]},
                {"text": "退款审批需要两名审批人。", "evidenceLabels": ["S2"]},
            ],
        },
        (evidence(label="S1"), evidence(label="S2")),
        max_answer_chars=16_000,
        max_claims=64,
        max_claim_chars=4_000,
        max_labels_per_claim=16,
    )

    assert answer == "Approvals need two reviewers.\n\n退款审批需要两名审批人。"
    assert tuple(claim.evidence_labels for claim in claims) == (("S1",), ("S2",))


@pytest.mark.parametrize("answer", ["", "I cannot answer from the supplied evidence."])
def test_grounded_output_projects_zero_claims_to_closed_abstention(answer: str) -> None:
    assert parse_grounded_answer_payload(
        {"answer": answer, "claims": []},
        (evidence(label="S1"),),
        max_answer_chars=16_000,
        max_claims=64,
        max_claim_chars=4_000,
        max_labels_per_claim=16,
    ) == ("", ())


def test_grounded_output_accepts_all_closed_upper_bounds() -> None:
    labels = tuple("L" * 64 if index == 0 else f"S{index}" for index in range(16))
    paragraphs = ["段" * 4_000, *(f"claim-{index}" for index in range(1, 64))]
    grounded_answer = "\n\n".join(paragraphs)
    answer_at_limit = grounded_answer + "\n\n" + "f" * (16_000 - len(grounded_answer) - 2)

    answer, claims = parse_grounded_answer_payload(
        {
            "answer": answer_at_limit,
            "claims": [
                {"text": paragraph, "evidenceLabels": list(labels)} for paragraph in paragraphs
            ],
        },
        tuple(evidence(label=label) for label in labels),
        max_answer_chars=16_000,
        max_claims=64,
        max_claim_chars=4_000,
        max_labels_per_claim=16,
    )

    assert len(answer) == 16_000
    assert len(claims) == 64
    assert claims[0].evidence_labels[0] == "L" * 64


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"answer": "Complete paragraph.", "claims": [], "extra": True},
        {"answer": "Complete paragraph."},
        {"answer": " ", "claims": [{"text": " ", "evidenceLabels": ["S1"]}]},
        {
            "answer": "\ud800",
            "claims": [{"text": "\ud800", "evidenceLabels": ["S1"]}],
        },
        {
            "answer": "x" * 16_001,
            "claims": [{"text": "x", "evidenceLabels": ["S1"]}],
        },
        {
            "answer": "Complete paragraph.",
            "claims": [{"text": f"claim-{index}", "evidenceLabels": ["S1"]} for index in range(65)],
        },
        {
            "answer": "Complete paragraph.",
            "claims": [{"text": "Complete paragraph.", "evidenceLabels": ["S1"], "extra": True}],
        },
        {
            "answer": "Complete paragraph.",
            "claims": [{"text": "Complete paragraph."}],
        },
        {
            "answer": "Complete paragraph.",
            "claims": [{"text": " ", "evidenceLabels": ["S1"]}],
        },
        {
            "answer": "\ud800",
            "claims": [{"text": "\ud800", "evidenceLabels": ["S1"]}],
        },
        {
            "answer": "x" * 4_001,
            "claims": [{"text": "x" * 4_001, "evidenceLabels": ["S1"]}],
        },
        {
            "answer": "First paragraph.\n\nSecond paragraph.",
            "claims": [
                {
                    "text": "First paragraph.\n\nSecond paragraph.",
                    "evidenceLabels": ["S1"],
                }
            ],
        },
        {
            "answer": "Complete paragraph.",
            "claims": [{"text": "Complete paragraph.", "evidenceLabels": "S1"}],
        },
        {
            "answer": "Complete paragraph.",
            "claims": [{"text": "Complete paragraph.", "evidenceLabels": []}],
        },
        {
            "answer": "Complete paragraph.",
            "claims": [{"text": "Complete paragraph.", "evidenceLabels": ["S1"] * 17}],
        },
        {
            "answer": "Complete paragraph.",
            "claims": [{"text": "Complete paragraph.", "evidenceLabels": [""]}],
        },
        {
            "answer": "Complete paragraph.",
            "claims": [{"text": "Complete paragraph.", "evidenceLabels": ["\ud800"]}],
        },
        {
            "answer": "Complete paragraph.",
            "claims": [{"text": "Complete paragraph.", "evidenceLabels": ["L" * 65]}],
        },
        {
            "answer": "Complete paragraph.",
            "claims": [{"text": "Complete paragraph.", "evidenceLabels": ["S99"]}],
        },
        {
            "answer": "Complete paragraph.",
            "claims": [{"text": "Complete paragraph.", "evidenceLabels": ["S1", "S1"]}],
        },
        {
            "answer": "Complete paragraph.",
            "claims": [{"text": "Complete", "evidenceLabels": ["S1"]}],
        },
        {
            "answer": "The log records a start and a pass.",
            "claims": [
                {"text": "09:41:02  START  Open request\n09:41:04  PASS", "evidenceLabels": ["S1"]}
            ],
        },
        {
            "answer": "Duplicate claim.",
            "claims": [
                {"text": "Duplicate claim.", "evidenceLabels": ["S1"]},
                {"text": "Duplicate claim.", "evidenceLabels": ["S1"]},
            ],
        },
    ],
    ids=(
        "not-object",
        "unknown-top-level-field",
        "missing-top-level-field",
        "blank-answer",
        "answer-not-utf8",
        "answer-length",
        "claim-count",
        "unknown-claim-field",
        "missing-claim-field",
        "blank-claim",
        "claim-not-utf8",
        "claim-length",
        "multiple-paragraphs",
        "labels-not-list",
        "zero-labels",
        "label-count",
        "blank-label",
        "label-not-utf8",
        "label-length",
        "unknown-label",
        "duplicate-label",
        "partial-paragraph",
        "quoted-fragment",
        "duplicate-claim-paragraph",
    ),
)
def test_grounded_output_rejects_malformed_unknown_partial_or_duplicate_payloads(
    payload: object,
) -> None:
    with pytest.raises(ValueError):
        parse_grounded_answer_payload(
            payload,
            (evidence(label="S1"), evidence(label="S2")),
            max_answer_chars=16_000,
            max_claims=64,
            max_claim_chars=4_000,
            max_labels_per_claim=16,
        )


def test_grounded_output_accepts_relation_labels_only_when_supplied() -> None:
    payload = {
        "answer": "退款审批需要两名审批人。",
        "claims": [{"text": "退款审批需要两名审批人。", "evidenceLabels": ["S1", "R1"]}],
    }

    answer, claims = parse_grounded_answer_payload(
        payload,
        (evidence(label="S1"),),
        max_answer_chars=16_000,
        max_claims=64,
        max_claim_chars=4_000,
        max_labels_per_claim=16,
        extra_labels=frozenset({"R1"}),
    )

    assert answer == "退款审批需要两名审批人。"
    assert claims[0].evidence_labels == ("S1", "R1")

    with pytest.raises(ValueError, match="unknown evidence label"):
        parse_grounded_answer_payload(
            payload,
            (evidence(label="S1"),),
            max_answer_chars=16_000,
            max_claims=64,
            max_claim_chars=4_000,
            max_labels_per_claim=16,
        )
