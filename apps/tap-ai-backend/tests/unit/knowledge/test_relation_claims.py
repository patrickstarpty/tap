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
    ClaimChunkSection,
    claim_mentions_both_endpoints,
    reconcile_relation_claims,
    relation_support_is_grounded_for_claim,
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


# The real q-14 R5 text (report 2 §6; `/tmp/tap-real3/edge-sample.csv:177`): a
# "医生" (doctor) definitions clause's exclusion list names "被保险人" and,
# among several unrelated examples of family members, "子女" -- so it
# literally mentions both endpoints of a 被保险人/子女 relation while being
# about an entirely different defined term. Both `claim_mentions_both_endpoints`
# and `relation_support_mentions_both_endpoints` pass on this text alone;
# only the claim-shaped check (`relation_support_is_grounded_for_claim`) can
# tell it apart from a chunk that actually states the relationship.
_UNRELATED_DOCTOR_DEFINITION = (
    "三十四、医生：指在医院内行医并拥有处方权的医生，亦指在被保险人接受诊断、医疗、处方或手术的"
    "地区内合法注册且有行医资格的医生；但不包括被保险人本人、被保险人的代理人、合伙人、雇员或雇"
    "主，或被保险人的家庭成员，如配偶、兄弟、姐妹、父母、子女以及其他具有类似亲密关系的人。"
)
# The real heading path of the chunk that carries that text (per
# `/tmp/tap-real3/qa-q-04.json`'s resolved citation for the same revision's
# `h_a86a9c…` glossary chunk): a document title plus "第二十七条 释义" --
# a definitions section whose many distinct glossary entries, in the real
# answer, share this exact nearest heading (the real S3 citation did too,
# which is how R5 slipped through the section-match fallback before the
# definitions-section tightening below).
_DEFINITIONS_SECTION_HEADING_PATH = ("友邦尊享智选康惠团体医疗保险", "第二十七条 释义")


def test_support_text_naming_both_endpoints_in_passing_does_not_satisfy_endpoint_checks_alone() -> (
    None
):
    """Documents the real q-14 R5 failure mode directly against the two
    text-only endpoint checks, before the claim-shaped check is even
    involved: both pass on the unrelated doctor-definition text, which is
    exactly why `relation_support_is_grounded_for_claim` (tested below) has
    to be the decisive gate."""
    relation = RelationEvidence(
        label="R1",
        edge_id="e-doctor-definition",
        subject_node_id="A",
        subject_label="被保险人",
        subject_aliases=(),
        object_node_id="B",
        object_label="子女",
        object_aliases=(),
        relation_type="RELATED_TO",
        relation_label="RELATED_TO",
        origin="EXTRACTED",
        confidence=0.9,
        support=(
            RelationSupport(
                chunk_id="chunk-doctor",
                source_revision_id="rev-1",
                document_revision_id="doc-1",
                content_digest="digest-doctor",
                anchor={"page": 1, "headingPath": list(_DEFINITIONS_SECTION_HEADING_PATH)},
                evidence_label="S9",
            ),
        ),
        on_path=False,
    )
    assert claim_mentions_both_endpoints("被保险人的子女可以作为被保险人。", relation)
    assert relation_support_mentions_both_endpoints(
        relation, {"chunk-doctor": _UNRELATED_DOCTOR_DEFINITION}
    )


_DOCTOR_DEFINITION_RELATION = RelationEvidence(
    label="R1",
    edge_id="e-doctor-definition",
    subject_node_id="A",
    subject_label="被保险人",
    subject_aliases=(),
    object_node_id="B",
    object_label="子女",
    object_aliases=(),
    relation_type="RELATED_TO",
    relation_label="RELATED_TO",
    origin="EXTRACTED",
    confidence=0.9,
    support=(
        RelationSupport(
            chunk_id="chunk-doctor",
            source_revision_id="rev-1",
            document_revision_id="doc-1",
            content_digest="digest-doctor",
            anchor={"page": 1, "headingPath": list(_DEFINITIONS_SECTION_HEADING_PATH)},
            evidence_label="S9",
        ),
    ),
    on_path=False,
)

_GROUP_CLAUSE_CLAIM_SECTION = ClaimChunkSection(
    chunk_id="chunk-group-medical-clause-2",
    source_revision_id="rev-1",
    heading_path=("第二条 投保范围",),
)


