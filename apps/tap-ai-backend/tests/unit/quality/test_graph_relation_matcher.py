from __future__ import annotations

from tap.quality.graph_relations import (
    EdgeCitation,
    ExpectedEdge,
    GoldenQuestion,
    QuestionResult,
    aggregate,
    edge_citations,
    match_question,
)


def _question(
    question_id: str,
    *,
    expected_entities: tuple[tuple[str, tuple[str, ...]], ...],
    expected_edges: tuple[ExpectedEdge, ...],
) -> GoldenQuestion:
    return GoldenQuestion(
        id=question_id,
        question="question text?",
        locale="zh",
        expected_entities=expected_entities,
        expected_edges=expected_edges,
        expected_sources=("source-a",),
    )


def _edge_citation(
    *,
    citation_id: str = "c-1",
    subject_label: str,
    relation_type: str,
    object_label: str,
    edge_id: str = "e-1",
) -> dict[str, object]:
    return {
        "kind": "edge",
        "citationId": citation_id,
        "evidenceLabel": "E1",
        "edgeId": edge_id,
        "subject": {"nodeId": "n-subject", "label": subject_label},
        "object": {"nodeId": "n-object", "label": object_label},
        "relationType": relation_type,
        "relationLabel": relation_type,
    }


def _chunk_citation(citation_id: str) -> dict[str, object]:
    return {"kind": "chunk", "citationId": citation_id, "evidenceLabel": "C1"}


def test_matches_after_normalization_and_alias():
    # Expected label "健康告知" has alias "健康问卷"; the citation uses the alias with a
    # trailing space, exercising both alias lookup and whitespace normalization.
    question = _question(
        "q-01",
        expected_entities=(
            ("健康告知", ("健康问卷",)),
            ("保单", ()),
        ),
        expected_edges=(ExpectedEdge(subject="健康告知", relation_type="REQUIRES", object="保单"),),
    )
    citations = [
        _edge_citation(subject_label="健康问卷 ", relation_type="REQUIRES", object_label="保单"),
    ]

    result = match_question(question, citations)

    assert result.status == "pass"
    assert result.correct_edges == ("健康告知 -REQUIRES-> 保单",)
    assert result.wrong_edges == ()


def test_alias_lookup_matches_width_and_whitespace_variant_of_the_entity_label():
    # M6 regression: `expectedEdges` subject text is a half-width "A", while the
    # `expectedEntities` label it must be looked up against is a full-width "Ａ" with
    # trailing whitespace. Before the fix, `_expected_keys` compared `entity_label ==
    # label` literally, so this entity was never matched and its alias ("Alpha") was
    # never added to the key set -- a citation using only the alias would miss.
    question = _question(
        "q-10",
        expected_entities=(("Ａ ", ("Alpha",)), ("B", ())),
        expected_edges=(ExpectedEdge(subject="A", relation_type="REQUIRES", object="B"),),
    )
    citations = [_edge_citation(subject_label="Alpha", relation_type="REQUIRES", object_label="B")]

    result = match_question(question, citations)

    assert result.status == "pass"
    assert result.correct_edges == ("A -REQUIRES-> B",)
    assert result.wrong_edges == ()


def test_alternative_edge_counts_as_hit_for_primary():
    # The primary edge is (A, REQUIRES, B); the alternative (A, APPLIES_TO, B) is what the
    # citation actually hits. The hit must still be recorded under the primary edge text.
    question = _question(
        "q-02",
        expected_entities=(("A", ()), ("B", ())),
        expected_edges=(
            ExpectedEdge(
                subject="A",
                relation_type="REQUIRES",
                object="B",
                alternatives=(ExpectedEdge(subject="A", relation_type="APPLIES_TO", object="B"),),
            ),
        ),
    )
    citations = [_edge_citation(subject_label="A", relation_type="APPLIES_TO", object_label="B")]

    result = match_question(question, citations)

    assert result.status == "pass"
    assert result.correct_edges == ("A -REQUIRES-> B",)
    assert result.missed_edges == ()
    assert result.wrong_edges == ()


def test_unmatched_edge_citation_is_wrong_even_with_hits():
    question = _question(
        "q-03",
        expected_entities=(("A", ()), ("B", ())),
        expected_edges=(ExpectedEdge(subject="A", relation_type="REQUIRES", object="B"),),
    )
    citations = [
        _edge_citation(
            citation_id="c-1", subject_label="A", relation_type="REQUIRES", object_label="B"
        ),
        _edge_citation(
            citation_id="c-2", subject_label="X", relation_type="USES", object_label="Y"
        ),
    ]

    result = match_question(question, citations)

    assert result.status == "wrong"
    assert result.correct_edges == ("A -REQUIRES-> B",)
    assert result.wrong_edges == ("X -USES-> Y",)


def test_zero_edge_citations_is_a_miss_not_wrong():
    question = _question(
        "q-04",
        expected_entities=(("A", ()), ("B", ())),
        expected_edges=(ExpectedEdge(subject="A", relation_type="REQUIRES", object="B"),),
    )
    citations = [_chunk_citation("c-1"), _chunk_citation("c-2")]

    result = match_question(question, citations)

    assert result.status == "miss"
    assert result.wrong_edges == ()
    assert result.correct_edges == ()
    assert result.chunk_citations == 2


