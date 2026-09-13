import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.graph.application.queries import InMemoryGraphStore
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    NeighborQuery,
    PathQuery,
    RelationOrigin,
)


def _draft(identity: str = "snapshot-1") -> GraphSnapshotDraft:
    snapshot = GraphSnapshot.create(
        snapshot_id=identity,
        project_id=VALIDATION_SCOPE.project_id,
        source_revision_ids=("source-revision-1",),
        document_revision_ids=("document-revision-1",),
    )
    evidence = Evidence(
        "evidence-1",
        identity,
        "source-revision-1",
        "document-revision-1",
        "chunk-1",
        {"kind": "text", "start": 0, "end": 5},
        "sha256:" + "a" * 64,
    )
    nodes = (
        GraphNode("node-1", identity, "Policy", "ENTITY", "policy"),
        GraphNode("node-2", identity, "Claim", "ENTITY", "claim"),
        GraphNode("node-3", identity, "Rule", "ENTITY", "rule"),
    )
    edges = (
        GraphEdge(
            "edge-1",
            identity,
            "node-1",
            "node-2",
            "GOVERNS",
            RelationOrigin.EXTRACTED,
            1.0,
            ("evidence-1",),
        ),
        GraphEdge(
            "edge-2",
            identity,
            "node-2",
            "node-3",
            "SUPPORTS",
            RelationOrigin.EXTRACTED,
            1.0,
            ("evidence-1",),
        ),
    )
    return GraphSnapshotDraft(snapshot, nodes, edges, (evidence,), ())


@pytest.mark.asyncio
async def test_publish_replaces_only_the_same_project_and_source_set_active_pointer():
    store = InMemoryGraphStore()
    first = await store.publish(VALIDATION_SCOPE, _draft("snapshot-1"))
    await store.publish(VALIDATION_SCOPE, _draft("snapshot-2"))
    assert first.snapshot_id == "snapshot-1"
    assert (
        await store.active_snapshot(VALIDATION_SCOPE, ("source-revision-1",))
    ).snapshot_id == "snapshot-2"
    assert (await store.get_snapshot(VALIDATION_SCOPE, "snapshot-1")).snapshot_id == "snapshot-1"


@pytest.mark.asyncio
async def test_cross_project_reads_return_no_graph_facts():
    store = InMemoryGraphStore()
    await store.publish(VALIDATION_SCOPE, _draft())
    other = ProjectScopeContext(
        enterprise_id=VALIDATION_SCOPE.enterprise_id,
        project_id="other-project",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode=IdentityMode.VALIDATION,
    )
    assert await store.active_snapshot(other, ("source-revision-1",)) is None
    assert await store.get_snapshot(other, "snapshot-1") is None


@pytest.mark.asyncio
async def test_neighbors_and_paths_enforce_depth_and_node_budgets():
    store = InMemoryGraphStore()
    await store.publish(VALIDATION_SCOPE, _draft())
    neighbors = await store.neighbors(
        VALIDATION_SCOPE, NeighborQuery("snapshot-1", "node-1", depth=1, node_limit=2)
    )
    assert [node.node_id for node in neighbors.nodes] == ["node-1", "node-2"]
    with pytest.raises(ValueError, match="depth"):
        await store.neighbors(
            VALIDATION_SCOPE, NeighborQuery("snapshot-1", "node-1", depth=3, node_limit=10)
        )
    with pytest.raises(ValueError, match="node limit"):
        PathQuery("snapshot-1", "node-1", "node-3", node_limit=501)
    path = await store.bounded_path(
        VALIDATION_SCOPE, PathQuery("snapshot-1", "node-1", "node-3", node_limit=3)
    )
    assert [node.node_id for node in path.nodes] == ["node-1", "node-2", "node-3"]