def test_r_label_whose_support_chunk_is_not_the_claims_cited_s_chunk_or_section_is_stripped() -> (
    None
):
    """The decisive fix: even with both endpoint checks passing (previous
    test), the R label is stripped when its support is neither one of the
    chunks this claim cites as S evidence, nor in the same revision and
    section as one of them -- the real q-14 R5 case, whose support is a
    different chunk in a different section ("第二十七条 释义" vs "第二条
    投保范围") from the claim's own S1."""
    claim = GeneratedClaim(text="被保险人的子女可以作为被保险人。", evidence_labels=("R1", "S1"))
    result = reconcile_relation_claims(
        _generation((claim,)),
        RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            relations=(_DOCTOR_DEFINITION_RELATION,),
        ),
        graph_version="7",
        evidence_content={"chunk-doctor": _UNRELATED_DOCTOR_DEFINITION},
        label_chunk_sections={"S1": _GROUP_CLAUSE_CLAIM_SECTION},
    )
    assert result.claims[0].evidence_labels == ("S1",)
    assert result.stripped == 1
    assert not relation_support_is_grounded_for_claim(
        _DOCTOR_DEFINITION_RELATION, (_GROUP_CLAUSE_CLAIM_SECTION,)
    )


def test_r_label_whose_support_chunk_matches_claims_cited_s_chunk_is_kept() -> None:
    relation = RelationEvidence(
        label="R1",
        edge_id="e-group-clause",
        subject_node_id="A",
        subject_label="团体",
        subject_aliases=(),
        object_node_id="B",
        object_label="投保人",
        object_aliases=(),
        relation_type="PART_OF",
        relation_label="可作为投保人",
        origin="EXTRACTED",
        confidence=0.9,
        support=(
            RelationSupport(
                chunk_id="chunk-group-medical-clause-2",
                source_revision_id="rev-1",
                document_revision_id="doc-1",
                content_digest="digest-group",
                evidence_label="S1",
                anchor={"page": 1, "headingPath": ["第二条 投保范围"]},
            ),
        ),
        on_path=False,
    )
    context = RelationContext(
        status=RelationContextStatus.APPLIED, graph_version="7", relations=(relation,)
    )
    claim = GeneratedClaim(text="团体可以作为投保人。", evidence_labels=("R1", "S1"))
    result = reconcile_relation_claims(
        _generation((claim,)),
        context,
        graph_version="7",
        evidence_content={"chunk-group-medical-clause-2": "团体可以作为投保人。"},
        label_chunk_sections={"S1": _GROUP_CLAUSE_CLAIM_SECTION},
    )
    assert result.claims[0].evidence_labels == ("R1", "S1")
    assert result.stripped == 0
    assert relation_support_is_grounded_for_claim(relation, (_GROUP_CLAUSE_CLAIM_SECTION,))


def test_r_label_whose_support_is_a_fresh_snippet_in_the_claims_same_section_is_kept() -> None:
    """The real-model R6 case: a fresh (not yet S-cited) snippet from the
    same 投保范围 clause as the claim's own S1 -- different chunk ID (a
    different sub-paragraph of the same section), same revision and
    section -- must still ground the R citation, not just an exact chunk
    match."""
    relation = RelationEvidence(
        label="R1",
        edge_id="e-group-clause-snippet",
        subject_node_id="A",
        subject_label="非法人组织",
        subject_aliases=(),
        object_node_id="B",
        object_label="投保人",
        object_aliases=(),
        relation_type="PART_OF",
        relation_label="可作为投保人",
        origin="EXTRACTED",
        confidence=0.9,
        support=(
            RelationSupport(
                chunk_id="chunk-group-medical-clause-2-part-b",
                source_revision_id="rev-1",
                document_revision_id="doc-1",
                content_digest="digest-group-b",
                anchor={"page": 1, "headingPath": ["第二条 投保范围"]},
                snippet="非法人组织可以作为投保人。",
            ),
        ),
        on_path=False,
    )
    context = RelationContext(
        status=RelationContextStatus.APPLIED, graph_version="7", relations=(relation,)
    )
    claim = GeneratedClaim(text="非法人组织可以作为投保人。", evidence_labels=("R1", "S1"))
    result = reconcile_relation_claims(
        _generation((claim,)),
        context,
        graph_version="7",
        label_chunk_sections={"S1": _GROUP_CLAUSE_CLAIM_SECTION},
    )
    assert result.claims[0].evidence_labels == ("R1", "S1")
    assert result.stripped == 0
    assert relation_support_is_grounded_for_claim(relation, (_GROUP_CLAUSE_CLAIM_SECTION,))