def test_symmetric_relation_matches_either_direction():
    conflicts_question = _question(
        "q-05a",
        expected_entities=(("A", ()), ("B", ())),
        expected_edges=(ExpectedEdge(subject="A", relation_type="CONFLICTS_WITH", object="B"),),
    )
    reversed_symmetric = match_question(
        conflicts_question,
        [_edge_citation(subject_label="B", relation_type="CONFLICTS_WITH", object_label="A")],
    )
    assert reversed_symmetric.status == "pass"
    assert reversed_symmetric.correct_edges == ("A -CONFLICTS_WITH-> B",)

    requires_question = _question(
        "q-05b",
        expected_entities=(("A", ()), ("B", ())),
        expected_edges=(ExpectedEdge(subject="A", relation_type="REQUIRES", object="B"),),
    )
    reversed_directed = match_question(
        requires_question,
        [_edge_citation(subject_label="B", relation_type="REQUIRES", object_label="A")],
    )
    assert reversed_directed.status == "wrong"
    assert reversed_directed.wrong_edges == ("B -REQUIRES-> A",)


def test_shared_expected_edge_scores_each_question():
    expected_edges = (ExpectedEdge(subject="A", relation_type="REQUIRES", object="B"),)
    first_question = _question(
        "q-06a", expected_entities=(("A", ()), ("B", ())), expected_edges=expected_edges
    )
    second_question = _question(
        "q-06b", expected_entities=(("A", ()), ("B", ())), expected_edges=expected_edges
    )

    first_result = match_question(
        first_question,
        [_edge_citation(subject_label="A", relation_type="REQUIRES", object_label="B")],
    )
    second_result = match_question(second_question, [])

    assert [first_result.status, second_result.status] == ["pass", "miss"]


def _results(*, passed: int, total: int) -> tuple[QuestionResult, ...]:
    return tuple(
        QuestionResult(
            question_id=f"q-{index:02d}",
            status="pass" if index < passed else "miss",
            correct_edges=(),
            missed_edges=(),
            wrong_edges=(),
            chunk_citations=0,
        )
        for index in range(total)
    )


def test_aggregate_threshold_boundary():
    assert aggregate(_results(passed=24, total=30))["passed"] is True
    assert aggregate(_results(passed=23, total=30))["passed"] is False
    assert aggregate(())["passed"] is False


def _minimal_edge_citation(
    *, subject_label: str, relation_type: str, object_label: str
) -> dict[str, object]:
    # Only the scoring fields: no citationId, evidenceLabel, edgeId or relationLabel.
    return {
        "kind": "edge",
        "subject": {"label": subject_label},
        "object": {"label": object_label},
        "relationType": relation_type,
    }


def test_edge_citation_without_display_fields_still_scores():
    question = _question(
        "q-07",
        expected_entities=(("A", ()), ("B", ())),
        expected_edges=(ExpectedEdge(subject="A", relation_type="REQUIRES", object="B"),),
    )
    citations = [
        _minimal_edge_citation(subject_label="A", relation_type="REQUIRES", object_label="B")
    ]

    assert edge_citations(citations) == (
        EdgeCitation(
            citation_id="",
            evidence_label="",
            edge_id="",
            subject_label="A",
            object_label="B",
            relation_type="REQUIRES",
            relation_label="",
        ),
    )

    result = match_question(question, citations)

    assert result.status == "pass"
    assert result.correct_edges == ("A -REQUIRES-> B",)
    assert result.wrong_edges == ()


def test_malformed_edge_citation_counts_as_wrong():
    question = _question(
        "q-08",
        expected_entities=(("A", ()), ("B", ())),
        expected_edges=(ExpectedEdge(subject="A", relation_type="REQUIRES", object="B"),),
    )
    malformed_missing_relation_type: dict[str, object] = {
        "kind": "edge",
        "subject": {"label": "A"},
        "object": {"label": "B"},
    }
    citations = [
        _edge_citation(subject_label="A", relation_type="REQUIRES", object_label="B"),
        malformed_missing_relation_type,
    ]

    result = match_question(question, citations)

    assert result.status == "wrong"
    assert result.correct_edges == ("A -REQUIRES-> B",)
    assert result.wrong_edges == ("<malformed edge citation>",)


def test_chunk_citations_counts_only_chunk_kind():
    question = _question(
        "q-09",
        expected_entities=(("A", ()), ("B", ())),
        expected_edges=(ExpectedEdge(subject="A", relation_type="REQUIRES", object="B"),),
    )
    malformed_edge: dict[str, object] = {
        "kind": "edge",
        "subject": {"label": "A"},
        "object": {"label": "B"},
    }
    unknown_kind: dict[str, object] = {"kind": "unknown", "citationId": "c-unknown"}
    citations = [
        _chunk_citation("c-chunk-1"),
        _chunk_citation("c-chunk-2"),
        malformed_edge,
        unknown_kind,
    ]

    result = match_question(question, citations)

    assert result.chunk_citations == 2
    assert result.wrong_edges == ("<malformed edge citation>",)
    assert result.status == "wrong"
