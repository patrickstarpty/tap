from dataclasses import replace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.application.queries import InMemoryGraphStore
from tap.modules.graph.domain.index import SnapshotIndex
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)
from tap.modules.knowledge.application.graph_enrichment import GraphAnswerEnricher


def chain(size=5):
    snapshot = GraphSnapshot.create(
        snapshot_id="s",
        project_id=VALIDATION_SCOPE.project_id,
        source_revision_ids=("sr",),
        document_revision_ids=("dr",),
    )
    evidence = (
        Evidence(
            "ev",
            "s",
            "sr",
            "dr",
            "hit",
            {"kind": "text", "start": 0, "end": 1},
            "sha256:" + "a" * 64,
        ),
    )
    nodes = tuple(
        GraphNode(f"n{i}", "s", f"Entity {i}", "ENTITY", f"entity_{i}", ("ev",) if i == 0 else ())
        for i in range(size)
    )
    edges = tuple(
        GraphEdge(
            f"e{i}",
            "s",
            f"n{i}",
            f"n{i + 1}",
            "DEPENDS_ON",
            RelationOrigin.EXTRACTED,
            1.0 - i / size,
            ("ev",),
        )
        for i in range(size - 1)
    )
    return GraphSnapshotDraft(snapshot, nodes, edges, evidence, ())


def test_expansion_seeds_retrieved_chunks_and_bounds_hops():
    from tap.modules.knowledge.application.graph_expansion import GraphContextExpander

    draft = chain()
    # Only n0 is supported by this chunk; edges have distinct evidence so their endpoints
    # cannot become additional seeds.
    draft = replace(
        draft,
        evidence=(
            *draft.evidence,
            replace(draft.evidence[0], evidence_id="other", chunk_id="unretrieved"),
        ),
        edges=tuple(replace(edge, evidence_ids=("other",)) for edge in draft.edges),
    )
    index = SnapshotIndex.build(draft)
    expander = GraphContextExpander()
    one = expander.expand(index, ("hit",), "unmatched", hops=1)
    two = expander.expand(index, ("hit",), "unmatched", hops=2)
    assert [node.node_id for node in one.nodes] == ["n0", "n1"]
    assert [edge.edge_id for edge in two.edges] == ["e0", "e1"]
    assert expander.expand(index, (), "unmatched", hops=2).nodes == ()


def test_expansion_caps_nodes_and_edges_with_deterministic_confidence_order():
    from tap.modules.knowledge.application.graph_expansion import GraphContextExpander

    index = SnapshotIndex.build(chain(100))
    graph = GraphContextExpander().expand(index, ("hit",), "unmatched", hops=2)
    assert len(graph.nodes) == 40
    assert len(graph.edges) <= 80
    assert list(graph.edges) == sorted(
        graph.edges, key=lambda edge: (-edge.confidence, edge.edge_id)
    )
    assert graph == GraphContextExpander().expand(index, ("hit",), "unmatched", hops=2)


@pytest.mark.asyncio
async def test_enrichment_assigns_relation_labels_and_evidence_labels():
    store = InMemoryGraphStore()
    await store.publish(VALIDATION_SCOPE, chain())
    result = await GraphAnswerEnricher(store).enrich(
        VALIDATION_SCOPE,
        ("sr",),
        "unmatched",
        chunk_ids=("hit",),
        evidence_labels={"hit": "S1"},
        hops=2,
    )
    edge = next(fact for fact in result.facts if fact["kind"] == "edge")
    assert edge["relationLabel"] == "R1"
    assert edge["evidenceLabels"] == ["S1"]
    assert edge["sourceLabel"] == "Entity 0"
    assert edge["snapshotId"] == "s"
