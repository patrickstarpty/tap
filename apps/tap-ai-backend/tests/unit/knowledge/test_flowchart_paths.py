from dataclasses import replace

from tap.modules.knowledge.domain.flowchart_paths import flowchart_path_context
from tap.modules.knowledge.domain.models import (
    ContentRole,
    DocumentAnchor,
    Evidence,
    IndexRevision,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)

DIGEST = "sha256:" + "a" * 64


def edge(start, end, condition="", *, label="S1", revision="revision-1"):
    text = f"流程图连线 {start} → {end}：{start} → {end}"
    if condition:
        text += f"；条件：{condition}"
    return Evidence(
        SourceFamily.DOC,
        label,
        label,
        None,
        text,
        SourceRevisionRef(
            "image",
            "doc",
            RevisionKind.BLOB_VERSION,
            revision,
            DIGEST,
            DocumentAnchor(bbox=(0, 0, 20, 20), inventory_item_id=label),
        ),
        DIGEST,
        ContentRole.SOURCE,
        label,
        label,
        IndexRevision("g", "v1", "v1"),
        "model",
        "acl",
        1.0,
        publication_id="published",
        approval_digest=DIGEST,
        approved_item_id=label,
    )


def test_paths_keep_branch_alternatives_separate_and_stop_cycles():
    context = flowchart_path_context(
        (
            edge("a", "b", "yes"),
            edge("b", "c", label="S2"),
            edge("a", "d", "no", label="S3"),
            edge("c", "a", label="S4"),
        )
    )
    paths = context["paths"]
    assert any(p["nodeIds"] == ["a", "b", "c", "a"] and p["termination"] == "cycle" for p in paths)
    assert any(p["nodeIds"] == ["a", "d"] for p in paths)
    assert all(
        not {"S1", "S3"} <= set(p["evidenceLabels"]) or p["nodeIds"].count("a") == 2 for p in paths
    )
    assert context["businessRulesStatus"] == "unknown-from-image"
    assert context["coverage"] == "retrieved-approved-edges-only"


def test_unpublished_edges_and_cross_revision_connections_are_not_paths():
    assert flowchart_path_context((replace(edge("a", "b"), publication_id=None),)) == {}
    context = flowchart_path_context(
        (edge("a", "b"), edge("b", "c", revision="revision-2", label="S2"))
    )
    assert all(len(path["evidenceLabels"]) == 1 for path in context["paths"])


def test_path_budget_marks_incomplete_traversal():
    context = flowchart_path_context(
        tuple(edge("a", str(i), label=f"S{i}") for i in range(10)), max_paths=2
    )
    assert len(context["paths"]) == 2
    assert context["truncated"] is True


def test_high_risk_payment_failure_retry_success_has_ordered_conditions():
    context = flowchart_path_context(
        (
            edge("risk", "payment", "high-risk", label="S1"),
            edge("payment", "retry", "failure", label="S2"),
            edge("retry", "payment", "retry-approved", label="S3"),
            edge("payment", "done", "success", label="S4"),
        )
    )
    retry = next(
        path
        for path in context["paths"]
        if path["nodeIds"] == ["risk", "payment", "retry", "payment", "done"]
    )
    assert [step["condition"] for step in retry["steps"]] == [
        "high-risk",
        "failure",
        "retry-approved",
        "success",
    ]
    assert retry["evidenceLabels"] == ["S1", "S2", "S3", "S4"]


def test_claim_cannot_combine_mutually_exclusive_branch_edges():
    from tap.modules.knowledge.domain.flowchart_paths import flowchart_claim_is_consistent

    context = flowchart_path_context((edge("a", "b", "yes"), edge("a", "c", "no", label="S2")))
    assert not flowchart_claim_is_consistent(("S1", "S2"), context)
    assert flowchart_claim_is_consistent(("S1",), context)


def test_retry_claim_may_combine_failure_and_success_at_different_visits():
    from tap.modules.knowledge.domain.flowchart_paths import flowchart_claim_is_consistent

    context = flowchart_path_context(
        (
            edge("pay", "retry", "failure"),
            edge("retry", "pay", label="S2"),
            edge("pay", "done", "success", label="S3"),
        )
    )
    assert flowchart_claim_is_consistent(("S1", "S2", "S3"), context)