def test_r_label_matching_a_different_claims_s_chunk_is_still_stripped() -> None:
    """Per-claim isolation: a chunk cited as S only by some *other* claim in
    the same answer must not ground this claim's R citation -- judging a
    claim's R labels against the whole answer's S set (tried and reverted)
    both let unrelated support through when any other claim cited S, and
    stripped a legitimate R-only claim whenever some other claim's S chunk
    did not happen to overlap."""
    other_claim_section = ClaimChunkSection(
        chunk_id="chunk-other-claim-s1",
        source_revision_id="rev-1",
        heading_path=("第三条 保险责任",),
    )
    relation = RelationEvidence(
        label="R1",
        edge_id="e-other-claim-chunk",
        subject_node_id="A",
        subject_label="团体",
        subject_aliases=(),
        object_node_id="B",
        object_label="投保人",
        object_aliases=(),
        relation_type="PART_OF",
        relation_label="可作为投保人",
        origin="EXTRACTED",
        confidence=0.9,
        support=(
            RelationSupport(
                chunk_id="chunk-other-claim-s1",
                source_revision_id="rev-1",
                document_revision_id="doc-1",
                content_digest="digest-other",
                evidence_label="S2",
                anchor={"page": 1, "headingPath": ["第三条 保险责任"]},
            ),
        ),
        on_path=False,
    )
    context = RelationContext(
        status=RelationContextStatus.APPLIED, graph_version="7", relations=(relation,)
    )
    claims = (
        GeneratedClaim(text="已付保险费的情况说明。", evidence_labels=("S2",)),
        GeneratedClaim(text="团体可以作为投保人。", evidence_labels=("R1", "S1")),
    )
    result = reconcile_relation_claims(
        _generation(claims),
        context,
        graph_version="7",
        label_chunk_sections={
            "S1": _GROUP_CLAUSE_CLAIM_SECTION,
            "S2": other_claim_section,
        },
    )
    second_claim = next(claim for claim in result.claims if "团体" in claim.text)
    assert second_claim.evidence_labels == ("S1",)
    assert result.stripped == 1


def test_r_label_without_any_s_evidence_in_this_claim_but_support_is_retrieved_is_kept() -> None:
    # This claim cites no S label at all, but the relation's only support
    # (`_SUPPORT`) carries `evidence_label="S1"` -- it is part of the
    # answer's retrieved context, cited by some claim or not (the real
    # report's R7 shape: support = a retrieved-but-uncited S chunk). No
    # chunk/section comparison applies; the endpoint-mention checks
    # (exercised elsewhere) are the only other gate.
    kept = reconcile_relation_claims(
        _generation((GeneratedClaim(text="核保需要健康告知。", evidence_labels=("R1",)),)),
        _CONTEXT,
        graph_version="7",
    )
    assert kept.claims[0].evidence_labels == ("R1",)
    assert kept.stripped == 0
    assert relation_support_is_grounded_for_claim(_RELATION, ())


def test_r_only_claim_with_unretrieved_snippet_support_is_stripped() -> None:
    """The real q-14 R5 shape as an R-only citation: the claim cites no S
    evidence, and the relation's only support is a snippet never retrieved
    this turn (no `evidence_label` -- known only through the merged graph's
    own fragment evidence, not this turn's search/augmentation). There is
    no S anchor in this claim to fall back on, so the citation is stripped
    even though the snippet's text names both endpoints (see the earlier
    `test_support_text_naming_both_endpoints_in_passing_does_not_satisfy_endpoint_checks_alone`-
    style text check, which would have passed this on text alone before
    this fix)."""
    snippet_only_relation = RelationEvidence(
        label="R1",
        edge_id="e-doctor-definition-snippet",
        subject_node_id="A",
        subject_label="被保险人",
        subject_aliases=(),
        object_node_id="B",
        object_label="子女",
        object_aliases=(),
        relation_type="RELATED_TO",
        relation_label="RELATED_TO",
        origin="EXTRACTED",
        confidence=0.9,
        support=(
            RelationSupport(
                chunk_id="chunk-doctor-snippet",
                source_revision_id="rev-1",
                document_revision_id="doc-1",
                content_digest="digest-doctor-snippet",
                anchor={"page": 1, "headingPath": list(_DEFINITIONS_SECTION_HEADING_PATH)},
                snippet=_UNRELATED_DOCTOR_DEFINITION,
            ),
        ),
        on_path=False,
    )
    context = RelationContext(
        status=RelationContextStatus.APPLIED, graph_version="7", relations=(snippet_only_relation,)
    )
    claim = GeneratedClaim(text="被保险人的子女可以作为被保险人。", evidence_labels=("R1",))
    result = reconcile_relation_claims(_generation((claim,)), context, graph_version="7")
    assert result.claims == ()
    assert result.dropped == 1
    assert not relation_support_is_grounded_for_claim(snippet_only_relation, ())


