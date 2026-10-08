"""Unit tests for `reconcile_relation_claims` (PR 3 task 4): per-claim R-label
validation against the standalone-named `RelationContext`.

Fixture context carries a single relation R1 (核保流程 —REQUIRES→ 健康告知) at
`graph_version="7"`; the subject node "核保流程" also carries the alias "核保",
the object node "健康告知" has no alias.
"""

from __future__ import annotations

from tap.modules.knowledge.application.relation_analysis import (
    RelationContext,
    RelationContextStatus,
    RelationEvidence,
    RelationSupport,
)
from tap.modules.knowledge.application.relation_claims import (
    claim_mentions_both_endpoints,
    reconcile_relation_claims,
)
from tap.modules.knowledge.ports.models import AnswerGeneration, GeneratedClaim

_SUPPORT = RelationSupport(
    chunk_id="chunk-1",
    source_revision_id="rev-1",
    document_revision_id="doc-1",
    content_digest="digest-chunk-1",
    anchor={"page": 1},
    evidence_label="S1",
)

_RELATION = RelationEvidence(
    label="R1",
    edge_id="e-1",
    subject_node_id="A",
    subject_label="核保流程",
    subject_aliases=("核保",),
    object_node_id="B",
    object_label="健康告知",
    object_aliases=(),
    relation_type="REQUIRES",
    relation_label="REQUIRES",
    origin="EXTRACTED",
    confidence=0.9,
    support=(_SUPPORT,),
    on_path=False,
)

_CONTEXT = RelationContext(
    status=RelationContextStatus.APPLIED,
    graph_version="7",
    relations=(_RELATION,),
)


def _generation(claims: tuple[GeneratedClaim, ...]) -> AnswerGeneration:
    return AnswerGeneration(
        text="\n\n".join(claim.text for claim in claims),
        claims=claims,
        model_id="model-1",
        profile_id="profile-1",
        provider_request_id=None,
    )


def test_relation_claim_without_both_endpoints_is_stripped_or_dropped() -> None:
    kept = reconcile_relation_claims(
        _generation(
            (GeneratedClaim(text="核保流程需要先完成体检。", evidence_labels=("R1", "S1")),)
        ),
        _CONTEXT,
        graph_version="7",
    )
    assert kept.claims[0].evidence_labels == ("S1",)
    assert kept.stripped == 1
    assert kept.dropped == 0

    dropped = reconcile_relation_claims(
        _generation((GeneratedClaim(text="核保流程需要先完成体检。", evidence_labels=("R1",)),)),
        _CONTEXT,
        graph_version="7",
    )
    assert dropped.claims == ()
    assert dropped.dropped == 1
    assert "核保流程需要先完成体检。" not in dropped.text


def test_relation_claim_naming_alias_is_kept() -> None:
    kept = reconcile_relation_claims(
        _generation((GeneratedClaim(text="核保需要健康告知。", evidence_labels=("R1",)),)),
        _CONTEXT,
        graph_version="7",
    )
    assert kept.claims[0].evidence_labels == ("R1",)
    assert kept.stripped == 0
    assert kept.dropped == 0
    assert kept.text == "核保需要健康告知。"
    assert claim_mentions_both_endpoints("核保需要健康告知。", _RELATION)


def test_r_label_outside_context_or_other_version_is_stripped() -> None:
    missing_label = reconcile_relation_claims(
        _generation((GeneratedClaim(text="核保需要健康告知。", evidence_labels=("R2", "S1")),)),
        _CONTEXT,
        graph_version="7",
    )
    assert missing_label.claims[0].evidence_labels == ("S1",)
    assert missing_label.stripped == 1

    other_version = reconcile_relation_claims(
        _generation((GeneratedClaim(text="核保需要健康告知。", evidence_labels=("R1", "S1")),)),
        _CONTEXT,
        graph_version="8",
    )
    assert other_version.claims[0].evidence_labels == ("S1",)
    assert other_version.stripped == 1


def test_all_claims_dropped_leaves_empty_text() -> None:
    result = reconcile_relation_claims(
        _generation((GeneratedClaim(text="核保需要健康告知。", evidence_labels=("R2",)),)),
        _CONTEXT,
        graph_version="7",
    )
    assert result.claims == ()
    assert result.text == ""
    assert result.dropped == 1


def test_punctuation_only_alias_never_matches_as_substring() -> None:
    # Subject's only name is punctuation-only ("、"); it must not be treated as a
    # universal substring match. The text mentions the object but never the subject.
    relation = RelationEvidence(
        label="R1",
        edge_id="e-2",
        subject_node_id="A",
        subject_label="、",
        subject_aliases=(),
        object_node_id="B",
        object_label="健康告知",
        object_aliases=(),
        relation_type="REQUIRES",
        relation_label="REQUIRES",
        origin="EXTRACTED",
        confidence=0.9,
        support=(_SUPPORT,),
        on_path=False,
    )
    assert not claim_mentions_both_endpoints("健康告知非常重要。", relation)
