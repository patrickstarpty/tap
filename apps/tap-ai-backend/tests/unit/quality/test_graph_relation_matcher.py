from __future__ import annotations

from tap.quality.graph_relations import (
    ExpectedEdge,
    GoldenQuestion,
    QuestionResult,
    aggregate,
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