def test_shared_definitions_section_heading_does_not_ground_without_exact_chunk_match() -> None:
    """The real report's actual failure (reviewer's re-review, not just the
    earlier "different section" test above): the claim's own S chunk and
    R5's support chunk share the *exact same* nearest heading, "第二十七条
    释义" -- the real S3 citation and R5's doctor-definition chunk both sit
    in that glossary section. A shared nearest heading is too coarse a
    signal there, since many unrelated glossary entries share it; only an
    exact chunk match grounds a citation in a definitions section."""
    claim_in_definitions_section = ClaimChunkSection(
        chunk_id="chunk-s3-glossary-entry",
        source_revision_id="rev-1",
        heading_path=_DEFINITIONS_SECTION_HEADING_PATH,
    )
    assert not relation_support_is_grounded_for_claim(
        _DOCTOR_DEFINITION_RELATION, (claim_in_definitions_section,)
    )
    claim = GeneratedClaim(text="被保险人的子女可以作为被保险人。", evidence_labels=("R1", "S1"))
    result = reconcile_relation_claims(
        _generation((claim,)),
        RelationContext(
            status=RelationContextStatus.APPLIED,
            graph_version="7",
            relations=(_DOCTOR_DEFINITION_RELATION,),
        ),
        graph_version="7",
        evidence_content={"chunk-doctor": _UNRELATED_DOCTOR_DEFINITION},
        label_chunk_sections={"S1": claim_in_definitions_section},
    )
    assert result.claims[0].evidence_labels == ("S1",)
    assert result.stripped == 1


def test_shared_non_definitions_section_heading_still_grounds_a_fresh_snippet() -> None:
    # Control for the previous test: the same shared-nearest-heading
    # fallback must still work when the shared heading is *not* a
    # definitions section (R6's real shape -- see
    # `test_r_label_whose_support_is_a_fresh_snippet_in_the_claims_same_section_is_kept`
    # for the full scenario; this re-asserts the same guarantee directly
    # against the helper so the two code paths (definitions vs not) are
    # each covered by their own direct assertion).
    assert relation_support_is_grounded_for_claim(
        RelationEvidence(
            label="R1",
            edge_id="e-group-clause-snippet-direct",
            subject_node_id="A",
            subject_label="非法人组织",
            subject_aliases=(),
            object_node_id="B",
            object_label="投保人",
            object_aliases=(),
            relation_type="PART_OF",
            relation_label="可作为投保人",
            origin="EXTRACTED",
            confidence=0.9,
            support=(
                RelationSupport(
                    chunk_id="chunk-group-medical-clause-2-part-b",
                    source_revision_id="rev-1",
                    document_revision_id="doc-1",
                    content_digest="digest-group-b",
                    anchor={"page": 1, "headingPath": ["第二条 投保范围"]},
                    snippet="非法人组织可以作为投保人。",
                ),
            ),
            on_path=False,
        ),
        (_GROUP_CLAUSE_CLAIM_SECTION,),
    )


def test_short_role_label_does_not_falsely_match_inside_a_longer_role_label() -> None:
    # "保险人" (the insurer) is a literal substring of "被保险人" (the
    # insured) -- a different role. A claim naming only "被保险人" must not
    # also be read as naming "保险人".
    relation = RelationEvidence(
        label="R1",
        edge_id="e-role-overlap",
        subject_node_id="A",
        subject_label="保险人",
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
    assert not claim_mentions_both_endpoints("被保险人的子女完成了健康告知。", relation)
    # A genuine, standalone occurrence of the short label still matches.
    assert claim_mentions_both_endpoints("保险人要求完成健康告知。", relation)


def test_short_label_does_not_falsely_match_inside_the_other_endpoints_longer_term() -> None:
    # Same false-positive risk, but driven purely by the *other* endpoint's
    # own term rather than the fixed role triad: "甲方" is a literal
    # substring of "乙方甲方代表" (a different, unrelated entity in this
    # made-up pair), so an occurrence of "甲方" found only inside the
    # object's own term must not count as naming the subject.
    relation = RelationEvidence(
        label="R1",
        edge_id="e-other-endpoint-overlap",
        subject_node_id="A",
        subject_label="甲方",
        subject_aliases=(),
        object_node_id="B",
        object_label="乙方甲方代表",
        object_aliases=(),
        relation_type="REQUIRES",
        relation_label="REQUIRES",
        origin="EXTRACTED",
        confidence=0.9,
        support=(_SUPPORT,),
        on_path=False,
    )
    # The object's own term contains "甲方" as a substring, but the claim
    # never names "甲方" outside of that occurrence.
    assert not claim_mentions_both_endpoints("乙方甲方代表已签署协议。", relation)
    # A genuine, standalone occurrence of the subject still matches.
    assert claim_mentions_both_endpoints("甲方已与乙方甲方代表签署协议。", relation)
