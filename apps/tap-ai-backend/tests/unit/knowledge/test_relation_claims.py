"""Unit tests for `reconcile_relation_claims`: per-claim R-label
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
    relation_support_mentions_both_endpoints,
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


def test_s_backed_support_whose_cited_chunk_never_names_both_endpoints_is_stripped() -> None:
    # R1's only support is S1, but S1's actual chunk content turns out to be
    # about something else entirely (the merged graph mis-associated the
    # edge with this chunk) -- a stand-in for the real-model finding where an
    # edge's evidence chunk shared surface terms with its labels but was
    # about an unrelated clause.
    unrelated_content = {"chunk-1": "体检当天需要空腹，报告将在三个工作日内送达。"}
    claim = GeneratedClaim(text="核保流程需要健康告知。", evidence_labels=("R1", "S1"))
    result = reconcile_relation_claims(
        _generation((claim,)),
        _CONTEXT,
        graph_version="7",
        evidence_content=unrelated_content,
    )
    assert result.claims[0].evidence_labels == ("S1",)
    assert result.stripped == 1
    assert not relation_support_mentions_both_endpoints(_RELATION, unrelated_content)


def test_s_backed_support_whose_cited_chunk_names_both_endpoints_is_kept() -> None:
    related_content = {"chunk-1": "核保流程要求先完成健康告知。"}
    claim = GeneratedClaim(text="核保流程需要健康告知。", evidence_labels=("R1", "S1"))
    result = reconcile_relation_claims(
        _generation((claim,)),
        _CONTEXT,
        graph_version="7",
        evidence_content=related_content,
    )
    assert result.claims[0].evidence_labels == ("R1", "S1")
    assert result.stripped == 0
    assert relation_support_mentions_both_endpoints(_RELATION, related_content)


def test_missing_support_content_is_not_penalized() -> None:
    # No `evidence_content` entry for "chunk-1" at all: the check has no
    # means to evaluate relevance, so it must not strip a citation it cannot
    # actually assess (distinct from the "content available but unrelated"
    # case above).
    claim = GeneratedClaim(text="核保流程需要健康告知。", evidence_labels=("R1", "S1"))
    result = reconcile_relation_claims(
        _generation((claim,)),
        _CONTEXT,
        graph_version="7",
    )
    assert result.claims[0].evidence_labels == ("R1", "S1")
    assert result.stripped == 0
    assert relation_support_mentions_both_endpoints(_RELATION, {})


def test_snippet_only_support_is_checked_against_its_own_text() -> None:
    snippet_support = RelationSupport(
        chunk_id="chunk-2",
        source_revision_id="rev-1",
        document_revision_id="doc-1",
        content_digest="digest-chunk-2",
        anchor={"page": 2},
        snippet="体检当天需要空腹，报告将在三个工作日内送达。",
    )
    relation = RelationEvidence(
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
        support=(snippet_support,),
        on_path=False,
    )
    context = RelationContext(
        status=RelationContextStatus.APPLIED,
        graph_version="7",
        relations=(relation,),
    )
    claim = GeneratedClaim(text="核保流程需要健康告知。", evidence_labels=("R1",))
    result = reconcile_relation_claims(_generation((claim,)), context, graph_version="7")
    assert result.claims == ()
    assert result.dropped == 1
    assert not relation_support_mentions_both_endpoints(relation, {})


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


def _edge_with_parenthetical_subject() -> RelationEvidence:
    return RelationEvidence(
        label="R1",
        edge_id="e-paren",
        subject_node_id="A",
        subject_label="冠状动脉介入手术（非切开心包手术）",
        subject_aliases=(),
        object_node_id="B",
        object_label="保险责任",
        object_aliases=(),
        relation_type="TRIGGERS",
        relation_label="TRIGGERS",
        origin="EXTRACTED",
        confidence=0.9,
        support=(_SUPPORT,),
        on_path=False,
    )


def test_claim_naming_label_without_trailing_qualifier_is_kept() -> None:
    # The merged node's label carries a disambiguating qualifier the model's
    # own prose typically omits -- stripping it from the label (not from the
    # claim text) must still let the citation through.
    relation = _edge_with_parenthetical_subject()
    assert claim_mentions_both_endpoints("冠状动脉介入手术会触发保险责任。", relation)


def test_claim_naming_full_width_variant_of_label_is_kept() -> None:
    # Full-width Latin letters are a common OCR/typing variant of the same
    # name; `normalize_key`'s NFKD/NFKC fold must resolve them to the same
    # half-width term before the substring check runs.
    relation = RelationEvidence(
        label="R1",
        edge_id="e-width",
        subject_node_id="A",
        subject_label="ABC医院",
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
    assert claim_mentions_both_endpoints("ＡＢＣ医院要求先完成健康告知。", relation)


def test_claim_naming_unrelated_entity_is_not_kept() -> None:
    relation = _edge_with_parenthetical_subject()
    assert not claim_mentions_both_endpoints("体检需要空腹，报告将在三个工作日内送达。", relation)


def test_single_character_label_is_rejected_as_a_substring_match() -> None:
    # A real (non-punctuation) one-character label is still too weak a
    # substring to trust: almost any sentence of reasonable length will
    # contain that character for an unrelated reason.
    relation = RelationEvidence(
        label="R1",
        edge_id="e-short",
        subject_node_id="A",
        subject_label="人",
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
    assert not claim_mentions_both_endpoints("本人已完成健康告知。", relation)
